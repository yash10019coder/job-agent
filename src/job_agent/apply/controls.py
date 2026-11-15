"""Which control moves an application forward, and which one finishes it?

No list of button names. The perceiver shortlists the controls structurally (main content / dialog / sticky footer,
form members first); one text-only model call then labels each as submit_final / next_step / back_or_cancel / other
from the page's headings and the control's wording and position. Answers are cached per page state, so a step costs
at most one small call. If the model is unavailable or unsure nothing is labelled, and callers treat that as "no
submit control found" (nothing is clicked). Pressing Submit still needs every guard in submit.check_ready.
"""
from __future__ import annotations

from urllib.parse import urlsplit

ROLES = ["submit_final", "next_step", "back_or_cancel", "other"]
_llm = None
_CACHE: dict[tuple, str] = {}
_SYSTEM = (
    "You label buttons on one page of an online job application. Roles: submit_final = pressing it sends the whole "
    "application (no further steps; its wording can be anything, e.g. 'Apply now', 'Send', 'Finish'); next_step = moves "
    "to another step or a review page of the same application; back_or_cancel = goes back, saves a draft, or cancels; "
    "other = anything else (links, language pickers, social buttons, open-the-form buttons). Controls inside the form "
    "that sit after the questions are the likely ones. Be conservative: if unsure, choose other.")


def use(llm) -> None:
    """Set the model used for labelling (entry points call this once)."""
    global _llm
    _llm = llm


def _key(model, a) -> tuple:
    u = urlsplit(model.url)
    return (u.netloc + u.path, model.title, tuple(model.headings[:2]), a.kind, a.region, a.label)


def shortlist(model) -> list:
    """Controls worth labelling: main/dialog/footer, one per identical label (repeated Edit/Delete buttons on imported entries
    would otherwise fill the list), and when there are many, the ones nearest the end of the page, where Next/Submit live."""
    acts = [a for a in model.actions if a.region in {"main", "dialog", "footer"}]
    last: dict[tuple, int] = {}
    for i, a in enumerate(acts):
        last[(a.kind, a.region, a.label.strip().lower())] = i
    acts = [a for i, a in enumerate(acts) if last[(a.kind, a.region, a.label.strip().lower())] == i]
    return acts[-14:]


def cached_role(model, a) -> str | None:
    return _CACHE.get(_key(model, a))


async def annotate(model) -> None:
    todo = [a for a in shortlist(model) if _key(model, a) not in _CACHE]
    if not todo or _llm is None:
        return
    schema = {"type": "object", "additionalProperties": False, "required": ["roles"], "properties": {"roles": {
        "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "role"],
                                   "properties": {"id": {"type": "string", "enum": [a.id for a in todo]},
                                                  "role": {"type": "string", "enum": ROLES}}}}}}
    lines = "\n".join(f"{a.id} | {a.kind} | {'inside form' if a.in_form else 'outside form'} | {a.region} | "
                      f"{'disabled' if a.disabled else 'enabled'} | {a.label}" for a in todo)
    user = (f"Page: {model.title}\nHeadings: {'; '.join(model.headings[:4])}\n"
            f"Question fields on the page: {sum(f.region in {'main', 'dialog'} for f in model.fields)}\nControls:\n{lines}")
    try:
        out = await _llm.complete_json("control_roles", _SYSTEM, user, schema, "roles")
    except Exception:
        return  # unlabelled => callers find no submit/next control and do nothing
    by_id = {a.id: a for a in todo}
    for item in out.get("roles", []):
        if item["id"] in by_id:
            _CACHE[_key(model, by_id[item["id"]])] = item["role"]


async def find(model, include_disabled: bool = False) -> tuple[list, list]:
    """(next-like controls, submit-like controls) among the shortlisted actions."""
    await annotate(model)
    ok = [a for a in shortlist(model) if include_disabled or not a.disabled]
    return ([a for a in ok if cached_role(model, a) == "next_step"], [a for a in ok if cached_role(model, a) == "submit_final"])
