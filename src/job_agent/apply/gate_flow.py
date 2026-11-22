"""Account gates: login, signup, "sign in or create account" choices and emailed codes.

The controller calls `handle_gate` when it classifies the page as one of these states. The flow never guesses: a rejected login is
not retried (lockouts), an account that already exists without a stored password is left to a human, SSO and captchas are never
touched, and a new account's password is stored in the OS keyring BEFORE the signup form is submitted. Secrets never reach traces.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from playwright.async_api import Page

from job_agent.apply import execute
from job_agent.apply.auth import Credential, CredentialStore, generate_password, site_key
from job_agent.apply.demo import DemoResult, fill_until_stable
from job_agent.apply.mailcode import CodeProvider, site_hints
from job_agent.apply.navigate import click_and_follow
from job_agent.apply.perceive import PageModel, Settler, perceive
from job_agent.apply.states import PageState

GATE_STATES = {PageState.LOGIN, PageState.SIGNUP, PageState.ACCOUNT_CHOICE, PageState.EMAIL_CODE}
_REGIONS = {"main", "dialog", "footer"}
_SSO = re.compile(r"\b(with|via|using)\b.{0,14}\b(google|linkedin|apple|facebook|microsoft|github|indeed)\b|^(google|linkedin|apple|facebook|microsoft)$", re.I)
_LOGIN_CTL = re.compile(r"^(sign ?in|log ?in|login|continue|next|submit)$", re.I)
_SIGNUP_CTL = re.compile(r"^(create( an| your)?( new)? (account|profile)|sign ?up|register|join( now)?|continue|next|submit)$", re.I)
_CODE_CTL = re.compile(r"^(verify( (email|code))?|confirm|continue|submit|next|validate)$", re.I)
_TO_LOGIN = re.compile(r"sign ?in|log ?in|login|returning|existing|already (have|registered)", re.I)
_TO_SIGNUP = re.compile(r"create( an| your)?( new)? (account|profile)|sign ?up|register|new (user|candidate|applicant)|join", re.I)
_REJECT = re.compile(r"incorrect|invalid|wrong|not recogni[sz]ed|failed|locked|does not match|doesn.t match|try again|unable to|expired", re.I)
_EXISTS = re.compile(r"already (exists|registered|in use|have an account|a member)|account exists|(email|address).{0,25}(taken|in use)|duplicate", re.I)
_PASSWORD_RULE = re.compile(r"password.{0,70}(must|should|least|character|uppercase|special|symbol|digit|number|weak|strong|requirement)", re.I)
_CODE_LABEL = re.compile(r"code|otp|pin|digit|verification|security", re.I)
_EMAIL_OPTION = re.compile(r"e-?mail|@", re.I)
_SEND_CTL = re.compile(r"^(send\b.*|get (a |the )?(pass)?code|continue|next)$", re.I)


@dataclass
class AuthKit:
    store: CredentialStore | None
    provider: CodeProvider | None
    username: str                       # the account email: the same address the application uses
    gate_since: datetime | None = None  # when we last submitted an account form (code emails arrive after it)
    allow_signup: bool = False          # creating accounts on real sites is a side effect: off unless asked for (--allow-signup / --submit)

    @classmethod
    def from_facts(cls, facts: dict, store: CredentialStore | None, provider: CodeProvider | None, allow_signup: bool = False) -> "AuthKit | None":
        email = (facts.get("contact") or {}).get("email")
        return cls(store, provider, email, allow_signup=allow_signup) if email and (store or provider) else None


@dataclass
class GateStep:
    kind: str                # continue | human
    detail: str = ""
    page: Page | None = None


def _human(detail: str) -> GateStep:
    return GateStep("human", detail)


def _pick(model: PageModel, wanted: re.Pattern) -> object | None:
    """The enabled control whose label matches, never an SSO button; primary controls first."""
    found = [a for a in model.actions if not a.disabled and a.region in _REGIONS and wanted.search(a.label.strip()) and not _SSO.search(a.label.strip())]
    found.sort(key=lambda a: not a.primary)
    return found[0] if found else None


def _complaints(model: PageModel) -> str:
    return " | ".join([*model.alerts, *[f.error for f in model.fields if f.error]])[:300]


async def _click(page: Page, control, kit: AuthKit) -> Page:
    perception = await perceive(page)
    hop = await click_and_follow(page, perception, control.id, control.frame, wait_s=6.0)
    kit.gate_since = datetime.now()
    return hop.page


async def _fill_account(page: Page, model: PageModel, username: str, password: str) -> None:
    perception = await perceive(page)
    for f in perception.model.fields:
        if f.region not in {"main", "dialog"}:
            continue
        if f.kind == "password":
            await execute.fill_text(page, perception, f, password)
        elif f.kind in {"email", "text"} and re.search(r"e-?mail|user ?name|login", f.label, re.I) and f.empty:
            await execute.fill_text(page, perception, f, username)


async def handle_gate(page: Page, state: PageState, kit: AuthKit, facts: dict, llm, resume: str | None) -> GateStep:
    perception = await perceive(page)
    model, site = perception.model, site_key(page.url)
    if state == PageState.ACCOUNT_CHOICE:
        if any(f.kind == "password" and f.region in {"main", "dialog"} for f in model.fields):
            # Sign-in AND create-account buttons next to real password boxes: it is a form to fill, not a choice to click
            # (clicking the button of an empty form just loops). A stored login means sign in, otherwise create the account.
            return await (_login(page, model, kit, site) if (kit.store and kit.store.get(site)) else _signup(page, model, kit, site, facts, llm, resume))
        return await _choose(page, model, kit, site)
    if state == PageState.LOGIN:
        return await _login(page, model, kit, site)
    if state == PageState.SIGNUP:
        return await _signup(page, model, kit, site, facts, llm, resume)
    return await _email_code(page, model, kit)


async def _choose(page, model, kit, site) -> GateStep:
    creds = kit.store.get(site) if kit.store else None
    can_create = bool(kit.store and kit.allow_signup)
    control = _pick(model, _TO_LOGIN) if creds else (_pick(model, _TO_SIGNUP) if can_create else None)
    if control is None:
        why = "no matching control" if creds or can_create else ("account creation is off (dry run: use --allow-signup)" if kit.store else "no keyring to keep a new password")
        return _human("sign in or create an account: " + why)
    return GateStep("continue", f"chose '{control.label}'", await _click(page, control, kit))


async def _login(page, model, kit, site) -> GateStep:
    creds = kit.store.get(site) if kit.store else None
    if creds is None:
        control = _pick(model, _TO_SIGNUP) if kit.store and kit.allow_signup else None
        if control is None:
            return _human("login needed and no stored login for this site")
        return GateStep("continue", "no stored login: going to account creation", await _click(page, control, kit))
    await _fill_account(page, model, creds.username, creds.password)
    control = _pick((await perceive(page)).model, _LOGIN_CTL)
    if control is None:
        return _human("no sign-in button found")
    page = await _click(page, control, kit)
    after = (await perceive(page)).model
    if _REJECT.search(_complaints(after)):
        return _human("login rejected by the site (not retried, to avoid a lockout)")
    return GateStep("continue", "signed in", page)


async def _signup(page, model, kit, site, facts, llm, resume) -> GateStep:
    if kit.store is None:
        return _human("account creation needs a working keyring to keep the password")
    if kit.store.get(site):  # we already made an account here: the way forward is to sign in
        control = _pick(model, _TO_LOGIN)
        if control is None:
            return _human("an account exists (created earlier) but the page has no sign-in control")
        return GateStep("continue", "account exists: going to sign in", await _click(page, control, kit))
    if not kit.allow_signup:
        return _human("account creation is off (dry run: use --allow-signup, or run with --submit)")
    password = generate_password()
    kit.store.save(site, Credential(kit.username, password))  # stored BEFORE the account exists
    for attempt in (1, 2):
        await _fill_account(page, model, kit.username, password)
        await fill_until_stable(page, llm, json.dumps(facts, indent=1), resume, DemoResult(), facts)  # names, consent boxes and the rest
        control = _pick((await perceive(page)).model, _SIGNUP_CTL)
        if control is None:
            kit.store.forget(site)
            return _human("no create-account button found")
        page = await _click(page, control, kit)
        after = (await perceive(page)).model
        problems = _complaints(after)
        if _EXISTS.search(problems):
            kit.store.forget(site)
            return _human("an account with this email already exists and we hold no password for it")
        if attempt == 1 and _PASSWORD_RULE.search(problems):  # the site wants a stronger password: one retry, stored first
            password = generate_password(24)
            kit.store.save(site, Credential(kit.username, password))
            model = after
            continue
        if problems and _REJECT.search(problems):
            kit.store.forget(site)
            return _human("signup rejected: " + problems[:120])
        return GateStep("continue", "account created", page)
    kit.store.forget(site)
    return _human("signup kept failing on the password rules")


async def _page_moved_on(page: Page, seconds: float = 10.0) -> bool:
    """Some sites submit the code the moment the last digit is typed and have no Verify button: the page itself moves on. True once the
    code box is gone; False when it is still there after `seconds`, or the site complained about the code."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        model = (await perceive(page)).model
        if _REJECT.search(_complaints(model)):
            return False
        if not any(f.region in {"main", "dialog"} and (f.maxlength == 1 or _CODE_LABEL.search(f.label)) for f in model.fields):
            return True
    return False


