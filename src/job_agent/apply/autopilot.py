"""Autopilot: apply, unattended, to every job in a list that is alive, fits, and can be completed.

Per job, in order: duplicate? fits (title/location)? already applied? rate limit / same company? alive? -> open a fresh window,
run the generic application loop (controller), close the window. Things the program must not or cannot do (captcha, account it
has no credentials for, SMS code, an unanswerable required question) become a logged SKIP, never a wait: nothing needs a human.
Writes results.jsonl, unanswered.jsonl and report.md to the run folder. Submits only when submit=True (and every guard passes).
"""
from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import Page

from job_agent.apply.controller import ROOT, run_application
from job_agent.apply.fit import fit, india_in_text
from job_agent.apply.states import Outcome
from job_agent.apply.submit import already_submitted
from job_agent.rate_limiter import RateLimiter

OpenPage = Callable[[str], Awaitable[Page]]
LiveCheck = Callable[[str], Awaitable[tuple[bool, str]]]


@dataclass
class JobResult:
    url: str
    company: str
    title: str
    status: str           # applied | ready | skipped | failed
    reason: str = ""
    outcome: str = ""
    steps: int = 0
    cost_usd: float = 0.0
    run_dir: str = ""


async def is_live(url: str) -> tuple[bool, str]:
    """Is the posting still there? Dead jobs often redirect to the board (`?error=true`) or return 404/410."""
    if url.startswith("file://"):
        return True, "local file"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url)
    except Exception as error:
        return False, f"unreachable ({type(error).__name__})"
    if r.status_code in (403, 429):
        return True, f"HTTP {r.status_code} to a plain probe (bot-protected): trying in the browser"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}"
    if "error=true" in str(r.url):
        return False, "redirected to the job board with an error flag"
    return True, "ok"


def _location_text(value) -> str:
    """Dumps store locations as text, {'label': ...} dicts or lists of those."""
    if isinstance(value, dict):
        return str(value.get("label") or value.get("name") or value.get("city") or "")
    if isinstance(value, list):
        return "; ".join(filter(None, (_location_text(v) for v in value)))
    return str(value or "")


_AGGREGATORS = ("linkedin.", "indeed.", "glassdoor.", "ziprecruiter.")


def jobs_from_dumps(data_dir: Path) -> list[dict]:
    """Every direct (non-aggregator) job link in the fetched job dumps; the fit gate and liveness check filter them later."""
    import glob

    out, seen = [], set()
    for f in sorted(glob.glob(str(data_dir / "*_jobs.json")) + glob.glob(str(data_dir / "sde_*.json"))):
        try:
            rows = json.load(open(f))
            rows = rows if isinstance(rows, list) else rows.get("jobs", [])
        except Exception:
            continue
        for r in rows:
            u = (r.get("job_url") or "") if isinstance(r, dict) else ""
            if u.startswith("http") and not any(a in urlsplit(u).netloc.lower() for a in _AGGREGATORS) and _posting_key(u) not in seen:
                seen.add(_posting_key(u))
                out.append({"url": u, "company": r.get("company"), "title": r.get("job_title"), "location": _location_text(r.get("location"))})
    return out


def _posting_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}".rstrip("/").lower()


