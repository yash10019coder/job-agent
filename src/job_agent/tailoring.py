from __future__ import annotations

from pathlib import Path

from scrapling import Selector

from job_agent.ats import build_ats_analysis
from job_agent.inventory import load_resume_inventory
from job_agent.latex import build_resume_variant, compile_tex_to_pdf, render_resume_tex, slugify
from job_agent.models import FetchMode, JobPosting, Lane, TailoringDecision, TailoringReport
from job_agent.parsing import parse_job
from job_agent.scraping import ScraplingClient


# Semantic aliases for fuzzy matching
_SKILL_ALIASES: dict[str, set[str]] = {
    "cloud": {"azure", "aws", "gcp", "cloud", "cloud-native", "azure cloud", "aws cloud"},
    "frontend": {"react", "next.js", "angular", "vue", "javascript", "typescript", "frontend", "front-end", "ui"},
    "devops": {"docker", "kubernetes", "k8s", "ci/cd", "jenkins", "terraform", "devops", "cicd"},
    "ml": {"machine learning", "deep learning", "tensorflow", "pytorch", "ml", "ai", "llm"},
    "api": {"rest", "restful", "graphql", "grpc", "websockets", "api", "apis"},
    "database": {"sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "postgres"},
    "mobile": {"android", "ios", "react native", "flutter", "mobile"},
}
# Build reverse lookup
_SKILL_TO_ALIASES: dict[str, set[str]] = {}
for alias, variants in _SKILL_ALIASES.items():
    for variant in variants:
        _SKILL_TO_ALIASES.setdefault(variant, set()).add(alias)
        _SKILL_TO_ALIASES.setdefault(alias, set()).add(alias)


def _expand_skill_aliases(skills: set[str]) -> set[str]:
    """Expand skills with their aliases for fuzzy matching."""
    expanded = set(skills)
    for skill in list(skills):
        skill_lower = skill.lower()
        if skill_lower in _SKILL_TO_ALIASES:
            expanded.update(_SKILL_TO_ALIASES[skill_lower])
    return expanded


def _inventory_skill_set(inventory) -> set[str]:
    skills = set()
    for category in inventory.skills:
        skills.update(item.lower() for item in category.items)
    for role in inventory.roles:
        for bullet in role.bullets:
            skills.update(skill.lower() for skill in bullet.skills)
    # Expand with aliases
    return _expand_skill_aliases(skills)


def choose_lane(job: JobPosting) -> Lane | None:
    if not job.lane_score:
        return None
    ranked = sorted(job.lane_score.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < 2:
        return None
    return ranked[0][0]


def _count_metrics(bullets: list) -> int:
    """Count bullets that have metrics (numbers, percentages)"""
    count = 0
    for bullet in bullets:
        if bullet.metrics and len(bullet.metrics) > 0:
            count += 1
        # Also check for numbers in text
        import re
        if re.search(r'\d+', bullet.text):
            count += 0.5
    return count


def _calculate_content_quality_score(inventory, lane: Lane | None) -> float:
    """
    Calculate content quality score based on ResumeWorded-like metrics:
    - Metrics presence (30%): Number of quantified achievements
    - Action verbs variety (20%): Use of strong action verbs
    - Skills coverage (25%): Diversity of skills mentioned
    - Experience depth (25%): Bullet points with substance
    """
    if not lane:
        return 50.0
    
    score = 0.0
    all_bullets = []
    
    # Collect all bullets from roles and projects for this lane
    for role in inventory.roles:
        for bullet in role.bullets:
            if lane.value in bullet.lane_tags:
                all_bullets.append(bullet)
    
    for project in inventory.projects:
        if lane.value in [tag for tag in project.bullets[0].lane_tags] if project.bullets else []:
            for bullet in project.bullets:
                if lane.value in bullet.lane_tags:
                    all_bullets.append(bullet)
    
    # Metrics score (30%)
    metrics_count = _count_metrics(all_bullets)
    max_expected_metrics = 10
    metrics_score = min(metrics_count / max_expected_metrics, 1.0) * 30
    
    # Action verbs score (20%)
    strong_action_verbs = [
        'architected', 'engineered', 'designed', 'implemented', 'optimized',
        'accelerated', 'improved', 'increased', 'reduced', 'achieved',
        'delivered', 'built', 'created', 'developed', 'launched', 'led'
    ]
    weak_action_verbs = ['worked', 'helped', 'contributed', 'participated', 'assisted']
    
    action_verb_score = 0.0
    for bullet in all_bullets:
        text_lower = bullet.text.lower()
        has_strong = any(verb in text_lower for verb in strong_action_verbs)
        has_weak = any(verb in text_lower for verb in weak_action_verbs)
        if has_strong and not has_weak:
            action_verb_score += 2.0
        elif has_strong:
            action_verb_score += 1.0
    
    action_verb_score = min(action_verb_score / len(all_bullets) * 10, 20.0) if all_bullets else 10.0
    
    # Skills coverage (25%)
    all_skills = set()
    for bullet in all_bullets:
        all_skills.update(skill.lower() for skill in bullet.skills)
    skill_score = min(len(all_skills) / 15, 1.0) * 25  # Expect at least 15 diverse skills
    
    # Experience depth (25%) - based on bullet count and substance
    depth_score = min(len(all_bullets) / 12, 1.0) * 25  # Expect ~12 quality bullets
    
    return round(metrics_score + action_verb_score + skill_score + depth_score, 1)


def _word_boundary_match(text: str, keyword: str) -> bool:
    """Check if keyword exists as whole word (case-insensitive)."""
    import re
    pattern = r'\b' + re.escape(keyword) + r'\b'
    return bool(re.search(pattern, text, re.IGNORECASE))


def _strict_keyword_match(text: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    """Return (matched, missing) using word-boundary matching."""
    matched, missing = [], []
    for kw in keywords:
        kw_lower = kw.lower()
        if _word_boundary_match(text, kw_lower):
            matched.append(kw_lower)
        else:
            missing.append(kw_lower)
    return matched, missing


def build_decision(job: JobPosting) -> TailoringDecision:
    import re
    
    inventory = load_resume_inventory()
    known_skills = _inventory_skill_set(inventory)
    lane = choose_lane(job)
    fit_score = 0.0
    if lane:
        fit_score = job.lane_score[lane]
    
    # Combine all JD text for matching
    jd_text = " ".join([
        job.raw_description,
        " ".join(job.required_skills),
        " ".join(job.preferred_skills),
        " ".join(job.keywords)
    ]).lower()
    
    # Match JD keywords against resume's known skills
    all_jd_keywords = job.required_skills + job.preferred_skills + job.keywords
    matched_keywords = [kw.lower() for kw in all_jd_keywords if kw.lower() in known_skills]
    missing_keywords = [kw.lower() for kw in all_jd_keywords if kw.lower() not in known_skills]
    
    # Required skills: how many are in resume?
    matched_required = [kw.lower() for kw in job.required_skills if kw.lower() in known_skills]
    missing_required = [kw.lower() for kw in job.required_skills if kw.lower() not in known_skills]
    
    # Preferred skills: how many are in resume?
    matched_preferred = [kw.lower() for kw in job.preferred_skills if kw.lower() in known_skills]
    missing_preferred = [kw.lower() for kw in job.preferred_skills if kw.lower() not in known_skills]
    
    # Calculate match ratios
    required_match_ratio = 1.0 if not job.required_skills else len(matched_required) / len(job.required_skills)
    preferred_match_ratio = 1.0 if not job.preferred_skills else len(matched_preferred) / len(job.preferred_skills)
    
    # Content quality score
    content_quality = _calculate_content_quality_score(inventory, lane)
    
    # STRICT ATS SCORING - Required skills are everything
    # No lane = no content/lane bonuses = lower score
    
    # Base score from required skills (50% - primary factor)
    required_score = required_match_ratio * 0.50
    
    # Preferred skills bonus (20%)
    preferred_score = preferred_match_ratio * 0.20
    
    # Keyword coverage (max 10%)
    keyword_score = min(len(matched_keywords), 5) * 0.02
    
    # Lane bonuses (max 20%) - only if lane detected
    lane_score = min(fit_score / 10, 1.0) * 0.10 if lane else 0.0
    content_score = content_quality / 100 * 0.10 if lane else 0.0
    
    raw_score = required_score + preferred_score + keyword_score + lane_score + content_score
    
    ats_score = round(min(raw_score * 100, 100.0), 1)
    
    # HARD CAPS — prevent score inflation for bad matches
    required_count = len(job.required_skills)
    if required_count > 0:
        missing_ratio = len(missing_required) / required_count
        if missing_ratio >= 0.5:  # Missing 50%+ required = capped at 45
            ats_score = min(ats_score, 45.0)
        elif missing_ratio >= 0.3:  # Missing 30%+ required = capped at 60
            ats_score = min(ats_score, 60.0)
    
    # RISK FLAGS
    risk_flags: list[str] = []
    missing_requirements = missing_required
    missing_preferred_list = missing_preferred
    
    if missing_required:
        risk_flags.append(f"Missing {len(missing_required)} required: {', '.join(missing_required[:3])}")
    if missing_preferred:
        risk_flags.append(f"Missing {len(missing_preferred)} preferred")
    if lane is None:
        risk_flags.append("Unable to classify job lane")
    
    # RECOMMENDATION — based on strict thresholds
    final_recommendation = "apply"
    reject_reason = None
    
    if ats_score < 40:
        final_recommendation = "skip"
        reject_reason = f"ATS score {ats_score} below threshold"
    elif ats_score < 55:
        final_recommendation = "review"
    elif missing_ratio >= 0.3 if required_count > 0 else False:
        final_recommendation = "review"
        reject_reason = "Too many missing required skills"
    elif lane is None or fit_score < 2:
        final_recommendation = "skip"
        reject_reason = "Low lane confidence"
    
    # SELECT BULLETS
    selected_role_bullets: list[str] = []
    selected_projects: list[str] = []
    selected_achievements: list[str] = []
    metric_gaps: list[str] = []
    
    if lane:
        profile = inventory.lane_profiles[lane]
        role_limits = {"samsung": 4 if lane is Lane.CXX else 3, "rupeek": 1 if lane is Lane.CXX else 2}
        matched_skill_set = {keyword.lower() for keyword in all_jd_keywords}
        roles = {role.id: role for role in inventory.roles}
        for role_id in profile.role_order:
            role = roles[role_id]
            ranked_bullets = sorted(
                role.bullets,
                key=lambda bullet: (
                    lane.value in bullet.lane_tags,
                    len({skill.lower() for skill in bullet.skills} & matched_skill_set),
                    bullet.evidence_strength.value == "high",
                    bool(bullet.metrics),
                ),
                reverse=True,
            )
            chosen_bullets = ranked_bullets[: role_limits.get(role_id, 2)]
            selected_role_bullets.extend(bullet.id for bullet in chosen_bullets)
            metric_gaps.extend(
                bullet.text for bullet in chosen_bullets if not bullet.metrics and lane.value in bullet.lane_tags
            )
        selected_projects = profile.preferred_projects[: 1 if lane is Lane.CXX else 2]
        selected_achievements = profile.preferred_achievements[:2]
    
    return TailoringDecision(
        chosen_lane=lane,
        fit_score=round(fit_score, 2),
        ats_score=ats_score,
        reject_reason=reject_reason,
        selected_role_bullet_ids=selected_role_bullets,
        selected_project_ids=selected_projects,
        selected_achievement_ids=selected_achievements,
        matched_keywords=sorted(matched_keywords),
        missing_requirements=sorted(missing_requirements),
        missing_preferred=sorted(missing_preferred_list),
        metric_gaps=metric_gaps[:3],
        risk_flags=risk_flags,
        required_skill_match_ratio=round(required_match_ratio, 2),
        preferred_skill_match_ratio=round(preferred_match_ratio, 2),
        final_recommendation=final_recommendation,
    )


def render_tailored_resume(job: JobPosting, decision: TailoringDecision, output_dir: Path) -> tuple[Path, Path | None]:
    inventory = load_resume_inventory()
    lane = decision.chosen_lane or Lane.FULLSTACK_BACKEND
    variant = build_resume_variant(inventory, lane, job=job, decision=decision)
    job_dir = output_dir / slugify(job.company or "company") / slugify(job.title)
    tex_path = render_resume_tex(variant, job_dir)
    pdf_path = None
    try:
        pdf_path = compile_tex_to_pdf(tex_path, job_dir)
    except Exception:  # pragma: no cover - environment dependent
        pdf_path = None
    return tex_path, pdf_path


def report_markdown(report: TailoringReport) -> str:
    company = report.job.company or "Unknown company"
    lines = [
        f"# Tailoring Report: {company} - {report.job.title}",
        "",
        f"- URL: {report.job.url}",
        f"- Source: {report.job.source.value}",
        f"- Chosen lane: {report.decision.chosen_lane.value if report.decision.chosen_lane else 'none'}",
        f"- Fit score: {report.decision.fit_score}",
        f"- ATS score: {report.decision.ats_score}",
        f"- Recommendation: {report.decision.final_recommendation}",
        "",
        "## Matched keywords",
        ", ".join(report.decision.matched_keywords) or "None",
        "",
        "## Missing requirements",
        "\n".join(f"- {item}" for item in report.decision.missing_requirements) or "None",
        "",
        "## Missing preferred",
        "\n".join(f"- {item}" for item in report.decision.missing_preferred) or "None",
        "",
        "## Match ratios",
        f"- Required skills matched: {report.decision.required_skill_match_ratio:.0%}",
        f"- Preferred skills matched: {report.decision.preferred_skill_match_ratio:.0%}",
        "",
        "## Metric gaps",
        "\n".join(f"- {item}" for item in report.decision.metric_gaps) or "None",
        "",
        "## Risk flags",
        "\n".join(f"- {item}" for item in report.decision.risk_flags) or "None",
    ]
    if report.ats_analysis:
        lines += [
            "",
            "## ATS Analysis",
            f"- Top JD keywords: {', '.join(report.ats_analysis.top_keywords) or 'None'}",
            f"- Matched priority keywords: {', '.join(report.ats_analysis.matched_priority_keywords) or 'None'}",
            f"- Missing priority keywords: {', '.join(report.ats_analysis.missing_priority_keywords) or 'None'}",
            f"- Suggested skills to surface: {', '.join(report.ats_analysis.skills_to_surface) or 'None'}",
            f"- Focus roles: {', '.join(report.ats_analysis.role_focus) or 'None'}",
            f"- Focus projects: {', '.join(report.ats_analysis.project_focus) or 'None'}",
            f"- ATS recommendation: {report.ats_analysis.summary_recommendation}",
        ]
    return "\n".join(lines)


def fetch_job_posting(url: str, mode: FetchMode) -> JobPosting:
    client = ScraplingClient()
    fetched = client.fetch(url, mode=mode)
    selector = Selector(fetched.html, url=fetched.url)
    return parse_job(selector, fetched.url)


def analyze_job_posting(job: JobPosting, output_dir: Path, use_agents: bool = False) -> TailoringReport:
    decision = build_decision(job)
    tex_path, pdf_path = render_tailored_resume(job, decision, output_dir)
    ats_analysis = build_ats_analysis(job, decision)
    report = TailoringReport(
        job=job,
        decision=decision,
        tailored_resume_path=tex_path,
        tailored_pdf_path=pdf_path,
        agent_analysis=None,
        ats_analysis=ats_analysis,
    )
    report_path = tex_path.with_name("tailoring_report.md")
    report_path.write_text(report_markdown(report), encoding="utf-8")
    return report


def analyze_job_url(url: str, mode: FetchMode, output_dir: Path, use_agents: bool = False) -> TailoringReport:
    job = fetch_job_posting(url, mode)
    return analyze_job_posting(job, output_dir, use_agents=False)
