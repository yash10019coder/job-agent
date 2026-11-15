"""Login, signup and emailed-code flows through the real controller on local pages (no network, a stub mail provider)."""
import asyncio
import json
import re
import tempfile
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.auth import Credential, MemoryStore
from job_agent.apply.controller import run_application
from job_agent.apply.gate_flow import AuthKit
from job_agent.apply.mailcode import MailCode, StaticCodeProvider
from job_agent.apply.prefill import build_facts
from job_agent.apply.states import Outcome, PageState
from job_agent.rate_limiter import RateLimiter
from test_apply_perceive import BRAVE, RESUME, ROOT, FakeLLM

FIX = ROOT / "tests/fixtures/multistep"
FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
EMAIL = FACTS["contact"]["email"]
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def flow(name, store=None, provider=None, with_kit=True, allow_signup=True):
    tmp = Path(tempfile.mkdtemp())
    kit = AuthKit(store, provider, EMAIL, allow_signup=allow_signup) if with_kit else None

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await (await browser.new_context()).new_page()
            await page.goto((FIX / name).resolve().as_uri())
            res = await run_application(page, FakeLLM(), FACTS, RESUME, tmp / "run", click_wait_s=2.0, limiter=RateLimiter(storage_path=tmp / "rl.jsonl"),
                                        log_path=tmp / "log.jsonl", auth=kit)
            still_open = bool(res.page and not res.page.is_closed())
            await browser.close()
            return res, tmp, still_open
    res, tmp, still_open = asyncio.run(go())
    trace = (tmp / "run" / "trace.jsonl").read_text()
    return res, trace, still_open


def test_login_with_a_stored_credential_reaches_the_form_and_the_trace_hides_the_password():
    store = MemoryStore()
    store.save("local", Credential(EMAIL, "Correct-Horse-9!"))
    res, trace, _ = flow("auth_login.html", store)
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)
    assert PageState.LOGIN in res.states
    assert "Correct-Horse" not in trace


def test_a_rejected_login_is_not_retried_and_goes_to_a_human():
    store = MemoryStore()
    store.save("local", Credential(EMAIL, "wrong-password"))
    res, trace, still_open = flow("auth_login.html", store)
    assert res.outcome == Outcome.NEEDS_HUMAN and "rejected" in res.detail and still_open
    assert trace.count('"gate": "login"') == 1            # exactly one attempt: no lockout risk


def test_login_page_without_a_stored_credential_needs_a_human():
    res, _, _ = flow("auth_login.html", MemoryStore())
    assert res.outcome == Outcome.NEEDS_HUMAN and "no stored login" in res.detail


def test_choice_page_creates_an_account_reads_the_code_and_reaches_the_form():
    store, provider = MemoryStore(), StaticCodeProvider(MailCode(code="482913"))
    res, trace, _ = flow("auth_choice.html", store, provider)
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)
    assert [s for s in res.states if s in {PageState.ACCOUNT_CHOICE, PageState.SIGNUP, PageState.EMAIL_CODE}] == [PageState.ACCOUNT_CHOICE, PageState.SIGNUP, PageState.EMAIL_CODE]
    cred = store.get("local")
    assert cred and cred.username == EMAIL and len(cred.password) >= 18      # saved before the account was created
    assert provider.calls and provider.calls[0][1] is not None                # the code was looked up with a since-time
    assert cred.password not in trace and "482913" not in trace               # secrets never reach the trace
    chosen = [json.loads(line) for line in trace.splitlines()]
    assert not any("Continue with Google" in str({k: v for k, v in entry.items() if k != "desc"}) for entry in chosen)   # SSO is never chosen (the page description may list the button)


def test_code_typed_into_one_box_per_digit():
    res, _, _ = flow("auth_signup_segmented.html", MemoryStore(), StaticCodeProvider(MailCode(code="482913")))
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)


def test_existing_account_without_a_stored_password_goes_to_a_human_and_nothing_is_kept():
    store = MemoryStore()
    res, _, still_open = flow("auth_exists.html", store, StaticCodeProvider())
    assert res.outcome == Outcome.NEEDS_HUMAN and "already exists" in res.detail and still_open
    assert store.get("local") is None                      # the password we generated was for an account that was never created


def test_password_rules_trigger_one_retry_with_a_longer_stored_password():
    store = MemoryStore()
    res, _, _ = flow("auth_pwrule.html", store, StaticCodeProvider(MailCode(code="482913")))
    assert res.outcome == Outcome.DRY_RUN_READY, (res.outcome, res.detail)
    assert len(store.get("local").password) >= 24


def test_no_keyring_means_no_account_is_created():
    res, _, _ = flow("auth_signup.html", store=None, provider=StaticCodeProvider(MailCode(code="1")))
    assert res.outcome == Outcome.NEEDS_HUMAN and "keyring" in res.detail


def test_emailed_code_without_gmail_needs_a_human():
    res, _, _ = flow("auth_code.html", MemoryStore(), None)
    assert res.outcome == Outcome.NEEDS_HUMAN and "Gmail" in res.detail


def test_a_wrong_code_is_rejected_not_retried():
    res, _, _ = flow("auth_code.html", MemoryStore(), StaticCodeProvider(MailCode(code="000000")))
    assert res.outcome == Outcome.NEEDS_HUMAN and "rejected" in res.detail


def test_without_an_auth_kit_gates_still_end_as_needs_human():
    res, _, _ = flow("auth_login.html", with_kit=False)
    assert res.outcome == Outcome.NEEDS_HUMAN and PageState.LOGIN in res.states


def test_a_dry_run_does_not_create_accounts_unless_asked():
    store = MemoryStore()
    res, _, _ = flow("auth_choice.html", store, StaticCodeProvider(MailCode(code="482913")), allow_signup=False)
    assert res.outcome == Outcome.NEEDS_HUMAN and "account creation is off" in res.detail
    assert store.get("local") is None
    res2, _, _ = flow("auth_signup.html", store, StaticCodeProvider(), allow_signup=False)
    assert res2.outcome == Outcome.NEEDS_HUMAN and "account creation is off" in res2.detail and store.get("local") is None


def test_logging_in_with_an_existing_credential_needs_no_signup_permission():
    store = MemoryStore()
    store.save("local", Credential(EMAIL, "Correct-Horse-9!"))
    res, _, _ = flow("auth_login.html", store, allow_signup=False)
    assert res.outcome == Outcome.DRY_RUN_READY
