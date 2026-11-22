"""POC: perceive -> resolve (Gemini Flash) -> act -> re-perceive loop on any form page. Dry run only.

Flow: (1) if we are on a job page, ask the model which action opens the application; (2) fill-until-
stable: answer every empty/invalid field in one batched call, apply answers (fields that can reveal
others are applied one at a time, with a settle + diff after each), repeat until nothing new appears;
(3) upload the resume; (4) stop BEFORE Submit and print a transcript with token/cost totals.
"""
from __future__ import annotations

import json
import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page

from job_agent.apply import execute, overlays, phone_country, repair
from job_agent.apply.llm import LLM, Decider, FlashDecider
from job_agent.apply.perceive import FieldModel, Perception, Settler, diff, explore, perceive
from job_agent.apply.prefill import Prefill, build_facts, prefill
from job_agent.apply.submit import SubmitOutcome, guarded_submit
from job_agent.rate_limiter import RateLimiter

MAX_ROUNDS = 6
MIN_CONFIDENCE = 0.6
MAX_DECIDER_OPTIONS = 400  # the option decider sees whole country/state lists (a 60-item cap once hid 'India')
MAX_LISTED_OPTIONS = 60  # longer lists (universities, countries) are matched locally, not sent to the model
_SUBMIT_WORDS = ("submit", "send application", "finish", "complete application", "apply now")


@dataclass
class Row:
    label: str
    kind: str
    answer: str
    confidence: float
    source: str
    status: str
    note: str = ""


@dataclass
class DemoResult:
    rows: list[Row] = field(default_factory=list)
    rounds: int = 0
    events: list[str] = field(default_factory=list)
    stopped_before: str | None = None
    submit: SubmitOutcome | None = None


_RESUME_WORDS = re.compile(r"\b(resume|cv|curriculum)\b", re.I)
_NOT_RESUME = re.compile(r"cover|letter|portfolio|transcript|certificate|writing sample|work sample|reference|photo|id\b|passport", re.I)


