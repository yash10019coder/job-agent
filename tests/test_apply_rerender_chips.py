"""Workday-style widgets: a typeahead that keeps its choice as a chip, and inputs the page replaces while you type."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply import execute
from job_agent.apply.perceive import perceive

BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")

CHIPS = """<!doctype html><html><body><main>
<label for="a">How Did You Hear About Us?</label>
<div><input id="a" aria-describedby="d1" aria-required="true" placeholder="Search"><div id="d1" aria-hidden="true">0 items selected</div></div>
<label for="b">Country Phone Code</label>
<div><input id="b" aria-describedby="d2" aria-required="true" placeholder="Search"><div id="d2" aria-hidden="true">1 item selected, India (+91)</div>
<ul role="listbox" aria-label="items selected"><li>India (+91)</li></ul></div>
</main></body></html>"""

# The page replaces the input after a keystroke (a framework re-render): the stamp the engine put on the old node is gone, the value is not.
RERENDER = """<!doctype html><html><body><main><label for="fn">First Name</label><div id="host"><input id="fn" type="text"></div>
<script>
const host = document.getElementById('host');
host.addEventListener('input', (e) => { const old = e.target, v = old.value; const fresh = document.createElement('input'); fresh.id = 'fn'; fresh.type = 'text'; fresh.value = v;
  fresh.addEventListener('input', () => {}); old.replaceWith(fresh); fresh.focus(); }, {once: true});
</script></main></body></html>"""


def run(coro):
    return asyncio.run(coro)


async def _page(pw, html):
    browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
    page = await browser.new_page()
    await page.set_content(html)
    return browser, page


def test_a_chip_widgets_choice_is_its_value_and_nothing_chosen_is_empty():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw, CHIPS)
            m = (await perceive(page)).model
            await browser.close()
            return {f.label: f for f in m.fields}
    f = run(go())
    assert f["How Did You Hear About Us?"].value == "" and f["How Did You Hear About Us?"].empty      # "0 items selected": still to answer
    assert f["Country Phone Code"].value == "India (+91)"                                              # "1 item selected, India (+91)": answered


def test_the_fill_check_reads_the_chip_too():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw, CHIPS)
            a, b = await execute._current(page.locator("#a")), await execute._current(page.locator("#b"))
            await browser.close()
            return a, b
    assert run(go()) == ("", "India (+91)")


def test_a_value_typed_into_a_box_the_page_then_replaced_is_not_reported_as_a_failure():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw, RERENDER)
            p = await perceive(page)
            field = next(f for f in p.model.fields if f.label == "First Name")
            res = await execute.fill_text(page, p, field, "Yash")
            got = await page.input_value("#fn")
            await browser.close()
            return res, got
    res, got = run(go())
    assert got == "Yash" and res.ok, res.detail                                                    # it was typed; the old node vanished, the new one holds it


# A search box: the result list only appears after Enter (enterkeyhint="search"); a picked result becomes a chip, the input stays empty.
SEARCH = """<!doctype html><html><body><main><label for="s">How Did You Hear About Us?</label>
<div style="position:relative"><input id="s" enterkeyhint="search" placeholder="Search" aria-describedby="d" aria-required="true" style="width:240px"><div id="d" aria-hidden="true">0 items selected</div>
<ul id="res" style="position:absolute;top:30px;left:0;margin:0;padding:0;list-style:none;background:#fff;border:1px solid #888"></ul></div>
<script>
const inp = document.getElementById('s'), res = document.getElementById('res'), d = document.getElementById('d');
inp.addEventListener('keydown', (e) => { if (e.key !== 'Enter') return; e.preventDefault(); res.innerHTML = '';
  ['Linkedin', 'Linkedin Recruiter', 'Campus Event'].filter((n) => n.toLowerCase().includes(inp.value.toLowerCase())).forEach((n) => { const li = document.createElement('li'); li.textContent = n;
    li.style.cssText = 'cursor:pointer;padding:4px 10px'; li.onclick = () => { d.textContent = '1 item selected, ' + n; inp.value = ''; res.innerHTML = ''; }; res.appendChild(li); }); });
</script></main></body></html>"""


def test_a_search_box_that_lists_results_only_after_enter_is_answered_and_the_chip_is_the_value():
    async def go():
        async with async_playwright() as pw:
            browser, page = await _page(pw, SEARCH)
            p = await perceive(page)
            field = next(f for f in p.model.fields if f.label.startswith("How Did You Hear"))
            res = await execute.fill_text(page, p, field, "LinkedIn")
            chip = await execute._current(page.locator("#s"))
            await browser.close()
            return res, chip
    res, chip = run(go())
    assert res.ok and chip.lower().startswith("linkedin"), (res.detail, chip)
