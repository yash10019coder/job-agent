from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlparse

from scrapling import Selector

from job_agent.models import JobPosting, Lane, SourceType

COMMON_SKILLS = {
    "c++",
    "stl",
    "linux",
    "tizen",
    "d-bus",
    "multithreading",
    "threading",
    "async",
    "webrtc",
    "node.js",
    "node",
    "javascript",
    "typescript",
    "python",
    "react",
    "next.js",
    "nextjs",
    "django",
    "express",
    "spring boot",
    "java",
    "microservices",
    "sql",
    "mongodb",
    "docker",
    "kubernetes",
    "rest",
    "apis",
    "backend",
    "frontend",
    "distributed systems",
    "system design",
    "cloud",
    "azure",
}

KEYWORD_SYNONYMS = {
    "node.js": "node.js",
    "node": "node.js",
    "nextjs": "next.js",
    "web rtc": "webrtc",
    "apis": "apis",
    "rest": "rest",
}

STOPWORDS = {
    "and",
    "the",
    "with",
    "for",
    "our",
    "job",
    "role",
    "team",
    "this",
    "that",
    "you",
    "your",
    "will",
    "have",
    "has",
    "from",
    "into",
    "their",
    "them",
    "what",
    "who",
    "apply",
    "experience",
    "software",
    "engineer",
}

# Patterns that indicate job is CLOSED/NO LONGER ACTIVE
CLOSED_JOB_PATTERNS = [
    r"no longer accepting",
    r"position has been filled",
    r"job closed",
    r"applications closed",
    r"not accepting applications",
    r"this job is no longer available",
    r"no longer available",
    r"position closed",
    r"no longer accepting applications",
    r"closed for applications",
    r"not accepting new applications",
]

# Patterns that indicate job IS ACTIVE (for reversal check)
ACTIVE_JOB_PATTERNS = [
    r"accepting applications",
    r"apply now",
    r"apply today",
    r"submit your application",
    r"easy apply",
]


def _is_job_active(selector: Selector) -> bool:
    """
    Check if job posting is still active and accepting applications.
    Returns True if active, False if closed/expired.
    """
    # Get full page text for analysis
    full_text = selector.get_all_text(separator=" ", strip=True)
    
    # Check for explicit closed indicators first
    for pattern in CLOSED_JOB_PATTERNS:
        if re.search(pattern, full_text, re.IGNORECASE):
            # Get surrounding context to check if there's an active indicator nearby
            matches = list(re.finditer(pattern, full_text, re.IGNORECASE))
            for match in matches:
                start = max(0, match.start() - 300)
                end = min(len(full_text), match.end() + 300)
                context = full_text[start:end].lower()
                
                # If there's an active pattern near the closed pattern, job might still be open
                for active_pattern in ACTIVE_JOB_PATTERNS:
                    if re.search(active_pattern, context, re.IGNORECASE):
                        return True  # Active indicator found nearby, assume job is open
                
            return False  # Closed pattern found without nearby active indicator
    
    # Check for specific closed indicators in buttons/text
    closed_button_patterns = [
        r"job\s+(?:is\s+)?closed",
        r"no\s+longer\s+(?:accepting|available)",
        r"position\s+(?:has\s+been\s+)?filled",
        r"not\s+(?:accepting|available)",
    ]
    
    for pattern in closed_button_patterns:
        if re.search(pattern, full_text, re.IGNORECASE):
            # Check for apply buttons nearby - if they exist, job might still be open
            apply_buttons = selector.css("button:has-text('Apply')")
            easy_apply = selector.css("button:has-text('Easy Apply')")
            if not apply_buttons and not easy_apply:
                return False
    
    # If no explicit closed indicators, assume job is active
    return True


@dataclass
class ParsedDocument:
    selector: Selector
    source: SourceType
    url: str


def detect_source(url: str) -> SourceType:
    host = urlparse(url).netloc.lower()
    if "greenhouse.io" in host:
        return SourceType.GREENHOUSE
    if "lever.co" in host:
        return SourceType.LEVER
    return SourceType.COMPANY


def _first_text(selector: Selector, patterns: list[str]) -> str | None:
    for pattern in patterns:
        matches = selector.css(pattern)
        if matches:
            value = matches[0].text.strip()
            if value:
                return value
    return None


def _candidate_text(selector: Selector, patterns: list[str]) -> str:
    best = ""
    for pattern in patterns:
        matches = selector.css(pattern)
        for match in matches:
            text = match.get_all_text(separator=" ", strip=True)
            if len(text) > len(best):
                best = text
    return best or selector.get_all_text(separator=" ", strip=True)


def _json_ld_job_posting(selector: Selector) -> dict[str, str]:
    for script in selector.css("script[type='application/ld+json']"):
        raw = script.text.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _iter_json_ld_items(payload):
            item_type = item.get("@type")
            if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
                hiring = item.get("hiringOrganization") or {}
                location = item.get("jobLocation") or {}
                place = location.get("address") if isinstance(location, dict) else {}
                locality = ""
                if isinstance(place, dict):
                    locality = ", ".join(
                        part
                        for part in [
                            place.get("addressLocality"),
                            place.get("addressRegion"),
                            place.get("addressCountry"),
                        ]
                        if part
                    )
                return {
                    "title": str(item.get("title") or item.get("name") or ""),
                    "company": str(hiring.get("name") or ""),
                    "location": locality,
                    "description": str(item.get("description") or ""),
                }
    return {}


