"""The generic loop on local multi-step pages (no network, scripted FakeLLM). Every Review Focus line has a test here."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.controller import run_application
from job_agent.apply.prefill import build_facts
from job_agent.apply.states import Outcome, PageState
from job_agent.rate_limiter import RateLimiter
from test_apply_perceive import BRAVE, RESUME, ROOT, FakeLLM

FIX = ROOT / "tests/fixtures/multistep"
FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def run(name: str, submit: bool = False, **kwargs):
    tmp = Path(tempfile.mkdtemp())

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto((FIX / name).resolve().as_uri())
            result = await run_application(page, FakeLLM(), FACTS, RESUME, tmp / "run", submit=submit, click_wait_s=kwargs.pop("click_wait_s", 2.0),
                                           limiter=RateLimiter(storage_path=tmp / "rl.jsonl"), log_path=tmp / "log.jsonl", **kwargs)
            end_url = result.page.url if result.page and not result.page.is_closed() else ""
            still_open = bool(result.page and not result.page.is_closed())
            await browser.close()
            return result, end_url, still_open, tmp
    return asyncio.run(go())


def trace_lines(tmp: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp / "run" / "trace.jsonl").read_text().splitlines()]


def test_multistep_flow_reaches_dry_run_ready_without_sending_anything():
    res, url, _, tmp = run("job_page.html")
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)
    assert url.endswith("review.html")                                   # stopped at the review step, never reached confirmation
    assert [s for s in res.states if s != PageState.EXTERNAL_HOP] == [PageState.JOB_PAGE, PageState.FORM_STEP, PageState.FORM_STEP, PageState.REVIEW]
    assert any(t.get("final") == "dry_run_ready" for t in trace_lines(tmp))


def test_same_flow_with_submit_ends_applied_only_after_the_confirmation_page():
    res, url, _, tmp = run("job_page.html", submit=True)
    assert res.outcome == Outcome.APPLIED, (res.outcome, res.detail)
    assert url.endswith("confirmation.html")
    assert json.loads((tmp / "log.jsonl").read_text().splitlines()[-1])["status"] == "submitted"


def test_login_gate_needs_a_human_and_leaves_the_window_open():
    res, _, still_open, _ = run("login_gate.html")
    assert res.outcome == Outcome.NEEDS_HUMAN and still_open and PageState.LOGIN in res.states


def test_captcha_gate_needs_a_human():
    res, _, still_open, _ = run("captcha_gate.html")
    assert res.outcome == Outcome.NEEDS_HUMAN and still_open and PageState.CAPTCHA in res.states


def test_newsletter_box_is_ignored_and_apply_is_clicked():
    res, url, _, _ = run("decoy_newsletter.html")
    assert res.outcome == Outcome.DRY_RUN_READY and res.states[0] == PageState.JOB_PAGE


def test_dead_job_that_redirects_to_the_board_is_closed_and_nothing_is_filled():
    res, url, _, _ = run("job_dead.html")
    assert res.outcome == Outcome.CLOSED and res.rows == [] and url.endswith("board.html?error=true")


def test_a_page_that_ignores_next_is_stuck_not_looped_forever():
    res, _, _, _ = run("nochange.html", click_wait_s=1.0)
    assert res.outcome == Outcome.STUCK and res.steps <= 6


def test_the_same_validation_error_three_times_ends_the_run():
    res, _, _, _ = run("validation_loop.html", click_wait_s=1.0)
    assert res.outcome == Outcome.ERROR_VALIDATION_LOOP, (res.outcome, res.detail)


def test_apply_hops_are_limited():
    res, _, _, _ = run("hop_loop.html", max_hops=3)
    assert res.outcome == Outcome.STUCK and len(res.hops) == 3


def test_apply_that_opens_a_new_tab_is_followed():
    res, url, _, _ = run("job_newtab.html")
    assert res.outcome == Outcome.DRY_RUN_READY and res.hops[0].kind == "new_tab"


def test_emailed_code_after_submit_is_not_recorded_as_applied():
    res, _, still_open, tmp = run("review_otp.html", submit=True)
    assert res.outcome == Outcome.NEEDS_HUMAN and still_open
    assert json.loads((tmp / "log.jsonl").read_text().splitlines()[-1])["status"] == "blocked_otp"


def test_embedded_iframe_form_is_filled_like_any_other():
    res, _, _, _ = run("embedded.html")
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)


def test_choice_dialog_is_answered_with_apply_manually():
    """Workday-style: Apply opens 'Autofill with Resume / Apply Manually / Use My Last Application'; the loop must not
    keep re-clicking the Apply button behind it (seen live: 4 hops, stuck)."""
    res, url, _, _ = run("workday_like.html")
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)


def test_a_page_that_renders_late_is_waited_for_not_judged_blank():
    res, _, _, _ = run("late_render.html")
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)


def test_an_optional_box_that_rejects_our_answer_is_left_blank_not_a_blocker():
    """Breezy-style numeric 'Desired salary': our policy answer is text; an optional field must not stop the run."""
    res, _, _, _ = run("optional_number.html")
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)
    assert any(r.label.startswith("Desired salary") and r.status.startswith("left blank") for r in res.rows)
