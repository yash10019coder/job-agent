"""A design-system dropdown: a role=button trigger named by a separate label (aria-labelledby), inside a shadow root, opening a list."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.demo import DemoResult, fill_until_stable
from job_agent.apply.llm import LLM
from job_agent.apply.perceive import perceive

BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")

FACTS = {"location": {"country": "India", "state": "Maharashtra", "city": "Mumbai", "phone_country_code": "+91"},
         "contact": {"email": "a@b.co", "phone": "+91 9000000000"}, "identity": {}, "consents": {}}

# Like ADP's <sdf-select-simple>: the label element and the trigger share a shadow root; the list items are role=option.
PAGE = """<!doctype html><html><body><main>
<button aria-expanded="false" aria-label="Menu">Menu</button>
<sel-box label="State *" start="" options="Karnataka|Maharashtra|Tamil Nadu"></sel-box>
<sel-box label="Country" start="United States" options="India|United States"></sel-box>
<sel-box label="" start="IN" options="India|United States" phone="1"></sel-box>
<script>
customElements.define('sel-box', class extends HTMLElement { connectedCallback() {
  const r = this.attachShadow({mode: 'open'}), start = this.getAttribute('start') || 'Select...';
  r.innerHTML = '<label id="label">' + this.getAttribute('label') + '</label>'
    + '<div id="trigger" role="button" aria-expanded="false" tabindex="0" aria-labelledby="label selected-label"><span id="selected-label">' + start + '</span></div>'
    + '<div id="list" role="listbox" style="display:none"></div>';
  const list = r.getElementById('list'), sel = r.getElementById('selected-label'), trg = r.getElementById('trigger');
  this.getAttribute('options').split('|').forEach((name) => { const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = name;
    o.onclick = () => { sel.textContent = name; list.style.display = 'none'; trg.setAttribute('aria-expanded', 'false'); }; list.appendChild(o); });
  trg.onclick = () => { const open = list.style.display === 'none'; list.style.display = open ? 'block' : 'none'; trg.setAttribute('aria-expanded', String(open)); };
} });</script></main></body></html>"""


def run(coro):
    return asyncio.run(coro)


async def _page(pw):
    browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
    page = await browser.new_page()
    await page.set_content(PAGE)
    return browser, page


def test_a_labelled_dropdown_trigger_is_a_field_and_a_phone_country_picker_or_menu_button_is_not():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            model = (await perceive(page)).model
            await browser.close()
            return model

    model = run(go())
    fields = {f.label: f for f in model.fields}
    assert fields["State"].kind == "combobox" and fields["State"].value == "" and fields["State"].required
    assert fields["Country"].kind == "combobox" and fields["Country"].value == "United States"
    assert len(model.fields) == 2                                     # the 'IN' phone picker is not a question of its own
    assert any(a.label == "Menu" for a in model.actions)               # a plain menu toggle stays an action


def test_state_is_chosen_and_a_site_default_country_that_disagrees_with_the_profile_is_corrected():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            await fill_until_stable(page, LLM(model="fake", provider="openrouter"), "{}", None, DemoResult(), FACTS)
            got = await page.evaluate("() => [...document.querySelectorAll('sel-box')].map((b) => b.shadowRoot.getElementById('selected-label').textContent)")
            await browser.close()
            return got

    state, country, picker = run(go())
    assert state == "Maharashtra" and country == "India" and picker == "IN"
