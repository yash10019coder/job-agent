"""Generic interaction primitives, keyed on widget behaviour (ARIA role / input type), never on site.

Each primitive acts, then verifies the result and returns an ActionResult; failures are reported as
text so the agent can retry or escalate. Elements are addressed only by agent ids from perceive.py.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date, datetime

from playwright.async_api import Page

from job_agent.apply.perceive import FieldModel, Option, Perception, Settler


@dataclass
class ActionResult:
    ok: bool
    detail: str = ""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_option(wanted: str, options: list[Option]) -> Option | None:
    """Exact (normalized) match first, then a close fuzzy match; None if nothing is convincing."""
    target = _norm(wanted)
    for opt in options:
        if _norm(opt.label) == target:
            return opt
    prefixed = [o for o in options if _norm(o.label).startswith(target + " ")]  # e.g. "India" -> "India +91"
    if len(prefixed) == 1:
        return prefixed[0]
    labels = {_norm(o.label): o for o in options}
    close = difflib.get_close_matches(target, list(labels), n=1, cutoff=0.8)
    return labels[close[0]] if close else None


def closest_options(wanted: str, options: list[Option], n: int = 15) -> list[Option]:
    """Shortlist the options most similar to `wanted` (for lists too long to show a model)."""
    labels = {_norm(o.label): o for o in options}
    near = difflib.get_close_matches(_norm(wanted), list(labels), n=n, cutoff=0.4)
    target = _norm(wanted)
    contains = [k for k in labels if target and target in k][:n]
    return [labels[k] for k in dict.fromkeys([*contains, *near])][:n]


_CURRENT_JS = "(el) => el.value !== undefined && el.value !== null && el.isContentEditable !== true ? el.value : el.innerText"


async def _current(loc) -> str:
    """Current text of an input, textarea or contenteditable (rich-text editors have no .value)."""
    return (await loc.evaluate(_CURRENT_JS)).strip()


_NOW_WORDS = re.compile(r"^(immediate(ly)?|asap|now|as soon as possible|right away|today|no notice.*|within .*)$", re.I)


def date_candidates(value: str, placeholder: str = "", today: date | None = None) -> list[str]:
    """Texts to try in a date box: the value as a real date (a start date like 'Immediately' means today), in the box's
    own format when the placeholder shows it, otherwise the common formats."""
    today = today or date.today()
    when = today
    if not _NOW_WORDS.match(value.strip()):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                when = datetime.strptime(value.strip(), fmt).date()
                break
            except ValueError:
                continue
        else:
            return [value]
    ph = placeholder.lower()
    ordered = []
    if "dd/mm" in ph:
        ordered.append("%d/%m/%Y")
    elif "mm/dd" in ph:
        ordered.append("%m/%d/%Y")
    elif "yyyy-mm" in ph:
        ordered.append("%Y-%m-%d")
    ordered += [f for f in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y") if f not in ordered]
    return [when.strftime(f) for f in ordered]


async def _type_date(page: Page, loc, field: FieldModel, value: str) -> ActionResult:
    """Type the date in each plausible format until the box keeps it (a box that rejects a format clears itself on blur)."""
    wanted_year = None
    for text in date_candidates(value, field.placeholder):
        try:
            await loc.click(timeout=3000)
            await loc.fill("")
            await loc.press_sequentially(text, delay=15)
            await loc.press("Tab")
            await page.wait_for_timeout(300)
            actual = (await _current(loc)).strip()
        except Exception as error:
            return ActionResult(False, f"date entry failed: {str(error).splitlines()[0][:90]}")
        wanted_year = re.search(r"\d{4}", text).group(0) if re.search(r"\d{4}", text) else None
        if actual and (wanted_year is None or wanted_year in actual):
            return ActionResult(True, f"date {actual}")
    return ActionResult(False, "the date box did not keep any date format (probably needs its calendar widget)")


async def set_date(page: Page, perception: Perception, field: FieldModel, value: str) -> ActionResult:
    """Dates: type it; for read-only (calendar-popup) inputs set the value the way a picker would and
    confirm the page kept it. A picker that reverts the value is reported as a failure, never assumed."""
    loc = perception.locator(field.id, field.frame)
    if not field.readonly:
        return await _type_date(page, loc, field, value)
    try:
        await loc.evaluate(
            """(el, v) => { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }""",
            value)
        await loc.blur()
        actual = await _current(loc)
    except Exception as error:
        return ActionResult(False, f"date set failed: {str(error).splitlines()[0][:90]}")
    same = re.sub(r"\D", "", actual) == re.sub(r"\D", "", value)
    return ActionResult(same, "ok" if same else f"date picker did not keep the value (got {actual!r})")


_SUGGEST_JS = """(el) => {
  // Clickable result rows that appeared under the input (typeahead lists carry no ARIA roles on many sites).
  const b = el.getBoundingClientRect();
  const seen = window.__agentPreSuggest || new WeakSet();
  return Array.from(document.querySelectorAll('li,div,a,span,button,p')).filter((e) => {
    if (e === el || e.contains(el) || e.children.length > 2 || seen.has(e)) return false;
    const r = e.getBoundingClientRect(), st = getComputedStyle(e);
    const text = (e.innerText || '').trim();
    return r.width > 30 && r.height > 8 && text && text.length <= 120 && st.display !== 'none' && st.visibility !== 'hidden' &&
      r.top >= b.bottom - 6 && r.top < b.bottom + 420 && Math.abs(r.left - b.left) < 140 && st.cursor === 'pointer';
  }).map((e, i) => { e.setAttribute('data-agent-sugg', String(i)); return { i, text: (e.innerText || '').trim() }; });
}"""
_MARK_PRE_JS = """() => { const s = new WeakSet(); document.querySelectorAll('li,div,a,span,button,p').forEach((e) => s.add(e)); window.__agentPreSuggest = s; }"""


def _overlap(want: str, cand: str) -> float:
    a, b = set(_norm(want).split()), set(_norm(cand).split())
    a |= {t[:3] for t in a}  # "india" ~ "ind" (country codes in result rows)
    return len(a & (b | {t[:3] for t in b})) / max(1, len(set(_norm(want).split())))


DWELL_MS = 500            # pause after typing so suggestion lists and validators can react
SUGGEST_SETTLE_TICKS = 6   # the list must look the same for ~0.6 s: results often re-render as the search request returns


async def _collect_rows(page: Page, loc, max_ticks: int, settle_ticks: int = SUGGEST_SETTLE_TICKS) -> list[dict]:
    """Suggestion rows that appeared after typing, once the list has stopped changing (or [] if none show up in time)."""
    rows: list[dict] = []
    last, stable = None, 0
    for _ in range(max_ticks):
        await page.wait_for_timeout(100)
        rows = await loc.evaluate(_SUGGEST_JS)
        sig = [r["text"] for r in rows]
        if rows and sig == last:
            stable += 1
            if stable >= settle_ticks:
                return rows
        else:
            stable = 0
        last = sig
    return rows


async def pick_suggestion(page: Page, perception: Perception, field: FieldModel, value: str) -> ActionResult:
    """Typeahead inputs that clear themselves unless a result is clicked: type, wait for the result rows that
    appear, click the best match, confirm the input keeps a value. Only rows that appeared after typing count."""
    loc = perception.locator(field.id, field.frame)
    frame = perception.frames[field.frame]
    query = re.split(r"[,/(]", value)[0].strip() or value
    try:
        await loc.click(timeout=3000)
        await loc.fill("")
        await frame.evaluate(_MARK_PRE_JS)
        await loc.press_sequentially(query, delay=80)
        rows = await _collect_rows(page, loc, max_ticks=80)  # up to ~8s for the search request, then wait for the list to settle
        if not rows:
            return ActionResult(False, "no suggestion list appeared after typing")
        best = max(rows, key=lambda r: _overlap(value, r["text"]))
        if _overlap(value, best["text"]) < 0.5 or _norm(best["text"]).split()[:1] != _norm(query).split()[:1]:
            return ActionResult(False, f"no suggestion matches {value!r} (saw {[r['text'][:30] for r in rows[:3]]})")
        await frame.locator(f'[data-agent-sugg="{best["i"]}"]').first.click(timeout=3000)
        await loc.blur()
        kept = await _current(loc)
    except Exception as error:
        return ActionResult(False, f"suggestion pick failed: {str(error).splitlines()[0][:90]}")
    return ActionResult(bool(kept), f"picked suggestion {best['text']!r}" if kept else "picked a suggestion but the input is empty again")


async def fill_text(page: Page, perception: Perception, field: FieldModel, value: str) -> ActionResult:
    loc = perception.locator(field.id, field.frame)
    try:
        await loc.click(timeout=3000)
        await perception.frames[field.frame].evaluate(_MARK_PRE_JS)
        await loc.fill(value, timeout=3000)
        if (await _current(loc)) != value:  # React-style controlled inputs may swallow fill()
            await loc.fill("")
            await loc.press_sequentially(value, delay=15)
        await page.wait_for_timeout(DWELL_MS)  # give the site time to show suggestions / validate before we leave the box
        chose = False
        rows = await _collect_rows(page, loc, max_ticks=20) if await loc.evaluate(_SUGGEST_JS) else []
        if rows:  # the site offered suggestions: take the matching one instead of leaving unconfirmed typed text
            best = max(rows, key=lambda r: _overlap(value, r["text"]))
            if _overlap(value, best["text"]) >= 0.5:
                await perception.frames[field.frame].locator(f'[data-agent-sugg="{best["i"]}"]').first.click(timeout=3000)
                chose = True
                await page.wait_for_timeout(DWELL_MS)
        await loc.blur()
        await page.wait_for_timeout(300)  # blur handlers may clear a value that was not picked from a suggestion list
        actual = await _current(loc)
    except Exception as error:
        return ActionResult(False, f"fill failed: {error}")
    same = re.sub(r"[^a-z0-9]", "", actual.lower()) == re.sub(r"[^a-z0-9]", "", value.lower())  # inputs may reformat (phone masks)
    if chose and not same and actual.strip() and _overlap(value, actual) >= 0.5:
        same = True   # the site replaced our text with the full suggestion it offered ("Mumbai" -> "Mumbai, Maharashtra, India")
    if not same and (field.kind == "tel" or re.search(r"phone|mobile|cell", field.label, re.I)):  # the country code may live in a separate box
        got, want = re.sub(r"\D", "", actual), re.sub(r"\D", "", value)
        same = len(got) >= 8 and want.endswith(got)
    if not same and not actual.strip() and field.kind == "text":
        picked = await pick_suggestion(page, perception, field, value)  # emptied on blur: a typeahead that needs a clicked result
        return picked if picked.ok else ActionResult(False, f"value not accepted (got ''); {picked.detail}")
    return ActionResult(same, "ok" if same else f"value not accepted (got {actual!r})")


async def select_native(perception: Perception, field: FieldModel, label: str) -> ActionResult:
    opt = match_option(label, field.options or [])
    if not opt:
        return ActionResult(False, f"no option matches {label!r}")
    loc = perception.locator(field.id, field.frame)
    try:
        await loc.select_option(label=opt.label, timeout=3000)
        chosen = await loc.evaluate("(el) => el.selectedOptions[0] ? el.selectedOptions[0].text.trim() : ''")
    except Exception as error:
        return ActionResult(False, f"select failed: {error}")
    return ActionResult(_norm(chosen) == _norm(opt.label), f"selected {chosen!r}")


async def set_choice(perception: Perception, field: FieldModel, wanted: str) -> ActionResult:
    """Radio groups, checkbox groups and single checkboxes (wanted: option label, or 'checked'/'unchecked')."""
    if field.kind == "checkbox":
        loc = perception.locator(field.id, field.frame)
        want = _norm(wanted) in {"checked", "yes", "true", "agree", "on"}
        try:
            await (loc.check(timeout=3000) if want else loc.uncheck(timeout=3000))
            return ActionResult(await loc.is_checked() == want, "ok")
        except Exception as error:
            try:   # a native box hidden behind a styled label (common): click the label like a user would
                await loc.evaluate("(el) => { const l = (el.labels && el.labels[0]) || el.closest('label'); (l || el).click(); }")
                await perception.frames[field.frame].page.wait_for_timeout(300)
                if await loc.is_checked() == want:
                    return ActionResult(True, "ok")
            except Exception:
                pass
            return ActionResult(False, f"checkbox failed: {str(error).splitlines()[0][:100]}")
    if field.cards:
        return await _choose_card(perception, field, wanted)
    wanted_list = [w.strip() for w in wanted.split(";") if w.strip()] if field.kind == "checkbox_group" else [wanted]
    chosen = []
    for want in wanted_list:  # checkbox groups may select several options ("A; B")
        opt = match_option(want, field.options or [])
        if not opt:
            return ActionResult(False, f"no option matches {want!r}")
        loc = perception.locator(opt.id, field.frame)
        try:
            await loc.check(timeout=3000)
        except Exception:
            # Native input hidden behind a styled label: click the label (or the input itself) like a user would.
            try:
                await loc.evaluate("(el) => { const l = (el.labels && el.labels[0]) || el.closest('label'); (l || el).click(); }")
            except Exception as error:
                return ActionResult(False, f"choice failed: {str(error).splitlines()[0][:90]}")
        if not await loc.is_checked():
            return ActionResult(False, f"{opt.label!r} did not stay checked")
        chosen.append(opt.label)
    return ActionResult(True, f"chose {', '.join(map(repr, chosen))}")


async def _choose_card(perception: Perception, field: FieldModel, wanted: str) -> ActionResult:
    """Clickable cards/pills with no input: click, then confirm the card now reports itself selected."""
    opt = match_option(wanted, field.options or [])
    if not opt:
        return ActionResult(False, f"no option matches {wanted!r}")
    loc = perception.locator(opt.id, field.frame)
    try:
        await loc.click(timeout=3000)
        selected = await loc.evaluate("(el) => window.__agentCardSelected ? window.__agentCardSelected(el) : false")
    except Exception as error:
        return ActionResult(False, f"card click failed: {str(error).splitlines()[0][:90]}")
    return ActionResult(selected, f"chose {opt.label!r}" if selected else f"clicked {opt.label!r} but the page shows no selected state")


async def read_combobox_options(page: Page, perception: Perception, field: FieldModel, settler: Settler, query: str = "") -> list[Option]:
    """Open a combobox (optionally typing a filter) and read the options of the popup it controls."""
    loc = perception.locator(field.id, field.frame)
    await loc.click(timeout=3000)
    editable = await loc.evaluate("(el) => /^(INPUT|TEXTAREA)$/.test(el.tagName) || el.isContentEditable")  # a div combobox cannot be typed into
    if query and editable:
        await loc.fill("")
        await loc.press_sequentially(query, delay=20)
    elif editable:  # make sure a previously typed filter is cleared so the full list shows
        try:
            if await loc.input_value():
                await loc.fill("")
        except Exception:
            pass
    await settler.settle()
    frame = perception.frames[field.frame]
    controlled = await loc.get_attribute("aria-controls") or await loc.get_attribute("aria-owns")
    scope = f'[id="{controlled}"] [role=option]' if controlled else "[role=option]"
    options = frame.locator(scope)
    async def read() -> list[Option]:
        got: list[Option] = []
        for i in range(min(await options.count(), 60)):
            item = options.nth(i)
            if await item.is_visible():
                got.append(Option(id=str(i), label=" ".join((await item.inner_text()).split())))
        return got

    # Suggestions arrive after the search request and often re-render: wait (up to 8 s when we typed a filter, 3 s otherwise)
    # until the list has looked the same for ~0.6 s, so we never pick from a half-loaded list.
    out, last, stable = await read(), None, 0
    for _ in range(80 if query else 30):
        sig = [o.label for o in out]
        stable = stable + 1 if (out and sig == last) else 0
        if stable >= SUGGEST_SETTLE_TICKS:
            break
        last = sig
        await page.wait_for_timeout(100)
        out = await read()
    return out


async def choose_combobox(page: Page, perception: Perception, field: FieldModel, settler: Settler, option: Option, query: str = "") -> ActionResult:
    """Click an option in the (already opened) combobox popup and confirm, after a pause, that the widget kept it.
    A typeahead can accept the click and then clear itself (or never register it), so one retry re-opens and re-picks."""
    res = await _choose_once(page, perception, field, settler, option)
    if res.ok or "removed the field" in res.detail:
        return res
    try:
        fresh = await perceive(page)   # ids are re-stamped on every read: find the field again
        cur = next((f for f in fresh.model.fields if f.key == field.key), None)
        if cur is None:
            return res
        first = option.label.split(",")[0].split()[0]
        options = await read_combobox_options(page, fresh, cur, settler, query=query or first)
        again = match_option(option.label, options)
        if not again:
            return res
        retry = await _choose_once(page, fresh, cur, settler, again)
        return ActionResult(retry.ok, retry.detail + " (second attempt)")
    except Exception:
        return res


async def _choose_once(page: Page, perception: Perception, field: FieldModel, settler: Settler, option: Option) -> ActionResult:
    frame = perception.frames[field.frame]
    loc = perception.locator(field.id, field.frame)
    controlled = await loc.get_attribute("aria-controls") or await loc.get_attribute("aria-owns")
    scope = f'[id="{controlled}"] [role=option]' if controlled else "[role=option]"
    before = await loc.evaluate("(el) => window.__agentFieldText(el)")
    target = frame.locator(scope).filter(has_text=re.compile(f"^\\s*{re.escape(option.label)}\\s*$")).first
    try:
        await target.click(timeout=3000)
        await settler.settle()
        await page.wait_for_timeout(DWELL_MS)   # the widget may apply or clear the value a moment after the click
    except Exception as error:
        return ActionResult(False, f"option click failed: {error}")
    try:
        shown = await loc.evaluate("(el) => (el.value || '') + ' ' + window.__agentFieldText(el)", timeout=2500)
        typed = await loc.evaluate("(el) => /^(INPUT|TEXTAREA)$/.test(el.tagName) ? (el.value || '') : null", timeout=2500)
    except Exception:
        # The page reacted to the selection by removing this very field (e.g. declining an EEO question collapses it).
        return ActionResult(True, f"selected {option.label!r}; the page then removed the field")
    closed = (await loc.get_attribute("aria-expanded")) in (None, "false")
    # The widget may display a shortened form of the option (e.g. "India +91" -> "+91"), so a changed,
    # non-empty display with the popup closed also counts as taken.
    ok = closed and (_norm(option.label) in _norm(shown) or _norm(shown) != _norm(before))
    if typed is not None:   # a plain text box that is empty after the pause did not keep the choice
        ok = ok and bool(typed.strip())
    return ActionResult(ok, f"selected {option.label!r}" if ok else f"popup/value not confirmed (shown={shown.strip()[:60]!r})")


async def upload(page: Page, perception: Perception, field: FieldModel, path: str, settler: Settler) -> ActionResult:
    loc = perception.locator(field.id, field.frame)
    try:
        await loc.set_input_files(path, timeout=5000)
    except Exception as error:
        return ActionResult(False, f"upload failed: {str(error).splitlines()[0][:90]}")
    await settler.settle()
    name = path.rsplit("/", 1)[-1]
    # Many sites re-render the widget after an upload, so the input may be gone: look for the filename instead.
    try:
        attached = await loc.evaluate("(el) => el.files && el.files[0] ? el.files[0].name : ''", timeout=1500)
    except Exception:
        attached = ""
    if not attached:
        for frame in perception.frames:
            try:
                if name in await frame.evaluate("() => document.body.innerText"):
                    attached = name
                    break
            except Exception:
                continue
    return ActionResult(bool(attached), f"uploaded {attached!r}" if attached else "file not attached")


async def click_action(perception: Perception, action_id: str, frame: int = 0) -> ActionResult:
    try:
        await perception.locator(action_id, frame).click(timeout=5000)
    except Exception as error:
        return ActionResult(False, f"click failed: {error}")
    return ActionResult(True, "clicked")