def _applied_today(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    today, n = datetime.now().date().isoformat(), 0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        n += entry.get("status") == "submitted" and str(entry.get("ts", "")).startswith(today)
    return n


def _to_result(job: dict, run, out_dir: Path, run_dir: Path) -> JobResult:
    base = dict(url=job["url"], company=str(job.get("company") or ""), title=str(job.get("title") or ""), outcome=run.outcome.value,
                steps=run.steps, cost_usd=run.llm_cost, run_dir=str(run_dir))
    o = run.outcome
    if o == Outcome.APPLIED:
        return JobResult(status="applied", reason=run.detail, **base)
    if o == Outcome.DRY_RUN_READY:
        return JobResult(status="ready", reason="all guards passed; dry run, nothing sent", **base)
    if o == Outcome.NEEDS_HUMAN:
        return JobResult(status="skipped", reason=f"needs a human or an account: {run.detail[:200]}", **base)
    if o == Outcome.NEEDS_ANSWER:
        with (out_dir / "unanswered.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"url": job["url"], "company": base["company"], "title": base["title"], "questions": run.detail}) + "\n")
        return JobResult(status="skipped", reason=f"unanswered required question: {run.detail[:200]}", **base)
    if o in (Outcome.CLOSED, Outcome.DEAD, Outcome.NO_APPLY_CONTROL):
        return JobResult(status="skipped", reason=f"{o.value}: {run.detail[:160]}", **base)
    return JobResult(status="failed", reason=f"{o.value}: {run.detail[:200]}", **base)


async def run_autopilot(jobs: list[dict], open_page: OpenPage, make_llm: Callable, facts: dict, resume: str | None, out_dir: Path,
                        submit: bool = False, max_per_day: int = 20, concurrency: int = 2, limiter: RateLimiter | None = None,
                        log_path: Path | None = None, live_check: LiveCheck = is_live, pace_s: tuple[float, float] = (20.0, 60.0),
                        click_wait_s: float = 10.0) -> list[JobResult]:
    out_dir.mkdir(parents=True, exist_ok=True)
    limiter = limiter or RateLimiter(storage_path=ROOT / "outputs" / "rate_limiter" / "history.jsonl")
    log_path = log_path or ROOT / "outputs" / "apply" / "submissions.jsonl"
    filters, results, seen, queue, verify = facts.get("job_filters"), [], set(), [], set()

    def skip(job: dict, reason: str) -> None:
        results.append(JobResult(job["url"], str(job.get("company") or ""), str(job.get("title") or ""), "skipped", reason))

    for job in jobs:  # cheap checks first: no browser, no network
        key = _posting_key(job["url"])
        if key in seen:
            skip(job, "duplicate of an earlier job in this list")
            continue
        seen.add(key)
        decision = fit(job, filters)
        if not decision.ok:
            skip(job, "does not fit: " + "; ".join(decision.reasons))
        elif already_submitted(log_path, job["url"]):
            skip(job, "already applied")
        else:
            queue.append(job)
            if decision.needs_page_check:
                verify.add(job["url"])
    live_queue = []
    for job in queue:
        alive, why = await live_check(job["url"])
        (live_queue.append(job) if alive else skip(job, f"dead link: {why}"))

    sem, lock = asyncio.Semaphore(concurrency), asyncio.Lock()
    reserved = _applied_today(log_path)

    async def work(index: int, job: dict) -> None:
        nonlocal reserved
        async with sem:
            async with lock:
                if submit and reserved >= max_per_day:
                    return skip(job, f"daily cap reached ({max_per_day})")
                allowed, reason = limiter.can_apply(job["url"])
                if not allowed:
                    return skip(job, f"rate limit: {reason}")
                if submit:
                    reserved += 1
                await asyncio.sleep(random.uniform(*pace_s))  # human-like gap between application starts
            page, run, run_dir = None, None, out_dir / f"{index:03d}"
            try:
                page = await open_page(job["url"])
                if job["url"] in verify:  # vague location in the dump: the job page itself must say India
                    text = await page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 8000)")
                    if not india_in_text(text):
                        async with lock:
                            reserved -= 1 if submit else 0
                        return skip(job, "location not verified as India (the dump says '" + str(job.get("location") or "") + "' and the page does not mention India)")
                run = await run_application(page, make_llm(), facts, resume, run_dir, submit=submit, limiter=limiter, log_path=log_path,
                                            click_wait_s=click_wait_s)
                result = _to_result(job, run, out_dir, run_dir)
            except Exception as error:
                result = JobResult(job["url"], str(job.get("company") or ""), str(job.get("title") or ""), "failed", f"crashed: {str(error).splitlines()[0][:160]}")
            finally:
                for p in {page, run.page if run else None}:
                    if p is not None and not p.is_closed():
                        try:
                            await p.close()
                        except Exception:
                            pass
            if submit and result.status != "applied":
                async with lock:
                    reserved -= 1  # the slot was not used
            results.append(result)

    await asyncio.gather(*[work(i, j) for i, j in enumerate(live_queue, 1)])
    _write_outputs(out_dir, results, submit)
    return results


def _write_outputs(out_dir: Path, results: list[JobResult], submit: bool) -> None:
    (out_dir / "results.jsonl").write_text("\n".join(json.dumps(asdict(r)) for r in results) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    reasons: dict[str, int] = {}
    for r in results:
        if r.status in ("skipped", "failed"):
            key = r.reason.split(":")[0][:60]
            reasons[key] = reasons.get(key, 0) + 1
    lines = [f"# Autopilot report {datetime.now():%Y-%m-%d %H:%M}", "", f"Mode: {'LIVE (submits)' if submit else 'dry run (nothing sent)'}",
             f"Jobs in list: {len(results)}   " + "   ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
             f"Model cost: ${sum(r.cost_usd for r in results):.3f}", ""]
    for status, title in (("applied", "Applied"), ("ready", "Ready (dry run)"), ("failed", "Failed"), ("skipped", "Skipped")):
        rows = [r for r in results if r.status == status]
        if rows:
            lines += [f"## {title} ({len(rows)})", *[f"- {r.company or '-'} | {r.title[:60]} | {r.reason[:140]} | {r.url}" for r in rows], ""]
    if reasons:
        lines += ["## Why jobs were skipped or failed", *[f"- {k}: {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])], ""]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
