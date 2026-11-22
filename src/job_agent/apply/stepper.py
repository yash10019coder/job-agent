"""Step-by-step runner: run the apply pipeline one stage at a time on a live job, inspecting each stage.

  open -> perceive -> prefill -> resolve -> fill -> guards -> submit

Every stage prints what it saw/decided. By default it pauses after each stage; the page stays open in your
Brave tab so you can look at it. Nothing is submitted unless --allow-submit is given AND you type 'submit'.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page

from job_agent.apply import execute
from job_agent.apply.demo import (DemoResult, Row, _trusted, fill_until_stable, pick_apply_action, resolve_batch)
from job_agent.apply import controls
from job_agent.apply.llm import LLM
from job_agent.apply.opening import open_application
from job_agent.apply.perceive import Settler, perceive
from job_agent.apply.prefill import prefill
from job_agent.apply.submit import _submit_button, check_ready, guarded_submit
from job_agent.rate_limiter import RateLimiter

STAGES = ["open", "perceive", "prefill", "resolve", "fill", "guards", "submit"]
ROOT = Path(__file__).resolve().parents[3]


def _cut(text, n) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _table(headers: list[str], rows: list[list], widths: list[int]) -> str:
    line = lambda cells: "  ".join(_cut(c, w).ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
    return "\n".join([line(headers), line(["-" * w for w in widths]), *[line(r) for r in rows]])


@dataclass
class Ctx:
    page: Page
    llm: LLM
    facts: dict
    context: str
    resume: str | None
    settler: Settler
    result: DemoResult = field(default_factory=DemoResult)
    answers: dict = field(default_factory=dict)


async def stage_open(c: Ctx) -> None:
    p = await perceive(c.page)
    print(f"url:     {c.page.url}\ntitle:   {p.model.title}\nfields:  {len(p.model.fields)}   actions: {len(p.model.actions)}   frames: {len(p.frames)}")
    controls.use(c.llm)
    c.page, note = await open_application(c.page, c.llm)
    c.settler = Settler(c.page)
    await c.settler.settle()
    print(note)


async def stage_perceive(c: Ctx) -> None:
    m = (await perceive(c.page)).model
    rows = [[f.id, f.kind, "*" if f.required else "", f.label, f.value,
             "(opens on click)" if f.async_options else (f"{len(f.options)} options" if f.options else ""), f.section] for f in m.fields]
    print(_table(["id", "kind", "req", "question", "value now", "options", "section"], rows, [8, 14, 3, 46, 18, 16, 20]))
    print(f"\nheadings: {m.headings[:4]}   alerts: {m.alerts}")
    print("actions:  " + " | ".join(_cut(a.label, 24) + ("*" if a.primary else "") for a in m.actions[:12]))


async def stage_prefill(c: Ctx) -> None:
    m = (await perceive(c.page)).model
    rows, hit = [], 0
    for f in m.fields:
        if f.kind == "file":
            rows.append([f.label, "file", "uploaded from --resume if it is a resume field"])
            continue
        pf = prefill(f, c.facts)
        hit += bool(pf)
        rows.append([f.label, (pf.value if not pf.blank else "(leave blank)") if pf else "-> model", pf.basis if pf else ""])
    print(_table(["question", "rule answer", "from"], rows, [52, 30, 40]))
    print(f"\n{hit} of {len(m.fields)} fields are answered by rule (no model call); the rest go to the model.")


async def stage_resolve(c: Ctx) -> None:
    """Ask the model for answers WITHOUT touching the page (so you can review them first)."""
    m = (await perceive(c.page)).model
    pre = {f.id for f in m.fields if prefill(f, c.facts)}
    batch = [f for f in m.fields if f.kind != "file" and f.id not in pre]
    if not batch:
        return print("every field is answered by rule; no model call needed")
    job = f"{m.title}\n{' | '.join(m.headings)}\n{m.text_digest[:2500]}"
    c.answers = await resolve_batch(c.llm, c.context, batch, job)
    rows = []
    for f in batch:
        a = c.answers.get(f.id, {})
        verdict = "apply" if _trusted(a) else ("option step decides" if f.kind in {"select", "combobox", "radio_group", "checkbox_group"} else
                                                ("blank (optional)" if not f.required else "BLOCKED"))
        rows.append([f.label, a.get("value", ""), a.get("source", ""), f"{a.get('confidence', 0):.2f}", verdict, a.get("basis", "")])
    print(_table(["question", "answer", "source", "conf", "verdict", "why"], rows, [40, 26, 8, 4, 18, 50]))
    print(f"\nmodel cost so far: {json.dumps(c.llm.totals)}")


async def stage_fill(c: Ctx) -> None:
    c.result = DemoResult()
    await fill_until_stable(c.page, c.llm, c.context, c.resume, c.result, c.facts)
    for e in c.result.events:
        print(e[:200])
    rows = [[r.label, r.answer, r.source, f"{r.confidence:.2f}", r.status] for r in c.result.rows]
    print("\n" + _table(["question", "answer", "source", "conf", "status"], rows, [44, 28, 8, 4, 40]))
    print(f"\nrounds: {c.result.rounds}   model cost so far: {json.dumps(c.llm.totals)}")


def _limiter() -> RateLimiter:
    return RateLimiter(storage_path=ROOT / "outputs" / "rate_limiter" / "history.jsonl")


async def stage_guards(c: Ctx) -> None:
    if not c.result.rows:
        print("(no fill results in this run: checking the page itself only)")
    problems = await check_ready(c.page, c.result.rows, _limiter(), ROOT / "outputs" / "apply" / "submissions.jsonl", c.page.url)
    button, n = await _submit_button(await perceive(c.page))
    print(f"submit control: {button.label if button else 'NONE'} ({n} candidate{'s' if n != 1 else ''})")
    print("ALL GUARDS PASSED: safe to submit" if not problems else f"{len(problems)} guard(s) failed:\n" + "\n".join(f"  - {p}" for p in problems))


def _same_posting(a: str, b: str) -> bool:
    key = lambda u: (u.split("?")[0].split("#")[0].rstrip("/").lower())  # noqa: E731
    return bool(a) and key(a) == key(b)


async def stage_submit(c: Ctx, allowed: bool, confirm_url: str | None = None) -> None:
    """Real submit needs a human decision: type 'submit' in an interactive terminal, or (non-interactive) pass
    --confirm-url with the exact posting URL. Without either, nothing is sent."""
    if not allowed:
        return print("skipped: pass --allow-submit to enable the real Submit step")
    if sys.stdin.isatty():
        typed = await asyncio.to_thread(input, "Type 'submit' to press Submit on THIS live application (anything else cancels): ")
        if typed.strip().lower() != "submit":
            return print("cancelled")
    elif not _same_posting(confirm_url or "", c.page.url):
        return print("REFUSED: no interactive terminal to type 'submit' in, and --confirm-url did not match this page's URL. Nothing was sent.")
    out = await guarded_submit(c.page, c.result.rows, c.settler, c.page.url, _limiter(),
                               ROOT / "outputs" / "apply" / "submissions.jsonl", submit=True)
    print(f"{out.status.upper()}: {out.detail}")
    for p in out.problems:
        print(f"  - {p}")


async def run_steps(page: Page, llm: LLM, facts: dict, resume: str | None, until: str, pause: bool, allow_submit: bool, confirm_url: str | None = None) -> None:
    page.set_default_timeout(8000)
    c = Ctx(page, llm, facts, json.dumps(facts, indent=1), resume, Settler(page))
    stop = STAGES.index(until)
    for i, name in enumerate(STAGES[: stop + 1], 1):
        print(f"\n━━ STEP {i}/{len(STAGES)}: {name} " + "━" * 50)
        if name == "submit":
            await stage_submit(c, allow_submit, confirm_url)
        else:
            await {"open": stage_open, "perceive": stage_perceive, "prefill": stage_prefill,
                   "resolve": stage_resolve, "fill": stage_fill, "guards": stage_guards}[name](c)
        if pause and i <= stop:
            nxt = STAGES[i] if i < len(STAGES) and i <= stop else None
            if nxt and i <= stop:
                key = await asyncio.to_thread(input, f"\n[Enter] run next step ({nxt})   [q] stop here: ")
                if key.strip().lower() == "q":
                    break
    print(f"\nmodel totals: {json.dumps(llm.totals)}\nThe job tab is still open in Brave. Nothing was submitted unless the submit step said SUBMITTED.")
