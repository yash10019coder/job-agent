from __future__ import annotations

import re
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from pypdf import PdfReader

from job_agent.inventory import load_resume_inventory
from job_agent.models import (
    AchievementEntry,
    Bullet,
    JobPosting,
    Lane,
    LaneProfile,
    ProjectEntry,
    ResumeInventory,
    ResumeVariant,
    RoleEntry,
    SkillCategory,
    TailoringDecision,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
RESUME_DIR = ROOT_DIR / "resume"
TECTONIC_BIN = ROOT_DIR / "tools" / "tectonic" / "tectonic.exe"


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "resume"


def _load_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(RESUME_DIR)),
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=True,
    )
    env.filters["latex"] = latex_escape
    return env


def _category_rank(profile: LaneProfile) -> dict[str, int]:
    return {name: index for index, name in enumerate(profile.skills_priority)}


def _score_bullet(
    bullet: Bullet,
    lane: Lane,
    matched_keywords: set[str] | None = None,
) -> float:
    score = 1.0
    if lane in bullet.lane_tags:
        score += 3.0
    score += {"high": 1.0, "medium": 0.5, "low": 0.0}[bullet.evidence_strength.value]
    if bullet.metrics:
        score += 0.25
    if matched_keywords:
        overlap = len({skill.lower() for skill in bullet.skills} & matched_keywords)
        score += overlap * 1.5
    return score


def _select_role_bullets(
    role: RoleEntry,
    lane: Lane,
    matched_keywords: set[str] | None = None,
    max_items: int = 3,
) -> RoleEntry:
    ranked = sorted(
        role.bullets,
        key=lambda bullet: _score_bullet(bullet, lane, matched_keywords),
        reverse=True,
    )
    selected = ranked[:max_items]
    return role.model_copy(update={"bullets": selected})


def _select_projects(
    inventory: ResumeInventory,
    lane: Lane,
    matched_keywords: set[str] | None = None,
    count: int = 3,
) -> list[ProjectEntry]:
    """Always return `count` (default 3) best projects — broad > lane-only."""
    profile = inventory.lane_profiles[lane]
    preferred_ids = set(profile.preferred_projects)
    ranked: list[tuple[float, ProjectEntry]] = []
    for project in inventory.projects:
        lane_bullets = [b for b in project.bullets if lane in b.lane_tags]
        all_bullets = lane_bullets or project.bullets
        if not all_bullets:
            continue
        base_score = 2.0 if project.id in preferred_ids else 0.0
        if lane_bullets:
            base_score += 1.0  # bonus for lane relevance, not hard filter
        for bullet in all_bullets[:3]:
            base_score += _score_bullet(bullet, lane, matched_keywords)
        ranked.append((base_score, project.model_copy(update={"bullets": all_bullets[:2]})))
    return [p for _, p in sorted(ranked, key=lambda x: x[0], reverse=True)[:count]]


def _select_achievements(inventory: ResumeInventory, lane: Lane) -> list[AchievementEntry]:
    profile = inventory.lane_profiles[lane]
    preferred_ids = set(profile.preferred_achievements)
    ranked = sorted(
        inventory.achievements,
        key=lambda achievement: (
            achievement.id not in preferred_ids,
            lane not in achievement.lane_tags,
        ),
    )
    return ranked[:2]


def _sort_skills(
    inventory: ResumeInventory,
    lane: Lane,
    matched_keywords: set[str] | None = None,
) -> list[SkillCategory]:
    profile = inventory.lane_profiles[lane]
    category_rank = _category_rank(profile)
    matched_keywords = matched_keywords or set()
    ordered_categories = sorted(
        inventory.skills,
        key=lambda category: category_rank.get(category.name, len(category_rank) + 1),
    )
    sorted_categories: list[SkillCategory] = []
    for category in ordered_categories:
        items = sorted(
            category.items,
            key=lambda item: (
                item.lower() not in matched_keywords,
                item.lower(),
            ),
        )
        sorted_categories.append(category.model_copy(update={"items": items}))
    return sorted_categories


def _matched_keywords_from_job(job: JobPosting | None) -> set[str]:
    if not job:
        return set()
    return {keyword.lower() for keyword in job.keywords + job.required_skills + job.preferred_skills}


def build_resume_variant(
    inventory: ResumeInventory,
    lane: Lane,
    job: JobPosting | None = None,
    decision: TailoringDecision | None = None,
) -> ResumeVariant:
    profile = inventory.lane_profiles[lane]
    matched_keywords = _matched_keywords_from_job(job)
    selected_roles: list[RoleEntry] = []
    role_lookup = {role.id: role for role in inventory.roles}
    role_limits = {"samsung": 4 if lane is Lane.CXX else 3, "rupeek": 1 if lane is Lane.CXX else 2}
    for role_id in profile.role_order:
        role = role_lookup[role_id]
        max_items = role_limits.get(role_id, 2)
        selected_role = _select_role_bullets(role, lane, matched_keywords, max_items=max_items)
        if decision and decision.selected_role_bullet_ids:
            allowed = set(decision.selected_role_bullet_ids)
            selected_role = selected_role.model_copy(
                update={"bullets": [bullet for bullet in selected_role.bullets if bullet.id in allowed]}
            )
        selected_roles.append(selected_role)
    projects = _select_projects(inventory, lane, matched_keywords)
    if decision and decision.selected_project_ids:
        allowed_projects = set(decision.selected_project_ids)
        projects = [project for project in projects if project.id in allowed_projects]
    achievements = _select_achievements(inventory, lane)
    if decision and decision.selected_achievement_ids:
        allowed_achievements = set(decision.selected_achievement_ids)
        achievements = [achievement for achievement in achievements if achievement.id in allowed_achievements]
    summary = profile.summary
    if job:
        summary = f"{profile.summary} Targeting {job.title} roles with emphasis on {', '.join(job.keywords[:3])}."
    return ResumeVariant(
        lane=lane,
        title=profile.title,
        headline=profile.headline,
        summary=summary,
        contact=inventory.contact,
        skills=_sort_skills(inventory, lane, matched_keywords),
        roles=selected_roles,
        projects=projects,
        achievements=achievements,
        education=inventory.education,
    )


def render_resume_tex(variant: ResumeVariant, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _load_env()
    template = env.get_template("template.tex.j2")
    filename = f"resume_{variant.lane.value}.tex"
    tex_path = output_dir / filename
    tex_path.write_text(template.render(variant=variant), encoding="utf-8")
    return tex_path


def compile_tex_to_pdf(tex_path: Path, output_dir: Path) -> Path:
    tex_path = tex_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{tex_path.stem}.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
    executable = str(TECTONIC_BIN if TECTONIC_BIN.exists() else "tectonic")
    subprocess.run(
        [executable, str(tex_path), "--outdir", str(output_dir)],
        cwd=str(ROOT_DIR),
        check=True,
        capture_output=True,
        text=True,
    )
    return pdf_path


def pdf_page_count(pdf_path: Path) -> int:
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


def generate_base_resumes(output_dir: Path) -> dict[Lane, tuple[Path, Path | None]]:
    inventory = load_resume_inventory()
    results: dict[Lane, tuple[Path, Path | None]] = {}
    for lane in Lane:
        variant = build_resume_variant(inventory, lane)
        tex_path = render_resume_tex(variant, output_dir)
        pdf_path: Path | None = None
        try:
            pdf_path = compile_tex_to_pdf(tex_path, output_dir)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pdf_path = None
        results[lane] = (tex_path, pdf_path)
    return results
