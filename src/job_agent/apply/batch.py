"""Run several applications concurrently (one Brave window each) and keep full run data under data/runs/.

Every job gets a folder: result.json (rows, events, submit outcome, LLM cost), transcript.txt,
page_before.json / page_after.json (semantic page models), events.log (timestamped), error.txt on crash.
The batch folder holds summary.json. Dry run unless submit=True.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page

from job_agent.apply.browser import BraveSession
from job_agent.apply.demo import render, run_demo
from job_agent.apply.llm import LLM
from job_agent.apply.perceive import Settler, perceive


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


_window_lock = asyncio.Lock()
MAX_PARALLEL = 10  # hard ceiling on jobs running at once, whatever a caller asks for


def clamp_parallel(n: int, limit: int = MAX_PARALLEL) -> int:
    return max(1, min(int(n), limit))


async def blank_window(session: BraveSession) -> Page:
    """Own window per job: background tabs get throttled by the browser, which would stall the page logic."""
    assert session.browser and session.context
    async with _window_lock:  # pick out OUR new page from the context, never another job's popup
        before = set(session.context.pages)
        cdp = await session.browser.new_browser_cdp_session()
        await cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True})
        for _ in range(100):
            fresh = [p for p in session.context.pages if p not in before and p.url in ("about:blank", "")]
            if fresh:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("new window did not appear")
        page = fresh[0]
    return page


async def new_window(session: BraveSession, url: str) -> Page:
    page = await blank_window(session)
    await page.goto(url, wait_until="domcontentloaded")
    await page.bring_to_front()
    try:  # single-page apps (Ashby, Rippling) render after DOMContentLoaded
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await Settler(page).settle()
    return page


def _write(path: Path, data) -> None:
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2, default=str), encoding="utf-8")


async def run_one(session: BraveSession, job: dict, run_dir: Path, facts: dict, resume: str | None,
                  model: str | None, submit: bool, sem: asyncio.Semaphore, engine: str = "demo", keep_open: bool = False, auth=None) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "events.log"

    def note(msg: str) -> None:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")

    summary = {"ats": job.get("ats", "-"), "company": job.get("company"), "title": job.get("title"), "url": job["url"],
               "dir": str(run_dir), "status": "crashed"}
    started = time.monotonic()
    llm = LLM(model=model or None)
    opened: list = []
    async with sem:
        for attempt in (1, 2):  # one retry: a window can be closed under us by the browser
            try:
                note(f"attempt {attempt}: open {job['url']}")
                attempt_fn = _attempt_controller if engine == "controller" else _attempt
                summary.update(await attempt_fn(session, job, run_dir, facts, resume, llm, submit, note, started, opened, auth))
                summary.pop("error", None)
                break
            except Exception:
                _write(run_dir / "error.txt", traceback.format_exc())
                note("CRASHED, see error.txt")
                summary["error"] = traceback.format_exc().strip().splitlines()[-1][:200]
        if not keep_open:  # never leave windows piling up
            for p in {p for p in opened if p is not None}:
                try:
                    if not p.is_closed():
                        await p.close()
                except Exception:
                    pass
    summary.update(llm=llm.totals, seconds=round(time.monotonic() - started, 1))
    note(f"done: {summary['status']}")
    return summary


async def _attempt(session, job, run_dir, facts, resume, llm, submit, note, started, opened, auth=None) -> dict:
    page = await new_window(session, job["url"])
    opened.append(page)
    _write(run_dir / "meta.json", job)
    _write(run_dir / "page_before.json", (await perceive(page)).model.model_dump())
    note("running fill loop")
    result = await run_demo(page, llm, json.dumps(facts, indent=1), resume=resume, facts=facts, submit=submit)
    for line in result.events:
        note(line)
    after = (await perceive(page)).model
    _write(run_dir / "page_after.json", after.model_dump())
    _write(run_dir / "transcript.txt", render(result, llm))
    _write(run_dir / "result.json", {"job": job, "rounds": result.rounds, "events": result.events,
                                      "rows": [asdict(r) for r in result.rows], "stopped_before": result.stopped_before,
                                      "submit": asdict(result.submit) if result.submit else None,
                                      "llm": llm.totals, "model": llm.model, "seconds": round(time.monotonic() - started, 1),
                                      "unparsed": after.unparsed})
    statuses = [r.status for r in result.rows]
    return dict(
        status=(result.submit.status if result.submit else "no_submit_control"),
        fields=len(result.rows), ok=sum(s == "ok" for s in statuses),
        blocked=sum(s.startswith("BLOCKED") for s in statuses),
        failed=sum(not (s == "ok" or s.startswith(("BLOCKED", "left blank", "skipped"))) for s in statuses),
        problems=(result.submit.problems if result.submit else []), unparsed=len(after.unparsed))


async def _attempt_controller(session, job, run_dir, facts, resume, llm, submit, note, started, opened, auth=None) -> dict:
    """The generic loop (classify -> act -> verify) on one job; dry run unless the caller passes submit (apply-run never does)."""
    from job_agent.apply.controller import run_application

    page = await new_window(session, job["url"])
    opened.append(page)
    _write(run_dir / "meta.json", job)
    note("running the application loop")
    result = await run_application(page, llm, facts, resume, run_dir, submit=submit, auth=auth)
    opened.append(result.page)
    end = result.page
    unparsed = 0
    if end is not None and not end.is_closed():
        after = (await perceive(end)).model
        unparsed = len(after.unparsed)
        _write(run_dir / "page_after.json", after.model_dump())
    statuses = [r.status for r in result.rows]
    _write(run_dir / "result.json", {
        "job": job, "outcome": result.outcome.value, "detail": result.detail, "steps": result.steps,
        "states": [s.value for s in result.states], "hops": [{"kind": h.kind, "to": h.to_url} for h in result.hops],
        "rows": [asdict(r) for r in result.rows], "llm": llm.totals, "model": llm.model, "seconds": round(time.monotonic() - started, 1)})
    note(f"outcome {result.outcome.value}: {result.detail[:160]}")
    return dict(status=result.outcome.value, detail=result.detail[:300], steps=result.steps, states=[s.value for s in result.states],
                fields=len(result.rows), ok=sum(s == "ok" for s in statuses), blocked=sum(s.startswith("BLOCKED") for s in statuses),
                failed=sum(not (s == "ok" or s.startswith(("BLOCKED", "left blank", "skipped"))) for s in statuses),
                problems=[result.detail] if result.detail else [], unparsed=unparsed)


async def run_batch(session: BraveSession, jobs: list[dict], base: Path, facts: dict, resume: str | None,
                    model: str | None, submit: bool = False, concurrency: int = 5, engine: str = "demo", keep_open: bool = False, auth=None) -> list[dict]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = base / stamp
    sem = asyncio.Semaphore(clamp_parallel(concurrency))
    tasks = [run_one(session, j, batch_dir / f"{i + 1}-{_slug(j.get('ats', 'job'))}-{_slug(j.get('company') or urlparse(j['url']).netloc)}", facts, resume, model, submit, sem, engine, keep_open, auth)
             for i, j in enumerate(jobs)]
    results = await asyncio.gather(*tasks)
    batch_dir.mkdir(parents=True, exist_ok=True)
    _write(batch_dir / "summary.json", {"submit": submit, "model": model, "jobs": results})
    return results
