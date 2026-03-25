"""Analyze skill gaps between resume inventory and job market, then suggest projects."""
from __future__ import annotations

import json
import os
from collections import Counter

from job_agent.inventory import load_resume_inventory
from job_agent.models import (
    FetchMode,
    GapReport,
    JobPosting,
    ProjectSuggestion,
    ResumeInventory,
)
from job_agent.tailoring import fetch_job_posting


def _inventory_skill_set(inventory: ResumeInventory) -> set[str]:
    """Collect all skills mentioned across skills categories, role bullets, and project bullets."""
    skills: set[str] = set()
    for category in inventory.skills:
        skills.update(skill.lower() for skill in category.items)
    for role in inventory.roles:
        for bullet in role.bullets:
            skills.update(skill.lower() for skill in bullet.skills)
        skills.update(tech.lower() for tech in role.tech_stack)
    for project in inventory.projects:
        skills.update(tech.lower() for tech in project.tech_stack)
        for bullet in project.bullets:
            skills.update(skill.lower() for skill in bullet.skills)
    return skills


def _bullet_skill_set(inventory: ResumeInventory) -> set[str]:
    """Skills actually surfaced in resume bullet text (a subset of inventory)."""
    skills: set[str] = set()
    for role in inventory.roles:
        for bullet in role.bullets:
            skills.update(skill.lower() for skill in bullet.skills)
    for project in inventory.projects:
        for bullet in project.bullets:
            skills.update(skill.lower() for skill in bullet.skills)
    return skills


def aggregate_job_skills(jobs: list[JobPosting]) -> dict[str, int]:
    """Count how often each skill appears across all analyzed job postings."""
    counter: Counter[str] = Counter()
    for job in jobs:
        for skill in job.required_skills + job.preferred_skills + job.keywords:
            counter[skill.lower()] += 1
    return dict(counter.most_common(50))


def build_gap_report(role_query: str, jobs: list[JobPosting], inventory: ResumeInventory) -> GapReport:
    inventory_skills = _inventory_skill_set(inventory)
    bullet_skills = _bullet_skill_set(inventory)
    skill_freq = aggregate_job_skills(jobs)

    # Skills that appear in jobs but not in inventory at all
    missing = [
        skill
        for skill, count in skill_freq.items()
        if skill not in inventory_skills and count >= 2
    ]

    # Skills in inventory but not actually surfaced in any bullet
    underrepresented = [
        skill
        for skill in inventory_skills
        if skill not in bullet_skills and any(skill in jskill for jskill in skill_freq)
    ]

    return GapReport(
        role_query=role_query,
        jobs_analyzed=len(jobs),
        missing_skills=missing[:20],
        underrepresented_skills=underrepresented[:10],
        skill_frequency=skill_freq,
    )


def suggest_projects_via_claude(
    gap_report: GapReport,
    inventory: ResumeInventory,
    num_suggestions: int = 3,
) -> list[ProjectSuggestion]:
    """Call Claude to suggest concrete projects that close the identified skill gaps."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Build a compact profile summary for the prompt
    existing_projects = [
        f"{p.name} ({', '.join(p.tech_stack)})" for p in inventory.projects
    ]
    existing_skills = [
        f"{cat.name}: {', '.join(cat.items)}" for cat in inventory.skills
    ]
    experience_summary = [
        f"{r.company} ({r.start}–{r.end}): {', '.join(r.tech_stack)}" for r in inventory.roles
    ]

    prompt = f"""You are a senior software engineer and career advisor.

Here is the engineer's current profile:

**Experience:**
{chr(10).join(experience_summary)}

**Skills:**
{chr(10).join(existing_skills)}

**Existing Projects:**
{chr(10).join(existing_projects)}

**Target role:** {gap_report.role_query}

**Skill gap analysis from {gap_report.jobs_analyzed} real job postings:**
- Missing skills (in jobs but not in profile): {', '.join(gap_report.missing_skills[:15]) or 'none'}
- Underrepresented skills (in profile but not showcased): {', '.join(gap_report.underrepresented_skills[:8]) or 'none'}
- Most demanded skills: {', '.join(list(gap_report.skill_frequency.keys())[:10])}

Suggest exactly {num_suggestions} concrete projects this engineer should build to maximally close the gap and strengthen their resume for a **{gap_report.role_query}** role.

For each project, respond with valid JSON matching this schema exactly:
{{
  "suggestions": [
    {{
      "name": "Project Name",
      "description": "1-2 sentence project description",
      "tech_stack": ["Tech1", "Tech2"],
      "bullets": ["Resume bullet 1 with metric", "Resume bullet 2"],
      "gap_skills_covered": ["skill1", "skill2"],
      "rationale": "Why this project helps for the target role"
    }}
  ]
}}

Only respond with the JSON object, no markdown fences, no extra text."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    data = json.loads(raw)
    return [ProjectSuggestion(**item) for item in data["suggestions"]]


def render_gap_report_md(gap_report: GapReport, suggestions: list[ProjectSuggestion]) -> str:
    missing_lines = [f"- {s}" for s in gap_report.missing_skills] or ["- None identified"]
    underrepresented_lines = [f"- {s}" for s in gap_report.underrepresented_skills] or ["- None"]
    top_skill_lines = [f"- {skill}: {count}" for skill, count in list(gap_report.skill_frequency.items())[:15]]
    lines = [
        f"# Gap Analysis Report: {gap_report.role_query}",
        "",
        f"- **Jobs analyzed:** {gap_report.jobs_analyzed}",
        "",
        "## Missing Skills",
        *missing_lines,
        "",
        "## Underrepresented Skills",
        *underrepresented_lines,
        "",
        "## Top Demanded Skills (by frequency)",
        *top_skill_lines,
        "",
        "## Suggested Projects to Build",
        "",
    ]
    for i, suggestion in enumerate(suggestions, 1):
        lines += [
            f"### {i}. {suggestion.name}",
            f"**Tech:** {', '.join(suggestion.tech_stack)}",
            "",
            suggestion.description,
            "",
            "**Resume bullets:**",
            *(f"- {b}" for b in suggestion.bullets),
            "",
            f"**Closes gaps:** {', '.join(suggestion.gap_skills_covered)}",
            "",
            f"**Why:** {suggestion.rationale}",
            "",
        ]
    return "\n".join(lines)


def run_gap_analysis(
    role_query: str,
    job_urls: list[str],
    fetch_mode: FetchMode = FetchMode.AUTO,
    num_suggestions: int = 3,
) -> tuple[GapReport, list[ProjectSuggestion]]:
    inventory = load_resume_inventory()
    jobs: list[JobPosting] = []

    for url in job_urls:
        try:
            job = fetch_job_posting(url, fetch_mode)
            jobs.append(job)
            print(f"  parsed: {job.title} @ {job.company or 'unknown'}")
        except Exception as exc:
            print(f"  skipped {url}: {exc}")

    gap_report = build_gap_report(role_query, jobs, inventory)
    suggestions = suggest_projects_via_claude(gap_report, inventory, num_suggestions)
    return gap_report, suggestions
