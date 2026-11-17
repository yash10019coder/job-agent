"""The country picker built into a phone box (a flag / "US" / "+1" button beside the number input).

Sites that put the picker in the same widget as the number do not offer it as a separate form field, so the field-based country-code
handling in repair.py never sees it, and the number is typed under the wrong country (the box then rejects it or reformats it).
Nothing here knows a site: the picker is the nearest button/combobox around the phone input whose text is a country code ("US", "+1")
or says country/flag, and the choice is made from the profile (location.country, location.phone_country_code).
"""
from __future__ import annotations

import asyncio
import weakref

from job_agent.apply.perceive import FieldModel, Perception
from job_agent.apply.repair import phone_parts

_DEEP = """const deep = (root, out) => { root.querySelectorAll('*').forEach((e) => { out.push(e); if (e.shadowRoot) deep(e.shadowRoot, out); }); return out; };
  const shown = (e) => { const b = e.getBoundingClientRect(), cs = getComputedStyle(e); return b.width > 4 && b.height > 4 && cs.visibility !== 'hidden' && cs.display !== 'none'; };"""

_FIND_JS = """(telId) => {
  %s
  const all = deep(document, []);
  all.forEach((e) => e.removeAttribute && e.removeAttribute('data-agent-pc'));
  const tel = all.find((e) => e.getAttribute && e.getAttribute('data-agent-id') === telId);
  if (!tel) return null;
  const up = (n) => n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  let anc = tel;
  for (let depth = 0; depth < 7 && (anc = up(anc)); depth++) {
    const kids = deep(anc, []);
    if (anc.shadowRoot) deep(anc.shadowRoot, kids);
    const hit = kids.find((e) => e !== tel && shown(e) && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName)
      && (e.tagName === 'BUTTON' || /^(button|combobox)$/.test(e.getAttribute('role') || ''))
      && (() => { const t = ((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')).trim();
                  return !/^(ok|go|no|up)$/i.test(t) && /^[A-Za-z]{2}$|^\\+\\d{1,3}$|country|flag|dial|phone code/i.test(t); })());
    if (hit) { hit.setAttribute('data-agent-pc', '1'); return {label: (hit.innerText || '').replace(/\\s+/g, ' ').trim()}; }
  }
  return null;
}""" % _DEEP

_OPTION_JS = """(want) => {
  %s
  const all = deep(document, []);
  all.forEach((e) => e.removeAttribute && e.removeAttribute('data-agent-pco'));
  const opts = all.filter((e) => /^(option|menuitem)$/.test(e.getAttribute('role') || '') || e.tagName === 'OPTION');
  const norm = (s) => (s || '').toLowerCase().replace(/\\(.*?\\)/g, ' ').replace(/[^a-z ]/g, ' ').replace(/\\s+/g, ' ').trim();
  const text = (e) => (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim();
  const name = norm(want.country);
  const pick = (name && opts.find((o) => norm(text(o)) === name))
            || (want.code && opts.find((o) => text(o).replace(/\\s/g, '').includes(want.code)));
  if (!pick) return {found: false, options: opts.length};
  pick.setAttribute('data-agent-pco', '1');
  const sel = pick.getAttribute('aria-selected') === 'true' || /(^|\\s)selected(\\s|$)/.test(String(pick.className || ''));
  return {found: true, selected: sel, options: opts.length, text: text(pick)};
}""" % _DEEP


async def _label(frame) -> str:
    try:
        found = await frame.evaluate("() => { const e = document.querySelector('[data-agent-pc]'); if (e) return (e.innerText||'').trim(); "
                                      "const w = (r) => { for (const x of r.querySelectorAll('*')) { if (x.getAttribute && x.hasAttribute('data-agent-pc')) return (x.innerText||'').trim(); "
                                      "if (x.shadowRoot) { const v = w(x.shadowRoot); if (v) return v; } } return ''; }; return w(document); }")
        return found or ""
    except Exception:
        return ""


_SET: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def remembered(page) -> dict[str, str]:
    """Per tab: phone box -> what its country picker showed right after we set it. It outlives a single fill pass because a site
    may put the picker back later (after a dialog closes, on the next step), when the number box still looks filled."""
    return _SET.setdefault(page, {})


async def picker_label(perception: Perception, field: FieldModel) -> str | None:
    """What the phone box's country picker shows right now ("IN"), or None when the box has none. Changes nothing on the page."""
    try:
        found = await perception.frames[field.frame].evaluate(_FIND_JS, field.id)
    except Exception:
        return None
    return found["label"] if found else None


async def ensure_country(page, perception: Perception, field: FieldModel, facts: dict, trust_selected: bool = True) -> str | None:
    """Set the country picker that belongs to this phone box to the profile's country. Returns what happened
    ("US -> India" / "already India"), or None when the box has no such picker or the choice did not take (the caller then
    types the number the old way).

    `trust_selected=False` is for a picker the site has just put back: its list can still mark the right country as selected while the
    box shows another one, so the choice is clicked again whatever the list says."""
    loc = facts.get("location") or {}
    country, code = str(loc.get("country") or "").strip(), phone_parts(facts)[0]
    if not country and not code:
        return None
    frame = perception.frames[field.frame]
    try:
        picker = await frame.evaluate(_FIND_JS, field.id)
        if not picker:
            return None
        before = picker["label"]
        await frame.locator('[data-agent-pc="1"]').first.click(timeout=3000)
        await asyncio.sleep(0.7)
        opt = await frame.evaluate(_OPTION_JS, {"country": country, "code": code})
        if not opt["found"]:
            await page.keyboard.press("Escape")
            return None
        if opt["selected"] and trust_selected:
            await page.keyboard.press("Escape")
            return f"already {country or code}"
        target = frame.locator('[data-agent-pco="1"]').first
        try:
            await target.scroll_into_view_if_needed(timeout=2000)
            await target.click(timeout=3000)
        except Exception:
            await target.evaluate("(el) => el.click()")   # a virtualised list item that will not take a mouse click
        await asyncio.sleep(0.8)
        after = await _label(frame)
        if after and after != before:
            return f"{before} -> {opt['text'] or country}"
        return None   # the label did not change: do not claim a country we cannot see
    except Exception:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return None
