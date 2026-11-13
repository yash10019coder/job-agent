"""The Apply click: click a control, then say exactly where we landed (new tab, navigation, modal, same page) or that
nothing happened. Event-driven waits, one retry, cookie banners that block the click are dismissed. Used for every
"click something that should move the application forward" in the controller."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import Page

from job_agent.apply.perceive import Perception, Settler

_SIGNATURE_JS = """() => ({
  sig: [document.body ? document.body.innerText.length : 0, document.querySelectorAll('input,select,textarea,button,a').length].join(':'),
  dialog: [...document.querySelectorAll('[role=dialog],dialog[open]')].some((e) => e.getClientRects().length > 0),
})"""

# Accept/dismiss buttons that sit inside a cookie/consent/privacy overlay (matched by words and layout, never by site).
_DISMISS_JS = """() => {
  const label = /^(accept( all)?( cookies)?|allow( all)?( cookies)?|agree|i agree|got it|ok|okay|accept (&|and) continue)$/i;
  for (const b of document.querySelectorAll('button,a,[role=button]')) {
    const t = (b.innerText || b.getAttribute('aria-label') || '').trim();
    if (!label.test(t) || !b.getClientRects().length) continue;
    const box = b.closest('[role=dialog],[class*=cookie i],[id*=cookie i],[class*=consent i],[id*=consent i],[aria-label*=cookie i],[aria-label*=consent i]') ||
      Array.from(document.querySelectorAll('div,section,aside')).find((d) => d.contains(b) && /cookie|consent|privacy/i.test(d.innerText || '') && ['fixed', 'sticky'].includes(getComputedStyle(d).position));
    if (box) { b.click(); return t; }
  }
  return '';
}"""


@dataclass
class Hop:
    page: Page            # the page to continue on (the new tab when one opened)
    kind: str             # new_tab | navigated | modal | same_page | no_effect
    from_url: str
    to_url: str
    error: str = ""


async def _state(page: Page) -> dict:
    try:
        state = await page.evaluate(_SIGNATURE_JS)
    except Exception:
        return {"sig": "", "dialog": False}
    state["sig"] += "|" + "|".join(f.url for f in page.frames)  # a step that changes only inside an iframe still counts as a change
    return state


async def _wait_for_effect(page: Page, before: dict, before_url: str, before_pages: set, wait_s: float) -> Hop | None:
    context = page.context
    for _ in range(int(wait_s / 0.1)):
        new = [p for p in context.pages if p not in before_pages and not p.is_closed()]
        if new:
            target = new[-1]
            try:
                await target.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            if not target.is_closed():
                await Settler(target).settle()
                return Hop(target, "new_tab", before_url, target.url)
        if page.url != before_url:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await Settler(page).settle()
            return Hop(page, "navigated", before_url, page.url)
        now = await _state(page)
        if now["dialog"] and not before["dialog"]:
            await Settler(page).settle()
            return Hop(page, "modal", before_url, page.url)
        if now["sig"] != before["sig"]:
            await Settler(page).settle()
            return Hop(page, "same_page", before_url, page.url)
        await asyncio.sleep(0.1)
    return None


async def click_and_follow(page: Page, perception: Perception, action_id: str, frame: int = 0, wait_s: float = 10.0, close_old: bool = True) -> Hop:
    before, before_url, before_pages = await _state(page), page.url, set(page.context.pages)
    last_error = ""
    for attempt in (1, 2):
        try:
            await perception.locator(action_id, frame).click(timeout=4000)
        except Exception as error:
            last_error = str(error).splitlines()[0][:120]
            dismissed = await perception.frames[frame].evaluate(_DISMISS_JS) if not page.is_closed() else ""
            if dismissed and attempt == 1:
                await Settler(page).settle()
                continue  # the banner was in the way: click again
            if attempt == 1:
                continue
            return Hop(page, "no_effect", before_url, page.url, f"click failed: {last_error}")
        hop = await _wait_for_effect(page, before, before_url, before_pages, wait_s if attempt == 1 else min(wait_s, 4.0))
        if hop:
            if hop.kind == "new_tab" and close_old and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
            return hop
        # nothing happened: one more try (the first click can be swallowed by a late-attaching handler or overlay)
    return Hop(page, "no_effect", before_url, page.url, last_error)
