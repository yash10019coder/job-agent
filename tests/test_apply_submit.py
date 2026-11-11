"""Guarded submit: nothing is pressed unless every guard passes, and success needs a detected confirmation."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.demo import run_demo
from job_agent.apply.prefill import build_facts
from job_agent.rate_limiter import RateLimiter
from test_apply_perceive import BRAVE, FakeLLM, RESUME, ROOT

FIX = (ROOT / "tests/fixtures/apply_demo.html").resolve().as_uri()
FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
CTX = json.dumps(FACTS)
@pytest.fixture
def tmp_path():  # pytest's tmpdir plugin is disabled in this repo's config
    return Path(tempfile.mkdtemp())


pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def go(tmp_path, query="", submit=True, llm=None, repeat=1):
    async def run():
        limiter = RateLimiter(storage_path=tmp_path / "rl.jsonl")
        log = tmp_path / "log.jsonl"
        outcomes = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            for _ in range(repeat):
                page = await browser.new_page()
                await page.goto(FIX + query)
                result = await run_demo(page, llm or FakeLLM(), CTX, RESUME, FACTS, submit=submit, limiter=limiter, log_path=log)
                outcomes.append((result.submit, await page.inner_text("body")))
            await browser.close()
        return outcomes, log, limiter

    return asyncio.run(run())


def test_dry_run_never_clicks_submit(tmp_path):
    (out, body), = go(tmp_path, submit=False)[0]
    assert out.status == "dry_run" and "Submit application" in body and "Thank you" not in body


def test_submits_when_all_guards_pass_and_records_it(tmp_path):
    outcomes, log, limiter = go(tmp_path)
    (out, body), = outcomes
    assert out.status == "submitted" and "Thank you for applying" in body
    assert json.loads(log.read_text().splitlines()[-1])["status"] == "submitted"
    assert limiter.get_status(FIX).daily_count == 1                       # counted against the rate limit


def test_missing_required_answer_blocks_the_click(tmp_path):
    class NoVisa(FakeLLM):
        async def complete_json(self, purpose, system, user, schema, name="answer"):
            out = await super().complete_json(purpose, system, user, schema, name)
            if purpose == "resolve_fields":                      # the model has no honest answer for the visa type
                for a in out["answers"]:
                    if a["value"] == "H-1B":
                        a["value"], a["source"], a["confidence"] = "", "unknown", 0.0
            return out
    (out, body), = go(tmp_path, llm=NoVisa())[0]
    assert out.status == "blocked_guard" and any("visa type" in p.lower() for p in out.problems)
    assert "Thank you" not in body                                          # the button was never pressed


def test_visible_captcha_blocks_the_click(tmp_path):
    (out, body), = go(tmp_path, "?captcha=1")[0]
    assert out.status == "blocked_guard" and any("captcha" in p for p in out.problems) and "Thank you" not in body


def test_email_code_step_is_reported_not_claimed_as_submitted(tmp_path):
    (out, _), = go(tmp_path, "?otp=1")[0]
    assert out.status == "blocked_otp" and not out.submitted


def test_site_rejection_is_reported_with_its_errors(tmp_path):
    (out, _), = go(tmp_path, "?reject=1")[0]
    assert out.status == "error_validation" and any("Invalid email" in p for p in out.problems)


def test_no_confirmation_is_never_assumed_to_be_success(tmp_path):
    (out, _), = go(tmp_path, "?silent=1")[0]
    assert out.status == "unknown_outcome" and not out.submitted


def test_same_posting_is_never_submitted_twice(tmp_path):
    outcomes, _, _ = go(tmp_path, repeat=2)
    assert outcomes[0][0].status == "submitted"
    assert outcomes[1][0].status == "blocked_guard" and any("already" in p for p in outcomes[1][0].problems)


def test_rate_limiter_no_longer_treats_all_greenhouse_companies_as_one(tmp_path):
    limiter = RateLimiter(storage_path=tmp_path / "h.jsonl")
    limiter.record_application("https://job-boards.greenhouse.io/affirm/jobs/1", "Affirm", "SWE")
    assert limiter.can_apply("https://job-boards.greenhouse.io/gitlab/jobs/2")[0] is True       # other company: allowed
    assert limiter.can_apply("https://job-boards.greenhouse.io/affirm/jobs/3")[0] is False      # same company: blocked
