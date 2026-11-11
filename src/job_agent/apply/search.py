"""LinkedIn job search: build the query/URL and run it in the current tab."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from playwright.async_api import Page

from job_agent.apply.linkedin import ensure_jobs_page

SEARCH_URL = "https://www.linkedin.com/jobs/search/"

# Role titles to OR together. "SDE"/"MTS" are the common abbreviations in postings.
DEFAULT_TITLES = (
    "Software Engineer",
    "Backend Engineer",
    "Backend Developer",
    "Software Development Engineer",
    "Member of Technical Staff",
    "MTS",
    "SDE",
    "BE",
)
# LinkedIn's search is now conversational: the old location/experience URL params (f_E, location,
# sortBy) are dropped on redirect and the filter dialog has no such fields. Location and
# experience therefore go into the query text itself ("... with 1 to 3 years of experience in India").
RESULTS_LIST = "[data-testid=lazy-column]"


@dataclass
class JobSearch:
    titles: tuple[str, ...] = DEFAULT_TITLES
    location: str = "India"
    min_years: int = 1
    max_years: int = 3

    @property
    def query(self) -> str:
        roles = " or ".join(self.titles)
        text = f"{roles} with {self.min_years} to {self.max_years} years of experience"
        return f"{text} in {self.location}" if self.location else text

    def url(self) -> str:
        return f"{SEARCH_URL}?{urlencode({'keywords': self.query})}"


async def read_cards(page: Page) -> list[str]:
    """Visible result cards (text blobs) from the results list - the lazy column with the most children."""
    return await page.evaluate(
        r"""(sel) => {
          const cols = [...document.querySelectorAll(sel)];
          if (!cols.length) return [];
          const col = cols.sort((a, b) => b.children.length - a.children.length)[0];
          return [...col.children]
            .filter(k => k.getAttribute('role') !== 'presentation' && k.innerText.trim())
            .map(k => k.innerText.trim().replace(/\n+/g, ' | '));
        }""",
        RESULTS_LIST,
    )


async def run_search(page: Page, search: JobSearch, timeout_ms: int = 20000) -> list[str]:
    """Open the Jobs tab, run the search, return the result cards that loaded."""
    await ensure_jobs_page(page)
    await page.goto(search.url(), wait_until="domcontentloaded")
    try:
        await page.locator(RESULTS_LIST).first.wait_for(timeout=timeout_ms)
        await page.wait_for_timeout(1500)  # let the cards render
    except Exception:
        return []
    return await read_cards(page)
