from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Lane(str, Enum):
    FULLSTACK_BACKEND = "fullstack_backend"


class EvidenceStrength(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FetchMode(str, Enum):
    AUTO = "auto"
    STATIC = "static"
    STEALTH = "stealth"
    DYNAMIC = "dynamic"


class SourceType(str, Enum):
    COMPANY = "company"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"


class Contact(BaseModel):
    name: str
    email: str
    phone: str
    linkedin: HttpUrl
    github: HttpUrl
    location: str


class LaneProfile(BaseModel):
    lane: Lane
    title: str
    headline: str
    summary: str
    role_order: list[str]
    preferred_bullets: list[str] = Field(default_factory=list)
    preferred_projects: list[str]
    preferred_achievements: list[str]
    skills_priority: list[str]


class Bullet(BaseModel):
    id: str
    text: str
    lane_tags: list[Lane]
    skills: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    evidence_strength: EvidenceStrength
    project_or_role_source: str


class RoleEntry(BaseModel):
    id: str
    company: str
    title: str
    location: str
    start: str
    end: str
    tech_stack: list[str] = Field(default_factory=list)
    bullets: list[Bullet]


class ProjectEntry(BaseModel):
    id: str
    name: str
    url: HttpUrl
    tech_stack: list[str]
    bullets: list[Bullet]


class AchievementEntry(BaseModel):
    id: str
    title: str
    detail: str
    lane_tags: list[Lane]
    link: HttpUrl | None = None


class EducationEntry(BaseModel):
    school: str
    degree: str
    location: str
    start: str
    end: str
    cgpa: str


class SkillCategory(BaseModel):
    name: str
    items: list[str]


class ResumeInventory(BaseModel):
    contact: Contact
    lane_profiles: dict[Lane, LaneProfile]
    education: EducationEntry
    skills: list[SkillCategory]
    roles: list[RoleEntry]
    projects: list[ProjectEntry]
    achievements: list[AchievementEntry]


class ResumeVariant(BaseModel):
    lane: Lane
    title: str
    headline: str
    summary: str
    contact: Contact
    skills: list[SkillCategory]
    roles: list[RoleEntry]
    projects: list[ProjectEntry]
    achievements: list[AchievementEntry]
    education: EducationEntry


class JobPosting(BaseModel):
    url: str
    source: SourceType
    title: str
    company: str | None = None
    location: str | None = None
    raw_description: str
    is_active: bool = True  # True if job is still accepting applications
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    seniority: str | None = None
    lane_score: dict[Lane, float] = Field(default_factory=dict)


class TailoringDecision(BaseModel):
    chosen_lane: Lane | None
    fit_score: float
    ats_score: float = 0.0
    reject_reason: str | None = None
    selected_role_bullet_ids: list[str] = Field(default_factory=list)
    selected_project_ids: list[str] = Field(default_factory=list)
    selected_achievement_ids: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    missing_preferred: list[str] = Field(default_factory=list)
    metric_gaps: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    required_skill_match_ratio: float = 0.0
    preferred_skill_match_ratio: float = 0.0
    final_recommendation: Literal["apply", "review", "skip"]


class AgentAnalysis(BaseModel):
    research_summary: str
    chosen_lane: Lane | None = None
    lane_rationale: str
    keyword_strategy: list[str] = Field(default_factory=list)
    reviewer_flags: list[str] = Field(default_factory=list)
    source: Literal["heuristic", "anthropic"]


class AtsAnalysis(BaseModel):
    top_keywords: list[str] = Field(default_factory=list)
    matched_priority_keywords: list[str] = Field(default_factory=list)
    missing_priority_keywords: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    role_focus: list[str] = Field(default_factory=list)
    project_focus: list[str] = Field(default_factory=list)
    skills_to_surface: list[str] = Field(default_factory=list)
    summary_recommendation: str


class TailoringReport(BaseModel):
    job: JobPosting
    decision: TailoringDecision
    tailored_resume_path: Path | None = None
    tailored_pdf_path: Path | None = None
    agent_analysis: AgentAnalysis | None = None
    ats_analysis: AtsAnalysis | None = None


class GapReport(BaseModel):
    role_query: str
    jobs_analyzed: int
    missing_skills: list[str]
    underrepresented_skills: list[str]
    skill_frequency: dict[str, int] = Field(default_factory=dict)


class ProjectSuggestion(BaseModel):
    name: str
    description: str
    tech_stack: list[str]
    bullets: list[str]
    gap_skills_covered: list[str]
    rationale: str


class DiscoveredJob(BaseModel):
    url: str
    title: str | None = None
    company: str | None = None
    source: SourceType | None = None
    chosen_lane: Lane | None = None
    fit_score: float | None = None
    recommendation: Literal["apply", "review", "skip"] | None = None
    report_path: Path | None = None


class DiscoveryReport(BaseModel):
    search_url: str
    fetch_mode: FetchMode
    discovered_jobs: list[DiscoveredJob] = Field(default_factory=list)


class BoardCandidate(BaseModel):
    url: str
    source: str
    confidence: float
    reason: str
    supported: bool = True


class BoardDiscoveryReport(BaseModel):
    input_url: str
    fetch_mode: FetchMode
    candidates: list[BoardCandidate] = Field(default_factory=list)


class ApplicationStatus(str, Enum):
    DISCOVERED = "discovered"
    REVIEW = "review"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class TrackerEvent(BaseModel):
    timestamp: str
    status: ApplicationStatus
    note: str = ""


class ApplicationRecord(BaseModel):
    id: str
    company: str
    title: str
    url: str
    lane: Lane | None = None
    status: ApplicationStatus
    fit_score: float | None = None
    ats_score: float | None = None
    source: SourceType | None = None
    notes: str = ""
    next_follow_up: str | None = None
    tailored_resume_path: Path | None = None
    tailored_pdf_path: Path | None = None
    created_at: str
    updated_at: str
    history: list[TrackerEvent] = Field(default_factory=list)


class ApplicationTracker(BaseModel):
    applications: list[ApplicationRecord] = Field(default_factory=list)


class TrackerSummary(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    by_lane: dict[str, int] = Field(default_factory=dict)
    attention_needed: list[ApplicationRecord] = Field(default_factory=list)


class SearchJobResult(BaseModel):
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    description: str = ""
    source_site: str
    job_type: str | None = None
    interval: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None


class SearchJobsReport(BaseModel):
    search_term: str
    location: str | None = None
    sites: list[str] = Field(default_factory=list)
    fetch_mode: FetchMode | None = None
    results: list[SearchJobResult] = Field(default_factory=list)