def _plain(text: str) -> str:
    """Lower-case, accents removed ('Résumé' -> 'resume')."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def resume_field_keys(model) -> set[str]:
    """Which file inputs take the resume: the ones labelled resume/CV (accent-insensitive); if none says so, the first
    file input that is not obviously something else (cover letter, portfolio...). Upload widgets are often labelled
    only 'Drop or select (.pdf)' or 'Main attachment'."""
    files = [f for f in model.fields if f.kind == "file"]
    explicit = [f for f in files if _RESUME_WORDS.search(_plain(f.label))]
    if explicit:
        return {explicit[0].key}
    others = [f for f in files if not _NOT_RESUME.search(_plain(f.label))]
    return {others[0].key} if others else set()


def build_context(profile_path: Path, inventory_path: Path) -> str:
    return json.dumps(build_facts(profile_path, inventory_path), indent=1)


_SOURCES = ["profile", "derived", "policy", "decline", "guess", "unknown"]
_ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "value", "confidence", "source", "basis"],
        "properties": {
            "id": {"type": "string"},
            "value": {"type": "string", "description": "Exact option label for choice fields; 'checked'/'unchecked' for checkboxes; empty if unknown"},
            "confidence": {"type": "number"},
            "source": {"type": "string", "enum": _SOURCES},
            "basis": {"type": "string", "description": "For derived/policy answers: the facts and reasoning used, in one sentence"},
        }}}},
}

_SYSTEM = (
    "You complete a job application for the applicant described in the facts, so that they never have to fill anything in. "
    "Every required field should get a real answer. Answer sources, in order of preference: "
    "(1) 'profile': stated directly in the facts. "
    "(2) 'derived': logically follows from the facts plus the job description/location. Treat the employment history as COMPLETE "
    "(if the company is not listed, the applicant has not worked there: answer No). Work eligibility follows citizenship versus the job's "
    "country (a job outside the applicant's citizenship country needs visa sponsorship; inside it does not). The current city is where the "
    "present role is based. Put the facts and reasoning in 'basis'. "
    "(3) 'policy': apply the answer_policy block (notice period, relocation, salary: 'Negotiable', etc.) for standing preferences. "
    "(4) 'decline': sensitive questions (gender, race, disability, veteran) follow the eeo block only. "
    "(5) 'guess': when the facts do not settle it, make the most plausible educated guess a typical candidate for this role would give "
    "(a believable number, the most likely option, a short sensible text) and put the reasoning in 'basis'. The applicant has asked that NO field "
    "be left unanswered, optional ones included, so always give a value. 'unknown' with an empty value only for yes/no questions about a criminal "
    "record, convictions or background-check history, and for 'I certify / I attest' signature statements. "
    "For fields with options, value must be exactly one option label. "
    "Privacy-policy/terms acknowledgement checkboxes: check them (source 'policy') only if consents.accept_privacy_and_terms is true. "
    "If options_total is given the option list is too long to show: give your best value (e.g. the university name) and it will be matched locally. "
    "If a choice field shows options null (they load only when opened) give your best value anyway (e.g. 'India'): it is used to filter the widget's real options, which are then matched. "
    "Free-text answers ('why this role', 'describe a project') must be 2-4 sentences, grounded only in the facts and the job description, "
    "free of filler, and respect maxlength. Free-text questions (required or not) always get an answer: use the facts where they apply and "
    "otherwise write a plausible, confident answer for this role (source 'guess')."
)


async def resolve_batch(llm: LLM, context: str, fields: list[FieldModel], job: str = "") -> dict[str, dict]:
    payload = [
        {"id": f.id, "type": f.kind, "question": f.label, "required": f.required, "help": f.help or None,
         "options": [o.label for o in f.options] if f.options and len(f.options) <= MAX_LISTED_OPTIONS else None,
         "options_total": len(f.options) if f.options and len(f.options) > MAX_LISTED_OPTIONS else None,
         "maxlength": f.maxlength,
         "format_hint": f.placeholder or f.pattern or None, "error": f.error or None, "section": f.section or None}
        for f in fields
    ]
    out = await llm.complete_json(
        "resolve_fields", _SYSTEM,
        f"Facts:\n{context}\n\nThe job being applied to (use for 'why this role/company' answers):\n{job}\n\nFields:\n{json.dumps(payload, indent=1)}",
        _ANSWER_SCHEMA, "fill")
    return {a["id"]: a for a in out["answers"]}


async def pick_apply_action(llm: LLM, perception: Perception) -> str | None:
    actions = [a for a in perception.model.actions if not a.disabled]
    if not actions:
        return None
    ids = [a.id for a in actions] + [""]
    schema = {"type": "object", "additionalProperties": False, "required": ["action_id"],
              "properties": {"action_id": {"type": "string", "enum": ids}}}
    listing = "\n".join(f"{a.id}: {a.kind} '{a.label}'" for a in actions)
    out = await llm.complete_json(
        "pick_apply", "Pick the single control that starts or opens the job application form (e.g. 'Apply'). "
        "Return an empty string if none does.", f"Page: {perception.model.title}\nControls:\n{listing}", schema, "nav")
    return out["action_id"] or None


_OPTION_KINDS = {"select", "combobox", "radio_group", "checkbox_group"}
NONE_OPTION = "NONE OF THESE"


def _trusted(ans: dict | None) -> bool:
    """Profile/decline/policy answers are applied; derived ones need a stated basis; guesses never are."""
    if not ans or not ans["value"].strip():
        return False
    if ans["source"] != "guess" and ans["confidence"] < MIN_CONFIDENCE:
        return False
    if ans["source"] == "guess":
        return True   # the applicant chose to have every field answered; guesses are listed in the report for review
    if ans["source"] in {"profile", "decline", "policy"}:
        return True
    return ans["source"] == "derived" and ans["confidence"] >= 0.7 and bool(ans.get("basis", "").strip())


async def _decide_from_options(page, perception, settler, field_: FieldModel, decider: Decider, context: str) -> dict | None:
    """One stubborn widget (a click that times out, a popup that never opens) must not end the whole application: no answer then."""
    try:
        return await _decide_from_options_inner(page, perception, settler, field_, decider, context)
    except Exception:
        return None


async def _decide_from_options_inner(page, perception, settler, field_: FieldModel, decider: Decider, context: str) -> dict | None:
    """The option-decider path: read the field's real options (opening dropdowns that load them lazily),
    then let the decision model pick with facts + job context. Optional fields may decline to choose."""
    options = field_.options or []
    if not options and field_.kind == "combobox":
        options = await execute.read_combobox_options(page, perception, field_, settler)
        await page.keyboard.press("Escape")  # close the popup again; applying the answer reopens it
    if not options:
        return None
    if field_.kind == "combobox" and len(options) >= 50 and hasattr(decider, "state_answer"):
        # Long (often virtualised) list: we only see a slice of it. State the answer in words; applying it types it
        # into the widget to filter the real options, which are then matched.
        said = await decider.state_answer(field_.label, context)
        if said.choice.strip().lower() in {"", "none"}:
            return None
        return {"id": field_.id, "value": said.choice, "confidence": said.confidence, "source": "derived" if said.confidence >= 0.7 else "guess", "basis": said.rationale}
    labels = [o.label for o in options][:MAX_DECIDER_OPTIONS]
    decision = await decider.decide(field_.label, labels, context)
    if decision.choice == NONE_OPTION:
        return None
    return {"id": field_.id, "value": decision.choice, "confidence": decision.confidence, "source": "derived" if decision.confidence >= 0.7 else "guess", "basis": decision.rationale}


async def _apply_field(page, perception, settler, field_: FieldModel, value: str, decider: Decider, context: str):
    """Returns (ActionResult, final_value)."""
    if field_.kind == "select":
        options = field_.options or []
        if len(options) > MAX_LISTED_OPTIONS and not execute.match_option(value, options):
            near = execute.closest_options(value, options, n=15)  # long list: shortlist locally, then decide
            if near:
                value = (await decider.decide(field_.label, [o.label for o in near], context)).choice
        return await execute.select_native(perception, field_, value), value
    if field_.kind in {"radio_group", "checkbox", "checkbox_group"}:
        return await execute.set_choice(perception, field_, value), value
    if field_.kind == "combobox":
        options = field_.options or await execute.read_combobox_options(page, perception, field_, settler)
        opt = execute.match_option(value, options)
        if not opt:  # long/async lists show only a slice: type the answer to filter, then retry with a shorter term
            for query in dict.fromkeys([value, value.split()[0]]):
                options = await execute.read_combobox_options(page, perception, field_, settler, query=query)
                opt = execute.match_option(value, options)
                if opt or options:
                    break
        if not opt and not options:  # filtering found nothing (our wording differs): show the full list and let the decider choose
            options = await execute.read_combobox_options(page, perception, field_, settler)
        if not opt and options:  # still no exact match: let the decider choose among what the widget offers
            decision = await decider.decide(field_.label, [o.label for o in options], context)
            opt = execute.match_option(decision.choice, options)
        if not opt:
            return execute.ActionResult(False, "no matching option in popup"), value
        return await execute.choose_combobox(page, perception, field_, settler, opt), opt.label
    if field_.kind == "date":
        return await execute.set_date(page, perception, field_, value), value
    return await execute.fill_text(page, perception, field_, value), value


async def fill_until_stable(page: Page, llm: LLM, context: str, resume: str | None, result: DemoResult, facts: dict | None = None) -> None:
    settler, decider = Settler(page), FlashDecider(llm)
    attempted: set[str] = set()
    job_context = ""
    refills: dict[str, int] = {}
    refilled_defaults: set[str] = set()
    picker_set = phone_country.remembered(page)   # phone box -> what its country picker showed right after we set it (kept per tab)
    late_check = False
    for round_no in range(1, MAX_ROUNDS + 2):
        perception = await explore(page, settler) if round_no == 1 else await perceive(page)  # first read also expands/scrolls
        # Sites sometimes wipe what we typed a few seconds later (e.g. a resume upload that is parsed and re-populates the form):
        # a field we filled successfully that is empty again is queued for one more fill (at most twice).
        kept = {f"{r.kind}|{re.sub(r'[^a-z0-9]+', ' ', r.label.lower()).strip()}" for r in result.rows
                if r.status == "ok" and r.answer.strip() and r.kind != "file"}
        intended = {f"{r.kind}|{re.sub(r'[^a-z0-9]+', ' ', r.label.lower()).strip()}": r.answer for r in result.rows
                    if r.status == "ok" and r.answer.strip() and r.kind in {"text", "email", "tel"}}

        def drifted(f: FieldModel) -> bool:   # the site (resume import / autofill) changed a contact-style value we had set
            want = intended.get(f.key)
            if not want or not f.value.strip() or f.kind not in {"text", "email", "tel"}:
                return False
            a, b = re.sub(r"[^a-z0-9]", "", f.value.lower()), re.sub(r"[^a-z0-9]", "", want.lower())
            if f.kind == "tel" or re.search(r"phone|mobile", f.label, re.I):
                a, b = re.sub(r"\D", "", f.value), re.sub(r"\D", "", want)
                return not (len(a) >= 8 and (b.endswith(a) or a.endswith(b)))
            return not (a == b or a in b or b in a)

        blocked_keys = {f"{r.kind}|{re.sub(r'[^a-z0-9]+', ' ', r.label.lower()).strip()}" for r in result.rows if r.status.startswith("BLOCKED")}
        lost = [f for f in perception.model.fields
                if (f.empty or drifted(f)) and f.key in attempted and refills.get(f.key, 0) < 2
                and (f.key in kept or (f.required and f.kind != "file" and f.key not in blocked_keys))]   # a required field still empty gets another try
        for f in lost:
            attempted.discard(f.key)
            refills[f.key] = refills.get(f.key, 0) + 1
        if lost:
            result.events.append("  site cleared or changed: " + ", ".join(f.label[:40] for f in lost) + " (filling again)")
        def contradicts(f: FieldModel) -> bool:
            """A value the SITE put there (a default country code, a location guessed from the IP) that disagrees with your profile."""
            if not facts or f.empty or f.kind in {"file", "checkbox", "checkbox_group", "radio_group"}:
                return False
            pf = prefill(f, facts)
            if pf is None or pf.blank or not pf.value.strip() or f.key in refilled_defaults:
                return False
            a, b = re.sub(r"[^a-z0-9]", "", f.value.lower()), re.sub(r"[^a-z0-9]", "", pf.value.lower())
            return bool(b) and a != b and a not in b and b not in a

        for f in perception.model.fields:
            if f.key not in attempted and contradicts(f):
                refilled_defaults.add(f.key)
                result.events.append(f"  site default '{f.value[:30]}' in '{f.label[:40]}' disagrees with your profile: filling it")
        picker_lost: set[str] = set()   # a phone country picker we set that the site has put back (the number box may still look filled)
        for f in perception.model.fields:
            if picker_set.get(f.key) and f.kind in {"tel", "text"} and refills.get(f.key, 0) < 3:
                now = await phone_country.picker_label(perception, f)
                if now is not None and now != picker_set[f.key]:
                    result.events.append(f"  country picker of '{f.label[:30]}' changed from {picker_set[f.key]} to {now}: setting it again")
                    picker_set.pop(f.key)
                    attempted.discard(f.key)
                    refills[f.key] = refills.get(f.key, 0) + 1
                    picker_lost.add(f.key)
        todo = [f for f in perception.model.fields if f.key not in attempted and (f.empty or f.invalid or f.key in refilled_defaults or f.key in picker_lost)]
        if not todo and not late_check and round_no > 1:
            late_check = True   # one last look after a pause: a late page-side reset shows up here
            await asyncio.sleep(4.0)
            await settler.settle()
            continue
        if not todo:
            break
        result.rounds = round_no
        if not job_context:
            m = perception.model
            job_context = f"{m.title}\n{' | '.join(m.headings)}\n{m.text_digest[:2500]}"
        result.events.append(f"round {round_no}: {len(todo)} field(s) to resolve: " + ", ".join(f.label[:40] for f in todo))
        # Standard questions are pre-answered deterministically from your facts; only the rest goes to the model.
        pre = {f.id: pf for f in todo if facts and (pf := prefill(f, facts))}
        for f in todo:   # a phone box next to a separate country-code control takes the national number only
            pf = pre.get(f.id)
            if pf is not None and pf.basis == "contact.phone" and repair.has_country_code_control(perception.model, f):
                national = repair.phone_parts(facts or {})[1]
                if national:
                    pre[f.id] = Prefill(national, "contact.phone (the country code is a separate control)")
        batch = [f for f in todo if f.kind != "file" and f.id not in pre]
        answers = await resolve_batch(llm, context, batch, job_context) if batch else {}
        for fid, pf in pre.items():
            answers[fid] = {"id": fid, "value": pf.value, "confidence": 1.0, "blank": pf.blank, "basis": pf.basis,
                            "source": "policy" if pf.basis.startswith("answer_policy") else "profile"}
        # Triggers one at a time (they may reveal fields); plain inputs afterwards.
        ordered = [f for f in todo if f.is_trigger] + [f for f in todo if not f.is_trigger]
        occurrence = {id(f): sum(1 for g in perception.model.fields[: perception.model.fields.index(f)] if g.key == f.key) for f in todo}
        for f in ordered:
            attempted.add(f.key)
            # Ids are re-stamped on every perception, so re-find this field by identity (kind+label, nth) in a fresh read.
            fresh = await perceive(page)
            matches = [g for g in fresh.model.fields if g.key == f.key]
            if len(matches) <= occurrence[id(f)]:
                result.rows.append(Row(f.label, f.kind, "", 0, "unknown", "skipped (field disappeared)"))
                if refills.get(f.key, 0) < 2:   # the page re-rendered under us (e.g. after a Country choice): look for it again next round
                    refills[f.key] = refills.get(f.key, 0) + 1
                    attempted.discard(f.key)
                continue
            cur = matches[occurrence[id(f)]]
            if cur.kind == "file":
                if resume and cur.key in resume_field_keys(fresh.model):
                    res = await execute.upload(page, fresh, cur, resume, settler)
                    status = "ok" if res.ok else (res.detail if cur.required else f"left blank (optional; could not attach: {res.detail[:60]})")
                    result.rows.append(Row(cur.label, cur.kind, Path(resume).name, 1.0, "profile", status))
                    if res.ok:   # many sites now parse the resume and rewrite the form: let that finish before filling on
                        await repair.wait_until_stable(page, settler, max_s=12.0)
                else:
                    why = "no --resume given" if not resume else "not a resume field (cover letter etc.)"
                    result.rows.append(Row(cur.label, cur.kind, "", 0, "unknown", f"skipped ({why})"))
                continue
            ans = answers.get(f.id)
            if ans and ans.get("blank"):
                result.rows.append(Row(f.label, f.kind, "", 1.0, "policy", "left blank (optional)", ans["basis"]))
                continue
            if not _trusted(ans) and cur.kind in _OPTION_KINDS:
                ans = await _decide_from_options(page, fresh, settler, cur, decider, context + "\n\nJob:\n" + job_context) or ans
            if not _trusted(ans):
                guess = (ans or {}).get("value", "")
                status = "BLOCKED (required, no honest answer)" if cur.required else "left blank (optional)"
                result.rows.append(Row(f.label, f.kind, guess, (ans or {}).get("confidence", 0), (ans or {}).get("source", "unknown"), status))
                continue
            if ans.get("basis") == "contact.phone" and cur.kind in {"tel", "text"} and facts:
                # A country picker built into the phone box ("US" / flag button) is not a form field of its own: set it to the profile's
                # country first, then the box takes the national number only.
                set_note = await phone_country.ensure_country(page, fresh, cur, facts, trust_selected=f.key not in picker_lost)
                if set_note:
                    result.events.append(f"  phone country: {set_note}")
                    ans = {**ans, "value": repair.phone_parts(facts)[1] or ans["value"]}
                    fresh = await perceive(page)
                    cur = next((g for g in fresh.model.fields if g.key == f.key), cur)
                    picker_set[f.key] = await phone_country.picker_label(fresh, cur) or ""
            try:
                res, final_value = await _apply_field(page, fresh, settler, cur, ans["value"], decider, context)
            except Exception as error:  # one stubborn widget must not end the run
                res, final_value = execute.ActionResult(False, f"error: {str(error).splitlines()[0][:90]}"), ans["value"]
            if not res.ok and overlays.blocked_by_overlay(res.detail):   # something may have popped up over the box (terms, cookies): clear it, retry once
                cleared = [n for n in await overlays.clear(page, allow_accept=bool((facts or {}).get("consents", {}).get("accept_privacy_and_terms")))
                           if n.startswith("dismissed")]
                if cleared:
                    result.events.append("  " + cleared[0][:110])
                    fresh = await perceive(page)
                    cur = next((g for g in fresh.model.fields if g.key == f.key), cur)
                    try:
                        res, final_value = await _apply_field(page, fresh, settler, cur, ans["value"], decider, context)
                    except Exception as error:
                        res, final_value = execute.ActionResult(False, f"error: {str(error).splitlines()[0][:90]}"), ans["value"]
            if not res.ok and cur.kind in _OPTION_KINDS and re.search(r"no (option|matching)", res.detail):
                alt = await _decide_from_options(page, await perceive(page), settler, cur, decider, context + "\n\nJob:\n" + job_context)
                if alt:  # our wording matched no real option: let the decider choose from what the widget offers
                    fresh = await perceive(page)
                    cur = next((g for g in fresh.model.fields if g.key == f.key), cur)
                    ans = alt
                    res, final_value = await _apply_field(page, fresh, settler, cur, alt["value"], decider, context)
            # Ticking a consent box usually opens its policy in an overlay that covers the rest of the form: clear it before the next field.
            await overlays.clear(page, allow_accept=bool((facts or {}).get("consents", {}).get("accept_privacy_and_terms")),
                                 log=lambda note: result.events.append("  " + note[:110]))
            status = "ok" if res.ok else res.detail
            if not res.ok and not cur.required and cur.kind in {"text", "textarea", "number", "tel", "email", "date", "url"}:
                try:  # an optional box that will not take our answer is left empty (and logged), not a reason to stop
                    await fresh.locator(cur.id, cur.frame).fill("", timeout=2000)
                except Exception:
                    pass
                status = f"left blank (optional; could not set: {res.detail[:70]})"
            elif not res.ok and not cur.required:   # an optional dropdown/choice that will not take our answer: leave it, close any popup
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                status = f"left blank (optional; could not set: {res.detail[:70]})"
            result.rows.append(Row(f.label, f.kind, final_value, ans["confidence"], ans["source"], status,
                                   ans.get("basis", "") if ans["source"] in {"derived", "policy"} else ""))
            if f.is_trigger:
                await settler.settle()
                change = diff(fresh.model, (await perceive(page)).model)
                if not change.empty:
                    result.events.append(f"  after '{f.label[:40]}': {change}")
                if change.added or change.removed:
                    break  # page shape changed: re-perceive and resolve the new fields next round
    await repair.validate_and_repair(page, llm, settler, facts or {}, result.events)   # read the form's own errors and fix formats before going on
    final = await perceive(page)
    for a in final.model.actions:
        if any(w in a.label.lower() for w in _SUBMIT_WORDS) and a.primary:
            result.stopped_before = a.label
            break


async def run_demo(page: Page, llm: LLM, context: str, resume: str | None = None, facts: dict | None = None,
                   submit: bool = False, limiter: RateLimiter | None = None, log_path: Path | None = None,
                   code_provider=None) -> DemoResult:
    result = DemoResult()
    from job_agent.apply import controls
    controls.use(llm)
    page.set_default_timeout(8000)  # fail fast on missing/replaced elements instead of Playwright's 30s default
    from job_agent.apply.opening import open_application  # local import: opening imports this module lazily too

    page, note = await open_application(page, llm)  # a job page rather than a form: open the application first
    settler = Settler(page)
    result.events.append(note)
    await fill_until_stable(page, llm, context, resume, result, facts)
    root = Path(__file__).resolve().parents[3]
    limiter = limiter or RateLimiter(storage_path=root / "outputs" / "rate_limiter" / "history.jsonl")
    result.submit = await guarded_submit(page, result.rows, settler, page.url, limiter,
                                         log_path or root / "outputs" / "apply" / "submissions.jsonl", submit, code_provider=code_provider)
    return result


def render(result: DemoResult, llm: LLM) -> str:
    lines = [f"rounds: {result.rounds}", *result.events, "", f"{'field':44} {'answer':28} {'conf':>4}  {'src':8} status"]
    for r in result.rows:
        lines.append(f"{r.label[:43]:44} {r.answer[:27]:28} {r.confidence:>4.2f}  {r.source:8} {r.status}")
    why = [r for r in result.rows if r.note]
    if why:
        lines += ["", "Why these answers (derived / policy):", *[f"  - {r.label[:50]}: {r.note[:160]}" for r in why]]
    guessed = [r for r in result.rows if r.source == "guess" and r.status == "ok"]
    if guessed:
        lines += ["", f"GUESSED ({len(guessed)}): answers the facts did not settle, worth a look:", *[f"  - {r.label[:50]} -> {r.answer[:40]}" for r in guessed]]
    blocked = [r for r in result.rows if r.status.startswith("BLOCKED")]
    if blocked:
        lines += ["", f"BLOCKED: {len(blocked)} required field(s) have no honest answer, so this application would not be submitted:",
                  *[f"  - {r.label}" for r in blocked]]
    out = result.submit
    if out is None:
        lines += ["", f"STOPPED before: {result.stopped_before or '(no submit control found)'}  [nothing submitted]"]
    else:
        headline = {"submitted": "SUBMITTED", "dry_run": "DRY RUN (not submitted)"}.get(out.status, f"NOT SUBMITTED: {out.status}")
        lines += ["", f"{headline} - {out.detail}", *[f"  - {p}" for p in out.problems]]
    lines.append(f"LLM: {json.dumps(llm.totals)}  model={llm.model}")
    return "\n".join(lines)
