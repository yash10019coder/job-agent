"""Interruptions on top of the application: a terms/privacy overlay, a cookie banner, a "stay in touch" pop-up.

Nothing here knows a site. A layer counts as an interruption when it is a modal (role=dialog / aria-modal / <dialog open>) or a
wide fixed banner, it holds no real input of its own (a dialog that asks for your details is part of the application, so it is left
alone), and it offers a plain way out. The way out is chosen by what the button SAYS:

    close / dismiss / not now  >  reject optional / necessary only  >  accept / agree

Accepting is allowed only when the profile allows it (consents.accept_privacy_and_terms, the same rule the consent tick boxes use),
and a button that signs in, signs up, submits or pays is never touched, whatever the layer says.
"""
from __future__ import annotations

import asyncio
import re

from playwright.async_api import Page

_FIND_JS = r"""() => {
  const vw = innerWidth, vh = innerHeight, els = [];
  const walk = (r) => r.querySelectorAll("*").forEach((e) => { els.push(e); if (e.shadowRoot) walk(e.shadowRoot); });
  walk(document);
  els.forEach((e) => e.removeAttribute && e.removeAttribute("data-agent-ov"));
  const shown = (e, min) => { const b = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return b.width >= min && b.height >= min && cs.visibility !== "hidden" && cs.display !== "none" && parseFloat(cs.opacity) > 0.05
           && b.bottom > 0 && b.right > 0 && b.top < vh && b.left < vw; };
  const label = (e) => (e.getAttribute("aria-label") || e.innerText || e.value || "").replace(/\s+/g, " ").trim();
  const isLayer = (e) => {
    if (e.tagName === "DIALOG") return e.open;
    if (/^(dialog|alertdialog)$/.test(e.getAttribute("role") || "") || e.getAttribute("aria-modal") === "true") return true;
    const cs = getComputedStyle(e);
    if (cs.position !== "fixed" && cs.position !== "sticky") return false;
    const b = e.getBoundingClientRect();
    return (parseInt(cs.zIndex) || 0) >= 1 && b.width >= vw * 0.5 && b.height >= 60 && b.top > -5;
  };
  const inside = (layer) => { const out = [], seen = [];
    const go = (root) => root.querySelectorAll("*").forEach((e) => { seen.push(e); if (e.shadowRoot) go(e.shadowRoot); });
    go(layer); if (layer.shadowRoot) go(layer.shadowRoot); return seen; };
  const layers = els.filter((e) => isLayer(e) && shown(e, 40));
  for (const layer of layers.reverse()) {                     // last in the document = on top
    const kids = inside(layer);
    const hasInput = kids.some((k) => /^(INPUT|TEXTAREA|SELECT)$/.test(k.tagName)
      && !/^(hidden|checkbox|radio|button|submit|image|file)$/.test(k.type || "") && shown(k, 8));
    if (hasInput) continue;                                   // a dialog that asks for details belongs to the application
    const buttons = kids.filter((k) => (/^(BUTTON|A)$/.test(k.tagName) || /^(button|link)$/.test(k.getAttribute("role") || "")
        || (k.tagName === "INPUT" && /^(button|submit)$/.test(k.type))) && shown(k, 8) && label(k) && label(k).length <= 40);
    if (!buttons.length) continue;
    buttons.forEach((b, i) => b.setAttribute("data-agent-ov", String(i)));
    return {text: (layer.innerText || "").replace(/\s+/g, " ").trim().slice(0, 240), buttons: buttons.map((b, i) => ({i, label: label(b)}))};
  }
  return null;
}"""

_NEVER = re.compile(r"sign ?in|log ?in|sign ?up|register|create|submit|apply|google|linkedin|facebook|indeed|apple|microsoft|"
                    r"delete|remove|buy|pay|subscribe|download|upgrade|join", re.I)
_CLOSE = re.compile(r"^(close|dismiss|no,? thanks|no thank you|not now|maybe later|skip|cancel|got it|ok|okay|[x×✕✖])$", re.I)
_REJECT = re.compile(r"^(reject( all)?|decline( all)?|deny( all)?|(use )?(only )?(strictly )?(necessary|essential)( cookies)?( only)?"
                     r"|continue without accepting)$", re.I)
_ACCEPT = re.compile(r"^(i agree|agree|accept( all)?( cookies)?|accept and (close|proceed)|allow( all)?( cookies)?|i accept|understood)$", re.I)
# A dialog that offers ways to START the application ("Autofill with Resume / Apply Manually / Use My Last Application") is part of
# the flow: the apply logic answers it, so it is never an interruption, even though it also has a Close button.
_FLOW = re.compile(r"autofill|use my last application|apply manually|apply without|apply with\b|as (a )?guest|start (your |the )?application|^(quick )?apply\b", re.I)
_BLOCKED = re.compile(r"intercepts pointer events|receives pointer events|obscured|Timeout \d+ms exceeded", re.I)


def blocked_by_overlay(detail: str) -> bool:
    """True when a failed click/fill reads like something sat on top of the target (worth looking for an overlay and trying once more)."""
    return bool(_BLOCKED.search(detail or ""))


def choose(buttons: list[dict], allow_accept: bool) -> dict | None:
    """The way out of a layer, by what the buttons say. None when the only options would sign in/up, submit, or accept without permission."""
    usable = [b for b in buttons if not _NEVER.search(b["label"])]
    for pattern in (_CLOSE, _REJECT) + ((_ACCEPT,) if allow_accept else ()):
        for b in usable:
            if pattern.match(b["label"].strip()):
                return b
    return None


async def clear(page: Page, allow_accept: bool = True, log=None, max_layers: int = 3) -> list[str]:
    """Dismiss interruptions covering the page, topmost first. Returns what was done (empty = nothing was in the way)."""
    done: list[str] = []
    for _ in range(max_layers):
        try:
            found = await page.evaluate(_FIND_JS)
        except Exception:
            return done
        if not found:
            return done
        if any(_FLOW.search(b["label"]) for b in found["buttons"]):
            return done   # an application choice, not an interruption
        pick = choose(found["buttons"], allow_accept)
        if pick is None:
            note = f"overlay with no safe way out ({[b['label'] for b in found['buttons']][:5]}): {found['text'][:120]!r}"
            if log:
                log(note)
            return done + [note]
        target = page.locator(f'[data-agent-ov="{pick["i"]}"]').first
        try:
            await target.click(timeout=2500)
        except Exception:
            try:
                await target.evaluate("(el) => el.click()")   # a stubborn custom control: click it from the page itself
            except Exception:
                return done
        note = f"dismissed overlay with {pick['label']!r}: {found['text'][:100]!r}"
        done.append(note)
        if log:
            log(note)
        await asyncio.sleep(0.8)   # let the layer animate away before looking again
    return done
