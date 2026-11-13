"""The generic application loop: observe -> classify -> act -> verify, until a terminal outcome.

No vendor knowledge: every decision comes from page evidence (classify.py). The loop never decides to submit by itself:
the final click always goes through submit.guarded_submit (every guard, dry run unless submit=True). States it cannot
handle yet (login, signup, SSO, emailed code, captcha) end as NEEDS_HUMAN with the browser window left open.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page

from job_agent.apply.classify import Classification, classify_page
from job_agent.apply.demo import DemoResult, Row, fill_until_stable
from job_agent.apply.llm import LLM
from job_agent.apply.navigate import Hop, click_and_follow
from job_agent.apply.perceive import Settler, diff, perceive
from job_agent.apply.states import Observation, Outcome, PageState
from job_agent.apply.submit import _STRONG_SUBMIT, _WEAK_SUBMIT, captcha_visible, check_step_ready, guarded_submit
from job_agent.apply.trace import Trace
from job_agent.rate_limiter import RateLimiter

ROOT = Path(__file__).resolve().parents[3]
_NEXT = re.compile(r"^(next|next step|continue|save (and|&) (continue|next)|proceed|continue to .+|review( (and|&) submit| application)?)$", re.I)
_HUMAN_STATES = {PageState.LOGIN, PageState.SIGNUP, PageState.ACCOUNT_CHOICE, PageState.SSO_ONLY, PageState.EMAIL_CODE, PageState.CAPTCHA}
_TERMINAL = {PageState.DEAD: Outcome.DEAD, PageState.CLOSED: Outcome.CLOSED, PageState.LISTING: Outcome.CLOSED,
             PageState.BOT_BLOCK: Outcome.BLOCKED, PageState.EMAIL_ONLY: Outcome.NEEDS_HUMAN}


@dataclass
class RunResult:
    outcome: Outcome
    detail: str = ""
    states: list[PageState] = field(default_factory=list)
    hops: list[Hop] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    llm_cost: float = 0.0
    steps: int = 0
    page: Page | None = None  # the page the run ended on (it may be a new tab)


class _Status:
    """Remembers the HTTP status of the page's last top-level navigation."""

    def __init__(self, page: Page) -> None:
        self.value: int | None = None
        page.on("response", lambda r: setattr(self, "value", r.status) if r.request.is_navigation_request() and r.frame == page.main_frame else None)


async def observe(page: Page, status: _Status, from_url: str | None) -> tuple[Observation, object]:
    perception = await perceive(page)
    hosts = sorted({urlparse(f.url).netloc for f in page.frames if f.url.startswith("http") and f != page.main_frame})
    return Observation(page.url, status.value, perception.model, hosts, await captcha_visible(page), from_url), perception


def _controls(model) -> tuple[list, list]:
    """(next-like controls, submit-like controls) among enabled actions."""
    enabled = [a for a in model.actions if not a.disabled and a.region in {"main", "dialog", "footer"}]  # sticky footers hold Next/Submit
    submit = [a for a in enabled if _STRONG_SUBMIT.search(a.label.strip())] or [a for a in enabled if _WEAK_SUBMIT.search(a.label.strip())]
    nxt = [a for a in enabled if _NEXT.match(a.label.strip()) and a not in submit]
    return nxt, submit


def _errors(problems: list[str]) -> tuple[str, ...]:
    return tuple(sorted(p for p in problems if p.startswith(("field shows an error", "page alert"))))


