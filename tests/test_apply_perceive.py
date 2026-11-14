"""Perception + fill-loop tests against a local form. No network, no real LLM (scripted FakeLLM)."""
import asyncio
import json
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.demo import DemoResult, build_context, fill_until_stable
from job_agent.apply.llm import LLM
from job_agent.apply.perceive import diff, perceive

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/apply_demo.html").resolve().as_uri()
RESUME = str(ROOT / "tests/fixtures/resume.pdf")
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"

pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


class FakeLLM(LLM):
    """Answers by label, like a model would; records every call."""

    def __init__(self):
        super().__init__(model="fake", provider="openrouter")
        self.purposes = []

    async def complete_json(self, purpose, system, user, schema, name="answer"):
        self.purposes.append(purpose)
        if purpose == "resolve_fields":
            fields = json.loads(user.split("Fields:\n", 1)[1])
            rules = {"first name": ("Yash", "profile"), "email": ("yash@example.com", "profile"),
                     "phone": ("+91 90000 00000", "profile"), "authorized": ("Yes", "profile"),
                     "sponsorship": ("Yes", "profile"), "visa type": ("H-1B", "profile"),
                     "hear about": ("LinkedIn", "profile"), "notice": ("30 days", "profile"),
                     "why do you": ("I like building reliable backend systems.", "derived"),
                     "privacy": ("checked", "profile")}
            answers = []
            for f in fields:
                q = f["question"].lower()
                hit = next((v for k, v in rules.items() if k in q), None)
                answers.append({"id": f["id"], "value": hit[0] if hit else "", "confidence": 0.9 if hit else 0.0,
                                "source": hit[1] if hit else "unknown", "basis": "from the facts" if hit else ""})
            return {"answers": answers}
        if purpose == "decide_option":   # option-decider fallback: pick the first real option
            options = json.loads(user.split("Options: ", 1)[1])
            return {"choice": options[0], "confidence": 0.9, "rationale": "scripted"}
        if purpose == "control_roles":   # scripted stand-in for the model: labels by wording, like a model would
            roles = []
            for line in user.split("Controls:\n", 1)[1].splitlines():
                cid, _kind, _form, _region, _state, label = [x.strip() for x in line.split("|", 5)]
                low = label.lower()
                role = ("submit_final" if low.startswith(("submit", "apply now", "send application", "finish", "complete application"))
                        else "next_step" if low.startswith(("next", "continue", "review", "save and continue", "save & continue")) else "other")
                roles.append({"id": cid, "role": role})
            return {"roles": roles}
        raise AssertionError(f"unexpected LLM call: {purpose}")


async def _page(pw):
    browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
    page = await browser.new_page()
    await page.goto(FIXTURE)
    return browser, page


def run(coro):
    return asyncio.run(coro)


def test_perceiver_reads_labels_kinds_groups_and_options():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            model = (await perceive(page)).model
            await browser.close()
            return model

    model = run(go())
    by_label = {f.label: f for f in model.fields}
    assert by_label["First name"].required and by_label["First name"].kind == "text"
    assert by_label["Email"].kind == "email"
    assert by_label["Phone number"].kind == "tel"          # label inferred from preceding text, no <label>
    assert by_label["Resume/CV"].kind == "file"
    auth = by_label["Are you legally authorized to work in India?"]
    assert auth.kind == "radio_group" and [o.label for o in auth.options] == ["Yes", "No"]  # grouped via fieldset legend
    assert by_label["How did you hear about us?"].kind == "combobox" and by_label["How did you hear about us?"].async_options
    assert [o.label for o in by_label["Notice period"].options][0] == "Immediately"          # placeholder option dropped
    assert "Which visa type?" not in by_label              # hidden conditional field is not perceived yet
    assert any(a.label == "Submit application" and a.primary for a in model.actions)


