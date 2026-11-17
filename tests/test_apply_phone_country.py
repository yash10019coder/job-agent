"""A country picker built into a phone box ("US" button + list of countries): set to the profile's country before the number is typed."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply import phone_country
from job_agent.apply.perceive import perceive

BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")

FACTS = {"location": {"country": "India", "phone_country_code": "+91"}, "contact": {"phone": "+91 9000000000"}}

# Like ADP's <sdf-phone-number-input>: picker and number box live in a shadow root; the list items are role=option, named by country.
WIDGET = """<phone-box start="%s" FLAKY></phone-box><script>
customElements.define('phone-box', class extends HTMLElement { connectedCallback() {
  const r = this.attachShadow({mode: 'open'});
  const rows = [['BR','Brazil'],['IO','British Indian Ocean Territory'],['IN','India'],['US','United States']];
  const start = this.getAttribute('start');
  r.innerHTML = '<label>Phone Number</label><div><div id="pick" role="button" aria-expanded="false" tabindex="0"><span id="lab">' + start + '</span></div>'
    + '<input id="num" type="tel" placeholder="Enter phone number"></div><div id="list" role="listbox" style="display:none"></div>';
  const list = r.getElementById('list'), lab = r.getElementById('lab'), pick = r.getElementById('pick');
  rows.forEach(([code, name]) => { const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = name;
    if (code === start) { o.setAttribute('aria-selected', 'true'); o.className = 'selected'; }
    o.onclick = () => { lab.textContent = code; r.getElementById('num').value = ''; list.style.display = 'none'; pick.setAttribute('aria-expanded', 'false');
      list.querySelectorAll('[role=option]').forEach((x) => x.removeAttribute('aria-selected')); o.setAttribute('aria-selected', 'true'); };
    list.appendChild(o); });
  pick.onclick = () => { const open = list.style.display === 'none'; list.style.display = open ? 'block' : 'none'; pick.setAttribute('aria-expanded', String(open)); };
  let flipped = false;
  r.getElementById('num').addEventListener('change', () => { if (this.hasAttribute('flaky') && !flipped) { flipped = true; setTimeout(() => { lab.textContent = 'US'; }, 300); } });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { list.style.display = 'none'; pick.setAttribute('aria-expanded', 'false'); } });
} });</script>"""


def run(coro):
    return asyncio.run(coro)


async def _go(html: str, facts=FACTS):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.set_content(f"<!doctype html><html><body><main>{html}</main></body></html>")
        p = await perceive(page)
        tel = next(f for f in p.model.fields if f.kind == "tel")
        note = await phone_country.ensure_country(page, p, tel, facts)
        label = await page.evaluate("() => { const r = document.querySelector('phone-box'); return r ? r.shadowRoot.getElementById('lab').textContent : null; }")
        open_list = await page.evaluate("() => { const r = document.querySelector('phone-box'); return r ? r.shadowRoot.getElementById('list').style.display : null; }")
        await browser.close()
        return note, label, open_list


def test_us_picker_is_changed_to_india_and_the_list_is_closed():
    note, label, open_list = run(_go(WIDGET.replace('FLAKY', '') % "US"))
    assert note and note.startswith("US -> India") and label == "IN" and open_list == "none"


def test_a_picker_already_on_india_is_left_alone():
    note, label, open_list = run(_go(WIDGET.replace('FLAKY', '') % "IN"))
    assert note == "already India" and label == "IN" and open_list == "none"


def test_british_indian_ocean_territory_is_not_mistaken_for_india():
    note, label, _ = run(_go(WIDGET.replace('FLAKY', '') % "BR"))
    assert label == "IN"                      # an exact country name, not the first option that contains "india"


def test_a_phone_box_without_a_picker_returns_none():
    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content('<!doctype html><html><body><main><label>Phone <input type="tel"></label><button>Continue</button></main></body></html>')
            p = await perceive(page)
            tel = next(f for f in p.model.fields if f.kind == "tel")
            out = await phone_country.ensure_country(page, p, tel, FACTS)
            await browser.close()
            return out
    assert run(go()) is None                  # Continue is a real button nearby, but not a country control


def test_without_a_country_in_the_profile_nothing_is_touched():
    note, label, _ = run(_go(WIDGET.replace('FLAKY', '') % "US", facts={"location": {}, "contact": {"phone": "9000000000"}}))
    assert note is None and label == "US"


def test_a_picker_the_site_puts_back_is_set_again_and_the_number_retyped():
    """On ADP the picker went back to US after we set it, while the number we typed stayed in the box: the box looked filled, so nothing noticed."""
    from job_agent.apply.demo import DemoResult, fill_until_stable
    from job_agent.apply.llm import LLM

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content("<!doctype html><html><body><main>" + (WIDGET % "US").replace("FLAKY", "flaky") + "</main></body></html>")
            result = DemoResult()
            await fill_until_stable(page, LLM(model="fake", provider="openrouter"), "{}", None, result, FACTS)
            state = await page.evaluate("() => { const r = document.querySelector('phone-box').shadowRoot; return {picker: r.getElementById('lab').textContent, number: r.getElementById('num').value}; }")
            await browser.close()
            return state, result.events

    state, events = run(go())
    assert state["picker"] == "IN" and state["number"] == "9000000000", (state, events)
    assert any("changed from" in e for e in events)      # it noticed the picker had been put back


def test_repair_never_puts_the_country_code_back_when_the_box_has_its_own_country_control():
    """The country lives in the picker, so the box takes the national number only. A repair that retried '+91 ...' (or a model that
    suggested it) is what broke the number on ADP after the picker had been set right."""
    import json
    from job_agent.apply import repair
    from job_agent.apply.llm import LLM
    from job_agent.apply.perceive import Settler

    class RepairLLM(LLM):
        def __init__(self):
            super().__init__(model="fake", provider="openrouter")
            self.asked = False

        async def complete_json(self, purpose, system, user, schema, name="answer"):
            self.asked = True
            fid = json.loads(user)["fields"][0]["id"]
            return {"fixes": [{"id": fid, "value": "+91 9000000000"}]}      # a model that "helpfully" adds the country code

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content("<!doctype html><html><body><main>" + (WIDGET % "IN").replace("FLAKY", "") + "</main></body></html>")
            await page.evaluate("() => { const n = document.querySelector('phone-box').shadowRoot.getElementById('num'); n.value = '90000'; n.setAttribute('aria-invalid', 'true'); }")
            llm, events = RepairLLM(), []
            await repair.validate_and_repair(page, llm, Settler(page), FACTS, events)
            value = await page.evaluate("() => document.querySelector('phone-box').shadowRoot.getElementById('num').value")
            await browser.close()
            return value, events, llm.asked

    value, events, asked = run(go())
    assert asked and value == "9000000000", (value, events)
    assert not any("+91" in e.replace("'+91 9000000000'", "") for e in events if "accepted as" in e)


def test_variants_for_a_box_with_its_own_country_control_carry_no_country_code():
    from job_agent.apply import repair
    from job_agent.apply.perceive import FieldModel
    f = FieldModel(id="x", kind="tel", label="Phone Number", value="90000")
    assert repair.variants(f, FACTS, "90000", national_only_=True) == ["9000000000"]
    assert "+919000000000" in repair.variants(f, FACTS, "90000")            # without a separate control the old forms are still tried
    assert repair.national_only("+91 90000 00000", FACTS) == "9000000000"


def test_a_box_the_site_never_registered_is_retyped_when_a_page_message_names_it():
    """ADP: the number sat in the box but the form behind it still said 'Phone Number is required.' (shown away from the box, so the box
    carried no error). Only typed key presses count for that widget: repair retypes the box a page message names by label."""
    from job_agent.apply import repair
    from job_agent.apply.perceive import Settler

    html = ("<main><label>Phone Number <input id='n' type='tel'></label><div id='msg' role='alert' style='display:none'>Phone Number is required.</div>"
            "<script>const n = document.getElementById('n'), m = document.getElementById('msg'); let typed = false;"
            "n.addEventListener('keydown', () => { typed = true; });"
            "n.addEventListener('blur', () => { m.style.display = typed ? 'none' : 'block'; });</script></main>")

    async def go():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            await page.set_content("<!doctype html><html><body>" + html + "</body></html>")
            await page.evaluate("document.getElementById('n').value = '9000000000'")     # set without key events, like fill()
            events = []
            left = await repair.validate_and_repair(page, None, Settler(page), FACTS, events)
            out = await page.evaluate("() => ({value: document.getElementById('n').value, msg: getComputedStyle(document.getElementById('msg')).display})")
            await browser.close()
            return left, out, events

    left, out, events = run(go())
    assert out == {"value": "9000000000", "msg": "none"}, (out, events)
    assert left == [] and any("retyped 'Phone Number'" in e for e in events)
