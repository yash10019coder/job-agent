"""Page-state classification from page evidence only (fields, actions, regions, text, frames): no vendor knowledge.

This module holds the evidence tests (is this an application form? a listing?) and, later, `classify()` itself.
Every test here is conservative in the direction that matters: anything that could lead to a Submit must be certain.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from job_agent.apply.perceive import FieldModel, PageModel

_MAIN = {"main", "dialog"}
_FILTER_LABEL = re.compile(r"^(search|keywords?|department|office|team|sort( by)?|filter|job (type|category|function)|category|employment type)\b", re.I)
_CONSENT = re.compile(r"subscribe|newsletter|consent|agree|privacy|terms|gdpr|marketing|updates", re.I)
_STEP_CTL = re.compile(r"^(next|continue|save (and|&) continue)\b", re.I)
_IDENTITY = re.compile(r"\b(first|last|full|legal|given|family|preferred) name\b|^name$|e-?mail", re.I)
_LISTING_HEADING = re.compile(r"\b\d+\s+(jobs|openings|positions|roles|results)\b|current openings|open positions|job search|search results|all jobs", re.I)


def _core_fields(model: PageModel) -> list[FieldModel]:
    """Fields that count toward 'this is an application': main content, not a search/filter box, not a consent tick."""
    out = []
    for f in model.fields:
        if f.region not in _MAIN or _FILTER_LABEL.match(f.label.strip()):
            continue
        if f.kind in {"checkbox", "checkbox_group"} and _CONSENT.search(f.label):
            continue
        out.append(f)
    return out


def _is_identity(f: FieldModel) -> bool:
    return f.kind in {"email", "tel"} or bool(_IDENTITY.search(f.label))


def is_application_form(model: PageModel) -> bool:
    """True only for an application-shaped form: >=3 real fields including name/email, or a file upload plus email.
    A newsletter box, a site search, a language picker or a job board's filters are not."""
    if any(f.kind == "password" and f.region in _MAIN for f in model.fields):
        return False  # a password field means an account page (login/signup), never the application itself
    core = _core_fields(model)
    identity = any(_is_identity(f) for f in core)
    has_file = any(f.kind == "file" for f in core)
    has_email = any(f.kind == "email" or re.search(r"e-?mail", f.label, re.I) for f in core)
    return (len(core) >= 3 and identity) or (has_file and has_email)


def is_listing_page(model: PageModel, from_url: str | None = None) -> bool:
    """A board/search listing: filters, a 'N jobs' heading, a board-root URL with an error flag, or a deeper job URL
    that landed on a shallower page. Two independent signals are required (a job page can have many links)."""
    if is_application_form(model):
        return False
    main_fields = [f for f in model.fields if f.region in _MAIN]
    strong = 0
    if main_fields and sum(bool(_FILTER_LABEL.match(f.label.strip())) for f in main_fields) >= max(2, int(len(main_fields) * 0.6)):
        strong += 1
    if _LISTING_HEADING.search(" ".join([model.title, *model.headings])):
        strong += 1
    here = urlparse(model.url)
    if "error" in parse_qs(here.query) and here.path.strip("/").count("/") <= 0:
        strong += 1
    if from_url:
        came = urlparse(from_url)
        if came.netloc == here.netloc and len(came.path.strip("/").split("/")) > len(here.path.strip("/").split("/")) and came.path.startswith(here.path.rstrip("/") or "/"):
            strong += 1
    return strong >= 2


# ---- the classifier ---------------------------------------------------------------------------------------
from dataclasses import dataclass, field  # noqa: E402

from job_agent.apply.gates import BOT, classify_gate  # noqa: E402
from job_agent.apply.reveal import pick_apply  # noqa: E402
from job_agent.apply.states import ALLOWED, Observation, PageState  # noqa: E402
from job_agent.apply import controls
from job_agent.apply.submit import _CODE_FIELD, _SUCCESS  # noqa: E402

_CLOSED = re.compile(
    r"no longer (accepting|available|open)|(position|job|posting|role|requisition|vacancy)( has been| is| was)? (now )?(filled|closed|expired|removed|no longer)|"
    r"job (filled|closed|expired)|not (currently )?accepting (new )?applications|has been (filled|closed)|page not found|no longer exists|couldn.t find (that|this) (job|page|position)", re.I)
