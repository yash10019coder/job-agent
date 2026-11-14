"""Job-fit gate for the autopilot: decide from the title and location alone (cheap, no page load) whether a posting is
worth an application. Conservative on purpose: an unknown location passes, a clearly wrong function or level does not."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_POSITIVE = re.compile(
    r"\b(backend|back[- ]end|software (engineer|developer)|sde|swe|full[- ]?stack|ai engineer|ml engineer|machine learning|applied (ai|ml)|llm|"
    r"platform engineer|data engineer|infrastructure engineer|python|golang|java developer|devops|sre|site reliability|cloud engineer|"
    r"member of technical staff|mts)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(principal|staff|distinguished|director|head of|vp|vice president|manager|intern(ship)?|trainee|apprentice|sales|marketing|recruit(er|ing)?|"
    r"support|customer|account (executive|manager)|designer|analyst|accountant|legal|finance|nurse|driver|warehouse|technician|mechanical|"
    r"electrical|civil|hardware|embedded|firmware|qa|sdet|test|field|project|network|solutions?|forward deployed|deployment|reliability practical|lead|new grad)\b", re.I)
_INDIA = re.compile(r"india|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|gurgaon|gurugram|noida|delhi|kolkata|ahmedabad|kochi|jaipur", re.I)
_ELSEWHERE = re.compile(
    r"\b(us|usa|u\.s\.|united states|canada|toronto|vancouver|uk|united kingdom|london|ireland|dublin|germany|berlin|munich|france|paris|spain|poland|"
    r"netherlands|amsterdam|sweden|australia|sydney|singapore|brazil|mexico|emea|europe|latam|north america|latin america|americas|japan|tokyo|israel)\b", re.I)
_UNINFORMATIVE = re.compile(r"^\s*(hybrid|in[- ]?office|on[- ]?site|remote|\d+ locations?|multiple locations?|various|flexible|tbd)\b[^,;]*$", re.I)
_OPEN = re.compile(r"\b(remote|anywhere|worldwide|global|apac|asia)\b", re.I)


@dataclass
class FitDecision:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    needs_page_check: bool = False  # the location was vague ("Hybrid", empty): confirm India on the job page before applying


def india_in_text(text: str) -> bool:
    return bool(_INDIA.search(text))


def fit(job: dict, filters: dict | None = None) -> FitDecision:
    """`filters` (from the profile's job_filters) may hold include_titles, exclude_titles (regex words) and locations (words)."""
    filters = filters or {}
    title, location = str(job.get("title") or ""), str(job.get("location") or "")
    reasons = []
    if _NEGATIVE.search(title):
        reasons.append(f"title looks like the wrong function or level: {_NEGATIVE.search(title).group(0)}")
    for word in filters.get("exclude_titles", []):
        if re.search(word, title, re.I):
            reasons.append(f"title excluded by your filter: {word}")
    includes = filters.get("include_titles")
    if includes and not any(re.search(w, title, re.I) for w in includes):
        reasons.append("title matches none of your include_titles")
    elif not includes and not _POSITIVE.search(title):
        reasons.append("title is not a software/AI/data/platform engineering role")
    wanted = filters.get("locations")
    vague = not location.strip() or bool(_UNINFORMATIVE.match(location) and not re.match(r"^\s*remote\b", location, re.I))
    if location.strip():
        if wanted:
            if not any(re.search(w, location, re.I) for w in wanted):
                reasons.append(f"location '{location[:40]}' is not in your locations")
        elif _INDIA.search(location):
            pass
        elif _ELSEWHERE.search(location):
            reasons.append(f"location '{location[:40]}' is outside India")
        elif not (_UNINFORMATIVE.match(location) or _OPEN.search(location)):
            reasons.append(f"location '{location[:40]}' is not India or open remote")
    return FitDecision(not reasons, reasons, needs_page_check=not reasons and vague and not wanted)
