"""The step-by-step runner prints every stage and never submits unless explicitly allowed."""
import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.prefill import build_facts
from job_agent.apply.stepper import STAGES, run_steps
from test_apply_perceive import BRAVE, FakeLLM, RESUME, ROOT, FIXTURE
from pathlib import Path

pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")
FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")


def run_all(until, allow_submit=False):
    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(FIXTURE)
            await run_steps(page, FakeLLM(), FACTS, RESUME, until, pause=False, allow_submit=allow_submit)
            body = await page.inner_text("body")
            await browser.close()
            return body

    return asyncio.run(go())


def test_runs_every_stage_in_order_and_stops_before_submit(capsys):
    body = run_all("submit")                                           # submit stage reached but not allowed
    out = capsys.readouterr().out
    positions = [out.index(f"STEP {i}/{len(STAGES)}: {name}") for i, name in enumerate(STAGES, 1)]
    assert positions == sorted(positions)                              # all seven stages, in order
    assert "First name" in out and "identity.first_name" in out       # perceive + prefill tables
    assert "ALL GUARDS PASSED" in out and "skipped: pass --allow-submit" in out
    assert "Thank you" not in body                                     # the form was filled but never submitted


def test_until_stops_early(capsys):
    run_all("prefill")
    out = capsys.readouterr().out
    assert "STEP 3/7: prefill" in out and "STEP 4/7" not in out


def test_non_interactive_submit_is_refused_without_matching_confirm_url():
    from job_agent.apply.stepper import _same_posting
    assert _same_posting("https://x.io/jobs/1/?utm=a", "https://x.io/jobs/1")
    assert not _same_posting("", "https://x.io/jobs/1")
    assert not _same_posting("https://x.io/jobs/2", "https://x.io/jobs/1")


def test_a_job_page_with_a_footer_language_picker_is_not_mistaken_for_a_form():
    """Seen live on Rippling: 'Display language' in the footer made the old entry points skip the Apply click."""
    import asyncio
    from pathlib import Path

    from playwright.async_api import async_playwright

    from job_agent.apply.opening import open_application
    from test_apply_perceive import BRAVE, ROOT

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await (await browser.new_context()).new_page()
            await page.goto((ROOT / "tests/fixtures/multistep/footer_language_decoy.html").resolve().as_uri())
            after, note = await open_application(page, None)
            again, note2 = await open_application(after, None)
            await browser.close()
            return after.url, note, note2
    url, note, note2 = asyncio.run(go())
    assert url.endswith("step1.html") and "clicked 'Apply now'" in note
    assert "already shows an application form" in note2     # a second call on the real form does nothing