async def run_application(page: Page, llm: LLM, facts: dict, resume: str | None, run_dir: Path | None = None, submit: bool = False,
                          max_steps: int = 40, max_hops: int = 3, deadline_s: float = 600, click_wait_s: float = 10.0,
                          limiter: RateLimiter | None = None, log_path: Path | None = None) -> RunResult:
    import json

    page.set_default_timeout(8000)
    limiter = limiter or RateLimiter(storage_path=ROOT / "outputs" / "rate_limiter" / "history.jsonl")
    log_path = log_path or ROOT / "outputs" / "apply" / "submissions.jsonl"
    trace, context = Trace(run_dir / "trace.jsonl" if run_dir else None), json.dumps(facts, indent=1)
    res, status, started = RunResult(Outcome.STUCK), _Status(page), time.monotonic()
    start_url, last_errors, same_error, idle = page.url, (), 0, 0

    def done(outcome: Outcome, detail: str) -> RunResult:
        res.outcome, res.detail, res.page = outcome, detail, page
        res.llm_cost = float(llm.totals.get("cost_usd", 0) or 0)
        trace.log(step=res.steps, final=outcome.value, detail=detail, cost_usd=res.llm_cost)
        return res

    while res.steps < max_steps and time.monotonic() - started < deadline_s:
        res.steps += 1
        if page.is_closed():
            return done(Outcome.STUCK, "the page was closed")
        obs, _ = await observe(page, status, start_url if res.steps == 1 or page.url != start_url else None)
        for _ in range(4):  # single-page apps render after the load event: give a blank page a few chances before judging it
            blank = not obs.model.fields and not obs.model.actions  # nothing to act on yet: still rendering
            if not blank or obs.status in (403, 404, 410, 429) or (obs.status or 0) >= 500:
                break
            await Settler(page).settle()
            await asyncio.sleep(1.0)
            obs, _ = await observe(page, status, start_url if page.url != start_url else None)
        c: Classification = await classify_page(obs, llm)
        prev = res.states[-1] if res.states else None
        res.states.append(c.state)
        trace.log(step=res.steps, url=page.url, state=c.state.value, confidence=c.confidence, reasons=c.reasons, source=c.source,
                  fields=len(obs.model.fields), actions=len(obs.model.actions))

        if c.state in _TERMINAL:
            return done(_TERMINAL[c.state], "; ".join(c.reasons))
        if c.state in _HUMAN_STATES:
            return done(Outcome.NEEDS_HUMAN, f"{c.state.value}: {'; '.join(c.reasons)} (window left open)")
        if c.state == PageState.CONFIRMATION:
            if prev in {PageState.FORM_STEP, PageState.REVIEW, PageState.EMAIL_CODE, PageState.CAPTCHA}:
                return done(Outcome.APPLIED, "confirmation after the final step")
            return done(Outcome.STUCK, "confirmation wording without a preceding submit: not counted as applied")
        if c.state == PageState.UNKNOWN:
            idle += 1
            if idle >= 2:
                return done(Outcome.STUCK, "page not recognised after a second look")
            await Settler(page).settle()
            continue

        if c.state == PageState.JOB_PAGE:
            if not c.advance_id:
                return done(Outcome.NO_APPLY_CONTROL, "no control that starts the application")
            if len(res.hops) >= max_hops:
                return done(Outcome.STUCK, f"more than {max_hops} Apply hops")
            perception = await perceive(page)
            action = next((a for a in perception.model.actions if a.id == c.advance_id), None)
            hop = await click_and_follow(page, perception, c.advance_id, action.frame if action else 0, wait_s=click_wait_s)
            res.hops.append(hop)
            trace.log(step=res.steps, action="apply", hop=hop.kind, to=hop.to_url, error=hop.error)
            if hop.kind == "no_effect":
                return done(Outcome.NO_APPLY_CONTROL, f"the Apply control did nothing ({hop.error or 'no change'})")
            if hop.page is not page:
                page, status = hop.page, _Status(hop.page)
                page.set_default_timeout(8000)
            if urlparse(hop.to_url).netloc != urlparse(hop.from_url).netloc:
                res.states.append(PageState.EXTERNAL_HOP)
            idle = 0
            continue

        # FORM_STEP or REVIEW
        step = DemoResult()
        if c.state == PageState.FORM_STEP:
            await fill_until_stable(page, llm, context, resume, step, facts)
            res.rows += step.rows
        problems = await check_step_ready(page, step.rows)
        if any("no honest answer" in p for p in problems):
            return done(Outcome.NEEDS_ANSWER, "; ".join(p for p in problems if "no honest answer" in p))
        if any("captcha" in p for p in problems):
            return done(Outcome.NEEDS_HUMAN, "captcha: solve it in the open window")
        errs = _errors(problems)
        if errs and errs == last_errors:
            same_error += 1
            if same_error >= 3:
                return done(Outcome.ERROR_VALIDATION_LOOP, "; ".join(errs)[:300])
        elif errs:
            last_errors, same_error = errs, 1
        elif problems:
            return done(Outcome.BLOCKED, "; ".join(problems)[:400])
        perception = await perceive(page)
        nxt, final = _controls(perception.model)
        if final and not nxt:
            out = await guarded_submit(page, res.rows, Settler(page), page.url, limiter, log_path, submit)
            trace.log(step=res.steps, action="submit", status=out.status, detail=out.detail, problems=out.problems)
            if out.status == "dry_run":
                return done(Outcome.DRY_RUN_READY, out.detail)
            if out.status == "submitted":
                res.states.append(PageState.CONFIRMATION)
                return done(Outcome.APPLIED, out.detail)
            if out.status in {"blocked_captcha", "blocked_otp"}:
                res.states.append(PageState.CAPTCHA if out.status == "blocked_captcha" else PageState.EMAIL_CODE)
                return done(Outcome.NEEDS_HUMAN, out.detail)
            if out.status == "blocked_guard":
                if any("no honest answer" in p for p in out.problems):
                    return done(Outcome.NEEDS_ANSWER, "; ".join(out.problems)[:400])
                return done(Outcome.BLOCKED, "; ".join(out.problems)[:400])
            if out.status == "error_validation":
                last_errors, same_error = tuple(out.problems), same_error + 1
                if same_error >= 3:
                    return done(Outcome.ERROR_VALIDATION_LOOP, "; ".join(out.problems)[:300])
                continue
            return done(Outcome.STUCK, out.detail)
        if len(nxt) != 1:
            return done(Outcome.NEEDS_HUMAN, f"{'no' if not nxt else 'several'} Next/Submit controls on this step: not guessing")
        before = perception.model
        hop = await click_and_follow(page, perception, nxt[0].id, nxt[0].frame, wait_s=click_wait_s)
        trace.log(step=res.steps, action=f"next:{nxt[0].label}", hop=hop.kind, error=hop.error)
        if hop.page is not page:
            page, status = hop.page, _Status(hop.page)
            page.set_default_timeout(8000)
        if hop.kind == "no_effect":
            after = (await perceive(page)).model
            if _errors([f"page alert: {a}" for a in after.alerts] + [f"field shows an error: {f.label}" for f in after.fields if f.error or f.invalid]):
                same_error += 1  # the site answered with the same complaint again
                if same_error >= 3:
                    return done(Outcome.ERROR_VALIDATION_LOOP, "; ".join(after.alerts)[:300] or "the site keeps rejecting this step")
                continue
            idle += 1
            if idle >= 2:
                return done(Outcome.STUCK, "the page does not react to Next")
        else:
            idle = 0
            if diff(before, (await perceive(page)).model).empty and hop.kind == "same_page":
                idle += 1
    return done(Outcome.STUCK, "step or time budget used up")
