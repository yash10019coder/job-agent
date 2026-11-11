"""Generic interaction primitives, keyed on widget behaviour (ARIA role / input type), never on site.

Each primitive acts, then verifies the result and returns an ActionResult; failures are reported as
text so the agent can retry or escalate. Elements are addressed only by agent ids from perceive.py.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

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


async def fill_text(page: Page, perception: Perception, field: FieldModel, value: str) -> ActionResult:
    loc = perception.locator(field.id, field.frame)
    try:
        await loc.click(timeout=3000)
        await loc.fill(value, timeout=3000)
        if (await loc.input_value()) != value:  # React-style controlled inputs may swallow fill()
            await loc.fill("")
            await loc.press_sequentially(value, delay=15)
        await loc.blur()
        actual = await loc.input_value()
    except Exception as error:
        return ActionResult(False, f"fill failed: {error}")
    same = re.sub(r"[^a-z0-9]", "", actual.lower()) == re.sub(r"[^a-z0-9]", "", value.lower())  # inputs may reformat (phone masks)
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
            return ActionResult(False, f"checkbox failed: {error}")
    wanted_list = [w.strip() for w in wanted.split(";") if w.strip()] if field.kind == "checkbox_group" else [wanted]
    chosen = []
    for want in wanted_list:  # checkbox groups may select several options ("A; B")
        opt = match_option(want, field.options or [])
        if not opt:
            return ActionResult(False, f"no option matches {want!r}")
        loc = perception.locator(opt.id, field.frame)
        try:
            await loc.check(timeout=3000)
            if not await loc.is_checked():
                return ActionResult(False, f"{opt.label!r} did not stay checked")
        except Exception as error:
            return ActionResult(False, f"choice failed: {error}")
        chosen.append(opt.label)
    return ActionResult(True, f"chose {', '.join(map(repr, chosen))}")


async def read_combobox_options(page: Page, perception: Perception, field: FieldModel, settler: Settler, query: str = "") -> list[Option]:
    """Open a combobox (optionally typing a filter) and read the options of the popup it controls."""
    loc = perception.locator(field.id, field.frame)
    await loc.click(timeout=3000)
    if query:
        await loc.fill("")
        await loc.press_sequentially(query, delay=20)
    else:  # make sure a previously typed filter is cleared so the full list shows
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
    out: list[Option] = []
    for i in range(min(await options.count(), 60)):
        item = options.nth(i)
        if await item.is_visible():
            out.append(Option(id=str(i), label=" ".join((await item.inner_text()).split())))
    return out


async def choose_combobox(page: Page, perception: Perception, field: FieldModel, settler: Settler, option: Option, query: str = "") -> ActionResult:
    """Click an option in the (already opened) combobox popup, then verify the widget took it."""
    frame = perception.frames[field.frame]
    loc = perception.locator(field.id, field.frame)
    controlled = await loc.get_attribute("aria-controls") or await loc.get_attribute("aria-owns")
    scope = f'[id="{controlled}"] [role=option]' if controlled else "[role=option]"
    before = await loc.evaluate("(el) => window.__agentFieldText(el)")
    target = frame.locator(scope).filter(has_text=re.compile(f"^\\s*{re.escape(option.label)}\\s*$")).first
    try:
        await target.click(timeout=3000)
        await settler.settle()
    except Exception as error:
        return ActionResult(False, f"option click failed: {error}")
    try:
        shown = await loc.evaluate("(el) => (el.value || '') + ' ' + window.__agentFieldText(el)", timeout=2500)
    except Exception:
        # The page reacted to the selection by removing this very field (e.g. declining an EEO question collapses it).
        return ActionResult(True, f"selected {option.label!r}; the page then removed the field")
    closed = (await loc.get_attribute("aria-expanded")) in (None, "false")
    # The widget may display a shortened form of the option (e.g. "India +91" -> "+91"), so a changed,
    # non-empty display with the popup closed also counts as taken.
    ok = closed and (_norm(option.label) in _norm(shown) or _norm(shown) != _norm(before))
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
