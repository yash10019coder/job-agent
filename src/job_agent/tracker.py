from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from job_agent.models import (
    ApplicationRecord,
    ApplicationStatus,
    ApplicationTracker,
    TailoringReport,
    TrackerEvent,
    TrackerSummary,
)
from job_agent.latex import slugify


DEFAULT_TRACKER_PATH = Path("outputs") / "tracker" / "applications.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_event(record: ApplicationRecord, status: ApplicationStatus, note: str = "") -> None:
    record.history.append(TrackerEvent(timestamp=_now(), status=status, note=note))


def load_tracker(path: Path = DEFAULT_TRACKER_PATH) -> ApplicationTracker:
    if not path.exists():
        return ApplicationTracker()
    return ApplicationTracker.model_validate_json(path.read_text(encoding="utf-8"))


def save_tracker(tracker: ApplicationTracker, path: Path = DEFAULT_TRACKER_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tracker.model_dump_json(indent=2), encoding="utf-8")
    return path


def record_id(company: str, title: str) -> str:
    return f"{slugify(company)}-{slugify(title)}"


def recommendation_to_status(recommendation: str) -> ApplicationStatus:
    if recommendation == "apply":
        return ApplicationStatus.DISCOVERED
    if recommendation == "review":
        return ApplicationStatus.REVIEW
    return ApplicationStatus.SKIPPED


def upsert_report(
    report: TailoringReport,
    path: Path = DEFAULT_TRACKER_PATH,
    status: ApplicationStatus | None = None,
    notes: str = "",
) -> tuple[ApplicationTracker, ApplicationRecord, Path]:
    tracker = load_tracker(path)
    identifier = record_id(report.job.company or "company", report.job.title)
    existing = next((item for item in tracker.applications if item.id == identifier), None)
    resolved_status = status or recommendation_to_status(report.decision.final_recommendation)
    now = _now()
    if existing:
        existing.url = report.job.url
        existing.company = report.job.company or existing.company
        existing.title = report.job.title
        existing.lane = report.decision.chosen_lane
        existing.status = resolved_status
        existing.fit_score = report.decision.fit_score
        existing.ats_score = report.decision.ats_score
        existing.source = report.job.source
        existing.notes = notes or existing.notes
        if existing.status != resolved_status or notes:
            _append_event(existing, resolved_status, notes)
        existing.tailored_resume_path = report.tailored_resume_path
        existing.tailored_pdf_path = report.tailored_pdf_path
        existing.updated_at = now
        record = existing
    else:
        record = ApplicationRecord(
            id=identifier,
            company=report.job.company or "Unknown company",
            title=report.job.title,
            url=report.job.url,
            lane=report.decision.chosen_lane,
            status=resolved_status,
            fit_score=report.decision.fit_score,
            ats_score=report.decision.ats_score,
            source=report.job.source,
            notes=notes,
            tailored_resume_path=report.tailored_resume_path,
            tailored_pdf_path=report.tailored_pdf_path,
            created_at=now,
            updated_at=now,
        )
        _append_event(record, resolved_status, notes)
        tracker.applications.append(record)
    saved = save_tracker(tracker, path)
    return tracker, record, saved


def update_application_status(
    company: str,
    title: str,
    status: ApplicationStatus,
    path: Path = DEFAULT_TRACKER_PATH,
    notes: str = "",
    next_follow_up: str = "",
) -> tuple[ApplicationTracker, ApplicationRecord, Path]:
    tracker = load_tracker(path)
    identifier = record_id(company, title)
    record = next((item for item in tracker.applications if item.id == identifier), None)
    if record is None:
        raise ValueError(f"No tracked application found for {company} - {title}")
    record.status = status
    if notes:
        record.notes = notes
    if next_follow_up:
        record.next_follow_up = next_follow_up
    _append_event(record, status, notes)
    record.updated_at = _now()
    saved = save_tracker(tracker, path)
    return tracker, record, saved


def list_applications(
    path: Path = DEFAULT_TRACKER_PATH,
    status: ApplicationStatus | None = None,
    lane: str | None = None,
) -> list[ApplicationRecord]:
    tracker = load_tracker(path)
    applications = tracker.applications
    if status:
        applications = [item for item in applications if item.status is status]
    if lane:
        applications = [item for item in applications if item.lane and item.lane.value == lane]
    return sorted(applications, key=lambda item: item.updated_at, reverse=True)


def summarize_tracker(path: Path = DEFAULT_TRACKER_PATH) -> TrackerSummary:
    tracker = load_tracker(path)
    by_status: dict[str, int] = {}
    by_lane: dict[str, int] = {}
    attention_needed: list[ApplicationRecord] = []
    for item in tracker.applications:
        by_status[item.status.value] = by_status.get(item.status.value, 0) + 1
        lane_key = item.lane.value if item.lane else "unknown"
        by_lane[lane_key] = by_lane.get(lane_key, 0) + 1
        if item.status in {ApplicationStatus.REVIEW, ApplicationStatus.INTERVIEWING} or item.next_follow_up:
            attention_needed.append(item)
    attention_needed = sorted(attention_needed, key=lambda item: item.updated_at, reverse=True)[:10]
    return TrackerSummary(
        total=len(tracker.applications),
        by_status=by_status,
        by_lane=by_lane,
        attention_needed=attention_needed,
    )


def export_tracker_csv(path: Path = DEFAULT_TRACKER_PATH, output_path: Path | None = None) -> Path:
    tracker = load_tracker(path)
    target = output_path or path.with_suffix(".csv")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "company",
                "title",
                "status",
                "lane",
                "fit_score",
                "ats_score",
                "url",
                "next_follow_up",
                "notes",
                "created_at",
                "updated_at",
            ],
        )
        writer.writeheader()
        for item in tracker.applications:
            writer.writerow(
                {
                    "company": item.company,
                    "title": item.title,
                    "status": item.status.value,
                    "lane": item.lane.value if item.lane else "",
                    "fit_score": item.fit_score or "",
                    "ats_score": item.ats_score or "",
                    "url": item.url,
                    "next_follow_up": item.next_follow_up or "",
                    "notes": item.notes,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
    return target
