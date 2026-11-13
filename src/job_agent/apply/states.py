"""The page-state model of an application: what kind of page we are on, what can follow, how a run can end.

States describe the PAGE (not the vendor): the classifier maps an Observation to one PageState and the controller
acts on it. Terminal states end the run; the rest have an allowed set of successors, used to spot impossible jumps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from job_agent.apply.perceive import PageModel


class PageState(str, Enum):
    DEAD = "dead"                      # 404/5xx or an unusable page
    CLOSED = "closed"                  # "no longer accepting applications"
    LISTING = "listing"                # a board/search listing (e.g. a dead job redirected to the board)
    BOT_BLOCK = "bot_block"            # Cloudflare-style challenge or 403 instead of a page
    CAPTCHA = "captcha"                # a captcha challenge needs a human
    JOB_PAGE = "job_page"              # a job description with a control that starts the application
    EXTERNAL_HOP = "external_hop"      # the apply control leads to another site
    EMAIL_ONLY = "email_only"          # apply by sending an email
    LOGIN = "login"
    SIGNUP = "signup"
    ACCOUNT_CHOICE = "account_choice"  # sign in / create account / guest
    SSO_ONLY = "sso_only"
    EMAIL_CODE = "email_code"          # an emailed verification code is requested
    FORM_STEP = "form_step"            # one step (or the only step) of the application form
    REVIEW = "review"                  # review/confirm step before the final submit
    CONFIRMATION = "confirmation"      # the site confirmed the application
    UNKNOWN = "unknown"


class Outcome(str, Enum):
    APPLIED = "applied"
    DRY_RUN_READY = "dry_run_ready"            # every guard passed; nothing was sent
    NEEDS_HUMAN = "needs_human"                # captcha, login/signup/SSO/code without credentials, ambiguous controls
    NEEDS_ANSWER = "needs_answer"              # a required question has no honest answer yet
    CLOSED = "closed"
    DEAD = "dead"
    NO_APPLY_CONTROL = "no_apply_control"
    BLOCKED = "blocked"                        # bot block or a failed guard
    STUCK = "stuck"                            # the page stopped reacting, or a budget ran out
    ERROR_VALIDATION_LOOP = "error_validation_loop"


TERMINAL_STATES = frozenset({PageState.DEAD, PageState.CLOSED, PageState.LISTING, PageState.BOT_BLOCK,
                             PageState.EMAIL_ONLY, PageState.CONFIRMATION})

_GATES = {PageState.LOGIN, PageState.SIGNUP, PageState.ACCOUNT_CHOICE, PageState.SSO_ONLY}
_LIVE = set(PageState) - TERMINAL_STATES

ALLOWED: dict[PageState, set[PageState]] = {s: set() for s in PageState}
ALLOWED[PageState.JOB_PAGE] = {PageState.FORM_STEP, PageState.EXTERNAL_HOP, PageState.EMAIL_ONLY, PageState.CLOSED, PageState.LISTING,
                               PageState.CAPTCHA, PageState.BOT_BLOCK, PageState.UNKNOWN} | _GATES
ALLOWED[PageState.EXTERNAL_HOP] = {PageState.JOB_PAGE, PageState.FORM_STEP, PageState.CLOSED, PageState.DEAD, PageState.LISTING,
                                   PageState.CAPTCHA, PageState.BOT_BLOCK, PageState.UNKNOWN} | _GATES
for _gate in _GATES:
    ALLOWED[_gate] = {PageState.FORM_STEP, PageState.EMAIL_CODE, PageState.CAPTCHA, PageState.JOB_PAGE, PageState.UNKNOWN} | _GATES
ALLOWED[PageState.EMAIL_CODE] = {PageState.FORM_STEP, PageState.CONFIRMATION, PageState.CAPTCHA, PageState.LOGIN, PageState.UNKNOWN}
ALLOWED[PageState.FORM_STEP] = {PageState.FORM_STEP, PageState.REVIEW, PageState.CONFIRMATION, PageState.EMAIL_CODE,
                                PageState.CAPTCHA, PageState.LOGIN, PageState.CLOSED, PageState.UNKNOWN}
ALLOWED[PageState.REVIEW] = {PageState.CONFIRMATION, PageState.EMAIL_CODE, PageState.CAPTCHA, PageState.FORM_STEP, PageState.UNKNOWN}
ALLOWED[PageState.CAPTCHA] = {PageState.FORM_STEP, PageState.REVIEW, PageState.CONFIRMATION, PageState.EMAIL_CODE, PageState.UNKNOWN}
ALLOWED[PageState.UNKNOWN] = set(_LIVE)


@dataclass
class Observation:
    """Everything the classifier may look at (page evidence only; vendor identity is not an input)."""

    url: str
    status: int | None
    model: PageModel
    frame_hosts: list[str] = field(default_factory=list)
    captcha: bool = False
    from_url: str | None = None  # where we came from (a dead job redirecting to a board is only visible this way)
