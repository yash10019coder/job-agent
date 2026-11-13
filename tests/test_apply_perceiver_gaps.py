"""Regression tests for the perceiver gaps recorded in tests/fixtures/perceiver_gaps.html."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply import execute
from job_agent.apply.perceive import explore, install_open_shadow, perceive

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (ROOT / "tests/fixtures/perceiver_gaps.html").resolve().as_uri()
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def run_on_fixture(coro_fn):
    async def main():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            context = await browser.new_context()
            await install_open_shadow(context)
            page = await context.new_page()
            await page.goto(FIXTURE)
            try:
                return await coro_fn(page)
            finally:
                await browser.close()
    return asyncio.run(main())


def by_label(model, text):
    return next((f for f in model.fields if text.lower() in f.label.lower()), None)


def test_gap_fields_are_perceived_after_explore():
    async def go(page):
        return (await explore(page)).model
    m = run_on_fixture(go)
    labels = [f.label for f in m.fields]
    assert not any("hp_trap" in f.label for f in m.fields), labels   # honeypot inputs a human cannot see
    assert by_label(m, "Date of birth").kind == "date" and by_label(m, "Date of birth").readonly   # readonly calendar input
    work = by_label(m, "Work mode")
    assert work and [o.label for o in work.options] == ["Remote", "On-site"]                        # display:none radios
    assert by_label(m, "Cover") or any(f.kind == "textarea" for f in m.fields)                      # srcdoc rich-text editor
    qa, qb = by_label(m, "Over 18"), by_label(m, "Own a car")                                       # nameless radio sets stay separate
    assert qa and qb and [o.label for o in qa.options] == ["Yes", "No"] and [o.label for o in qb.options] == ["Yes", "No"]
    assert by_label(m, "Inside details") and by_label(m, "Extra details")                           # <details> + aria-expanded
    assert by_label(m, "Lazy question")                                                             # scroll-triggered
    emp = by_label(m, "Employment type")
    assert emp and emp.cards and [o.label for o in emp.options] == ["Full-time", "Contract"]        # clickable cards
    assert by_label(m, "Closed shadow") and by_label(m, "Open shadow"), labels                      # closed roots forced open


def test_gap_widgets_can_be_driven():
    async def go(page):
        p = await explore(page)
        m = p.model
        out = {}
        out["date"] = await execute.set_date(page, p, by_label(m, "Date of birth"), "05/06/1999")
        out["radio"] = await execute.set_choice(p, by_label(m, "Work mode"), "On-site")
        out["card"] = await execute.set_choice(p, by_label(m, "Employment type"), "Contract")
        out["rich"] = await execute.fill_text(page, p, next(f for f in m.fields if f.kind == "textarea"), "Hello letter")
        out["checked"] = await page.evaluate("() => document.querySelector('input[name=wm][value=b]').checked")
        return out
    out = run_on_fixture(go)
    assert out["date"].ok, out["date"]
    assert out["radio"].ok and out["checked"], out["radio"]
    assert out["card"].ok, out["card"]
    assert out["rich"].ok, out["rich"]


def test_inputs_inside_web_components_get_labels_from_the_host_or_autocomplete():
    async def go(page):
        await page.goto((ROOT / "tests/fixtures/web_component_form.html").resolve().as_uri())
        return (await perceive(page)).model
    m = run_on_fixture(go)
    assert [f.label for f in m.fields] == ["Given name", "Email address", "Last name", "Phone"]


def test_a_div_combobox_with_a_make_a_selection_placeholder_is_empty_and_can_be_chosen():
    from job_agent.apply import demo
    from job_agent.apply.perceive import Settler

    async def go(page):
        await page.goto((ROOT / "tests/fixtures/div_combobox.html").resolve().as_uri())
        p = await perceive(page)
        field = next(f for f in p.model.fields if f.kind == "combobox")
        before = field.value
        res, _ = await demo._apply_field(page, p, Settler(page), field, "India +91", None, "")
        return before, res, await page.inner_text("#cb")
    before, res, shown = run_on_fixture(go)
    assert before == "" and res.ok, (before, res)
    assert shown == "India +91"
