"""Thin structured-output LLM client with per-call cost logging.

Providers: Vertex AI / Gemini (GEMINI_API_KEY, native REST) or OpenRouter (OPENROUTER_API_KEY).
Gemini is preferred when its key is present. `Decider` is the seam for a dedicated decision model
such as jev: same `decide()` contract, swap the implementation.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
VERTEX_ENDPOINT = "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent"
DEFAULT_MODELS = {"vertex": "gemini-3.5-flash-lite", "openrouter": "google/gemini-3.5-flash-lite"}
# USD per million tokens (input, output incl. thinking); estimates used only when the API reports no cost.
PRICES = {"gemini-3.6-flash": (0.75, 3.75), "gemini-3.8-flash": (0.75, 3.75), "gemini-3.5-flash-lite": (0.30, 2.50), "gemini-3.1-flash-lite": (0.25, 1.50)}


def _find_key(name: str) -> str:
    key = os.environ.get(name, "")
    if key:
        return key
    for directory in (Path.cwd(), *Path(__file__).resolve().parents):
        env = directory / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                var, _, value = line.partition("=")
                if var.strip() == name and value.strip():
                    return value.strip().strip("\"'")
    return ""


def pick_provider() -> str:
    if _find_key("GEMINI_API_KEY"):
        return "vertex"
    if _find_key("OPENROUTER_API_KEY"):
        return "openrouter"
    raise RuntimeError("No API key: set GEMINI_API_KEY (Vertex/Gemini) or OPENROUTER_API_KEY in the environment or .env")


@dataclass
class CallLog:
    purpose: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    seconds: float


@dataclass
class LLM:
    model: str | None = None
    provider: str | None = None
    thinking: str = "low"  # Gemini thinking level: minimal | low | medium | high (more = slower and pricier)
    calls: list[CallLog] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.provider = self.provider or pick_provider()
        self.model = self.model or DEFAULT_MODELS[self.provider]

    async def complete_json(self, purpose: str, system: str, user: str, schema: dict, name: str = "answer") -> dict:
        if self.provider == "vertex":
            url = VERTEX_ENDPOINT.format(model=self.model)
            headers = {"x-goog-api-key": _find_key("GEMINI_API_KEY"), "Content-Type": "application/json"}
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseJsonSchema": schema,
                                     "thinkingConfig": {"thinkingLevel": self.thinking}},
            }
        else:
            url = OPENROUTER_ENDPOINT
            headers = {"Authorization": f"Bearer {_find_key('OPENROUTER_API_KEY')}", "Content-Type": "application/json"}
            body = {
                "model": self.model, "temperature": 0, "usage": {"include": True},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
            }
        last_error = ""
        async with httpx.AsyncClient(timeout=90) as client:
            for attempt in range(3):
                started = asyncio.get_running_loop().time()
                response = await client.post(url, json=body, headers=headers)
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}"
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                if response.status_code >= 400:  # surface the API's own message, never the key
                    detail = response.json().get("error", {}).get("message", response.text)[:300] if response.text else ""
                    raise RuntimeError(f"{self.provider} API error {response.status_code}: {detail}")
                data = response.json()
                elapsed = asyncio.get_running_loop().time() - started
                if self.provider == "vertex":
                    usage = data.get("usageMetadata", {})
                    pin = usage.get("promptTokenCount", 0)
                    pout = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
                    rate = PRICES.get(self.model, (0.0, 0.0))
                    self.calls.append(CallLog(purpose, pin, pout, (pin * rate[0] + pout * rate[1]) / 1e6, elapsed))
                    parts = data["candidates"][0]["content"]["parts"]
                    return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
                usage = data.get("usage", {})
                self.calls.append(CallLog(purpose, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
                                          float(usage.get("cost", 0) or 0), elapsed))
                return json.loads(data["choices"][0]["message"]["content"])
        raise RuntimeError(f"LLM call failed after retries ({last_error})")

    @property
    def totals(self) -> dict:
        return {
            "calls": len(self.calls),
            "prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "completion_tokens": sum(c.completion_tokens for c in self.calls),
            "cost_usd": round(sum(c.cost for c in self.calls), 5),
            "cost_note": "estimated from list prices" if self.provider == "vertex" else "reported by OpenRouter",
        }


@dataclass
class Decision:
    choice: str
    confidence: float
    rationale: str = ""


class Decider(Protocol):
    async def decide(self, question: str, options: list[str], context: str) -> Decision: ...


_NONE = "NONE OF THESE"
_LONG_LIST = 40  # above this, choosing from the full list overwhelms small models: answer first, then match locally


def _n(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


class FlashDecider:
    """Picks one of the given options. Short lists: an enum of the exact labels (the model cannot invent one).
    Long lists (countries, states, schools): the model first states the correct answer in plain words, we match it
    locally, and only an ambiguous match goes back to the model as a short enum. A jev adapter would implement the
    same `decide()`."""

    SYSTEM = ("You pick the single best option for a job-application question on behalf of the applicant. Use the facts and "
              "the job description in the context, and DERIVE where needed: treat the employment history as complete; work "
              "eligibility follows citizenship versus the job's country; apply answer_policy for standing preferences; sensitive "
              "questions follow the eeo block. If the facts do not settle it, choose "
              "the most plausible option for a typical candidate for this role (an educated guess); never refuse to choose.")

    def __init__(self, llm: LLM) -> None:
        self.llm = llm

    async def decide(self, question: str, options: list[str], context: str) -> Decision:
        if len(options) > _LONG_LIST:
            return await self._decide_long(question, options, context)
        return await self._pick(question, options, context)

    async def _pick(self, question: str, options: list[str], context: str) -> Decision:
        schema = {
            "type": "object", "additionalProperties": False, "required": ["choice", "confidence", "rationale"],
            "properties": {"choice": {"type": "string", "enum": options}, "confidence": {"type": "number", "description": "0 to 1"},
                           "rationale": {"type": "string"}},
        }
        user = f"Context:\n{context}\n\nQuestion: {question}\nOptions: {json.dumps(options)}"
        out = await self.llm.complete_json("decide_option", self.SYSTEM, user, schema, "decision")
        return Decision(out["choice"], float(out["confidence"]), out.get("rationale", ""))

    async def state_answer(self, question: str, context: str, list_size: int = 0) -> Decision:
        """Long lists only: the correct answer in plain words (e.g. 'India'), to be matched/typed into the widget."""
        schema = {
            "type": "object", "additionalProperties": False, "required": ["answer", "confidence", "rationale"],
            "properties": {"answer": {"type": "string", "description": "The correct answer in a few plain words (e.g. 'India'), or 'NONE' if the facts do not support one"},
                           "confidence": {"type": "number"}, "rationale": {"type": "string"}},
        }
        user = f"Context:\n{context}\n\nQuestion: {question}\n(The form offers a long list of options; state the answer in plain words.)"
        out = await self.llm.complete_json("decide_long_answer", self.SYSTEM, user, schema, "answer")
        return Decision(out["answer"].strip(), float(out["confidence"]), out.get("rationale", ""))

    async def _decide_long(self, question: str, options: list[str], context: str) -> Decision:
        said = await self.state_answer(question, context, len(options))
        out = {"answer": said.choice}
        confidence, why, answer = said.confidence, said.rationale, _n(said.choice)
        real = [o for o in options if o != _NONE]
        if not answer or answer == "none":
            return Decision(_NONE if _NONE in options else real[0], 0.0 if _NONE not in options else confidence, why)
        exact = [o for o in real if _n(o) == answer]
        prefixed = [o for o in real if _n(o).startswith(answer + " ")]
        if len(exact) == 1 or (not exact and len(prefixed) == 1):     # e.g. "India" -> "India +91"
            return Decision((exact or prefixed)[0], confidence, why)
        import difflib
        by_norm = {_n(o): o for o in real}
        near = list(dict.fromkeys([by_norm[k] for k in by_norm if answer in k] + [by_norm[k] for k in difflib.get_close_matches(answer, list(by_norm), n=15, cutoff=0.4)]))[:15]
        if not near:
            return Decision(_NONE if _NONE in options else real[0], 0.0, f"no option resembles {out['answer']!r}")
        picked = await self._pick(question, near + ([_NONE] if _NONE in options else []), context)
        return Decision(picked.choice, min(confidence, picked.confidence) if picked.choice != _NONE else 0.0, why or picked.rationale)