def _iter_json_ld_items(payload) -> list[dict]:
    if isinstance(payload, dict):
        if "@graph" in payload and isinstance(payload["@graph"], list):
            return [item for item in payload["@graph"] if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _normalize_skill(skill: str) -> str:
    return KEYWORD_SYNONYMS.get(skill, skill)


def _skill_in_text(text: str, skill: str) -> bool:
    if skill == "c++":
        return "c++" in text
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])")
    return bool(pattern.search(text))


def _find_skills(text: str) -> set[str]:
    normalized_text = text.lower()
    return {_normalize_skill(skill) for skill in COMMON_SKILLS if _skill_in_text(normalized_text, skill)}


def extract_job_signals(title: str, description: str) -> tuple[list[str], list[str], list[str], str | None]:
    text = f"{title}\n{description}".lower()
    found_skills = sorted(_find_skills(text))
    required = set()
    preferred = set()
    required_sections = re.findall(
        r"(?:requirements|required|must have|you have|need to have)[^:\n]*:\s*(.*?)(?=(?:preferred|nice to have|bonus|good to have)[^:\n]*:|$)",
        description,
        flags=re.IGNORECASE | re.DOTALL,
    )
    preferred_sections = re.findall(
        r"(?:preferred|nice to have|bonus|good to have)[^:\n]*:\s*(.*?)(?=(?:requirements|required|must have|you have|need to have)[^:\n]*:|$)",
        description,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for section in required_sections:
        required.update(_find_skills(section))
    for section in preferred_sections:
        preferred.update(_find_skills(section))
    if not required or not preferred:
        sections = re.split(r"\n|[.;]", description)
        for section in sections:
            normalized = section.lower()
            skill_matches = _find_skills(normalized)
            if not skill_matches:
                continue
            if not required and any(
                token in normalized for token in ["must have", "requirements", "required", "you have", "need to have"]
            ):
                required.update(skill_matches)
            elif not preferred and any(
                token in normalized for token in ["preferred", "nice to have", "bonus", "good to have"]
            ):
                preferred.update(skill_matches)
    if not required:
        required.update(found_skills[:6])
    keyword_counter = Counter(re.findall(r"\b[a-z][a-z0-9+.#-]{1,20}\b", text))
    common_keywords = [word for word, _ in keyword_counter.most_common(20)]
    seniority = None
    title_lower = title.lower()
    for marker in ["intern", "junior", "associate", "senior", "lead", "staff"]:
        if marker in title_lower:
            seniority = marker
            break
    keywords = []
    for skill in found_skills + common_keywords:
        if skill not in keywords and len(skill) > 2 and skill not in STOPWORDS:
            keywords.append(skill)
    return sorted(required), sorted(preferred), keywords[:12], seniority


def lane_scores(title: str, description: str) -> dict[Lane, float]:
    text = f"{title}\n{description}".lower()
    # C++ terms removed - keeping only fullstack_backend lane
    backend_terms = [
        "backend",
        "full stack",
        "full-stack",
        "node.js",
        "python",
        "typescript",
        "react",
        "next.js",
        "microservices",
        "rest",
        "api",
        "sql",
        "mongodb",
        "distributed",
    ]
    backend_score = sum(2.0 if term in title.lower() else 1.0 for term in backend_terms if term in text)
    return {
        Lane.FULLSTACK_BACKEND: round(backend_score, 2),
    }


def parse_job(selector: Selector, url: str) -> JobPosting:
    source = detect_source(url)
    json_ld = _json_ld_job_posting(selector)
    if source is SourceType.GREENHOUSE:
        title = _first_text(selector, ["h1", "[data-mapped='job-title']", ".app-title"]) or "Unknown Role"
        company = _first_text(selector, [".company-name", "[data-mapped='company-name']", ".company"]) or urlparse(url).netloc
        location = _first_text(selector, [".location", "[data-mapped='location']"])
        description = _candidate_text(selector, ["#content", "#app_body", ".content"])
    elif source is SourceType.LEVER:
        title = _first_text(selector, ["h2", "h1", ".posting-headline h2"]) or "Unknown Role"
        company = _first_text(selector, [".posting-categories .sort-by-company"])
        if not company:
            title_tag = _first_text(selector, ["title"])
            if title_tag and " - " in title_tag:
                company = title_tag.split(" - ", 1)[0]
            else:
                logo = selector.css(".main-header-logo img")
                company = logo[0].attrib.get("alt", "").replace(" logo", "") if logo else urlparse(url).netloc
        location = _first_text(selector, [".posting-categories .sort-by-location", ".location"])
        description = _candidate_text(selector, [".content", ".posting", ".section-wrapper"])
    else:
        title = _first_text(selector, ["h1", "meta[property='og:title']", "title"]) or "Unknown Role"
        company = _first_text(selector, ["meta[property='og:site_name']", ".company", ".organization"]) or urlparse(url).netloc
        location = _first_text(selector, [".location", "[data-location]", ".job-location"])
        description = _candidate_text(selector, ["main", "article", "[role='main']", ".job-description"])
    if json_ld:
        title = json_ld.get("title") or title
        company = json_ld.get("company") or company
        location = json_ld.get("location") or location
        if len(json_ld.get("description", "")) > len(description):
            description = json_ld["description"]
    
    # Check if job is still active
    is_active = _is_job_active(selector)
    
    required, preferred, keywords, seniority = extract_job_signals(title, description)
    return JobPosting(
        url=url,
        source=source,
        title=title,
        company=company,
        location=location,
        raw_description=description,
        is_active=is_active,
        required_skills=required,
        preferred_skills=preferred,
        keywords=keywords,
        seniority=seniority,
        lane_score=lane_scores(title, description),
    )