_CODE_LABEL = re.compile(r"verification code|security code|confirmation code|one[- ]time|\botp\b|enter the code|pass ?code|6[- ]digit", re.I)
_REVIEW = re.compile(r"\b(review( and submit| your application| & submit)?|application summary|confirm (your )?(application|details))\b", re.I)


@dataclass
class Classification:
    state: PageState
    advance_id: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    source: str = "rules"  # rules | llm


def _head_text(model: PageModel) -> str:
    """Title + headings + the first part of the visible text: where a page states what it is."""
    return " ".join([model.title, *model.headings, model.text_digest[:400]])


def _classify_rules(obs: Observation) -> Classification:
    """Map one observation to a PageState by rules. Order matters: the states that stop a run come first, and
    FORM_STEP/REVIEW/CONFIRMATION are only returned on application-shaped evidence."""
    m, head = obs.model, _head_text(obs.model)
    form = is_application_form(m)
    gate = classify_gate(m, obs.frame_hosts, obs.status, obs.captcha)
    sig = gate["signals"]
    apply_id = pick_apply(m)

    if obs.captcha:  # a visible challenge is something a person can solve: it wins over bot-block wording
        return Classification(PageState.CAPTCHA, None, 0.95, ["a captcha challenge is visible"])
    if obs.status in (403, 429) or (not form and BOT.search(head[:600])):
        return Classification(PageState.BOT_BLOCK, None, 0.9, ["bot block: status or challenge text"])
    if obs.status is not None and obs.status >= 400:
        return Classification(PageState.DEAD, None, 0.95, [f"HTTP {obs.status}"])
    if not form and _CLOSED.search(head):
        return Classification(PageState.CLOSED, None, 0.85, ["closed/expired wording in the page head"])
    if is_listing_page(m, obs.from_url):
        return Classification(PageState.LISTING, None, 0.85, ["board/listing page (filters, 'N jobs', or a job URL that landed on the board)"])
    if not form and (_SUCCESS.search(head) or re.search(r"/(confirmation|thank|submitted|success|applied)\b", obs.url, re.I)):
        return Classification(PageState.CONFIRMATION, None, 0.8, ["confirmation wording (the controller still requires a post-submit transition)"])
    boxes = [f for f in m.fields if f.region in _MAIN and f.kind in {"text", "number", "tel"} and f.maxlength == 1]
    if 4 <= len(boxes) <= 8 or any(_CODE_LABEL.search(f.label) for f in m.fields if f.region in _MAIN and f.kind != "email") or (not form and len(m.fields) <= 3 and _CODE_FIELD.search(head)):
        return Classification(PageState.EMAIL_CODE, None, 0.85, ["a verification-code field or prompt"])
    if not form and re.search(r"verif|pass ?code|one[- ]time|security code", head, re.I) and any(
            f.kind == "radio_group" and f.region in _MAIN and any(re.search(r"@|e-?mail", o.label, re.I) for o in (f.options or [])) for f in m.fields):
        return Classification(PageState.EMAIL_CODE, None, 0.8, ["choose where the verification code is sent (an email option is offered)"])
    if not form and not apply_id:
        if sig["password_field"]:
            state = PageState.SIGNUP if (sig.get("password_count", 0) >= 2 or (sig["signup_text"] and not sig["login_text"])) else PageState.LOGIN if (sig["login_text"] and not sig["signup_text"]) else PageState.ACCOUNT_CHOICE
            return Classification(state, None, 0.85, ["a password field in main content"])
        if sig["login_text"] and sig["signup_text"]:
            return Classification(PageState.ACCOUNT_CHOICE, None, 0.6, ["login and signup wording in main content"])
        if sig["sso"]:
            return Classification(PageState.SSO_ONLY, None, 0.7, [f"only SSO choices: {', '.join(sig['sso'])}"])
        if sig["guest_option"]:
            return Classification(PageState.ACCOUNT_CHOICE, None, 0.6, ["guest option wording"])
        if sig["login_text"]:
            return Classification(PageState.LOGIN, None, 0.55, ["login wording in main content"])
    if not form and _REVIEW.search(" ".join([m.title, *m.headings])):
        submit_btn = next((a for a in m.actions if not a.disabled and controls.cached_role(m, a) == "submit_final"), None)
        if submit_btn:
            return Classification(PageState.REVIEW, submit_btn.id, 0.75, ["review heading with a Submit control and no questions"])
    if form:
        if _REVIEW.search(" ".join([m.title, *m.headings])):
            return Classification(PageState.REVIEW, None, 0.7, ["review/summary heading with form fields"])
        return Classification(PageState.FORM_STEP, None, 0.85, ["application-shaped fields in main content"])
    main_inputs = [f for f in m.fields if f.region in _MAIN]
    if (main_inputs and not apply_id and all(f.kind in {"checkbox", "checkbox_group"} and f.required and _CONSENT.search(f.label) for f in main_inputs)
            and any(a.region in _MAIN and not a.disabled and _STEP_CTL.match(a.label.strip()) for a in m.actions)):
        # An application that was started earlier, or a step that is only an agreement ("Data Protection Statement": tick the box, Next).
        # Consent boxes do not count as application fields, so without this the step reads as a page with nothing to do.
        return Classification(PageState.FORM_STEP, None, 0.7, ["a step that holds only a required consent box and a Next control"])
    if apply_id:
        return Classification(PageState.JOB_PAGE, apply_id, 0.8, ["an Apply control and no application form"])
    return Classification(PageState.UNKNOWN, None, 0.0, ["no rule matched"])


