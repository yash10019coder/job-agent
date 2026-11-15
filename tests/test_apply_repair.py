"""Format handling: phone split from a separate country-code control, blur-time errors read and repaired before moving on."""
import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.demo import DemoResult, fill_until_stable
from job_agent.apply.perceive import FieldModel, Option, PageModel, perceive
from job_agent.apply.prefill import build_facts
from job_agent.apply.repair import has_country_code_control, phone_parts, variants
from test_apply_perceive import BRAVE, RESUME, ROOT, FakeLLM

FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
FIX = (ROOT / "tests/fixtures/repair_form.html").resolve().as_uri()


def test_phone_parts_splits_the_profile_number():
    code, national = phone_parts({"contact": {"phone": "+91 89575 57113"}, "location": {"phone_country_code": "+91"}})
    assert code == "+91" and national == "8957557113"
    assert phone_parts({"contact": {"phone": "9876543210"}, "location": {}}) == ("", "9876543210")


def test_country_code_control_is_found_by_value_options_or_label():
    phone = FieldModel(id="b", kind="tel", label="Phone number")
    by_value = FieldModel(id="a", kind="combobox", label="Search", value="+1 US")
    by_label = FieldModel(id="a", kind="select", label="Country code")
    opts = [Option(id=str(i), label=f"Land {i} +{40 + i}") for i in range(6)]
    by_options = FieldModel(id="a", kind="select", label="Region", options=opts)
    other = FieldModel(id="a", kind="text", label="City")
    for ctl, want in ((by_value, True), (by_label, True), (by_options, True), (other, False)):
        model = PageModel(url="u", fields=[ctl, phone])
        assert has_country_code_control(model, model.fields[1]) is want


def test_variants_for_a_rejected_phone_and_url():
    tel = FieldModel(id="t", kind="tel", label="Phone number")
    facts = {"contact": {"phone": "+91 8957557113"}, "location": {"phone_country_code": "+91"}}
    assert variants(tel, facts, "+918957557113")[0] == "8957557113"
    url = FieldModel(id="u", kind="url", label="LinkedIn URL")
    assert variants(url, facts, "linkedin.com/in/x") == ["https://linkedin.com/in/x"]


pytestmark_live = pytest.mark.skipif(not BRAVE, reason="Brave not installed")


class RepairLLM(FakeLLM):
    async def complete_json(self, purpose, system, user, schema, name="answer"):
        if purpose == "resolve_fields":
            fields = json.loads(user.split("Fields:\n", 1)[1])
            return {"answers": [{"id": f["id"], "value": "Negotiable" if "ctc" in f["question"].lower() else "", "confidence": 0.9,
                                 "source": "policy" if "ctc" in f["question"].lower() else "unknown", "basis": "policy"} for f in fields]}
        if purpose == "repair_fields":
            payload = json.loads(user)
            return {"fixes": [{"id": f["id"], "value": "1200000"} for f in payload["fields"] if "ctc" in f["label"].lower()]}
        return await super().complete_json(purpose, system, user, schema, name)


@pytestmark_live
def test_phone_next_to_a_country_code_gets_the_national_number_and_errors_are_repaired():
    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(FIX)
            result = DemoResult()
            await fill_until_stable(page, RepairLLM(), json.dumps(FACTS), RESUME, result, FACTS)
            state = await page.evaluate("() => ({ph: document.getElementById('ph').value, ct: document.getElementById('ct').value, "
                                        "phBad: document.getElementById('ph').getAttribute('aria-invalid'), ctBad: document.getElementById('ct').getAttribute('aria-invalid')})")
            await browser.close()
            return state, result

    state, result = asyncio.run(go())
    assert state["ph"] == "8957557113" and state["phBad"] != "true", state    # national number only, no 'Enter a valid number'
    assert state["ct"] == "1200000" and state["ctBad"] != "true", state          # 'Negotiable' was rejected, the model repaired it
    assert any("model repair" in e for e in result.events), result.events


@pytestmark_live
def test_yes_no_toggle_buttons_are_a_required_question_and_get_answered():
    """Ashby-style Yes/No answers are aria-pressed buttons, not radios: they must show up as a field and be pressed."""
    url = (ROOT / "tests/fixtures/toggle_buttons.html").resolve().as_uri()

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url)
            before = (await perceive(page)).model
            result = DemoResult()
            await fill_until_stable(page, FakeLLM(), json.dumps(FACTS), RESUME, result, FACTS)
            pressed = await page.evaluate("() => [...document.querySelectorAll('.yesno button')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.innerText)")
            await browser.close()
            return before, pressed, result

    before, pressed, result = asyncio.run(go())
    question = next(f for f in before.fields if "sponsorship" in f.label)
    assert question.required and question.kind == "radio_group" and [o.label for o in question.options] == ["Yes", "No"]
    assert not any(a.label in {"Yes", "No"} for a in before.actions)       # the buttons are one question, not two loose actions
    assert len(pressed) == 1, (pressed, [r for r in result.rows])           # exactly one answer was chosen


@pytestmark_live
def test_checkboxes_sharing_a_fieldset_are_one_required_question_and_get_answered():
    """Ashby-style 'How did you hear about us?': a fieldset whose title is a <label>, with one separately named checkbox per answer."""
    url = (ROOT / "tests/fixtures/checkbox_fieldset.html").resolve().as_uri()

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url)
            before = (await perceive(page)).model
            result = DemoResult()
            await fill_until_stable(page, FakeLLM(), json.dumps(FACTS), RESUME, result, FACTS)
            checked = await page.evaluate("() => [...document.querySelectorAll('input[type=checkbox]')].filter(c => c.checked).map(c => c.name)")
            await browser.close()
            return before, checked, result

    before, checked, result = asyncio.run(go())
    group = next(f for f in before.fields if f.kind == "checkbox_group")
    assert group.label.startswith("How did you hear") and group.required and len(group.options) == 3
    assert not any(f.kind == "checkbox" for f in before.fields)              # not three unrelated checkboxes
    assert len(checked) >= 1, (checked, result.rows)                          # an answer was ticked


@pytestmark_live
def test_site_defaults_that_contradict_the_profile_are_overwritten_and_shared_labels_use_placeholders():
    """A location the SITE guessed ('Indianapolis') must not stand when the profile says otherwise; inputs that all inherit one
    section heading as their label are told apart by their placeholders."""
    url = (ROOT / "tests/fixtures/site_defaults.html").resolve().as_uri()

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.goto(url)
            before = (await perceive(page)).model
            result = DemoResult()
            await fill_until_stable(page, FakeLLM(), json.dumps(FACTS), RESUME, result, FACTS)
            vals = await page.evaluate("() => ({fn: fn.value, ln: ln.value, em: em.value, loc: loc.value})")
            await browser.close()
            return before, vals, result

    before, vals, result = asyncio.run(go())
    labels = [f.label for f in before.fields]
    assert "First Name" in labels and "Email Address" in labels and "Get in touch directly with the team." not in labels, labels
    assert vals["fn"] == FACTS["identity"]["first_name"] and vals["em"] == FACTS["contact"]["email"], vals
    assert FACTS["location"]["city"].lower() in vals["loc"].lower() and "indianapolis" not in vals["loc"].lower(), (vals, result.events)
