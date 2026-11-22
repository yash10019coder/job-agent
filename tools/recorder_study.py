"""Study a full-capture recording and turn it into engine work.

  study(folder)    -> report.md: for every interaction, was the control the person used one the engine's reader listed (decided by
                      element identity, the agent-id stamp, not by label guessing), what the engine thought each page was, how the
                      person's answers compare with what the engine would have answered, and which overlays appeared.
  promote(folder)  -> sanitised fixtures (tests/fixtures/recorded/<site>/) that tests/test_apply_recorded.py replays through the reader.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_NEUTRAL_TAGS = {"div", "span", "main", "body", "html", "section", "p", "svg", "path", "circle", "slot", "li", "ul", "form", "header", "footer", "nav"}


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def load(folder: str | Path) -> tuple[list[dict], dict, list[dict]]:
    rows = [json.loads(l) for l in (Path(folder) / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    states = {r["id"]: r for r in rows if r.get("t") == "state"}
    by_epoch = {r["epoch"]: r["id"] for r in states.values() if r.get("epoch")}
    order = list(states)
    inter = [r for r in rows if r.get("t") in {"click", "change", "key"} and r.get("el")]
    for it in inter:
        it["_exact"] = it.get("before_epoch") in by_epoch
        if it["_exact"]:
            it["before"] = by_epoch[it["before_epoch"]]                      # exact: the page read whose stamps were live at the click
        else:
            it["before"] = _nearest_state(it, states, order)                 # older recordings: the state (this one or a following one) that holds the stamp
            i = order.index(it["before"]) if it.get("before") in order else 0
            it["_cands"] = [states[sid]["model"] for sid in order[i:i + 4]]   # ...and by name, since stamps alone are ambiguous there
    return rows, states, inter


def _by_name(el: dict, models: list[dict]) -> tuple[str, str] | None:
    """Older recordings: is there a field, option or action in these reads whose name fits the control the person used?"""
    for m in models:
        for f in m.get("fields", []):
            if _compatible(el, [f["label"]]):
                return "field", f"{f['kind']} '{f['label'][:50]}' (matched by name: an old recording)"
            for o in (f.get("options") or []):
                if o.get("label") and _compatible(el, [o["label"]]):
                    return "option", f"option '{o['label'][:30]}' of {f['kind']} '{f['label'][:40]}' (by name)"
        for a in m.get("actions", []):
            if _compatible(el, [a["label"]]):
                return "action", f"'{a['label'][:50]}' (matched by name: an old recording)"
    return None


def _labels_for(model: dict, agent: str) -> list[str]:
    """What the reader calls the control that carries this stamp (a field, one of its options, or an action)."""
    out = []
    for f in model.get("fields", []):
        if f["id"] == agent:
            out.append(f["label"])
        for o in (f.get("options") or []):
            if o.get("id") == agent:
                out += [o.get("label", ""), f["label"]]
    out += [a["label"] for a in model.get("actions", []) if a["id"] == agent]
    return out


def _compatible(el: dict, labels: list[str]) -> bool:
    """Stamps are positional (f0-n7 exists in every read), so without epochs an id alone proves nothing: the names must also fit."""
    mine = [norm(x) for x in (_label(el), el.get("name"), el.get("text"), el.get("group")) if x]
    theirs = [norm(x) for x in labels if x]
    return any(a and b and (a in b or b in a) for a in mine for b in theirs)


def _nearest_state(it: dict, states: dict, order: list[str]) -> str | None:
    """Without epochs a click can be linked to a state that was already superseded by a newer read (a snapshot was still being written).
    Look at the recorded state and the next few for the one that actually contains the stamp the click carried."""
    agent = ((it.get("el") or {}).get("agent") or {}).get("id")
    first = it.get("before")
    if not agent or first not in order:
        return first
    for sid in order[order.index(first): order.index(first) + 4]:
        if _compatible(it["el"], _labels_for(states[sid]["model"], agent)):
            return sid
    return first


_FIND_TARGET_JS = """() => { const all = []; const walk = (r) => r.querySelectorAll('*').forEach((e) => { all.push(e); if (e.shadowRoot) walk(e.shadowRoot); }); walk(document);
  const tag = (re) => all.find((e) => re.test(e.getAttribute('data-rec-target') || ''));
  const up = (n) => n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
  for (const start of [tag(/^(control|both)$/), tag(/^(hit|both)$/)]) { let n = start;
    for (let i = 0; n && i < 12; i++, n = up(n)) { const id = n.getAttribute && n.getAttribute('data-agent-id'); if (id) return id; } }
  return null; }"""


async def _replay_all(folder: Path, inter: list[dict]) -> None:
    """The reader's view of the page AS IT WAS at each action: load the saved DOM, run the engine's own reader, and ask which stamp the
    control the person used ended up with (it, or one of its ancestors, carries a field/action id, or nothing does)."""
    import shutil
    from playwright.async_api import async_playwright
    from job_agent.apply.classify import classify
    from job_agent.apply.perceive import perceive
    from job_agent.apply.states import Observation
    brave = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=brave if brave and Path(brave).exists() else None, headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        for it in inter:
            name = it.get("dom_file")
            if not name or not (folder / "states" / name).is_file():
                continue
            try:
                await page.set_content((folder / "states" / name).read_text(encoding="utf-8"))
                per = await perceive(page)
                agent = await page.evaluate(_FIND_TARGET_JS)
                c = classify(Observation(it.get("url", ""), None, per.model, [], False, None))
                it["_replay"] = {"id": name, "model": per.model.model_dump(), "engine": {"state": c.state.value, "confidence": c.confidence, "reasons": c.reasons}}
                it["_exact"] = True
                it["el"]["agent"] = {"id": agent} if agent else None
            except Exception as error:
                it["_replay_error"] = f"{type(error).__name__}: {str(error)[:120]}"
        await browser.close()


def analyse(folder: str | Path) -> tuple[dict, list[dict], list[tuple]]:
    """(states, interactions, verdicts). Interactions that saved their own DOM are judged on that copy; older ones on the saved states."""
    import asyncio
    folder = Path(folder)
    rows, states, inter = load(folder)
    if any(i.get("dom_file") for i in inter):
        asyncio.run(_replay_all(folder, inter))
    out, recent = [], []
    for it in inter:
        before = {"id": it["_replay"]["id"], "model": it["_replay"]["model"], "engine": it["_replay"]["engine"]} if it.get("_replay") else states.get(it.get("before"))
        v = verdict(it, before, None, recent[-4:])
        recent.append(v)
        out.append((it, before, v))
    return states, inter, out


def _ids(model: dict) -> tuple[dict, dict, dict]:
    fields = {f["id"]: f for f in model.get("fields", [])}
    options = {o["id"]: f for f in model.get("fields", []) for o in (f.get("options") or []) if o.get("id")}
    actions = {a["id"]: a for a in model.get("actions", [])}
    return fields, options, actions


def _label(el: dict) -> str:
    """The question / control name a person would use for this element: 'State * | Select...' -> 'State'."""
    first = (el.get("name") or "").split(" | ")[0]
    return re.sub(r"\s*\*+\s*", " ", first).strip() or el.get("group") or el.get("text") or ""


def verdict(it: dict, before: dict | None, prev: dict | None, prev_verdict: tuple | None) -> tuple[str, str]:
    """('field'|'option'|'action'|'list-option'|'neutral'|'not-seen', detail) for one interaction, against the state the person saw."""
    el = it["el"]
    if before is None:
        return "unknown", "no earlier state was captured"
    fields, options, actions = _ids(before["model"])
    agent = (el.get("agent") or {}).get("id")
    if not it.get("_exact", True):
        hit = _by_name(el, it.get("_cands") or [before["model"]])
        if hit:
            return hit
        agent = None                                                          # an old recording: an id alone points at some other control in that read
    if agent in fields:
        f = fields[agent]
        return "field", f"{f['kind']} '{f['label'][:50]}'"
    if agent in options:
        f = options[agent]
        return "option", f"option of {f['kind']} '{f['label'][:50]}'"
    if agent in actions:
        a = (el.get("attrs") or {})
        if el.get("role") != "link" and (a.get("aria-haspopup") in {"listbox", "menu", "true"} or el.get("expanded") is not None):
            return "not-seen", f"a dropdown trigger the reader lists only as the plain action '{actions[agent]['label'][:40]}': it opens a list, so it is a question to answer, not a button to press"
        return "action", f"'{actions[agent]['label'][:50]}'"
    if el.get("role") in {"option", "menuitem", "listitem", "listbox"} or any(c.get("role") in {"listbox", "menu"} for c in el.get("chain", [])):
        prevs = prev_verdict if isinstance(prev_verdict, list) else [prev_verdict]
        opener = next((p for p in reversed(prevs) if p and ((p[0] == "field" and re.search(r"combobox|select", p[1])) or (p[0] == "not-seen" and "dropdown trigger" in p[1]))), None)
        if opener:
            if opener[0] == "not-seen":
                return "list-option", "an option of a dropdown the reader lists only as an action (reported once, at the trigger)"
            return "list-option", f"an option in the list opened from {opener[1]}"
        return "not-seen", "an option inside a list that no field of the reader opened"
    name = _label(el)
    if not el.get("role") and el.get("tag") in _NEUTRAL_TAGS and not name:
        return "neutral", "a click on page furniture"
    return "not-seen", f"the reader listed no field or action for this {el.get('role') or el.get('tag')} '{name[:50]}'"


def _value_changes(prev: dict, cur: dict) -> list[tuple[str, str, str]]:
    """(label, old, new) for fields whose value changed between two states of the same page."""
    pf = {f"{f['kind']}|{norm(f['label'])}": f for f in prev["model"].get("fields", [])}
    out = []
    for f in cur["model"].get("fields", []):
        p = pf.get(f"{f['kind']}|{norm(f['label'])}")
        if p is not None and p["value"] != f["value"] and f["value"].strip():
            val = f["value"]
            for o in (f.get("options") or []):
                if o.get("value") == val and o.get("label"):
                    val = o["label"]               # radio groups report an internal code as the value: show the option's own wording
            out.append((f["label"], p["value"], val))
    return out


def study(folder: str | Path) -> str:
    states, inter, judged = analyse(folder)
    lines, gaps, answers, overlays = [], [], [], []
    flagged: set[str] = set()   # one 'page not recognised' note per page copy, not one per click
    seen = {"field": 0, "option": 0, "action": 0, "list-option": 0, "neutral": 0, "not-seen": 0, "unknown": 0}
    for it, before, v in judged:
        seen[v[0]] += 1
        el = it["el"]
        shown = _label(el) or el.get("tag")
        extra = f" = {it['value']!r}" if it["t"] == "change" else ""
        where = (before or {}).get("id", "?")
        lines.append(f"- step {it['step']:>3} {it['t']:<6} {el.get('role') or el.get('tag')} '{shown[:45]}'{extra} -> {v[0]}: {v[1]}  [{where}]")
        if v[0] == "not-seen":
            gaps.append(f"**step {it['step']}** `{el.get('role') or el.get('tag')}` '{shown[:60]}' - {v[1]}\n  - selector: `{el.get('selector', '')[:200]}`\n  - html: `{(el.get('html') or '')[:300]}`")
        sig = (before or {}).get("model", {}).get("url", "").split("?")[0] + "|" + (before or {}).get("model", {}).get("title", "")[:40] + "|" + " ".join((before or {}).get("model", {}).get("headings", [])[:1])
        if before is not None and v[0] in {"field", "option", "action", "list-option"} and before["engine"]["state"] == "unknown" and sig not in flagged:
            flagged.add(sig)
            gaps.append(f"**step {it['step']}** the engine classified the page as it was at {where} as `unknown` while the person acted on it (first control: '{shown[:50]}')")
    ordered = list(states.values())
    for prev, cur in zip(ordered, ordered[1:]):
        if prev["url"].split("/")[2:3] != cur["url"].split("/")[2:3] and prev["url"].startswith("http"):
            pass
        would = {norm(w["label"]): w for w in prev["engine"].get("would_answer", [])}
        for label, old, new in _value_changes(prev, cur):
            w = would.get(norm(label))
            if w is not None and not w["blank"] and norm(w["value"]) not in norm(new) and norm(new) not in norm(w["value"]):
                answers.append(f"'{label[:50]}': the person chose **{new[:50]}**, the engine would answer **{w['value'][:50]}** ({w['basis']})")
        for m in (cur.get("delta") or {}).get("muts", []):
            if m.get("op") == "add" and m.get("modal") and not cur["after_steps"]:
                overlays.append(f"{cur['id']}: a dialog appeared by itself (`{m.get('tag')}` '{m.get('name', '')[:40]}') - overlay handling must cope")
    classes = [(s["id"], s["engine"]["state"], s["title"][:40], len(s["model"].get("fields", [])), len(s["model"].get("actions", []))) for s in ordered]
    out = ["# Recording study", "",
           f"{len(inter)} interactions, {len(states)} states. Reader coverage of the controls the person used: "
           + ", ".join(f"{k} {v}" for k, v in seen.items() if v), "",
           f"## Reader gaps ({len(gaps)})", *([f"- {g}" for g in gaps] or ["- none: every control the person used was listed by the reader"]), "",
           f"## Answers that differ from what the engine would give ({len(answers)})", *([f"- {a}" for a in answers] or ["- none"]), "",
           f"## Overlays that appeared on their own ({len(overlays)})", *([f"- {o}" for o in overlays] or ["- none"]), "",
           "## What the engine thought each page was", *[f"- {i} `{s}` '{t}' fields={f} actions={a}" for i, s, t, f, a in classes], "",
           "## Timeline", *lines]
    text = "\n".join(out)
    (Path(folder) / "report.md").write_text(text, encoding="utf-8")
    return text


# ---- promote: sanitised fixtures --------------------------------------------------------------------------------------------

def _pii(facts: dict | None) -> dict[str, str]:
    if not facts:
        return {}
    c, i = facts.get("contact") or {}, facts.get("identity") or {}
    out: dict[str, str] = {}
    for key, repl in [(c.get("email"), "candidate@example.com"), (c.get("linkedin"), "https://linkedin.com/in/candidate"), (c.get("github"), "https://github.com/candidate"),
                      (i.get("full_name") or c.get("name"), "Alex Smith"), (i.get("first_name"), "Alex"), (i.get("last_name"), "Smith"), (i.get("preferred_name"), "Alex")]:
        if key and len(str(key)) >= 3:
            out[str(key)] = repl
    return out


def sanitize(html: str, facts: dict | None = None) -> str:
    for token, repl in sorted(_pii(facts).items(), key=lambda kv: -len(kv[0])):
        html = re.sub(re.escape(token), repl, html, flags=re.I)
    phone = re.sub(r"\D", "", str((facts or {}).get("contact", {}).get("phone") or ""))
    if len(phone) >= 8:
        national = phone[-10:]
        html = re.sub(r"(?:\+?\d{1,3}[\s-]?)?" + r"[\s-]?".join(re.escape(national[i:i + 5]) for i in (0, 5)), "9000000000", html)
        html = html.replace(national, "9000000000")
    html = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "candidate@example.com", html)
    html = re.sub(r"\s*data-agent-id=\"[^\"]*\"", "", html)
    return html


_KIND_FOR_ROLE = {"radio": {"radio_group", "cards"}, "checkbox": {"checkbox", "checkbox_group"}, "textbox": {"text", "email", "tel", "number", "textarea", "url", "date", "password"},
                  "combobox": {"combobox", "select"}, "file": {"file"}, "listbox": {"combobox", "select"}}


def expectation(it: dict, v: tuple) -> dict | None:
    el = it["el"]
    role = el.get("role") or ""
    if role in {"option", "menuitem", "listitem"} or v[0] in {"neutral", "list-option", "unknown"}:
        return None                                                       # parts of a widget, not controls the reader must list on their own
    if (el.get("expanded") is not None or (el.get("attrs") or {}).get("aria-haspopup") == "listbox") and role in {"button", "combobox", ""}:
        role = "combobox"                                                 # a trigger that opens a list is a dropdown field
    label = (el.get("group") if role in {"radio", "checkbox"} and el.get("group") else None) or _label(el)   # a radio answers a question: expect the question
    named = re.match(r"^(?:\w+) '(.*?)'(?: \(|$)", v[1]) if v[0] == "field" else None
    if named:
        label = named.group(1)                                            # the reader's own name for it: the regression pins that, not the aria-label noise
    if not label:
        return None
    return {"step": it["step"], "label": label, "group": el.get("group", ""), "role": role, "kinds": sorted(_KIND_FOR_ROLE.get(role, set())) if role in _KIND_FOR_ROLE else [],
            "as": "field" if role in _KIND_FOR_ROLE else "action", "seen_when_recorded": v[0] in {"field", "option", "action"}}


_STYLE = re.compile(r'<style data-rec-from="(?:page|link|adopted)">.*?</style>', re.S)


def slim(html: str) -> str:
    """Drop the inlined stylesheet text (most of a saved page's size); elements the page hid keep their data-rec-hidden marker."""
    return _STYLE.sub("", html).replace("<head>", "<head><style>[data-rec-hidden]{display:none!important}</style>", 1)


def promote(folder: str | Path, site: str, *, only_gaps: bool = False, facts: dict | None = None, dest: Path | None = None,
            steps: set[int] | None = None, slim_pages: bool = True) -> list[Path]:
    """One fixture per distinct page copy the person acted on: the DOM at that action (A###), or for older recordings the saved state."""
    folder = Path(folder)
    states, inter, judged = analyse(folder)
    dest = (dest or ROOT / "tests" / "fixtures" / "recorded") / site
    dest.mkdir(parents=True, exist_ok=True)
    groups: dict[str, dict] = {}
    for it, before, v in judged:
        e = expectation(it, v)
        if not e or (only_gaps and e["seen_when_recorded"]) or (steps and it["step"] not in steps):
            continue
        if it.get("dom_file") and (folder / "states" / it["dom_file"]).is_file():
            key, src = it["dom_file"][:-5], folder / "states" / it["dom_file"]
        elif it.get("before") in states:
            key, src = it["before"], folder / "states" / f"{it['before']}.html"
        else:
            continue
        g = groups.setdefault(key, {"src": src, "url": it.get("url", ""), "engine": (before or {}).get("engine", {}).get("state", ""), "interactions": []})
        g["interactions"].append(e)
    written = []
    for key, g in groups.items():
        page = sanitize(g["src"].read_text(encoding="utf-8"), facts)
        (dest / f"{key}.html").write_text(slim(page) if slim_pages else page, encoding="utf-8")
        meta = {"state": key, "site": site, "url": g["url"], "engine_state_when_recorded": g["engine"], "interactions": g["interactions"]}
        (dest / f"{key}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        written += [dest / f"{key}.html", dest / f"{key}.json"]
    return written
