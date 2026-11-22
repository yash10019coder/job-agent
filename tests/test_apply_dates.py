"""Date boxes: 'Immediately' means today, typed in whichever format the box keeps."""
import asyncio
import shutil
from datetime import date
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply import execute
from job_agent.apply.perceive import perceive

ROOT = Path(__file__).resolve().parents[1]
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"


def test_candidates_turn_start_words_into_todays_date_in_the_boxs_format():
    today = date(2026, 11, 14)
    assert execute.date_candidates("Immediately", "DD/MM/YYYY", today)[0] == "14/11/2026"
    assert execute.date_candidates("Immediately", "MM/DD/YYYY", today)[0] == "11/14/2026"
    assert execute.date_candidates("Immediately", "Pick date...", today)[0] == "11/14/2026"
    assert execute.date_candidates("2027-01-05", "", today)[0] == "01/05/2027"
    assert execute.date_candidates("next quarter", "", today) == ["next quarter"]   # not a date: left as typed


@pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")
def test_placeholder_only_date_box_is_a_date_field_and_keeps_a_typed_date():
    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto((ROOT / "tests/fixtures/date_picker.html").resolve().as_uri())
            p = await perceive(page)
            field = next(f for f in p.model.fields if "start" in f.label.lower())
            res = await execute.set_date(page, p, field, "Immediately")
            value = await page.input_value("#d")
            await browser.close()
            return field.kind, res, value
    kind, res, value = asyncio.run(go())
    assert kind == "date" and res.ok and len(value) == 10 and value.count("/") == 2
