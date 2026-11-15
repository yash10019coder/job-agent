"""Page meaning by embeddings: no wording rules, no LLM call.

`describe` turns a PageModel into a short description (title, headings, notice, text start, field and button labels; never
values). A small local embedding model (fastembed, paraphrase-multilingual-mpnet-base-v2, CPU, multilingual) vectorises it, and the nearest labeled example
pages (semantic_bank.py, plus any learned examples in data/semantic_learned.json) vote for a state. The result carries a
confidence (similarity to the winning state) and a margin over the runner-up; callers treat low values as "unsure".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from job_agent.apply.semantic_bank import BANK

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
LEARNED = Path(__file__).resolve().parents[3] / "data" / "semantic_learned.json"
TOP_K = 3   # a state's score = mean similarity of its K closest examples


def describe(model, max_chars: int = 900) -> str:
    """Compact meaning-bearing text for one page. Field VALUES are never included (they can hold personal data)."""
    main = [f for f in model.fields if f.region in {"main", "dialog"}]
    parts = [f"Title: {model.title[:90]}"]
    if model.headings:
        parts.append("Headings: " + "; ".join(h[:80] for h in model.headings[:4]))
    if model.alerts:
        parts.append("Notice: " + " ".join(a[:200] for a in model.alerts[:2]))
    text = re.sub(r"\s+", " ", model.text_digest or "").strip()
    if text:
        parts.append("Text: " + text[:260])
    if main:
        parts.append("Fields: " + "; ".join(f"{f.kind} {f.label[:50]}" for f in main[:12]))
    buttons = [a.label[:40] for a in model.actions if a.region in {"main", "dialog", "footer"}][:10]
    if buttons:
        parts.append("Buttons: " + "; ".join(buttons))
    return " | ".join(parts)[:max_chars]


@lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME)


@lru_cache(maxsize=2048)
def _embed_cached(text: str) -> tuple:
    vec = np.asarray(next(iter(_embedder().embed([text]))), dtype=np.float32)
    return tuple((vec / (np.linalg.norm(vec) or 1.0)).tolist())


def embed(text: str) -> np.ndarray:
    return np.asarray(_embed_cached(text), dtype=np.float32)


@lru_cache(maxsize=1)
def _bank() -> tuple[list[str], np.ndarray]:
    examples = [(state, text) for state, texts in BANK.items() for text in texts]
    if LEARNED.exists():
        try:
            examples += [(e["state"], e["text"]) for e in json.loads(LEARNED.read_text(encoding="utf-8"))]
        except (ValueError, KeyError, TypeError):
            pass
    vecs = np.stack([embed(t) for _, t in examples])
    return [s for s, _ in examples], vecs


@dataclass
class Meaning:
    state: str
    confidence: float          # cosine similarity of the winning state's closest examples (0..1)
    margin: float              # winner minus runner-up
    scores: dict[str, float]


def predict_text(text: str, exclude: str | None = None) -> Meaning:
    labels, vecs = _bank()
    sims = vecs @ embed(text)
    scores: dict[str, float] = {}
    for state in dict.fromkeys(labels):
        idx = [i for i, l in enumerate(labels) if l == state]
        top = sorted((float(sims[i]) for i in idx), reverse=True)[:TOP_K]
        scores[state] = sum(top) / len(top)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return Meaning(ranked[0][0], ranked[0][1], ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0), scores)


def predict(model) -> Meaning:
    return predict_text(describe(model))


def learn(model, state: str) -> None:
    """Remember a real page (description only) under its correct state; the next load of the bank includes it."""
    data = json.loads(LEARNED.read_text(encoding="utf-8")) if LEARNED.exists() else []
    data.append({"state": state, "text": describe(model)})
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    LEARNED.write_text(json.dumps(data, indent=1), encoding="utf-8")
    _bank.cache_clear()


# --- wording-driven states only (closed / dead / confirmation / email_code / bot_block, else "other") -----------------
WORDING = {"closed", "dead", "confirmation", "email_code", "bot_block"}


def prose(model) -> str:
    """Only what the page SAYS (title, headings, notices, text start): no field or button lists."""
    parts = [model.title[:90], *[h[:80] for h in model.headings[:4]], *[a[:200] for a in model.alerts[:2]],
             re.sub(r"\s+", " ", model.text_digest or "").strip()[:260]]
    return ". ".join(p for p in parts if p)


@lru_cache(maxsize=1)
def _wording_bank() -> tuple[list[str], np.ndarray]:
    def view(text: str) -> str:
        d = {k: v for k, _, v in (seg.partition(": ") for seg in text.split(" | "))}
        return ". ".join(d[k] for k in ("Title", "Headings", "Notice", "Text") if d.get(k))

    examples = [(s if s in WORDING else "other", view(t)) for s, ts in BANK.items() for t in ts]
    if LEARNED.exists():
        try:
            examples += [(e["state"] if e["state"] in WORDING else "other", view(e["text"])) for e in json.loads(LEARNED.read_text(encoding="utf-8"))]
        except (ValueError, KeyError, TypeError):
            pass
    return [s for s, _ in examples], np.stack([embed(t) for _, t in examples])


def predict_wording(model) -> Meaning:
    labels, vecs = _wording_bank()
    sims = vecs @ embed(prose(model))
    scores: dict[str, float] = {}
    for state in dict.fromkeys(labels):
        top = sorted((float(sims[i]) for i, l in enumerate(labels) if l == state), reverse=True)[:TOP_K]
        scores[state] = sum(top) / len(top)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return Meaning(ranked[0][0], ranked[0][1], ranked[0][1] - ranked[1][1], scores)


UNKNOWN_QUEUE = Path(__file__).resolve().parents[3] / "data" / "semantic_unknown.jsonl"


def record_unknown(model, url: str) -> None:
    """Queue a page the classifier could not place (description only, no field values) so it can be labelled later."""
    from urllib.parse import urlsplit

    try:
        UNKNOWN_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with UNKNOWN_QUEUE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"host": urlsplit(url).netloc, "text": describe(model)}) + "\n")
    except OSError:
        pass


def label_queue(states: list[str], ask) -> int:
    """Walk the queue; `ask(text) -> state | ''` (empty = skip). Labelled pages become examples; skipped ones stay queued."""
    if not UNKNOWN_QUEUE.exists():
        return 0
    keep, done = [], 0
    data = json.loads("[" + ",".join(l for l in UNKNOWN_QUEUE.read_text(encoding="utf-8").splitlines() if l.strip()) + "]")
    for item in data:
        state = ask(item["text"])
        if state in states:
            learned = json.loads(LEARNED.read_text(encoding="utf-8")) if LEARNED.exists() else []
            learned.append({"state": state, "text": item["text"]})
            LEARNED.parent.mkdir(parents=True, exist_ok=True)
            LEARNED.write_text(json.dumps(learned, indent=1), encoding="utf-8")
            done += 1
        else:
            keep.append(item)
    UNKNOWN_QUEUE.write_text("".join(json.dumps(i) + "\n" for i in keep), encoding="utf-8")
    _bank.cache_clear()
    _wording_bank.cache_clear()
    return done
