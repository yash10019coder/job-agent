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

from job_agent.apply.classify import _REVIEW, Classification, classify_page, continue_application
from job_agent.apply.demo import DemoResult, Row, fill_until_stable
from job_agent.apply import controls, overlays
from job_agent.apply.gate_flow import GATE_STATES, AuthKit, handle_gate
from job_agent.apply.llm import LLM
from job_agent.apply.navigate import Hop, click_and_follow
from job_agent.apply.perceive import Settler, diff, perceive
from job_agent.apply.semantic import describe
from job_agent.apply.states import Observation, Outcome, PageState
from job_agent.apply.submit import captcha_visible, check_step_ready, guarded_submit
from job_agent.apply.trace import Trace
from job_agent.rate_limiter import RateLimiter

ROOT = Path(__file__).resolve().parents[3]
_SERVER_ERROR = re.compile(r"unable to (apply|submit|process)|try again later|something went wrong|temporarily|server error|could not (process|submit)", re.I)
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
    m = perception.model
    if m.fields or any(a.in_form for a in m.actions) or _REVIEW.search(" ".join([m.title, *m.headings])):
        await controls.annotate(perception.model)  # step/submit roles, needed by classify() and the controls below
    hosts = sorted({urlparse(f.url).netloc for f in page.frames if f.url.startswith("http") and f != page.main_frame})
    return Observation(page.url, status.value, perception.model, hosts, await captcha_visible(page), from_url), perception


async def _wait_until_stable(page: Page, max_s: float = 10.0) -> None:
    """Wait until two reads 1 s apart show the same URL, fields and buttons (or the time runs out)."""
    await Settler(page).settle()
    last, deadline = None, time.monotonic() + max_s
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        m = (await perceive(page)).model
        sig = (page.url, tuple(f.label for f in m.fields), tuple(a.label for a in m.actions), tuple(m.headings))
        if sig == last and not m.busy:
            return
        last = sig


async def _wait_for_person(page: Page, wait_s: float, trace: Trace, what: str) -> bool:
    """A captcha is for a person: bring the window forward and wait (up to wait_s) for them to solve it, then carry on.
    Nothing is solved or bypassed by the agent. Returns True once the challenge is gone."""
    if wait_s <= 0:
        return False
    try:
        await page.bring_to_front()
    except Exception:
        pass
    trace.log(step=0, note=f"WAITING FOR YOU: {what} in this window (up to {int(wait_s)} s)")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if page.is_closed():
            return False
        if not await captcha_visible(page):
            await asyncio.sleep(2.0)   # let the page react to the solved challenge
            trace.log(step=0, note="captcha solved: continuing")
            return True
        await asyncio.sleep(2.0)
    return False


def _error_problems(problems: list[str]) -> list[str]:
    return [p for p in problems if p.startswith(("field shows an error", "page alert"))]


def _errors(problems: list[str]) -> tuple[str, ...]:
    return tuple(sorted(p for p in problems if p.startswith(("field shows an error", "page alert"))))


