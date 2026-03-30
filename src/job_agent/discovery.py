from __future__ import annotations

import json
from pathlib import Path

from job_agent.models import DiscoveredJob, DiscoveryReport, FetchMode, Lane
from job_agent.scraping import ScraplingClient
from job_agent.tailoring import analyze_job_posting, build_decision, fetch_job_posting
from job_agent.tracker import upsert_report


def render_discovery_report_md(report: DiscoveryReport, skipped_closed: int = 0) -> str:
    lines = [
        f"# Discovery Report",
        "",
        f"- Search URL: {report.search_url}",
        f"- Fetch mode: {report.fetch_mode.value}",
        f"- Jobs discovered: {len(report.discovered_jobs)}",
    ]
    if skipped_closed > 0:
        lines.append(f"- Jobs skipped (closed/inactive): {skipped_closed}")
    lines.extend(["", "## Results"])
    for job in report.discovered_jobs:
        lines += [
            f"- {job.title or 'Unknown role'} @ {job.company or 'Unknown company'}",
            f"  URL: {job.url}",
            f"  Lane: {job.chosen_lane.value if job.chosen_lane else 'unknown'}",
            f"  Recommendation: {job.recommendation or 'n/a'}",
        ]
    return "\n".join(lines)


def discover_jobs(
    search_url: str,
    mode: FetchMode,
    output_dir: Path,
    limit: int = 25,
    lane_filter: Lane | None = None,
    analyze: bool = False,
    use_agents: bool = False,
    track: bool = False,
    tracker_path: Path | None = None,
    skip_closed: bool = True,
) -> DiscoveryReport:
    scraper = ScraplingClient()
    urls = scraper.discover_job_links(search_url, mode=mode, limit=limit)
    discovered: list[DiscoveredJob] = []
    skipped_closed = 0
    for url in urls:
        try:
            job = fetch_job_posting(url, mode)
            
            # Skip closed/inactive jobs
            if skip_closed and not job.is_active:
                skipped_closed += 1
                continue
            
            decision = build_decision(job)
            if lane_filter and decision.chosen_lane is not lane_filter:
                continue
            report_path = None
            if analyze:
                report = analyze_job_posting(job, output_dir, use_agents=use_agents)
                report_path = report.tailored_resume_path
                if track:
                    upsert_report(report, path=tracker_path or Path("outputs/tracker/applications.json"))
            discovered.append(
                DiscoveredJob(
                    url=url,
                    title=job.title,
                    company=job.company,
                    source=job.source,
                    chosen_lane=decision.chosen_lane,
                    fit_score=decision.fit_score,
                    recommendation=decision.final_recommendation,
                    report_path=report_path,
                )
            )
        except Exception:
            discovered.append(DiscoveredJob(url=url))

    report = DiscoveryReport(search_url=search_url, fetch_mode=mode, discovered_jobs=discovered)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "discovery_report.md").write_text(render_discovery_report_md(report), encoding="utf-8")
    (output_dir / "discovery_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
