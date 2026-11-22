"""What happens after Submit that does not depend on the vendor: the site asks for an emailed security code, we read it from the
mailbox, type it in (one box or one box per character) and let the same guards run again before the second Submit.

Concurrency: ten applications can run at once and several can be for the same company, whose code emails look identical. So the
click -> email -> code -> second click stretch is serialised PER COMPANY (a lock), and a mail is only taken if it arrived after the click."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from playwright.async_api import Page

from job_agent.apply import execute
from job_agent.apply.mailcode import CodeProvider
from job_agent.apply.perceive import perceive

_CODE_LABEL = re.compile(r"security code|verification code|one[- ]time|\botp\b|confirm your email|enter the code|code", re.I)
_NOT_COMPANY = {"www", "com", "net", "org", "io", "co", "careers", "jobs", "apply", "career", "recruiting", "boards", "board", "job",
                "greenhouse", "lever", "ashbyhq", "workable", "app", "global", "en"}
_locks: dict[str, asyncio.Lock] = {}


def company_key(url: str, title: str = "") -> str:
    """Best single word naming the employer: the board path ('job-boards.greenhouse.io/gitlab/...') or the site's own name."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if re.search(r"greenhouse|lever\.co|ashbyhq|workable|smartrecruiters", host):
        seg = next((s for s in parts.path.split("/") if s and not s.isdigit() and s not in {"jobs", "embed", "job_app"}), "")
        if seg:
            return seg.lower()
    words = [w for w in re.split(r"[.\-]", host) if len(w) >= 3 and w not in _NOT_COMPANY and not w.isdigit()]
    if words:
        return words[0]
    at = re.search(r"\bat ([A-Za-z0-9&. ]{2,40})$", title)
    return at.group(1).strip().lower() if at else host


def company_lock(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


async def enter_emailed_code(page: Page, provider: CodeProvider, key: str, since: datetime, timeout_s: float = 90.0) -> str | None:
    """Fetch the code mailed after `since` and type it in. Returns None on success, otherwise why it could not be done."""
    mail = await provider.fetch([key], since - timedelta(seconds=5), timeout_s)
    if mail is None or not mail.code:
        return "no email with a code arrived in time"
    perception = await perceive(page)
    fields = [f for f in perception.model.fields if f.region in {"main", "dialog"} and f.kind in {"text", "number", "tel"} and not f.readonly]
    segmented = [f for f in fields if f.maxlength == 1 and f.empty]
    if 4 <= len(segmented) <= 10:
        for f, ch in zip(segmented, mail.code):
            res = await execute.fill_text(page, perception, f, ch)
            if not res.ok:
                return f"could not type the code: {res.detail}"
        return None
    target = next((f for f in fields if _CODE_LABEL.search(f.label) and f.empty), None)
    if target is None:
        return "the page has no empty box for the code"
    res = await execute.fill_text(page, perception, target, mail.code)
    return None if res.ok else f"could not type the code: {res.detail}"
