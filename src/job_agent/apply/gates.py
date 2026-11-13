"""Detect what stands between an application page and its form: login, signup, SSO, guest option, emailed
code, captcha, bot block. Pure rules over a page model (no model calls, nothing typed or submitted)."""
from __future__ import annotations

import re

SSO = re.compile(r"\b(continue|sign in|log in|login|apply|sign up|register)\b.{0,25}\b(google|linkedin|apple|facebook|microsoft|indeed|github)\b", re.I)
GUEST = re.compile(r"(as (a )?guest|without (an )?account|without (signing|logging|registering)|no account|skip (sign|log))", re.I)
LOGIN = re.compile(r"\b(sign ?in|log ?in|already (registered|have an account|a member))\b", re.I)
SIGNUP = re.compile(r"\b(create (an )?(new )?account|sign ?up|register|join now|new (user|candidate)|get started)\b", re.I)
CODE = re.compile(r"(verification code|one[- ]time (code|password)|\botp\b|enter the code|we (have )?(sent|will send) you a (code|link)|send (me )?a (code|link)|check your email)", re.I)
BOT = re.compile(r"(verify (that )?you are (a )?human|checking your browser|attention required|access denied|are you a robot|unusual traffic|just a moment)", re.I)
CAPTCHA_HOSTS = re.compile(r"recaptcha|hcaptcha|challenges\.cloudflare\.com|funcaptcha|arkoselabs", re.I)


def classify_gate(model, frame_hosts: list[str], status: int | None = None, captcha_visible: bool = False) -> dict:
    kinds = {f.kind for f in model.fields}
    main = {"main", "dialog"}
    main_fields = [f for f in model.fields if f.region in main]
    main_actions = [a.label for a in model.actions if a.region in main]
    labels = " | ".join(f.label for f in model.fields)
    actions = [a.label for a in model.actions]
    text = " ".join([model.title, " ".join(model.headings), model.text_digest[:1500], labels, " | ".join(actions)])
    # login/signup/SSO are judged from MAIN content only: a "Login" link in the site header is not a gate
    gate_text = " ".join([model.title, " ".join(model.headings), " | ".join(main_actions), " | ".join(f.label for f in main_fields)])
    form_like = len(main_fields) >= 3 and any(f.kind == "email" or re.search(r"email|first name|full name", f.label, re.I) for f in main_fields)
    signals = {
        "password_field": "password" in kinds,
        "login_text": bool(LOGIN.search(gate_text)),
        "signup_text": bool(SIGNUP.search(gate_text)),
        "sso": sorted({m.group(2).lower() for a in main_actions for m in [SSO.search(a)] if m}),
        "guest_option": bool(GUEST.search(text)),
        "email_code": bool(CODE.search(text)),
        "captcha_frame": any(CAPTCHA_HOSTS.search(h) for h in frame_hosts),
        "captcha_visible": captcha_visible,
        "bot_block": bool(BOT.search(text[:600])) or status in (403, 429),
        "form_fields": len(model.fields),
    }
    if signals["bot_block"] and not form_like:
        gate = "bot_block"
    elif signals["captcha_visible"] and not form_like:
        gate = "captcha"
    elif signals["email_code"] and not signals["password_field"]:
        gate = "email_code"
    elif signals["password_field"] or (signals["login_text"] and signals["signup_text"] and not form_like):
        gate = "account_required (login+signup)" if signals["signup_text"] else "login"
    elif signals["sso"] and not form_like:
        gate = "sso_only"
    elif signals["guest_option"]:
        gate = "guest_option"
    elif form_like:
        gate = "none (form visible)"
    elif signals["login_text"] and not form_like:
        gate = "login"
    else:
        gate = "unclear"
    notes = [k for k, v in signals.items() if v and k != "form_fields"]
    if signals["captcha_frame"] and gate.startswith("none"):
        notes.append("captcha present on form (blocks submit)")
    return {"gate": gate, "signals": signals, "notes": notes}


def gate_strength(gate: str, signals: dict) -> str:
    """How much to trust the gate call: 'strong' = a password field, code prompt, captcha/block, or SSO-only page;
    'weak' = only login/signup WORDS, which a site header or nav link also contains."""
    if gate.startswith(("none", "unclear")):
        return "n/a"
    if signals.get("password_field") or signals.get("email_code") or signals.get("bot_block") or signals.get("captcha_visible") or gate == "sso_only":
        return "strong"
    return "weak (login/signup words only; may be a nav link)"
