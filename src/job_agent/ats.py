from __future__ import annotations

from job_agent.inventory import load_resume_inventory
from job_agent.models import AtsAnalysis, JobPosting, TailoringDecision


def _inventory_terms() -> tuple[set[str], dict[str, str], dict[str, str]]:
    inventory = load_resume_inventory()
    known_terms: set[str] = set()
    role_map: dict[str, str] = {}
    project_map: dict[str, str] = {}
    for category in inventory.skills:
        known_terms.update(item.lower() for item in category.items)
    for role in inventory.roles:
        role_map[role.id] = f"{role.company} - {role.title}"
        known_terms.update(stack.lower() for stack in role.tech_stack)
        for bullet in role.bullets:
            known_terms.update(skill.lower() for skill in bullet.skills)
    for project in inventory.projects:
        project_map[project.id] = project.name
        known_terms.update(stack.lower() for stack in project.tech_stack)
        for bullet in project.bullets:
            known_terms.update(skill.lower() for skill in bullet.skills)
    return known_terms, role_map, project_map


def build_ats_analysis(job: JobPosting, decision: TailoringDecision) -> AtsAnalysis:
    known_terms, role_map, project_map = _inventory_terms()
    priority_keywords = []
    for keyword in job.required_skills + job.preferred_skills + job.keywords:
        normalized = keyword.lower()
        if normalized not in priority_keywords:
            priority_keywords.append(normalized)
    top_keywords = priority_keywords[:12]
    matched_priority = [keyword for keyword in top_keywords if keyword in known_terms]
    missing_priority = [keyword for keyword in top_keywords if keyword not in known_terms]
    role_focus = [role_map.get(role_id, role_id) for role_id in _selected_role_ids(decision.selected_role_bullet_ids)]
    project_focus = [project_map.get(project_id, project_id) for project_id in decision.selected_project_ids]
    skills_to_surface = matched_priority[:6]
    # New strict scoring breakdown (matching tailoring.py formula)
    score_breakdown = {
        "required_match": round(decision.required_skill_match_ratio * 100, 1),  # 45% weight
        "preferred_match": round(decision.preferred_skill_match_ratio * 100, 1),  # 15% weight
        "keyword_coverage": round(len(matched_priority) / max(len(top_keywords), 1) * 100, 1),  # 16% max
        "lane_fit": round(min(decision.fit_score, 10) / 10 * 100, 1),  # 10% max
        "content_quality": 50.0,  # Placeholder - calculated in tailoring.py
        "ats_score": decision.ats_score,
        "missing_critical": len(missing_priority),
    }
    lane_label = decision.chosen_lane.value if decision.chosen_lane else "best-fit"
    if missing_priority:
        recommendation = (
            f"Lead the {lane_label} resume with evidence for {', '.join(matched_priority[:3]) or 'core strengths'} "
            f"and manually review gaps around {', '.join(missing_priority[:3])}."
        )
    else:
        recommendation = (
            f"Lead the {lane_label} resume with evidence for {', '.join(matched_priority[:4]) or 'your strongest keywords'} "
            "and keep the top bullets tightly aligned to the JD language."
        )
    return AtsAnalysis(
        top_keywords=top_keywords,
        matched_priority_keywords=matched_priority,
        missing_priority_keywords=missing_priority,
        score_breakdown=score_breakdown,
        role_focus=role_focus,
        project_focus=project_focus,
        skills_to_surface=skills_to_surface,
        summary_recommendation=recommendation,
    )


def _selected_role_ids(selected_role_bullet_ids: list[str]) -> list[str]:
    role_ids: list[str] = []
    for bullet_id in selected_role_bullet_ids:
        role_id = bullet_id.split("_", 1)[0]
        if role_id not in role_ids:
            role_ids.append(role_id)
    return role_ids
