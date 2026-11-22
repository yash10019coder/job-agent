"""click_and_follow: the Apply click must land somewhere we can name (new tab, navigation, modal, same page) or say it did not."""
import asyncio
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.navigate import click_and_follow
from job_agent.apply.perceive import perceive

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/multistep"
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def follow(name: str, action_label: str, **kwargs):
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto((FIX / name).resolve().as_uri())
            perception = await perceive(page)
            action = next(a for a in perception.model.actions if a.label == action_label)
            hop = await click_and_follow(page, perception, action.id, action.frame, **kwargs)
            after = (await perceive(hop.page)).model
            info = (hop.kind, hop.to_url.rsplit("/", 1)[-1], len(context.pages), [f.label for f in after.fields], page.is_closed())
            await browser.close()
            return info
    return asyncio.run(run())


def test_same_page_change():
    kind, _, pages, fields, _ = follow("apply_samepage.html", "Apply")
    assert kind == "same_page" and pages == 1 and fields == ["Full name", "Email"]


def test_new_tab_is_followed_and_the_old_tab_is_closed():
    kind, to, pages, fields, old_closed = follow("apply_newtab.html", "Apply now")
    assert kind == "new_tab" and to == "form_page.html" and pages == 1 and old_closed and "Email" in fields


def test_same_tab_navigation():
    kind, to, pages, fields, _ = follow("apply_navigate.html", "Apply now")
    assert kind == "navigated" and to == "form_page.html" and pages == 1 and "Full name" in fields


def test_modal_that_opens_on_the_same_page():
    kind, _, _, fields, _ = follow("apply_modal.html", "Apply")
    assert kind == "modal" and fields == ["Full name", "Email", "Phone"]


def test_cookie_banner_in_the_way_is_dismissed_then_the_click_works():
    kind, to, _, _, _ = follow("apply_banner.html", "Apply")
    assert kind == "navigated" and to == "form_page.html"


def test_a_click_that_does_nothing_is_reported_not_assumed():
    kind, _, _, _, _ = follow("apply_noeffect.html", "Apply", wait_s=1.5)
    assert kind == "no_effect"


def test_a_tab_opened_by_another_job_is_not_mistaken_for_ours():
    """Two jobs share one browser context. While job A waits for its (do-nothing) Apply click, job B opens a tab:
    A must report no_effect, not 'new_tab'. (Seen live: Breezy 'followed' the JazzHR window.)"""
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            context = await browser.new_context()
            a, b = await context.new_page(), await context.new_page()
            await a.goto((FIX / "apply_noeffect.html").resolve().as_uri())
            await b.goto((FIX / "job_page.html").resolve().as_uri())
            perception = await perceive(a)
            action = next(x for x in perception.model.actions if x.label == "Apply")
            opener = asyncio.create_task(b.evaluate("setTimeout(() => window.open('form_page.html'), 400)"))
            hop = await click_and_follow(a, perception, action.id, action.frame, wait_s=2.0)
            await opener
            await browser.close()
            return hop.kind
    assert asyncio.run(run()) == "no_effect"
