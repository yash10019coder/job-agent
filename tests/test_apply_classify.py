"""Page-evidence tests for the classifier: what is an application form, what is a listing, what is a gate."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.classify import is_application_form, is_listing_page
from job_agent.apply.gates import classify_gate
from job_agent.apply.perceive import PageModel, perceive

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests/fixtures/states").glob("*.json"))
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"


def load(path: Path) -> tuple[dict, PageModel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, PageModel(**data["model"])


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_application_form_and_listing_tests_match_the_labels(path):
    data, model = load(path)
    assert is_application_form(model) is data["expect_application_form"], path.stem
    assert is_listing_page(model, data.get("from_url")) is data["expect_listing"], path.stem


def test_board_search_filters_are_not_an_application_form():
    _, board = load(ROOT / "tests/fixtures/states/twilio_board_after_dead_job.json")
    assert [f.label for f in board.fields] == ["Search", "Department", "Office"]
    assert not is_application_form(board)


def test_a_single_email_field_with_a_consent_box_is_not_an_application_form():
    _, gsk = load(ROOT / "tests/fixtures/states/gsk_newsletter.json")
    assert not is_application_form(gsk)


@pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")
def test_header_login_link_does_not_make_a_visible_form_a_gate():
    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content(
                "<header><a href='#'>Login</a> <a href='#'>Sign up</a></header><main><h1>Apply for Backend Engineer</h1><form>"
                "<label for=a>Full name</label><input id=a required><label for=b>Email</label><input id=b type=email required>"
                "<label for=c>Phone</label><input id=c type=tel></form></main>")
            model = (await perceive(page)).model
            await browser.close()
            return model
    model = asyncio.run(go())
    gate = classify_gate(model, [])
    assert gate["gate"].startswith("none") and not gate["signals"]["login_text"] and not gate["signals"]["signup_text"]
    assert is_application_form(model)
