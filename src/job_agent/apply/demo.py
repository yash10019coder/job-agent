"""POC: perceive -> resolve (Gemini Flash) -> act -> re-perceive loop on any form page. Dry run only.

Flow: (1) if we are on a job page, ask the model which action opens the application; (2) fill-until-
stable: answer every empty/invalid field in one batched call, apply answers (fields that can reveal
others are applied one at a time, with a settle + diff after each), repeat until nothing new appears;
(3) upload the resume; (4) stop BEFORE Submit and print a transcript with token/cost totals.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page

from job_agent.apply import execute
from job_agent.apply.llm import LLM, Decider, FlashDecider
from job_agent.apply.perceive import FieldModel, Perception, Settler, diff, perceive
from job_agent.apply.prefill import build_facts, prefill
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


def build_context(profile_path: Path, inventory_path: Path) -> str:
    return json.dumps(build_facts(profile_path, inventory_path), indent=1)


_SOURCES = ["profile", "derived", "policy", "decline", "unknown"]
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
    "(5) 'unknown' with an empty value: ONLY when no honest answer can be derived. Never invent employers, dates, numbers, credentials or "
    "experience with technologies that the facts do not support. Optional fields with no honest answer should be left empty (source unknown). "
    "For fields with options, value must be exactly one option label. "
    "Privacy-policy/terms acknowledgement checkboxes: check them (source 'policy') only if consents.accept_privacy_and_terms is true. "
    "If options_total is given the option list is too long to show: give your best value (e.g. the university name) and it will be matched locally. "
    "If a choice field shows options null (they load only when opened) give your best value anyway (e.g. 'India'): it is used to filter the widget's real options, which are then matched. "
    "Free-text answers ('why this role', 'describe a project') must be 2-4 sentences, grounded only in the facts and the job description, "
    "honest, free of filler, and respect maxlength. REQUIRED free-text questions must always get an answer: if the facts show no direct "
    "experience with what is asked, say so plainly in one sentence and then cite the closest REAL related experience from the facts "
    "(never claim experience or results that the facts do not show). Optional free-text questions with nothing honest to say stay unknown."
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
    if not ans or not ans["value"].strip() or ans["confidence"] < MIN_CONFIDENCE:
        return False
    if ans["source"] in {"profile", "decline", "policy"}:
        return True
    return ans["source"] == "derived" and ans["confidence"] >= 0.7 and bool(ans.get("basis", "").strip())


async def _decide_from_options(page, perception, settler, field_: FieldModel, decider: Decider, context: str) -> dict | None:
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
        return {"id": field_.id, "value": said.choice, "confidence": said.confidence, "source": "derived", "basis": said.rationale}
    labels = [o.label for o in options][:MAX_DECIDER_OPTIONS]
    decision = await decider.decide(field_.label, labels + ([] if field_.required else [NONE_OPTION]), context)
    if decision.choice == NONE_OPTION:
        return None
    return {"id": field_.id, "value": decision.choice, "confidence": decision.confidence, "source": "derived", "basis": decision.rationale}


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
    return await execute.fill_text(page, perception, field_, value), value


async def fill_until_stable(page: Page, llm: LLM, context: str, resume: str | None, result: DemoResult, facts: dict | None = None) -> None:
    settler, decider = Settler(page), FlashDecider(llm)
    attempted: set[str] = set()
    job_context = ""
    for round_no in range(1, MAX_ROUNDS + 1):
        perception = await perceive(page)
        todo = [f for f in perception.model.fields if f.key not in attempted and (f.empty or f.invalid)]
        if not todo:
            break
        result.rounds = round_no
        if not job_context:
            m = perception.model
            job_context = f"{m.title}\n{' | '.join(m.headings)}\n{m.text_digest[:2500]}"
        result.events.append(f"round {round_no}: {len(todo)} field(s) to resolve: " + ", ".join(f.label[:40] for f in todo))
        # Standard questions are pre-answered deterministically from your facts; only the rest goes to the model.
        pre = {f.id: pf for f in todo if facts and (pf := prefill(f, facts))}
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
                continue
            cur = matches[occurrence[id(f)]]
            if cur.kind == "file":
                if resume and re.search(r"\b(resume|cv|curriculum)\b", cur.label, re.I):
                    res = await execute.upload(page, fresh, cur, resume, settler)
                    result.rows.append(Row(cur.label, cur.kind, Path(resume).name, 1.0, "profile", "ok" if res.ok else res.detail))
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
            try:
                res, final_value = await _apply_field(page, fresh, settler, cur, ans["value"], decider, context)
            except Exception as error:  # one stubborn widget must not end the run
                res, final_value = execute.ActionResult(False, f"error: {str(error).splitlines()[0][:90]}"), ans["value"]
            if not res.ok and cur.kind in _OPTION_KINDS and re.search(r"no (option|matching)", res.detail):
                alt = await _decide_from_options(page, await perceive(page), settler, cur, decider, context + "\n\nJob:\n" + job_context)
                if alt:  # our wording matched no real option: let the decider choose from what the widget offers
                    fresh = await perceive(page)
                    cur = next((g for g in fresh.model.fields if g.key == f.key), cur)
                    ans = alt
                    res, final_value = await _apply_field(page, fresh, settler, cur, alt["value"], decider, context)
            result.rows.append(Row(f.label, f.kind, final_value, ans["confidence"], ans["source"], "ok" if res.ok else res.detail,
                                   ans.get("basis", "") if ans["source"] in {"derived", "policy"} else ""))
            if f.is_trigger:
                await settler.settle()
                change = diff(fresh.model, (await perceive(page)).model)
                if not change.empty:
                    result.events.append(f"  after '{f.label[:40]}': {change}")
                if change.added or change.removed:
                    break  # page shape changed: re-perceive and resolve the new fields next round
    final = await perceive(page)
    for a in final.model.actions:
        if any(w in a.label.lower() for w in _SUBMIT_WORDS) and a.primary:
            result.stopped_before = a.label
            break


async def run_demo(page: Page, llm: LLM, context: str, resume: str | None = None, facts: dict | None = None,
                   submit: bool = False, limiter: RateLimiter | None = None, log_path: Path | None = None) -> DemoResult:
    result = DemoResult()
    page.set_default_timeout(8000)  # fail fast on missing/replaced elements instead of Playwright's 30s default
    settler = Settler(page)
    perception = await perceive(page)
    if not perception.model.fields:  # a job page rather than a form: open the application first
        action = await pick_apply_action(llm, perception)
        if action:
            label = next(a.label for a in perception.model.actions if a.id == action)
            res = await execute.click_action(perception, action)
            result.events.append(f"clicked '{label}': {res.detail}")
            await settler.settle()
    await fill_until_stable(page, llm, context, resume, result, facts)
    root = Path(__file__).resolve().parents[3]
    limiter = limiter or RateLimiter(storage_path=root / "outputs" / "rate_limiter" / "history.jsonl")
    result.submit = await guarded_submit(page, result.rows, settler, page.url, limiter,
                                         log_path or root / "outputs" / "apply" / "submissions.jsonl", submit)
    return result


def render(result: DemoResult, llm: LLM) -> str:
    lines = [f"rounds: {result.rounds}", *result.events, "", f"{'field':44} {'answer':28} {'conf':>4}  {'src':8} status"]
    for r in result.rows:
        lines.append(f"{r.label[:43]:44} {r.answer[:27]:28} {r.confidence:>4.2f}  {r.source:8} {r.status}")
    why = [r for r in result.rows if r.note]
    if why:
        lines += ["", "Why these answers (derived / policy):", *[f"  - {r.label[:50]}: {r.note[:160]}" for r in why]]
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
