"""Page-evidence tests for the classifier: what is an application form, what is a listing, what is a gate."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from collections import Counter

from job_agent.apply.classify import classify, is_application_form, is_listing_page
from job_agent.apply.states import Observation, PageState
from job_agent.apply.gates import classify_gate
from job_agent.apply.perceive import PageModel, perceive

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted(p for p in (ROOT / "tests/fixtures/states").glob("*.json") if p.stem != "page_labels")
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


LABELED = [p for p in FIXTURES if "expect_state" in json.loads(p.read_text(encoding="utf-8"))]


def observation(data: dict, model: PageModel) -> Observation:
    return Observation(data["url"], data["status"], model, data.get("frame_hosts", []), False, data.get("from_url"))


@pytest.mark.parametrize("path", LABELED, ids=[p.stem for p in LABELED])
def test_pages_classify_as_their_page_text_says(path):
    data, model = load(path)
    got = classify(observation(data, model))
    assert got.state.value in data["acceptable"], (got.state, got.reasons, data.get("labels"))


def test_no_page_is_wrongly_called_a_form_step_or_review():
    """The invariant that matters: anything that could lead to a Submit click must be certain."""
    wrong, table = [], Counter()
    for path in LABELED:
        data, model = load(path)
        got = classify(observation(data, model)).state
        table[(data["expect_state"], got.value)] += 1
        if got in {PageState.FORM_STEP, PageState.REVIEW} and data["expect_state"] not in {"form_step", "review"}:
            wrong.append(path.stem)
    print("\n(expected, got) -> count:", dict(table))
    assert not wrong, wrong


def page(title="", headings=(), fields=(), actions=(), text="", url="https://x.example/p"):
    f = [{"id": f"f0-n{i}", "kind": k, "label": lb, "required": False} for i, (k, lb) in enumerate(fields, 1)]
    a = [{"id": f"f0-a{i}", "kind": "button", "label": lb} for i, lb in enumerate(actions, 1)]
    return PageModel(url=url, title=title, headings=list(headings), fields=f, actions=a, text_digest=text)


def obs(model, status=200, captcha=False, from_url=None):
    return Observation(model.url, status, model, [], captcha, from_url)


def test_stop_states_come_from_status_and_captcha():
    plain = page("Software Engineer", ["Software Engineer"], actions=["Apply"])
    assert classify(obs(plain, status=404)).state == PageState.DEAD
    assert classify(obs(plain, status=403)).state == PageState.BOT_BLOCK
    assert classify(obs(plain, captcha=True)).state == PageState.CAPTCHA
    assert classify(obs(plain)).state == PageState.JOB_PAGE and classify(obs(plain)).advance_id == "f0-a1"


def test_confirmation_needs_confirmation_wording_and_no_form():
    thanks = page("Thank you for applying", ["Application received"], text="Your application has been submitted.")
    assert classify(obs(thanks)).state == PageState.CONFIRMATION
    form_with_thanks_text = page("Apply", fields=[("text", "Full name"), ("email", "Email"), ("tel", "Phone")], text="Thank you for applying to us")
    assert classify(obs(form_with_thanks_text)).state == PageState.FORM_STEP


def test_email_code_review_and_account_gates():
    assert classify(obs(page("Verify your email", fields=[("text", "Security code")]))).state == PageState.EMAIL_CODE
    review = page("Review your application", ["Review your application"], fields=[("text", "Full name"), ("email", "Email"), ("tel", "Phone")], actions=["Submit application"])
    assert classify(obs(review)).state == PageState.REVIEW
    assert classify(obs(page("Sign in", ["Sign in"], fields=[("email", "Email"), ("password", "Password")], actions=["Sign in"]))).state == PageState.LOGIN
    signup = page("Create account", ["Create an account"], fields=[("email", "Email"), ("password", "Password"), ("password", "Confirm password")], actions=["Create account"])
    assert classify(obs(signup)).state == PageState.SIGNUP
    sso = page("Apply", ["Apply to this job"], actions=["Continue with Google", "Continue with LinkedIn"])
    assert classify(obs(sso)).state == PageState.SSO_ONLY


class StubLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    async def complete_json(self, purpose, system, user, schema, name="answer"):
        self.calls += 1
        return self.answer


def test_model_fallback_runs_only_on_unknown_pages_and_cannot_invent_a_form():
    blank = page("Welcome", ["Welcome"], text="hello")
    llm = StubLLM({"state": "form_step", "advance_id": "", "confidence": 0.99})
    got = asyncio.run(__import__("job_agent.apply.classify", fromlist=["classify_page"]).classify_page(obs(blank), llm))
    assert llm.calls == 1 and got.state == PageState.UNKNOWN and "rejected" in got.reasons[0]
    known = page("Software Engineer", ["Software Engineer"], actions=["Apply"])
    llm2 = StubLLM({"state": "dead", "advance_id": "", "confidence": 1.0})
    asyncio.run(__import__("job_agent.apply.classify", fromlist=["classify_page"]).classify_page(obs(known), llm2))
    assert llm2.calls == 0   # rules already knew: no model call


def test_model_fallback_can_name_the_advance_control_and_confidence_is_capped():
    odd = page("Opportunities", ["Opportunities"], actions=["Learn more", "Start your journey"])
    llm = StubLLM({"state": "job_page", "advance_id": "f0-a2", "confidence": 0.99})
    got = asyncio.run(__import__("job_agent.apply.classify", fromlist=["classify_page"]).classify_page(obs(odd), llm))
    assert got.state == PageState.JOB_PAGE and got.advance_id == "f0-a2" and got.confidence <= 0.7 and got.source == "llm"


def test_choice_dialog_prefers_the_option_that_needs_no_account_or_upload():
    from job_agent.apply.reveal import pick_apply
    m = page("Job", ["Job"], actions=["Apply"])
    m.actions.append(type(m.actions[0])(id="f0-d1", kind="button", label="Autofill with Resume", region="dialog"))
    m.actions.append(type(m.actions[0])(id="f0-d2", kind="button", label="Apply Manually", region="dialog"))
    m.actions.append(type(m.actions[0])(id="f0-d3", kind="button", label="Use My Last Application", region="dialog"))
    assert pick_apply(m) == "f0-d2"
    only_other = page("Job", ["Job"], actions=["Apply"])
    only_other.actions.append(type(only_other.actions[0])(id="f0-d9", kind="button", label="Use My Last Application", region="dialog"))
    assert pick_apply(only_other) == "f0-a1"   # no safe dialog choice: fall back to the main Apply control


def test_model_cannot_call_an_almost_empty_page_closed_or_a_listing():
    from job_agent.apply.classify import classify_page
    blank = page("", [], text="")
    for claimed in ("closed", "listing", "dead"):
        got = asyncio.run(classify_page(obs(blank), StubLLM({"state": claimed, "advance_id": "", "confidence": 0.9})))
        assert got.state == PageState.UNKNOWN, claimed


def test_a_real_apply_button_beats_a_stray_apply_link():
    from job_agent.apply.reveal import pick_apply
    m = page("Job", ["Job"])
    ActionModel = type(page("x", actions=["a"]).actions[0])
    m.actions += [ActionModel(id="link1", kind="link", label="Apply", region="main"), ActionModel(id="btn1", kind="button", label="Apply", region="main")]
    assert pick_apply(m) == "btn1"


def test_resume_input_is_found_by_label_accent_or_position():
    from job_agent.apply.demo import resume_field_keys
    from job_agent.apply.perceive import FieldModel

    def chosen(*labels):
        model = PageModel(url="u", fields=[FieldModel(id=f"f{i}", kind="file", label=lb) for i, lb in enumerate(labels)])
        keys = resume_field_keys(model)
        return [f.label for f in model.fields if f.key in keys]
    assert chosen("Cover letter", "R\u00e9sum\u00e9*") == ["R\u00e9sum\u00e9*"]
    assert chosen("Main attachment", "Cover Letter") == ["Main attachment"]
    assert chosen("Drop or select (.doc / .docx / .pdf)", "Cover letter") == ["Drop or select (.doc / .docx / .pdf)"]
    assert chosen("Cover letter", "Portfolio") == []


def test_confirm_your_email_box_is_not_an_emailed_code_prompt():
    form = page("Easy apply", ["Apply"], fields=[("text", "First name"), ("text", "Last name"), ("email", "Email"), ("email", "Confirm your email")])
    assert classify(obs(form)).state == PageState.FORM_STEP
    code = page("Verify", ["Verify your email"], fields=[("text", "Verification code")])
    assert classify(obs(code)).state == PageState.EMAIL_CODE


def test_workday_style_page_with_set_and_verify_password_is_a_signup_not_a_choice():
    """Both a Create Account and a Sign In button, but two password boxes (set + verify): it is the signup form itself."""
    combined = page("Software Engineer (II)", ["Create Account"], fields=[("text", "Email Address"), ("password", "Password"), ("password", "Verify New Password"),
                                                                       ("checkbox", "I have read and consent")], actions=["Create Account", "Sign In"])
    assert classify(obs(combined)).state == PageState.SIGNUP