SEMANTIC_MIN_CONFIDENCE = 0.45   # similarity of the winning state's closest examples (multilingual mpnet scale; calibrated on the saved pages)
SEMANTIC_MIN_MARGIN = 0.06       # lead over the runner-up (usually "other")
_SEM_STATES = {"closed": PageState.CLOSED, "dead": PageState.DEAD, "confirmation": PageState.CONFIRMATION,
               "email_code": PageState.EMAIL_CODE, "bot_block": PageState.BOT_BLOCK}


def semantic_enabled() -> bool:
    import os
    return os.environ.get("JOB_AGENT_SEMANTIC", "") not in {"", "0", "false", "no"}


def classify(obs: Observation) -> Classification:
    """Rules, plus (behind JOB_AGENT_SEMANTIC / --semantic) a meaning-based verdict for the wording-driven states.
    Structure always wins: a real application form is never 'closed', 'dead', 'confirmation' or 'bot block', a code page needs
    an input box, and a visible captcha or an HTTP error is decided before any wording is looked at."""
    rules = _classify_rules(obs)
    if not semantic_enabled() or obs.captcha or (obs.status is not None and obs.status >= 400):
        return rules
    m = obs.model
    try:
        from job_agent.apply import semantic
        heard = semantic.predict_wording(m)
    except Exception as error:   # embedding model missing/unavailable: behave exactly like the rules
        rules.reasons.append(f"semantic unavailable ({str(error)[:60]})")
        return rules
    state = _SEM_STATES.get(heard.state)
    note = f"semantic: {heard.state} {heard.confidence:.2f} (+{heard.margin:.2f})"
    if state is None or heard.confidence < SEMANTIC_MIN_CONFIDENCE or heard.margin < SEMANTIC_MIN_MARGIN:
        rules.reasons.append(note + " - not confident enough to act")
        return rules
    if is_application_form(m) and state != PageState.EMAIL_CODE:
        rules.reasons.append(note + " - vetoed: the page has an application form")
        return rules
    if state == PageState.EMAIL_CODE and not any(f.region in _MAIN for f in m.fields):
        rules.reasons.append(note + " - vetoed: no input box for a code")
        return rules
    if state == rules.state:
        rules.reasons.append(note + " - agrees with the rules")
        return rules
    return Classification(state, None, round(min(0.9, heard.confidence + 0.1), 2),
                          [note, f"rules said {rules.state.value}: {'; '.join(rules.reasons)[:120]}"], "semantic")