async def _choose_delivery(page, kit) -> GateStep | Page | None:
    """'Select your verification method' (text message / email): the code can only be read from the mailbox, so pick the email option and
    press Send; the code box comes next. None when the page offers no such choice (the code box is already there)."""
    perception = await perceive(page)
    group = next((f for f in perception.model.fields if f.kind == "radio_group" and f.region in {"main", "dialog"} and f.options
                  and any(_EMAIL_OPTION.search(o.label) for o in f.options)), None)
    if group is None:
        return None
    option = next(o for o in group.options if _EMAIL_OPTION.search(o.label))
    if not (await execute.set_choice(perception, group, option.label)).ok:
        return _human("the page asks where to send the code and the email option could not be chosen")
    control = _pick((await perceive(page)).model, _SEND_CTL)
    if control is None:
        return _human("the email option is chosen but there is no Send button")
    return await _click(page, control, kit)   # also restarts the clock: only mail that arrives after this click counts


async def _email_code(page, model, kit) -> GateStep:
    if kit.provider is None:
        return _human("an emailed code is needed and Gmail is not connected")
    sent = await _choose_delivery(page, kit)
    if isinstance(sent, GateStep):
        return sent
    page = sent or page
    since = (kit.gate_since or datetime.now()) - timedelta(seconds=30)
    mail = await kit.provider.fetch(site_hints(page.url), since, 90.0)
    if mail is None:
        return _human("no verification email arrived in time")
    if mail.code is None and mail.link:
        await page.goto(mail.link)
        await Settler(page).settle()
        kit.gate_since = None
        return GateStep("continue", "opened the verification link", page)
    perception = await perceive(page)
    fields = [f for f in perception.model.fields if f.region in {"main", "dialog"} and f.kind in {"text", "number", "tel"}]
    segmented = [f for f in fields if f.maxlength == 1]
    if 4 <= len(segmented) <= 8:
        for f, ch in zip(segmented, mail.code):
            await execute.fill_text(page, perception, f, ch)
    else:
        target = next((f for f in fields if _CODE_LABEL.search(f.label)), fields[0] if fields else None)
        if target is None:
            return _human("the page has no box for the code")
        await execute.fill_text(page, perception, target, mail.code)
    control = _pick((await perceive(page)).model, _CODE_CTL)
    if control is not None:
        page = await _click(page, control, kit)
    elif not await _page_moved_on(page):
        return _human("no verify button found and the page did not move on by itself")
    if _REJECT.search(_complaints((await perceive(page)).model)):
        return _human("the site rejected the code")
    kit.gate_since = None
    return GateStep("continue", "code entered", page)
