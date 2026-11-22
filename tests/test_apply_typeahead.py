"""A typeahead that empties itself on blur unless a suggestion is clicked (Lever's location field)."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply import execute
from job_agent.apply.perceive import perceive

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/typeahead.html").resolve().as_uri()
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def test_typeahead_value_is_kept_by_clicking_the_matching_suggestion():
    async def main():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(FIXTURE)
            p = await perceive(page)
            field = next(f for f in p.model.fields if "location" in f.label.lower())
            res = await execute.fill_text(page, p, field, "Mumbai, Maharashtra, India")
            picked = await page.evaluate("() => document.getElementById('picked').value")
            await browser.close()
            return res, picked
    res, picked = asyncio.run(main())
    assert res.ok, res
    assert picked == "Mumbai, Maharashtra, IND"   # the right row, not 'Mumbai, Kuala Lumpur' or 'Munich'
