"""Input formats and validation: write values the way the form wants them, read the errors it shows, repair, and only then go on.

* `phone_parts` / `has_country_code_control`: when a separate country-code control sits next to the phone box, the box gets the
  national number only (a flat "+91 89575..." there is what produced "Enter a valid number").
* `validate_and_repair`: blur every input so blur-time validation shows up, read field errors and page alerts, try the cheap
  deterministic fixes (phone variants, URL scheme, digits-only numbers), then ONE small text-only model call that sees the error
  texts and each field's constraints. At most two rounds; every change is reported.
* `wait_until_stable`: after a resume upload many sites parse it and rewrite the form; wait for that to finish before filling on.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from playwright.async_api import Page

from job_agent.apply import execute
from job_agent.apply.perceive import FieldModel, PageModel, Settler, perceive

_TEXTLIKE = {"text", "email", "tel", "url", "number", "textarea", "date"}
_BLUR_JS = """() => { let n = 0;
  for (const el of document.querySelectorAll('input:not([type=file]):not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=submit]):not([type=button]), textarea')) {
    if (!el.value || !el.offsetParent) continue;
    try { el.focus(); el.blur(); n++; } catch (e) {}
  } return n; }"""


def phone_parts(facts: dict) -> tuple[str, str]:
    """(country code like '+91', national number digits) from the profile."""
    contact, loc = facts.get("contact") or {}, facts.get("location") or {}
    digits = re.sub(r"\D", "", str(contact.get("phone") or ""))
    code = re.sub(r"\D", "", str(loc.get("phone_country_code") or ""))
    if code and digits.startswith(code) and len(digits) - len(code) >= 6:
        digits = digits[len(code):]
    return (f"+{code}" if code else ""), digits


def has_country_code_control(model: PageModel, field: FieldModel) -> bool:
    """A neighbouring select/combobox that holds (or offers) dial codes like '+91' or is labelled as a country/dial code."""
    try:
        i = model.fields.index(field)
    except ValueError:
        return False
    for other in model.fields[max(0, i - 2): i + 2]:
        if other is field or other.kind not in {"combobox", "select"}:
            continue
        if re.match(r"\s*\+\d{1,3}\b", other.value or "") or re.search(r"country code|dial|phone code|calling code", f"{other.label} {other.help}", re.I):
            return True
        if other.options and sum(bool(re.match(r"\s*(.*\s)?\+\d{1,3}\b", o.label)) for o in other.options[:20]) >= 5:
            return True
    return False


def variants(field: FieldModel, facts: dict, current: str) -> list[str]:
    """Deterministic alternatives for a value the site rejected."""
    out: list[str] = []
    if field.kind == "tel" or re.search(r"phone|mobile|cell", field.label, re.I):
        code, national = phone_parts(facts)
        out = [national, f"{code}{national}", f"{code} {national}".strip(), f"0{national}"]
    elif field.kind == "url" or re.search(r"url|link|website|profile", field.label, re.I):
        if current and not re.match(r"https?://", current):
            out = ["https://" + current.lstrip("/")]
        elif current.startswith("https://"):
            out = [current.replace("https://", "https://www.", 1), current.replace("https://", "http://", 1)]
    elif field.kind == "number":
        out = [re.sub(r"[^\d.]", "", current)]
    seen, uniq = {current}, []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


async def _blur_all(page: Page, settler: Settler) -> None:
    for frame in page.frames:
        try:
            await asyncio.wait_for(frame.evaluate(_BLUR_JS), 3.0)
        except Exception:
            continue
    await settler.settle()
    await asyncio.sleep(0.4)


def _bad_fields(model: PageModel) -> list[FieldModel]:
    return [f for f in model.fields if f.region in {"main", "dialog"} and f.kind in _TEXTLIKE and f.value.strip() and (f.error or f.invalid)]


def _useful_alerts(model: PageModel) -> list[str]:
    echo = " ".join([model.title, *model.headings]).lower()
    return [a for a in model.alerts if a.strip() and a.lower().strip(" .") not in echo][:5]


async def _refind(page: Page, key: str):
    p = await perceive(page)
    return p, next((f for f in p.model.fields if f.key == key), None)


async def validate_and_repair(page: Page, llm, settler: Settler, facts: dict, events: list[str], max_rounds: int = 2) -> list[str]:
    """Returns the remaining problems (empty = nothing the page complains about)."""
    for _ in range(max_rounds):
        await _blur_all(page, settler)
        model = (await perceive(page)).model
        bad, alerts = _bad_fields(model), _useful_alerts(model)
        if not bad and not alerts:
            return []
        for f in bad:                                   # 1. cheap deterministic fixes
            for cand in variants(f, facts, f.value):
                p, cur = await _refind(page, f.key)
                if cur is None:
                    break
                await execute.fill_text(page, p, cur, cand)
                await _blur_all(page, settler)
                _, cur = await _refind(page, f.key)
                if cur is not None and not (cur.error or cur.invalid):
                    events.append(f"  repaired '{f.label[:40]}': {f.error[:50] or 'invalid'} -> accepted as {cand!r}")
                    break
        model = (await perceive(page)).model
        bad, alerts = _bad_fields(model), _useful_alerts(model)
        if not bad and not alerts:
            return []
        if llm is not None:                             # 2. one small text-only call that has read the error texts
            await _model_repair(page, llm, model, bad, alerts, facts, events)
    model = (await perceive(page)).model
    return [f"{f.label}: {f.error or 'invalid'}" for f in _bad_fields(model)] + _useful_alerts(model)


async def _model_repair(page: Page, llm, model: PageModel, bad: list[FieldModel], alerts: list[str], facts: dict, events: list[str]) -> None:
    candidates = bad or [f for f in model.fields if f.region in {"main", "dialog"} and f.kind in _TEXTLIKE and f.value.strip()][:12]
    schema = {"type": "object", "additionalProperties": False, "required": ["fixes"], "properties": {"fixes": {
        "type": "array", "maxItems": 4, "items": {"type": "object", "additionalProperties": False, "required": ["id", "value"],
                                                  "properties": {"id": {"type": "string", "enum": [f.id for f in candidates]}, "value": {"type": "string"}}}}}}
    code, national = phone_parts(facts)
    payload = {"page_messages": alerts, "fields": [{"id": f.id, "label": f.label, "kind": f.kind, "value": f.value, "error": f.error or None,
                                                    "pattern": f.pattern, "placeholder": f.placeholder or None, "maxlength": f.maxlength, "help": f.help or None}
                                                   for f in candidates],
               "phone": {"country_code": code, "national_number": national}}
    system = ("A web form rejected some inputs. From the error texts and each field's constraints decide what value the field wants and return "
              "only the fields you can fix, with the corrected value (same meaning, the format the form asks for; never invent new facts). "
              "If a message is about a field that is not listed, or you cannot tell, return nothing for it.")
    try:
        out = await llm.complete_json("repair_fields", system, json.dumps(payload, indent=1), schema, "repair")
    except Exception:
        return
    for fix in out.get("fixes", []):
        f = next((c for c in candidates if c.id == fix["id"]), None)
        if f is None or not fix["value"].strip() or fix["value"] == f.value:
            continue
        p, cur = await _refind(page, f.key)
        if cur is not None:
            res = await execute.fill_text(page, p, cur, fix["value"])
            events.append(f"  model repair '{f.label[:40]}': {f.value[:20]!r} -> {fix['value'][:30]!r} ({'ok' if res.ok else res.detail[:40]})")


async def wait_until_stable(page: Page, settler: Settler, max_s: float = 10.0) -> None:
    """Wait until two reads 1 s apart look the same (e.g. after a resume upload that the site parses and applies)."""
    await settler.settle()
    last, deadline = None, time.monotonic() + max_s
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        m = (await perceive(page)).model
        sig = (tuple((f.label, f.value) for f in m.fields), tuple(a.label for a in m.actions), tuple(m.alerts))
        if sig == last and not m.busy:
            return
        last = sig