def continue_application(prev: PageState | None, model: PageModel, has_next: bool, has_submit: bool) -> Classification | None:
    """What the loop needs is not a page type but "what can I do here": inside an application, a page that has input fields and
    a step control is the next step of it, whatever else it holds (education links, uploads, sections). The previous state limits
    what can follow (states.ALLOWED); a loading page, a password page or a page with nothing to fill is not decided here."""
    if prev is None or PageState.FORM_STEP not in ALLOWED.get(prev, set()) or model.busy:
        return None
    inputs = [f for f in model.fields if f.region in _MAIN]
    if not inputs or any(f.kind == "password" for f in inputs) or not (has_next or has_submit):
        return None
    return Classification(PageState.FORM_STEP, None, 0.6, [f"after {prev.value}: inputs and a step control, so the next step of the same application"])


async def classify_page(obs: Observation, llm=None) -> Classification:
    """Rules first; a text-only model call only when the rules find nothing. The model may name the state and the
    control that advances, but it cannot create a FORM_STEP/REVIEW/CONFIRMATION that the page evidence does not support."""
    result = classify(obs)
    if result.state != PageState.UNKNOWN or llm is None:
        return result
    m = obs.model
    controls = [a for a in m.actions if not a.disabled and a.region in _MAIN][:25]
    ids = [a.id for a in controls] + [""]
    states = [s.value for s in PageState if s != PageState.UNKNOWN]
    schema = {"type": "object", "additionalProperties": False, "required": ["state", "advance_id", "confidence"],
              "properties": {"state": {"type": "string", "enum": states}, "advance_id": {"type": "string", "enum": ids},
                             "confidence": {"type": "number"}}}
    listing = "\n".join(f"{a.id}: {a.kind} '{a.label}'" for a in controls)
    fields = "\n".join([f"- {f.kind}: {f.label}" for f in m.fields if f.region in _MAIN][:15])
    user = (f"URL: {obs.url}\nTitle: {m.title}\nHeadings: {' | '.join(m.headings[:5])}\nControls:\n{listing}\nFields:\n{fields}\n"
            f"Text: {m.text_digest[:600]}")
    out = await llm.complete_json(
        "classify_page", "Classify this job-application web page into exactly one state and name the control that moves the "
        "application forward (empty if none). job_page = description with a control that starts the application; form_step = "
        "application questions; login/signup/account_choice/sso_only = account gate; email_code = asks for an emailed code; "
        "closed/dead/listing = job unavailable or a list of jobs; confirmation = application received.", user, schema, "page_state")
    state, why = PageState(out["state"]), ["model fallback: no rule matched"]
    if state in {PageState.FORM_STEP, PageState.REVIEW} and not is_application_form(m):
        state, why = PageState.UNKNOWN, ["model said form but the page has no application-shaped fields: rejected"]
    if state == PageState.CONFIRMATION and not _SUCCESS.search(_head_text(m)):
        state, why = PageState.UNKNOWN, ["model said confirmation but the page has no confirmation wording: rejected"]
    if state == PageState.EMAIL_ONLY:
        state, why = PageState.UNKNOWN, ["model said 'apply by email': not accepted from a guess (needs real evidence)"]
    if state in {PageState.DEAD, PageState.CLOSED, PageState.BOT_BLOCK, PageState.LISTING} and any(f.region in _MAIN for f in m.fields) and not obs.captcha:
        state, why = PageState.UNKNOWN, ["model said the page is unavailable but it has input fields: rejected"]
    if m.busy and state != PageState.UNKNOWN:
        state, why = PageState.UNKNOWN, ["the page is still loading (busy indicator): not judged yet"]
    thin = not m.fields and len(m.actions) < 3 and len(m.text_digest.strip()) < 80
    if thin and state in {PageState.DEAD, PageState.CLOSED, PageState.LISTING, PageState.BOT_BLOCK, PageState.EMAIL_ONLY}:
        state, why = PageState.UNKNOWN, ["model said the job is unavailable but the page has almost no content (still loading?): rejected"]
    return Classification(state, out["advance_id"] or None, min(float(out["confidence"]), 0.7), why, "llm")
