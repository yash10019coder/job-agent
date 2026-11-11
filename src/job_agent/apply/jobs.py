"""Parse LinkedIn result cards into JobRefs and open each job in its own tab.

LinkedIn's part ends once a job is open in a new tab: the tab (plus a JobRef) is handed to an
apply handler. The handler is the seam for the auto-apply stage.
"""
from __future__ import annotations

import asyncio
import random
import re
from urllib.parse import parse_qs, urlparse
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

from playwright.async_api import Page

from job_agent.apply.search import RESULTS_LIST

JOB_URL = "https://www.linkedin.com/jobs/view/{job_id}/"
_CARD_KEY = re.compile(r"job-card-component-ref-(\d+)")


@dataclass
class JobRef:
    job_id: str
    title: str
    company: str | None = None
    location: str | None = None
    posted: str | None = None
    verified: bool = False
    promoted: bool = False
    raw: list[str] = field(default_factory=list)
    apply_type: str | None = None  # easy_apply | external | closed | unknown (set after the job opens)
    external_url: str | None = None  # company/ATS apply URL for external jobs

    @property
    def url(self) -> str:
        return JOB_URL.format(job_id=self.job_id)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["url"] = self.url
        data.pop("raw")
        return data


def parse_card(job_id: str, lines: list[str]) -> JobRef:
    """lines = card innerText lines: [title (Verified job)?, title, company, location, ..., Posted ...]."""
    verified = bool(lines) and lines[0].endswith("(Verified job)")
    body = lines[1:] if verified else lines[:]  # drop the accessibility duplicate of the title
    title = (body[0] if body else lines[0] if lines else "").strip()
    company = body[1].strip() if len(body) > 1 else None
    location = body[2].strip() if len(body) > 2 else None
    posted = next((l for l in lines if re.match(r"(Posted|Reposted)\b", l)), None)
    return JobRef(
        job_id=job_id, title=title, company=company, location=location, posted=posted,
        verified=verified, promoted=any(l.strip() == "Promoted" for l in lines), raw=lines,
    )


async def parse_cards(page: Page) -> list[JobRef]:
    rows = await page.evaluate(
        r"""(sel) => {
          const cols = [...document.querySelectorAll(sel)].sort((a, b) => b.children.length - a.children.length);
          if (!cols.length) return [];
          return [...cols[0].children].filter(k => k.getAttribute('role') !== 'presentation' && k.innerText.trim())
            .map(k => {
              const keyed = k.querySelector('[componentkey^="job-card-component-ref-"]');
              return { key: keyed ? keyed.getAttribute('componentkey') : '',
                       lines: k.innerText.split('\n').map(s => s.trim()).filter(Boolean) };
            });
        }""",
        RESULTS_LIST,
    )
    jobs, seen = [], set()
    for row in rows:
        match = _CARD_KEY.search(row["key"])
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            jobs.append(parse_card(match.group(1), row["lines"]))
    return jobs


async def detect_apply_type(page: Page) -> tuple[str, str | None]:
    """Classify the opened job's apply method -> (apply_type, external_url).

    The apply control's accessible name is its aria-label ("Apply on company website",
    "Easy Apply to this job"), not its visible text, so match on aria-label.
    """
    body = (await page.locator("main").first.inner_text()) if await page.locator("main").count() else ""
    if re.search(r"No longer accepting applications", body, re.I):
        return "closed", None
    if await page.locator("[aria-label^='Easy Apply' i]").count():
        return "easy_apply", None
    external = page.locator("a[aria-label*='Apply on company website' i]")
    if await external.count():
        href = await external.first.get_attribute("href") or ""
        target = parse_qs(urlparse(href).query).get("url", [None])[0]  # linkedin.com/safety/go/?url=<real url>
        return "external", target or href or None
    return "unknown", None


# Hand-off seam for the auto-apply stage: receives the job's own tab and returns an outcome string.
ApplyHandler = Callable[[Page, JobRef], Awaitable[str]]


async def pending_handler(page: Page, job: JobRef) -> str:
    return "pending"  # replaced by the auto-apply stage


async def open_jobs(
    context,
    jobs: list[JobRef],
    handler: ApplyHandler = pending_handler,
    keep_open: bool = False,
    delay_range: tuple[float, float] = (2.0, 5.0),
) -> list[dict]:
    """Open each job in a new tab, one at a time; once it's open, hand the tab to `handler`."""
    results = []
    for index, job in enumerate(jobs):
        tab = await context.new_page()
        entry = job.to_dict()
        try:
            await tab.goto(job.url, wait_until="domcontentloaded")
            await tab.locator("main").first.wait_for(timeout=15000)
            await tab.bring_to_front()
            job.apply_type, job.external_url = await detect_apply_type(tab)
            entry.update(apply_type=job.apply_type, external_url=job.external_url)
            entry["outcome"] = await handler(tab, job)
        except Exception as error:  # one bad job must not stop the run
            entry["outcome"] = f"error: {error}"
        results.append(entry)
        if not keep_open:
            await tab.close()
        if index < len(jobs) - 1:
            await asyncio.sleep(random.uniform(*delay_range))
    return results
