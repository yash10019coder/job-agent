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
