"""Overlays on top of the application: recognised by what they are (modal / banner with a plain way out), never by site."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply import execute, overlays
from job_agent.apply.perceive import perceive

BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
needs_browser = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")

FORM = '<main><label>Phone <input id="phone" type="text"></label><button id="go">Continue</button></main>'
BACKDROP = "position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1000;display:flex;align-items:center;justify-content:center"
BOX = "background:#fff;padding:24px;min-width:320px"


def modal(buttons: str, body: str = "Terms and Conditions. Please read and accept our terms to continue.") -> str:
    return (f'<div style="{BACKDROP}"><div role="dialog" aria-modal="true" style="{BOX}"><p>{body}</p>{buttons}</div></div>')


def run(coro):
    return asyncio.run(coro)


async def _with_page(html: str, body):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.set_content(f"<!doctype html><html><body>{html}</body></html>")
        try:
            return await body(page)
        finally:
            await browser.close()


# ---- the choice, by what the buttons say -------------------------------------------------------

def _b(*labels):
    return [{"i": i, "label": t} for i, t in enumerate(labels)]


def test_close_beats_reject_beats_accept():
    assert overlays.choose(_b("Accept all", "Reject all", "Close"), True)["label"] == "Close"
    assert overlays.choose(_b("Accept all", "Reject all"), True)["label"] == "Reject all"
    assert overlays.choose(_b("I agree", "View terms"), True)["label"] == "I agree"


def test_accepting_needs_permission():
    assert overlays.choose(_b("I agree", "View terms"), False) is None
    assert overlays.choose(_b("I agree", "Not now"), False)["label"] == "Not now"


def test_never_signs_in_up_submits_or_pays():
    assert overlays.choose(_b("Continue with Google", "Sign in", "Create account", "Submit application", "Apply now"), True) is None


def test_a_failed_click_that_reads_like_an_overlay():
    assert overlays.blocked_by_overlay('<div class="x"> intercepts pointer events')
    assert overlays.blocked_by_overlay("Locator.click: Timeout 3000ms exceeded.")
    assert not overlays.blocked_by_overlay("no option matches 'Yes'")


# ---- the page --------------------------------------------------------------------------------------

@needs_browser
def test_terms_overlay_blocks_the_box_until_it_is_cleared():
    async def body(page):
        await page.set_content("<!doctype html><html><body>" + FORM + modal('<button onclick="this.closest(\'[role=dialog]\').parentElement.remove()">I agree</button><a href="#">View terms</a>') + "</body></html>")
        p = await perceive(page)
        phone = next(f for f in p.model.fields if f.label.startswith("Phone"))
        before = await execute.fill_text(page, p, phone, "9000000000")
        notes = await overlays.clear(page, allow_accept=True)
        p = await perceive(page)
        phone = next(f for f in p.model.fields if f.label.startswith("Phone"))
        after = await execute.fill_text(page, p, phone, "9000000000")
        return before, notes, after

    before, notes, after = run(_with_page("", body))
    assert not before.ok and overlays.blocked_by_overlay(before.detail)   # the overlay really blocks, and the error says so
    assert notes and notes[0].startswith("dismissed overlay with 'I agree'")
    assert after.ok


@needs_browser
def test_overlay_is_left_alone_when_accepting_is_not_allowed():
    async def body(page):
        return await overlays.clear(page, allow_accept=False), await page.locator("[role=dialog]").count()

    notes, dialogs = run(_with_page(FORM + modal("<button>I agree</button>"), body))
    assert notes and notes[0].startswith("overlay with no safe way out") and dialogs == 1


@needs_browser
def test_close_is_preferred_over_accept_on_the_page():
    async def body(page):
        return await overlays.clear(page, allow_accept=True), await page.locator("[role=dialog]").count()

    html = FORM + modal('<button onclick="this.closest(\'[role=dialog]\').parentElement.remove()">Accept</button>'
                        '<button aria-label="Close" onclick="this.closest(\'[role=dialog]\').parentElement.remove()">×</button>')
    notes, dialogs = run(_with_page(html, body))
    assert notes[0].startswith("dismissed overlay with 'Close'") and dialogs == 0


@needs_browser
def test_a_dialog_that_asks_for_details_is_part_of_the_application():
    async def body(page):
        return await overlays.clear(page, allow_accept=True), await page.locator("[role=dialog]").count()

    html = FORM + modal('<label>Email <input type="email"></label><button>Close</button>', "Enter your email to continue")
    notes, dialogs = run(_with_page(html, body))
    assert notes == [] and dialogs == 1


@needs_browser
def test_social_sign_in_dialog_is_not_clicked():
    async def body(page):
        return await overlays.clear(page, allow_accept=True)

    notes = run(_with_page(FORM + modal("<button>Continue with Google</button><button>Sign in</button>", "Join to apply"), body))
    assert notes and notes[0].startswith("overlay with no safe way out")


@needs_browser
def test_cookie_banner_at_the_bottom():
    async def body(page):
        notes = await overlays.clear(page, allow_accept=True)
        return notes, await page.locator("#cookies").count()

    banner = ('<div id="cookies" style="position:fixed;bottom:0;left:0;right:0;height:90px;z-index:50;background:#222;color:#fff">'
              'We use cookies. <button onclick="document.getElementById(\'cookies\').remove()">Accept all cookies</button></div>')
    notes, left = run(_with_page(FORM + banner, body))
    assert notes[0].startswith("dismissed overlay with 'Accept all cookies'") and left == 0


@needs_browser
def test_overlay_inside_a_web_component_shadow_root():
    """Design-system sites (ADP, Salesforce) render their dialogs inside open shadow roots."""
    comp = ("<terms-modal></terms-modal><script>customElements.define('terms-modal', class extends HTMLElement { connectedCallback() {"
            f"const r = this.attachShadow({{mode:'open'}}); r.innerHTML = '<div style=\"{BACKDROP}\"><div role=dialog aria-modal=true style=\"{BOX}\">"
            "<p>Terms of use</p><button id=ok>Got it</button></div></div>'; r.getElementById('ok').onclick = () => this.remove(); } });</script>")

    async def body(page):
        notes = await overlays.clear(page, allow_accept=True)
        return notes, await page.locator("terms-modal").count()

    notes, left = run(_with_page(FORM + comp, body))
    assert notes[0].startswith("dismissed overlay with 'Got it'") and left == 0


@needs_browser
def test_a_plain_page_has_nothing_to_clear():
    async def body(page):
        return await overlays.clear(page, allow_accept=True)

    assert run(_with_page(FORM, body)) == []


@needs_browser
def test_ticking_a_consent_box_that_opens_its_policy_does_not_block_the_next_field():
    """ADP: the consent tick opens the Employer Privacy Policy over the form. The fields after it were filled under that overlay
    (the country picker click was blocked), so overlays are cleared after every field, not only at the start of a step."""
    from job_agent.apply.demo import DemoResult, fill_until_stable
    from job_agent.apply.llm import LLM

    html = ("<main><label>First Name <input id='fn' type='text'></label>"
            "<label><input id='consent' type='checkbox' onclick=\"document.getElementById('policy').style.display='flex'; this.checked=false\"> "
            "I have read and accept the Privacy Policy</label><button id='go'>Continue</button></main>"
            f"<div id='policy' style=\"display:none;{BACKDROP}\"><div role='dialog' aria-modal='true' style=\"{BOX}\">"
            "<p>Privacy Policy. By submitting my application I certify that I have reviewed and understand this policy.</p>"
            "<button onclick=\"document.getElementById('policy').style.display='none'; document.getElementById('consent').checked=true\">Agree</button></div></div>")
    facts = {"identity": {"first_name": "Yash"}, "consents": {"accept_privacy_and_terms": True}, "contact": {}, "location": {}}

    async def body(page):
        result = DemoResult()
        await fill_until_stable(page, LLM(model="fake", provider="openrouter"), "{}", None, result, facts)
        return (await page.input_value("#fn"), await page.is_checked("#consent"),
                await page.evaluate("document.getElementById('policy').style.display"), result.events)

    first, ticked, policy, events = run(_with_page(html, body))
    assert first == "Yash" and ticked and policy == "none", (first, ticked, policy, events)
    assert any("dismissed overlay with 'Agree'" in e for e in events)


@needs_browser
def test_a_dialog_that_offers_ways_to_start_the_application_is_left_for_the_apply_logic():
    """Workday-style: 'Autofill with Resume / Apply Manually / Use My Last Application' also has a Close button, but it is a choice, not a nuisance."""
    async def body(page):
        return await overlays.clear(page, allow_accept=True), await page.locator("[role=dialog]").count()

    html = FORM + modal("<button>Autofill with Resume</button><button>Apply Manually</button><button>Use My Last Application</button><button aria-label='Close'>×</button>", "Start your application")
    notes, dialogs = run(_with_page(html, body))
    assert notes == [] and dialogs == 1
