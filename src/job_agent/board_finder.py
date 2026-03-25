from __future__ import annotations

from pathlib import Path

from job_agent.models import BoardDiscoveryReport, FetchMode
from job_agent.scraping import ScraplingClient


def render_board_report_md(report: BoardDiscoveryReport) -> str:
    lines = [
        "# Board Discovery Report",
        "",
        f"- Input URL: {report.input_url}",
        f"- Fetch mode: {report.fetch_mode.value}",
        f"- Candidates found: {len(report.candidates)}",
        "",
        "## Candidates",
    ]
    for candidate in report.candidates:
        lines += [
            f"- {candidate.url}",
            f"  Source: {candidate.source}",
            f"  Supported: {'yes' if candidate.supported else 'not yet'}",
            f"  Confidence: {candidate.confidence}",
            f"  Reason: {candidate.reason}",
        ]
    return "\n".join(lines)


def find_boards(url: str, mode: FetchMode, output_dir: Path) -> BoardDiscoveryReport:
    scraper = ScraplingClient()
    report = scraper.discover_board_candidates(url, mode=mode)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "board_candidates.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "board_candidates.md").write_text(render_board_report_md(report), encoding="utf-8")
    return report