def test_fill_until_stable_discovers_conditional_field_and_never_submits():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            llm, result = FakeLLM(), DemoResult()
            ctx = build_context(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
            await fill_until_stable(page, llm, ctx, RESUME, result)
            state = await page.evaluate("""() => ({
              first: document.getElementById('fn').value, visa: document.getElementById('vt').value,
              visaShown: !document.getElementById('visa').classList.contains('hidden'),
              source: document.getElementById('src').value, notice: document.getElementById('np').value,
              spons: document.querySelector('input[name=spons]:checked')?.value,
              consent: document.querySelector('input[name=consent]').checked,
              file: document.getElementById('cv').files[0]?.name, why: document.getElementById('why').value})""")
            await browser.close()
            return llm, result, state

    llm, result, state = run(go())
    assert state["spons"] == "yes" and state["visaShown"] and state["visa"] == "H-1B"   # conditional field found + filled
    assert state["source"] == "LinkedIn"                                                  # async combobox: open, read popup, pick
    assert state["notice"] == "30 days" and state["consent"] and state["file"] == "resume.pdf" and state["first"] == "Yash"
    assert result.rounds >= 2                                                              # needed a second round for the new field
    assert any("added: Which visa type?" in e for e in result.events)
    assert result.stopped_before == "Submit application"                                  # reached submit, did not press it
    assert llm.purposes.count("resolve_fields") == result.rounds                          # one batched call per round
    assert all(r.status == "ok" for r in result.rows), [r for r in result.rows if r.status != "ok"]


def test_diff_reports_added_and_removed_fields():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            before = (await perceive(page)).model
            await page.check('input[name=spons][value=yes]')
            after = (await perceive(page)).model
            await page.check('input[name=spons][value=no]')
            gone = (await perceive(page)).model
            await browser.close()
            return diff(before, after), diff(after, gone)

    shown, hidden = run(go())
    assert shown.added == ["Which visa type?"] and hidden.removed == ["Which visa type?"]


def test_unsupported_answers_are_never_typed_in():
    """A derived answer with no stated basis is not applied; required fields become BLOCKED, optional ones stay blank."""
    class Baseless(FakeLLM):
        async def complete_json(self, purpose, system, user, schema, name="answer"):
            out = await super().complete_json(purpose, system, user, schema, name)
            if purpose == "resolve_fields":
                for a in out["answers"]:
                    a["source"], a["basis"] = "derived", ""      # claims derivation but gives no basis
            return out

    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            result = DemoResult()
            ctx = build_context(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
            await fill_until_stable(page, Baseless(), ctx, RESUME, result)
            first = await page.evaluate("() => document.getElementById('fn').value")
            why = await page.evaluate("() => document.getElementById('why').value")
            await browser.close()
            return first, why, result

    first, why, result = run(go())
    assert first == "" and why == ""                                                   # nothing unsupported got typed in
    assert any(r.label == "First name" and r.status.startswith("BLOCKED") for r in result.rows)           # required
    assert any(r.label.startswith("Why do you") and r.status == "left blank (optional)" for r in result.rows)  # optional


def test_option_decider_fills_choices_the_batch_call_left_unknown():
    """Choice fields the batch resolver can't answer go to the option decider, which sees the real options."""
    class OnlyDecider(FakeLLM):
        async def complete_json(self, purpose, system, user, schema, name="answer"):
            if purpose == "resolve_fields":
                fields = json.loads(user.split("Fields:\n", 1)[1])
                return {"answers": [{"id": f["id"], "value": "", "confidence": 0.0, "source": "unknown", "basis": ""} for f in fields]}
            return await super().complete_json(purpose, system, user, schema, name)

    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            llm, result = OnlyDecider(), DemoResult()
            ctx = build_context(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
            await fill_until_stable(page, llm, ctx, RESUME, result)
            state = await page.evaluate("() => ({np: document.getElementById('np').value, src: document.getElementById('src').value})")
            await browser.close()
            return llm, state

    llm, state = run(go())
    assert "decide_option" in llm.purposes
    assert state["np"] == "Immediately" and state["src"] == "LinkedIn"      # first real option picked by the (scripted) decider


def test_header_login_link_is_region_header():
    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content(
                "<header><a href='#'>Login</a></header><main><form>"
                "<label for=a>Full name</label><input id=a><label for=b>Email</label><input id=b type=email></form>"
                "<footer><a href='#'>Privacy</a></footer></main>"
                "<div role=dialog><button>Accept cookies</button></div>")
            model = (await perceive(page)).model
            await browser.close()
            return model
    m = run(go())
    assert next(a for a in m.actions if a.label == "Login").region == "header"
    assert next(a for a in m.actions if a.label == "Privacy").region == "footer"
    assert next(a for a in m.actions if a.label == "Accept cookies").region == "dialog"
    assert [f.region for f in m.fields] == ["main", "main"]


def test_guesses_are_typed_in_so_no_field_is_left_unanswered():
    """The applicant asked for every field to be answered: a low-confidence 'guess' is applied and listed for review."""
    class Guesser(FakeLLM):
        async def complete_json(self, purpose, system, user, schema, name="answer"):
            out = await super().complete_json(purpose, system, user, schema, name)
            if purpose == "resolve_fields":
                for a in out["answers"]:
                    if not a["value"]:
                        a.update(value="Because the work matches my backend experience.", confidence=0.2, source="guess", basis="typical answer")
            return out

    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            result = DemoResult()
            ctx = build_context(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
            await fill_until_stable(page, Guesser(), ctx, RESUME, result)
            await browser.close()
            return result

    result = run(go())
    assert not [r for r in result.rows if r.status.startswith("BLOCKED")]
