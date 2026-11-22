"""LinkedIn page-state helpers: detect the Jobs page, navigate there if needed."""
from __future__ import annotations

from urllib.parse import urlparse

from playwright.async_api import Page

LINKEDIN_HOME = "https://www.linkedin.com/feed/"
LINKEDIN_JOBS = "https://www.linkedin.com/jobs/"
_JOBS_NAV = "nav a[href*='linkedin.com/jobs'], header a[href*='linkedin.com/jobs'], nav a[href^='/jobs']"
_AUTH_PATHS = ("/login", "/uas/", "/checkpoint", "/authwall", "/signup")


class LinkedInNotLoggedIn(RuntimeError):
    pass


def _parts(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.netloc.lower(), parsed.path


def is_linkedin(page: Page) -> bool:
    host, _ = _parts(page.url)
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def is_login_page(page: Page) -> bool:
    return is_linkedin(page) and _parts(page.url)[1].startswith(_AUTH_PATHS)


def is_jobs_page(page: Page) -> bool:
    """True on /jobs/, /jobs/search/, /jobs/collections/..., /jobs/view/<id> (not /jobs-something else)."""
    if not is_linkedin(page):
        return False
    path = _parts(page.url)[1]
    return path == "/jobs" or path.startswith("/jobs/")


async def ensure_jobs_page(page: Page, timeout_ms: int = 15000) -> bool:
    """Make sure `page` is on LinkedIn's Jobs tab. Returns True if it had to navigate."""
    if is_jobs_page(page):
        return False
    if not is_linkedin(page):
        await page.goto(LINKEDIN_HOME, wait_until="domcontentloaded")
    if is_login_page(page):
        raise LinkedInNotLoggedIn("LinkedIn shows a login page; log in once in the agent browser window (profile name job-agent) first.")

    nav = page.locator(_JOBS_NAV).first
    try:
        await nav.click(timeout=5000)  # same path a user takes: click the "Jobs" tab
        await page.wait_for_url(lambda url: _parts(url)[1].startswith("/jobs"), timeout=timeout_ms)
    except Exception:
        await page.goto(LINKEDIN_JOBS, wait_until="domcontentloaded")  # fallback if nav is missing/changed

    if is_login_page(page):
        raise LinkedInNotLoggedIn("LinkedIn redirected to login while opening Jobs.")
    if not is_jobs_page(page):
        raise RuntimeError(f"Could not reach the LinkedIn Jobs page (at {page.url})")
    await page.wait_for_load_state("domcontentloaded")
    return True
