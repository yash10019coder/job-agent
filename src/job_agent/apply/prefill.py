"""Deterministic pre-answers: the standard questions every ATS asks, answered from your facts with no model call.

Runs BEFORE the LLM so common fields are always answered the same way (and for free). Anything not matched here
falls through to the batch resolver / option decider. Rules never guess: a rule only fires when its fact exists.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from job_agent.apply.perceive import FieldModel

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _month_index(text: str, today: date) -> int | None:
    text = text.strip().lower()
    if text in {"present", "current", "now"}:
        return today.year * 12 + today.month
    match = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", text)
    return int(match.group(2)) * 12 + _MONTHS[match.group(1)] if match and match.group(1) in _MONTHS else None


def _union_months(intervals: list[tuple[int, int]]) -> int:
    total, end = 0, -1
    for start, stop in sorted(intervals):
        start = max(start, end)
        if stop > start:
            total, end = total + stop - start, stop
    return total


def compute_experience(inv: dict, today: date | None = None, hide_roles: str = "") -> dict:
    """Years of experience per technology and overall, from role dates (overlaps counted once).
    `hide_roles` (regex on the title) drops roles from the per-technology years only; totals still count them."""
    today = today or date.today()
    by_tech: dict[str, list[tuple[int, int]]] = {}
    professional, everything = [], []
    for role in inv["roles"]:
        start, stop = _month_index(role["start"], today), _month_index(role["end"], today)
        if start is None or stop is None:
            continue
        everything.append((start, stop))
        if not re.search(r"open source|gsoc|contributor", role["title"] + " " + role["company"], re.I):
            professional.append((start, stop))
        if hide_roles and re.search(hide_roles, role["title"], re.I):
            continue
        for tech in role.get("tech_stack", []):
            by_tech.setdefault(tech, []).append((start, stop))
    years = lambda iv: round(_union_months(iv) / 12, 1)  # noqa: E731
    return {
        "total_years_including_freelance_and_open_source": years(everything),
        "professional_years_(employment_and_freelance)": years(professional),
        "years_by_technology": dict(sorted(((t, years(iv)) for t, iv in by_tech.items()), key=lambda kv: -kv[1])),
        "_note": "computed from role dates; overlapping roles counted once",
    }


def build_facts(profile_path: Path, inventory_path: Path) -> dict:
    if not profile_path.exists():  # real profile is gitignored (personal answers): fall back to the committed template
        profile_path = profile_path.with_name("applicant_profile.example.json")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    inv = json.loads(inventory_path.read_text(encoding="utf-8"))
    ov = profile.get("facts_overrides", {})  # your presentation choices; the inventory file itself is never edited
    hide = ov.get("hide_roles_matching", "")
    shown = [r for r in inv["roles"] if not (hide and re.search(hide, r["title"], re.I))]
    def shown_dates(r: dict) -> tuple[str, str]:  # display only: the resume's dates, e.g. an ended job that the inventory still says "Present"
        fix = next((o for o in ov.get("role_display_overrides", []) if re.search(o["match"], r["company"], re.I)), {})
        return fix.get("start", r["start"]), fix.get("end", r["end"])
    roles = [f"{r['title']} at {r['company']} ({shown_dates(r)[0]} - {shown_dates(r)[1]}), {r['location']}" for r in shown]
    experience = compute_experience(inv, hide_roles=hide)
    tech = experience["years_by_technology"]
    for name, years in ov.get("years_by_technology_min", {}).items():
        tech[name] = max(tech.get(name, 0), years)
    experience["years_by_technology"] = dict(sorted(tech.items(), key=lambda kv: -kv[1]))
    skills = ov.get("skills") or {c["name"]: c["items"] for c in inv["skills"]}
    return {
        "contact": inv["contact"], "education": inv["education"], "roles": roles, "skills": skills,
        "current_role": {"title": shown[0]["title"], "company": shown[0]["company"]},
        "experience": experience,
        **{k: v for k, v in profile.items() if not k.startswith("_") and k != "facts_overrides"},
    }


@dataclass
class Prefill:
    value: str
    basis: str
    blank: bool = False  # intentionally leave empty (optional field with nothing to say)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+ ]", " ", text.lower())).strip()


def prefill(field: FieldModel, facts: dict) -> Prefill | None:
    """Return a deterministic answer for a standard question, or None to leave it to the model."""
    label, kind = _norm(field.label), field.kind
    ident, loc = facts.get("identity", {}), facts.get("location", {})
    contact, policy = facts.get("contact", {}), facts.get("answer_policy", {})
    legal, prefs = facts.get("legal", {}), facts.get("preferences", {})

    def say(value, basis, blank=False):
        return Prefill(str(value), basis, blank) if (value or blank) else None

    if kind == "file":
        return None
    cond = re.match(r"^if (you are |you re )?(currently )?(located|based|residing|living|a resident|resident) in (the )?(\w+( \w+){0,2})", label)
    if cond and kind in {"text", "textarea"}:  # "If located in the US, which city?": not applicable when we are elsewhere
        stop = {"which", "what", "where", "how", "please", "do", "are", "in", "and", "can", "did", "is", "does", "provide", "list", "state", "city"}
        place_tokens = []
        for token in cond.group(5).split():
            if token in stop:
                break
            place_tokens.append(token)
        place = " ".join(place_tokens)
        ours = {_norm(loc.get(k, "")) for k in ("country", "state", "city")} | {"indian"}
        if place and place not in ours:
            return say("N/A", f"conditional question about {place}; you are located in {loc.get('country', 'another country')}")
    if re.search(r"not a robot|i am (a )?human|captcha|verify you are human", label):
        return say("", "human-verification checkbox: never ticked by the agent", blank=True)
    if re.search(r"pronunciation", label):
        return say("", "nothing to add", blank=True)
    if re.search(r"\bpronouns?\b", label):
        gender = str(facts.get("eeo", {}).get("gender", "")).lower()   # stated in the profile; never inferred from a name
        pronouns = {"male": "He/him", "female": "She/her"}.get(gender)
        return say(pronouns, "eeo.gender in the profile") if pronouns else say("Prefer not to say", "no gender stated in the profile")
    if re.search(r"(preferred|nick) name|what should we call you|name you d? ?prefer", label):
        return say(ident.get("preferred_name"), "identity.preferred_name")
    if re.search(r"^(legal )?first name|given name", label):
        return say(ident.get("first_name"), "identity.first_name")
    if re.search(r"^(legal )?last name|surname|family name", label):
        return say(ident.get("last_name"), "identity.last_name")
    if re.search(r"^(full |legal )?name$|^your name$", label):
        return say(ident.get("full_name") or contact.get("name"), "identity.full_name")
    if kind == "email" or re.search(r"^e ?mail( address)?$|email address", label):
        return say(contact.get("email"), "contact.email")
    if re.search(r"phone (country|code)|country code|dial", label):
        return say(f"{loc.get('country')} {loc.get('phone_country_code')}".strip(), "location.phone_country_code")
    if kind == "tel" or re.search(r"^(mobile |phone |contact )?(number|phone|mobile)( number)?$|phone number|mobile number", label):
        return say(contact.get("phone"), "contact.phone")
    if re.search(r"linkedin", label):
        return say(contact.get("linkedin"), "contact.linkedin")
    if re.search(r"github", label):
        return say(contact.get("github"), "contact.github")
    if re.search(r"twitter|^x handle|portfolio|personal (website|site)|^website$|other links|blog", label):
        return say("", "no such link in the facts", blank=True) if not field.required else None
    if re.search(r"^country$|country of residence|current country|country (you )?(currently )?(reside|live|located|based) in|^country( |/)region$", label):
        return say(loc.get("country"), "location.country")
    if re.search(r"^(current )?(city|town)$|^location \(?city\)?$|^city of residence|^current location$|^location$", label):
        return say(loc.get("city"), "location.city")
    if re.search(r"^(state|province|region)( of residence)?$|^state/province", label):
        return say(loc.get("state"), "location.state")
    if re.search(r"current (company|employer)|^employer$|^company$|most recent (company|employer)", label):
        return say(facts.get("current_role", {}).get("company"), "current_role.company")
    if re.search(r"current (job )?title|^job title$|most recent (job )?title", label):
        return say(facts.get("current_role", {}).get("title"), "current_role.title")
    if re.search(r"reason(s)? for (leaving|leaving your|resign|your departure)|why (did|are) you (leave|leaving|left)|why .*\bleft\b", label):
        return say(policy.get("reason_for_leaving"), "answer_policy.reason_for_leaving")
    if re.search(r"notice period", label):
        return say(policy.get("notice_period"), "answer_policy.notice_period")
    if re.search(r"(earliest )?(start|joining) date|when can you (start|join)|availability to start", label):
        return say(policy.get("earliest_start_date"), "answer_policy.earliest_start_date")
    if kind != "number" and re.search(r"expected (ctc|salary|compensation)|salary expectation|desired (salary|compensation)", label):
        return say(policy.get("expected_ctc"), "answer_policy.expected_ctc")
    if kind != "number" and re.search(r"current (ctc|salary|compensation)", label):
        return say(policy.get("current_ctc"), "answer_policy.current_ctc")
    if re.search(r"relocat", label) and kind != "textarea":
        return say(policy.get("willing_to_relocate"), "answer_policy.willing_to_relocate")
    if re.search(r"how did you (first )?(hear|learn|find)|where did you (hear|find)|source of application|referral source", label):
        return say(facts.get("referral_source_default"), "referral_source_default")
    if re.search(r"(18|eighteen) (years|or older)|at least 18|legal age", label):
        return say("Yes" if legal.get("is_18_or_older") else "", "legal.is_18_or_older")
    if re.search(r"background (check|screening)", label) and legal.get("background_check_consent") is not None:
        return say("Yes" if legal["background_check_consent"] else "No", "legal.background_check_consent")
    if re.search(r"non ?compete|restrictive covenant|non ?solicit", label) and legal.get("has_non_compete_or_restrictive_agreements") is not None:
        return say("Yes" if legal["has_non_compete_or_restrictive_agreements"] else "No", "legal.has_non_compete_or_restrictive_agreements")
    if re.search(r"security clearance", label) and legal.get("security_clearance"):
        return say("No" if legal["security_clearance"].lower() == "none" else legal["security_clearance"], "legal.security_clearance")
    if kind == "checkbox" and re.search(r"privacy|terms|consent|i agree|acknowledge|read and understood", label):
        return say("checked", "consents.accept_privacy_and_terms") if facts.get("consents", {}).get("accept_privacy_and_terms") else None
    if re.search(r"english", label) and re.search(r"proficien|fluen|level", label) and prefs.get("english_proficiency"):
        return say(prefs["english_proficiency"], "preferences.english_proficiency")
    return None
