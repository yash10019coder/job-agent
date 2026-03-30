from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys

from job_agent.discovery import render_discovery_report_md
from job_agent.models import (
    DiscoveredJob,
    FetchMode,
    JobPosting,
    Lane,
    SearchJobResult,
    SearchJobsReport,
    SourceType,
)
from job_agent.tailoring import analyze_job_posting, analyze_job_url, build_decision, fetch_job_posting
from job_agent.tracker import upsert_report


def _load_jobspy():
    try:
        module = import_module("jobspy")
    except ModuleNotFoundError as error:  # pragma: no cover - depends on local install
        hint = ""
        if sys.version_info >= (3, 13):
            hint = " Your current Python is 3.13; JobSpy may install more reliably in a Python 3.11/3.12 environment."
        raise RuntimeError(
            "JobSpy is not installed. Run `.\\.venv\\Scripts\\python.exe -m pip install python-jobspy` "
            "or install the optional dependency before using `search-jobs`."
            + hint
        ) from error
    if not hasattr(module, "scrape_jobs"):
        raise RuntimeError("Installed jobspy package does not expose `scrape_jobs`.")
    return module.scrape_jobs


def _coalesce(row: dict, *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() != "nan":
            return text
    return None


def _normalize_jobspy_result(row: dict) -> SearchJobResult:
    return SearchJobResult(
        title=_coalesce(row, "title", "job_title") or "Unknown role",
        company=_coalesce(row, "company", "company_name"),
        location=_coalesce(row, "location"),
        url=_coalesce(row, "job_url", "url", "job_url_direct") or "",
        description=_coalesce(row, "description", "job_description") or "",
        source_site=_coalesce(row, "site", "site_name") or "jobspy",
        job_type=_coalesce(row, "job_type"),
        interval=_coalesce(row, "interval"),
        min_amount=_to_float(row.get("min_amount")),
        max_amount=_to_float(row.get("max_amount")),
    )


def _to_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _job_posting_from_search_result(result: SearchJobResult) -> JobPosting:
    return JobPosting(
        url=result.url,
        source=SourceType.COMPANY,
        title=result.title,
        company=result.company,
        location=result.location,
        raw_description=result.description,
    )


def search_jobs(
    search_term: str,
    location: str,
    sites: list[str],
    results_wanted: int,
    output_dir: Path,
    lane_filter: Lane | None = None,
    analyze: bool = False,
    use_agents: bool = False,
    track: bool = False,
    tracker_path: Path | None = None,
) -> SearchJobsReport:
    scrape_jobs = _load_jobspy()
    frame = scrape_jobs(
        site_name=sites,
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
    )
    rows = frame.to_dict("records")
    results = [_normalize_jobspy_result(row) for row in rows if _coalesce(row, "job_url", "url", "job_url_direct")]
    report = SearchJobsReport(
        search_term=search_term,
        location=location,
        sites=sites,
        fetch_mode=FetchMode.AUTO,
        results=results,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    discovered: list[DiscoveredJob] = []
    skipped_closed = 0
    for result in results:
        job = _job_posting_from_search_result(result)
        # Force live fetch to accurately parse `is_active` status from the DOM
        try:
            job = fetch_job_posting(result.url, FetchMode.AUTO)
        except Exception:
            pass
        
        # Skip closed/inactive jobs
        if not job.is_active:
            skipped_closed += 1
            continue
        
        decision = build_decision(job)
        if lane_filter and decision.chosen_lane is not lane_filter:
            continue
        if analyze:
            if job.raw_description.strip():
                tailoring_report = analyze_job_posting(job, output_dir, use_agents=use_agents)
            else:
                tailoring_report = analyze_job_url(result.url, FetchMode.AUTO, output_dir, use_agents=use_agents)
            if track:
                upsert_report(tailoring_report, path=tracker_path or Path("outputs/tracker/applications.json"))
            report_path = tailoring_report.tailored_resume_path
        else:
            report_path = None
        discovered.append(
            DiscoveredJob(
                url=result.url,
                title=result.title,
                company=result.company,
                source=job.source,
                chosen_lane=decision.chosen_lane,
                fit_score=decision.fit_score,
                recommendation=decision.final_recommendation,
                report_path=report_path,
            )
        )
    (output_dir / "jobspy_results.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    discovery_like = output_dir / "jobspy_discovery.md"
    discovery_like.write_text(
        render_discovery_report_md(
            type("DiscoveryShim", (), {"search_url": search_term, "fetch_mode": FetchMode.AUTO, "discovered_jobs": discovered})()
        ),
        encoding="utf-8",
    )
    return report
