"""Identify the ATS/vendor behind a page from evidence the page itself gives, not from its URL alone.

Evidence (strongest first): hosts the page actually requests (XHR/scripts/frames), iframe sources, form
actions, apply-link hosts, then vendor markers inside the page source. Custom career domains hide the vendor
in the URL but not in these.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

# vendor -> (host regexes, page-source markers)
SIGNATURES: dict[str, tuple[list[str], list[str]]] = {
    "workday": ([r"myworkdayjobs\.com", r"myworkdaysite\.com", r"\.workday\.com"], ["workday"]),
    "icims": ([r"icims\.com", r"jibeapply\.com", r"jibecdn\.com"], ["icims", "jibe"]),
    "taleo": ([r"taleo\.net"], ["taleo"]),
    "oracle_cloud_hcm": ([r"oraclecloud\.com", r"oracle\.com/.*candidate"], ["candidateexperience"]),
    "successfactors": ([r"successfactors\.(com|eu)", r"sapsf\.(com|eu)", r"jobs2web\.com", r"career\d*\.sap"], ["successfactors", "jobs2web"]),
    "phenom": ([r"phenompeople\.com", r"phenom\.com"], ["phenompeople", "phapp"]),
    "avature": ([r"avature\.net"], ["avature"]),
    "radancy": ([r"tmpwebeng\.com", r"radancy\.net", r"talentbrew"], ["talentbrew", "radancy"]),
    "eightfold": ([r"eightfold\.ai"], ["eightfold"]),
    "smartrecruiters": ([r"smartrecruiters\.com"], ["smartrecruiters"]),
    "greenhouse": ([r"greenhouse\.io"], ["greenhouse"]),
    "lever": ([r"lever\.co"], ["lever.co"]),
    "ashby": ([r"ashbyhq\.com"], ["ashbyhq"]),
    "workable": ([r"workable\.com"], ["workable"]),
    "jazzhr": ([r"applytojob\.com", r"jazz\.co", r"resumatorapi"], ["applytojob"]),
    "breezy": ([r"breezy\.hr"], ["breezy"]),
    "bamboohr": ([r"bamboohr\.com"], ["bamboohr"]),
    "rippling": ([r"rippling\.com"], ["rippling"]),
    "adp": ([r"adp\.com", r"workforcenow"], ["workforcenow"]),
    "dayforce": ([r"dayforcehcm\.com", r"ceridian"], ["dayforce"]),
    "ukg": ([r"ukg\.com", r"ultipro\.com", r"ukg\.net"], ["ultipro"]),
    "teamtailor": ([r"teamtailor\.com"], ["teamtailor"]),
    "paylocity": ([r"paylocity\.com"], ["paylocity"]),
    "paycom": ([r"paycomonline\.net", r"paycom\.com"], ["paycom"]),
    "neogov": ([r"governmentjobs\.com", r"neogov\.com", r"schooljobs\.com"], ["neogov"]),
    "frontline": ([r"frontlineeducation\.com", r"applitrack\.com"], ["applitrack"]),
    "jobvite": ([r"jobvite\.com"], ["jobvite"]),
    "cornerstone": ([r"csod\.com"], ["csod"]),
    "pageup": ([r"pageuppeople\.com"], ["pageup"]),
    "paradox": ([r"paradox\.ai"], ["paradox"]),
    "keka": ([r"keka\.com"], ["keka"]),
    "darwinbox": ([r"darwinbox\.(in|com)"], ["darwinbox"]),
    "freshteam": ([r"freshteam\.com"], ["freshteam"]),
    "harri": ([r"harri\.com"], ["harri"]),
    "recruitee": ([r"recruitee\.com"], ["recruitee"]),
}
# markers worth searching for inside the page source (passed to the browser so no HTML is stored)
HTML_MARKERS = sorted({m for _, (_, ms) in SIGNATURES.items() for m in ms})

COLLECT_JS = """(markers) => {
  const hosts = (urls) => urls.map((u) => { try { return new URL(u, location.href).host; } catch (e) { return ""; } }).filter(Boolean);
  const html = document.documentElement.outerHTML.toLowerCase();
  const gen = document.querySelector("meta[name=generator]");
  return {
    scripts: hosts(Array.from(document.scripts).map((s) => s.src).filter(Boolean)),
    iframes: hosts(Array.from(document.querySelectorAll("iframe")).map((f) => f.src || f.getAttribute("data-src") || "").filter(Boolean)),
    forms: hosts(Array.from(document.forms).map((f) => f.action).filter(Boolean)),
    apply_links: hosts(Array.from(document.querySelectorAll("a[href]")).filter((a) => /apply|application|interested/i.test(a.innerText || a.getAttribute("aria-label") || "")).map((a) => a.href)),
    generator: gen ? gen.content : "",
    markers: markers.filter((m) => html.includes(m)),
  };
}"""


def _match(host: str) -> str | None:
    for vendor, (patterns, _) in SIGNATURES.items():
        if any(re.search(p, host) for p in patterns):
            return vendor
    return None


def identify(final_url: str, request_hosts: list[str], collected: dict) -> dict:
    """Score vendors from the evidence; returns {vendor, confidence, evidence}. 'unknown' if nothing fires."""
    score: Counter = Counter()
    why: dict[str, list[str]] = {}

    def add(vendor: str, weight: int, note: str) -> None:
        score[vendor] += weight
        why.setdefault(vendor, []).append(note)

    for source, weight, hosts in (("final_url", 5, [urlparse(final_url).netloc.lower()]),
                                  ("request", 3, request_hosts), ("script", 2, collected.get("scripts", [])),
                                  ("iframe", 4, collected.get("iframes", [])), ("form_action", 4, collected.get("forms", [])),
                                  ("apply_link", 4, collected.get("apply_links", []))):
        for host in set(h.lower() for h in hosts):
            vendor = _match(host)
            if vendor:
                add(vendor, weight, f"{source}:{host}")
    for marker in collected.get("markers", []):
        for vendor, (_, markers) in SIGNATURES.items():
            if marker in markers:
                add(vendor, 1, f"source:{marker}")
    if not score:
        return {"vendor": "unknown", "confidence": "none", "evidence": []}
    (vendor, top), *rest = score.most_common()
    strong = top >= 4
    ambiguous = bool(rest) and rest[0][1] >= top - 1 and rest[0][1] >= 4
    return {"vendor": vendor, "confidence": "high" if strong and not ambiguous else "medium" if strong else "low",
            "evidence": why[vendor][:6], "also": [v for v, s in rest[:2] if s >= 3]}