async def run_application(page: Page, llm: LLM, facts: dict, resume: str | None, run_dir: Path | None = None, submit: bool = False,
                          max_steps: int = 40, max_hops: int = 3, deadline_s: float = 600, click_wait_s: float = 10.0,
                          limiter: RateLimiter | None = None, log_path: Path | None = None, auth: AuthKit | None = None,
                          captcha_wait_s: float = 0.0) -> RunResult:
    import json

    page.set_default_timeout(8000)
    controls.use(llm)
    limiter = limiter or RateLimiter(storage_path=ROOT / "outputs" / "rate_limiter" / "history.jsonl")
    log_path = log_path or ROOT / "outputs" / "apply" / "submissions.jsonl"
    trace, context = Trace(run_dir / "trace.jsonl" if run_dir else None, echo=True, label=run_dir.name if run_dir else "run"), json.dumps(facts, indent=1)
    res, status, started = RunResult(Outcome.STUCK), _Status(page), time.monotonic()
    start_url, last_errors, same_error, idle, gate_steps, unknown_looks = page.url, (), 0, 0, 0, 0
    server_retry, apply_asks = False, 0

    def done(outcome: Outcome, detail: str) -> RunResult:
        res.outcome, res.detail, res.page = outcome, detail, page
        res.llm_cost = float(llm.totals.get("cost_usd", 0) or 0)
        trace.log(step=res.steps, final=outcome.value, detail=detail, cost_usd=res.llm_cost, model=getattr(llm, "model", None), llm_calls=getattr(llm, "breakdown", []))
        return res

    while res.steps < max_steps and time.monotonic() - started < deadline_s:
        res.steps += 1
        if page.is_closed():
            return done(Outcome.STUCK, "the page was closed")
        if res.steps > 1:   # a single-page app swaps its content a moment after the click: read the page once it stops changing
            await _wait_until_stable(page)
        # A terms/cookie overlay on top of the page hides the real controls from the user and blocks every click: clear it first.
        await overlays.clear(page, allow_accept=bool(facts.get("consents", {}).get("accept_privacy_and_terms")),
                             log=lambda note, s=res.steps: trace.log(step=s, overlay=note))
        obs, _ = await observe(page, status, start_url if res.steps == 1 or page.url != start_url else None)
        for _ in range(4):  # single-page apps render after the load event: give a blank page a few chances before judging it
            blank = not obs.model.fields and not obs.model.actions  # nothing to act on yet: still rendering
            if not blank or obs.captcha or obs.status in (403, 404, 410, 429) or (obs.status or 0) >= 500:   # a visible captcha is judged at once
                break
            await Settler(page).settle()
            await asyncio.sleep(1.0)
            obs, _ = await observe(page, status, start_url if page.url != start_url else None)
        c: Classification = await classify_page(obs, None)   # rules (and the semantic check when enabled) first, no model call
        if c.state == PageState.UNKNOWN:
            nxt_c, final_c = await controls.find(obs.model)
            c = continue_application(res.states[-1] if res.states else None, obs.model, bool(nxt_c), bool(final_c)) or c
        if c.state == PageState.UNKNOWN and unknown_looks < 3:
            unknown_looks += 1   # a page that is still loading or mid-transition reads as unknown: look again before asking a model
            trace.log(step=res.steps, url=page.url, state="unknown", note=f"look {unknown_looks}/3 again", desc=describe(obs.model))
            await asyncio.sleep(3.0)
            continue
        if c.state == PageState.UNKNOWN:
            c = await classify_page(obs, llm)
        unknown_looks = 0
        prev = res.states[-1] if res.states else None
        res.states.append(c.state)
        trace.log(step=res.steps, url=page.url, state=c.state.value, confidence=c.confidence, reasons=c.reasons, source=c.source,
                  fields=len(obs.model.fields), actions=len(obs.model.actions), desc=describe(obs.model)[:600])

        if c.state in _TERMINAL:
            return done(_TERMINAL[c.state], "; ".join(c.reasons))
        if auth is not None and c.state in GATE_STATES:
            gate_steps += 1
            if gate_steps > 8:
                return done(Outcome.NEEDS_HUMAN, "account steps did not lead anywhere after 8 tries")
            gate = await handle_gate(page, c.state, auth, facts, llm, resume)
            trace.log(step=res.steps, gate=c.state.value, result=gate.kind, detail=gate.detail)  # descriptive text only: no secrets
            if gate.kind == "human":
                return done(Outcome.NEEDS_HUMAN, f"{c.state.value}: {gate.detail} (window left open)")
            if gate.page is not None and gate.page is not page:
                page, status = gate.page, _Status(gate.page)
                page.set_default_timeout(8000)
            continue
        if c.state in (PageState.FORM_STEP, PageState.JOB_PAGE, PageState.REVIEW):
            gate_steps = 0
        if c.state == PageState.CAPTCHA and await _wait_for_person(page, captcha_wait_s, trace, "solve the captcha"):
            continue
        if c.state in _HUMAN_STATES:
            return done(Outcome.NEEDS_HUMAN, f"{c.state.value}: {'; '.join(c.reasons)} (window left open)")
        if c.state == PageState.CONFIRMATION:
            if prev in {PageState.FORM_STEP, PageState.REVIEW, PageState.EMAIL_CODE, PageState.CAPTCHA}:
                return done(Outcome.APPLIED, "confirmation after the final step")
            return done(Outcome.STUCK, "confirmation wording without a preceding submit: not counted as applied")
        if c.state == PageState.UNKNOWN:
            from job_agent.apply.semantic import record_unknown
            record_unknown(obs.model, page.url)   # queued (description only) so it can be labelled and learned
            fresh = await perceive(page)
            only_next, only_final = await controls.find(fresh.model)
            if prev in {PageState.FORM_STEP, PageState.REVIEW, PageState.EMAIL_CODE} and idle == 0 and len(only_next) == 1 and not only_final:
                idle += 1   # inside an application with exactly one way forward: take it once
                hop = await click_and_follow(page, fresh, only_next[0].id, only_next[0].frame, wait_s=click_wait_s)
                trace.log(step=res.steps, action=f"unrecognised page, only way forward: {only_next[0].label}", hop=hop.kind, error=hop.error)
                if hop.page is not page:
                    page, status = hop.page, _Status(hop.page)
                    page.set_default_timeout(8000)
                continue
            if not any(f.region in {"main", "dialog"} for f in fresh.model.fields) and apply_asks < 2:
                # A job page whose apply control the rules do not know ("Apply with Flexiple"): let a text-only model point at it once.
                from job_agent.apply.demo import pick_apply_action
                apply_asks += 1
                pointed = await pick_apply_action(llm, fresh)
                action = next((a for a in fresh.model.actions if a.id == pointed), None) if pointed else None
                if action is not None:
                    hop = await click_and_follow(page, fresh, action.id, action.frame, wait_s=click_wait_s)
                    trace.log(step=res.steps, action=f"apply (model-picked): {action.label}", hop=hop.kind, error=hop.error)
                    if hop.page is not page:
                        page, status = hop.page, _Status(hop.page)
                        page.set_default_timeout(8000)
                    continue
            return done(Outcome.NEEDS_HUMAN, "page not recognised after looking again; window left open (the page was queued for labelling)")

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
        if c.state == PageState.FORM_STEP or (c.state == PageState.REVIEW and any(f.region in {"main", "dialog"} for f in obs.model.fields)):
            await fill_until_stable(page, llm, context, resume, step, facts)   # review pages can hold inputs too (a required consent box)
            res.rows += step.rows
            trace.log(step=res.steps, filled=[f"{r.label[:30]} = {str(r.answer)[:24]!r} -> {r.status[:70]}" for r in step.rows], fill_events=step.events[-14:])
        problems = await check_step_ready(page, step.rows)
        if any("no honest answer" in p for p in problems):
            return done(Outcome.NEEDS_ANSWER, "; ".join(p for p in problems if "no honest answer" in p))
        if any("captcha" in p for p in problems):
            if await _wait_for_person(page, captcha_wait_s, trace, "solve the captcha"):
                continue
            return done(Outcome.NEEDS_HUMAN, "captcha: solve it in the open window")
        errs = _errors(problems)
        hard = [p for p in problems if p not in set(_error_problems(problems))]
        if hard:   # failed fills / required fields still empty / unclear controls never go through, even if the page also shows errors
            return done(Outcome.BLOCKED, "; ".join(hard)[:400])
        if errs and errs == last_errors:
            same_error += 1
            if same_error >= 3:
                return done(Outcome.ERROR_VALIDATION_LOOP, "; ".join(errs)[:300])
        elif errs:
            last_errors, same_error = errs, 1
        elif problems:
            return done(Outcome.BLOCKED, "; ".join(problems)[:400])
        perception = await perceive(page)
        nxt, final = await controls.find(perception.model)
        if final and not nxt:
            out = await guarded_submit(page, res.rows, Settler(page), page.url, limiter, log_path, submit,
                                        code_provider=auth.provider if auth else None)
            trace.log(step=res.steps, action="submit", status=out.status, detail=out.detail, problems=out.problems)
            if out.status == "dry_run":
                return done(Outcome.DRY_RUN_READY, out.detail)
            if out.status == "submitted":
                res.states.append(PageState.CONFIRMATION)
                return done(Outcome.APPLIED, out.detail)
            if out.status == "blocked_captcha" and await _wait_for_person(page, captcha_wait_s, trace, "solve the captcha that appeared after Submit"):
                res.states.append(PageState.CAPTCHA)
                continue   # look again: a confirmation, or the form (guards run again before any second Submit)
            if out.status in {"blocked_captcha", "blocked_otp"}:
                res.states.append(PageState.CAPTCHA if out.status == "blocked_captcha" else PageState.EMAIL_CODE)
                return done(Outcome.NEEDS_HUMAN, out.detail)
            if out.status == "blocked_guard":
                if any("no honest answer" in p for p in out.problems):
                    return done(Outcome.NEEDS_ANSWER, "; ".join(out.problems)[:400])
                return done(Outcome.BLOCKED, "; ".join(out.problems)[:400])
            if out.status == "error_validation" and any(_SERVER_ERROR.search(p) for p in out.problems):
                # "Unable to apply, try again later": the site's own failure, not a field problem. Pressing Submit again and again could
                # create duplicate applications if the server did accept one, so: one patient retry, then stop with the site's words.
                if server_retry:
                    return done(Outcome.BLOCKED, "the site refused the submission: " + "; ".join(out.problems)[:200])
                server_retry = True
                trace.log(step=res.steps, note="site error after Submit: waiting 40 s before one retry")
                await asyncio.sleep(40.0)
                continue
            if out.status == "error_validation":
                last_errors, same_error = tuple(out.problems), same_error + 1
                if same_error >= 3:
                    return done(Outcome.ERROR_VALIDATION_LOOP, "; ".join(out.problems)[:300])
                continue
            return done(Outcome.STUCK, out.detail)
        if not nxt and not final:
            disabled = [a.label for a in controls.shortlist(perception.model) if a.disabled and controls.cached_role(perception.model, a) in {"next_step", "submit_final"}]
            if disabled:
                empty = [f.label for f in perception.model.fields if f.required and f.empty][:4]
                return done(Outcome.BLOCKED, f"'{disabled[0]}' is disabled: a required field is probably still unfilled ({', '.join(empty) or 'unknown which'})")
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
