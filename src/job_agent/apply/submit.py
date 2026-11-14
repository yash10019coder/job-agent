"""Guarded submit: press Submit only when every guard passes, then verify what actually happened.

Guards (any failure => nothing is clicked): no BLOCKED/failed/low-confidence answers, no required field empty or
invalid, no visible captcha, rate limits and the same-company/24h rule, and never twice for the same posting.
After the click the outcome is read from the page (confirmation text/URL, validation errors, email-code step,
captcha) and only a detected confirmation counts as 'submitted'.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Page

from job_agent.apply import controls, execute
from job_agent.apply.perceive import Perception, Settler, perceive
from job_agent.rate_limiter import RateLimiter

MIN_SUBMIT_CONFIDENCE = 0.7
_SUCCESS = re.compile(
    r"(thank you|thanks) for (applying|your (application|interest|submission))|application (has been |was |is )?(successfully )?"
    r"(submitted|received|sent|complete)|we('ve| have) received your application|successfully (submitted|applied)|you('ve| have) applied",
    re.I)
_CODE_FIELD = re.compile(r"security code|verification code|one[- ]time|\botp\b|confirm your email|enter the code", re.I)
_CAPTCHA_JS = r"""() => [...document.querySelectorAll('iframe')].filter(f => {
  const tag = (f.src || '') + ' ' + (f.title || '');
  // A challenge or "I'm not a robot" widget needs a human; the invisible-reCAPTCHA badge (size=invisible) does not.
  const needsHuman = /bframe|hcaptcha-challenge|hcaptcha\.com\/captcha|challenges\.cloudflare\.com|recaptcha\/(api2|enterprise)\/anchor\?(?!.*size=invisible)/i.test(tag);
  const r = f.getBoundingClientRect();
  const onScreen = r.width >= 100 && r.height >= 60 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  return needsHuman && onScreen && f.checkVisibility({ visibilityProperty: true, opacityProperty: true });
}).length"""


@dataclass
class SubmitOutcome:
    status: str  # dry_run | submitted | blocked_guard | blocked_captcha | blocked_otp | error_validation | unknown_outcome
    detail: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def submitted(self) -> bool:
        return self.status == "submitted"


def _posting_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}".rstrip("/").lower()


def already_submitted(log_path: Path, url: str) -> bool:
    if not log_path.exists():
        return False
    key = _posting_key(url)
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("status") == "submitted" and _posting_key(entry.get("url", "")) == key:
            return True
    return False


def _log(log_path: Path, url: str, title: str, outcome: SubmitOutcome) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "url": url, "title": title,
                             "status": outcome.status, "detail": outcome.detail, "problems": outcome.problems}) + "\n")


async def captcha_visible(page: Page) -> bool:
    for frame in page.frames:
        try:
            if await asyncio.wait_for(frame.evaluate(_CAPTCHA_JS), 2.0):
                return True
        except Exception:
            continue
    return False


async def _submit_button(perception: Perception):
    """(button, how_many_candidates), as labelled by controls.py (no button-name lists)."""
    _, submit = await controls.find(perception.model)
    if not submit:
        return None, 0
    return (next((a for a in submit if a.primary), submit[0]), len(submit))


async def _step_problems(page: Page, rows: list):
    """Reasons this step is not ready to move on (answers + what the page itself shows). Returns (problems, perception)."""
    problems = []
    for r in rows:
        if r.status.startswith("BLOCKED"):
            problems.append(f"required field has no honest answer: {r.label}")
        elif r.status == "ok":
            if r.confidence < MIN_SUBMIT_CONFIDENCE and r.source != "guess":
                problems.append(f"low-confidence answer: {r.label} ({r.confidence:.2f})")
        elif not r.status.startswith(("left blank", "skipped")):   # anything else is a failed fill (required-but-empty is caught below)
            problems.append(f"could not fill: {r.label} ({r.status})")
    perception = await perceive(page)
    for f in perception.model.fields:
        if f.required and f.empty and f.kind != "checkbox_group":
            problems.append(f"required field still empty: {f.label}")
        if f.invalid or f.error:
            problems.append(f"field shows an error: {f.label} ({f.error or 'invalid'})")
    problems += [f"required control not understood by the agent: {u['text']}" for u in perception.model.unparsed if u.get("required")]
    problems += [f"page alert: {a}" for a in perception.model.alerts]
    if await captcha_visible(page):
        problems.append("a captcha is showing (not solved automatically)")
    return problems, perception


async def check_step_ready(page: Page, rows: list) -> list[str]:
    """Per-step guards: may we click Next/Continue? (Final-only guards live in check_ready.)"""
    return (await _step_problems(page, rows))[0]


async def check_ready(page: Page, rows: list, limiter: RateLimiter, log_path: Path, url: str) -> list[str]:
    """Every reason NOT to submit; an empty list means all guards passed."""
    problems, perception = await _step_problems(page, rows)
    button, count = await _submit_button(perception)
    if not button:
        problems.append("no enabled Submit control found")
    elif count > 1:
        problems.append(f"{count} Submit-like controls: ambiguous")
    allowed, reason = limiter.can_apply(url)
    if not allowed:
        problems.append(f"rate limit: {reason}")
    if already_submitted(log_path, url):
        problems.append("this posting was already submitted")
    return problems


CLASSIFY_WAIT_S = 10.0  # a captcha challenge or confirmation can appear well after the click


async def _classify(page: Page, settler: Settler, before_labels: set[str]) -> SubmitOutcome:
    """Watch the page after Submit until something conclusive shows (captcha, email code, confirmation, errors)
    or the wait runs out. A single early look missed an hCaptcha challenge that pops up a second or two later."""
    await settler.settle()
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    deadline = time.monotonic() + CLASSIFY_WAIT_S
    while True:
        if await captcha_visible(page):
            return SubmitOutcome("blocked_captcha", "a captcha challenge appeared after Submit; solve it in the open window (nothing is assumed sent)")
        perception = await perceive(page)
        body = " ".join(f"{perception.model.title} {' '.join(perception.model.headings)} {perception.model.text_digest}".split())
        new_code = [f.label for f in perception.model.fields if _CODE_FIELD.search(f.label) and f.label not in before_labels]
        if new_code:
            return SubmitOutcome("blocked_otp", f"the site wants an emailed code ({new_code[0]}); email verification is not automated yet")
        if _SUCCESS.search(body) or re.search(r"/(confirmation|thank|submitted|success|applied)\b", page.url, re.I):
            return SubmitOutcome("submitted", "confirmation detected on the page")
        errors = [a for a in perception.model.alerts] + [f"{f.label}: {f.error or 'invalid'}" for f in perception.model.fields if f.invalid or f.error]
        if errors:
            return SubmitOutcome("error_validation", "the site rejected the form", errors[:6])
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)
    widget = any(re.search(r"hcaptcha|recaptcha|turnstile", f.url, re.I) for f in page.frames if f != page.main_frame)
    note = "; a captcha widget is on the page but no challenge was visible, check the tab" if widget else ""
    return SubmitOutcome("unknown_outcome", "clicked Submit but found neither a confirmation nor an error; do NOT assume it was sent" + note)


async def guarded_submit(page: Page, rows: list, settler: Settler, url: str, limiter: RateLimiter, log_path: Path, submit: bool) -> SubmitOutcome:
    title = await page.title()
    problems = await check_ready(page, rows, limiter, log_path, url)
    if problems:
        outcome = SubmitOutcome("blocked_guard", f"{len(problems)} guard(s) failed; nothing was submitted", problems)
    elif not submit:
        outcome = SubmitOutcome("dry_run", "all guards passed; not submitting (--submit not given)")
    else:
        perception = await perceive(page)
        button, _ = await _submit_button(perception)
        before = {f.label for f in perception.model.fields}
        clicked = await execute.click_action(perception, button.id, button.frame)
        outcome = await _classify(page, settler, before) if clicked.ok else SubmitOutcome("unknown_outcome", clicked.detail)
        if outcome.submitted:
            limiter.record_application(url, company=limiter._extract_domain(url), title=title, success=True)
    if outcome.status != "dry_run":
        _log(log_path, url, title, outcome)
    return outcome
