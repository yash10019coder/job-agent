"""A dropdown built from a button (<button aria-haspopup="listbox">Select One</button>, popup list appended to <body>): read as a question and answered."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.demo import DemoResult, fill_until_stable
from job_agent.apply.perceive import perceive
from test_apply_perceive import FakeLLM

BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")

FACTS = {"referral_source_default": "LinkedIn", "contact": {}, "location": {}, "identity": {}, "consents": {}}

PAGE = """<!doctype html><html><body>
<header><button id="lang" aria-haspopup="listbox" aria-label="Select Language">English</button></header>
<main>
<label for="src">How Did You Hear About Us?*</label>
<button id="src" aria-haspopup="listbox" aria-label="How Did You Hear About Us? Select One Required" type="button">Select One</button>
<fieldset><legend>Are you legally authorized to work in India? Please answer truthfully; this is a legal declaration of your work status.</legend>
<button id="auth" aria-haspopup="listbox" aria-label=" Select One Required" type="button">Select One</button></fieldset>
<button id="nav" aria-haspopup="menu" aria-expanded="false">Account</button>
</main>
<script>
const open = (btn, names) => { document.querySelectorAll('#pop').forEach((p) => p.remove());
  const ul = document.createElement('ul'); ul.id = 'pop'; ul.setAttribute('role', 'listbox'); ul.style.cssText = 'position:absolute;top:200px;left:20px;background:#fff;border:1px solid #888';
  names.forEach((n) => { const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = n; li.style.padding = '4px 12px';
    li.onclick = () => { btn.textContent = n; ul.remove(); }; ul.appendChild(li); }); document.body.appendChild(ul); };
document.getElementById('src').onclick = (e) => open(e.currentTarget, ['Select One', 'Campus Event', 'LinkedIn', 'Referral']);
document.getElementById('auth').onclick = (e) => open(e.currentTarget, ['Select One', 'Yes', 'No']);
document.getElementById('lang').onclick = (e) => open(e.currentTarget, ['English', 'Hindi']);
</script></body></html>"""


def run(coro):
    return asyncio.run(coro)


async def _page(pw):
    browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
    page = await browser.new_page()
    await page.set_content(PAGE)
    return browser, page


def test_listbox_buttons_are_questions_and_header_pickers_or_menus_stay_buttons():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            model = (await perceive(page)).model
            await browser.close()
            return model
    model = run(go())
    fields = {f.label.split("?")[0]: f for f in model.fields}
    src = fields["How Did You Hear About Us"]
    assert src.kind == "combobox" and src.value == "" and src.required                       # "Select One" is the placeholder, not an answer
    legal = next(f for f in model.fields if f.label.startswith("Are you legally authorized"))   # no label at all: the question is the text above it
    assert legal.kind == "combobox" and legal.required and len(legal.label) > 60
    assert {a.label for a in model.actions} >= {"English", "Account"}                         # the header language picker and a menu button are not questions
    assert len(model.fields) == 2


def test_the_engine_opens_the_popup_list_and_picks_the_answer():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw)
            await fill_until_stable(page, FakeLLM(), "{}", None, DemoResult(), FACTS)
            got = await page.evaluate("() => [document.getElementById('src').textContent, document.getElementById('auth').textContent, document.getElementById('lang').textContent]")
            await browser.close()
            return got
    src, auth, lang = run(go())
    assert src == "LinkedIn" and auth == "Yes" and lang == "English"                          # both questions answered; the language picker untouched
