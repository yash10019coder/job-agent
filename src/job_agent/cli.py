from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Fix Windows console Unicode (removes crash on arrow chars etc.)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from job_agent.board_finder import find_boards
from job_agent.discovery import discover_jobs
from job_agent.gap_analysis import render_gap_report_md, run_gap_analysis
from job_agent.jobspy_adapter import search_jobs
from job_agent.latex import generate_base_resumes
from job_agent.models import ApplicationStatus, FetchMode, Lane
from job_agent.tailoring import analyze_job_url
from job_agent.tracker import (
    export_tracker_csv,
    list_applications,
    summarize_tracker,
    update_application_status,
    upsert_report,
)


def _generate_resumes(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    results = generate_base_resumes(output_dir)
    payload = {
        lane.value: {
            "tex": str(paths[0]),
            "pdf": str(paths[1]) if paths[1] else None,
            "warning": None if paths[1] else "tectonic not found; TeX generated but PDF skipped.",
        }
        for lane, paths in results.items()
    }
    print(json.dumps(payload, indent=2))
    return 0


def _analyze_job(args: argparse.Namespace) -> int:
    report = analyze_job_url(args.url, FetchMode(args.mode), Path(args.output_dir), use_agents=args.use_agents)
    if args.track:
        upsert_report(
            report,
            path=Path(args.tracker_path),
            status=ApplicationStatus(args.status) if args.status else None,
            notes=args.notes,
        )
    print(report.model_dump_json(indent=2))
    return 0


def _discover_jobs(args: argparse.Namespace) -> int:
    lane_filter = Lane(args.lane) if args.lane else None
    report = discover_jobs(
        search_url=args.search_url,
        mode=FetchMode(args.mode),
        output_dir=Path(args.output_dir),
        limit=args.limit,
        lane_filter=lane_filter,
        analyze=args.analyze,
        use_agents=args.use_agents,
        track=args.track,
        tracker_path=Path(args.tracker_path),
    )
    print(report.model_dump_json(indent=2))
    return 0


def _find_boards(args: argparse.Namespace) -> int:
    report = find_boards(args.url, FetchMode(args.mode), Path(args.output_dir))
    print(report.model_dump_json(indent=2))
    return 0


def _search_jobs(args: argparse.Namespace) -> int:
    lane_filter = Lane(args.lane) if args.lane else None
    sites = [site.strip() for site in args.sites.split(",") if site.strip()]
    report = search_jobs(
        search_term=args.query,
        location=args.location,
        sites=sites,
        results_wanted=args.results,
        output_dir=Path(args.output_dir),
        lane_filter=lane_filter,
        analyze=args.analyze,
        use_agents=args.use_agents,
        track=args.track,
        tracker_path=Path(args.tracker_path),
    )
    print(report.model_dump_json(indent=2))
    return 0


def _suggest_projects(args: argparse.Namespace) -> int:
    job_urls: list[str] = []
    if args.job_urls:
        job_urls = [u.strip() for u in args.job_urls.split(",") if u.strip()]
    if args.job_urls_file:
        urls_path = Path(args.job_urls_file)
        job_urls += [line.strip() for line in urls_path.read_text().splitlines() if line.strip()]

    if not job_urls:
        print("Error: provide at least one job URL via --job-urls or --job-urls-file")
        return 1

    print(f"Analyzing {len(job_urls)} job posting(s) for role: {args.role!r}...")
    gap_report, suggestions = run_gap_analysis(
        role_query=args.role,
        job_urls=job_urls,
        fetch_mode=FetchMode(args.mode),
        num_suggestions=args.num_suggestions,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "gap_report.md"
    report_path.write_text(render_gap_report_md(gap_report, suggestions), encoding="utf-8")

    suggestions_path = output_dir / "suggested_projects.json"
    suggestions_path.write_text(
        json.dumps([s.model_dump() for s in suggestions], indent=2),
        encoding="utf-8",
    )

    print(f"Gap report  -> {report_path}")
    print(f"Suggestions -> {suggestions_path}")
    print(f"Missing skills: {', '.join(gap_report.missing_skills[:8]) or 'none'}")
    for s in suggestions:
        print(f"  - {s.name} [{', '.join(s.tech_stack)}]")
    return 0


def _list_applications(args: argparse.Namespace) -> int:
    records = list_applications(
        path=Path(args.tracker_path),
        status=ApplicationStatus(args.status) if args.status else None,
        lane=args.lane or None,
    )
    print(json.dumps([record.model_dump(mode="json") for record in records], indent=2))
    return 0


def _update_application(args: argparse.Namespace) -> int:
    _, record, saved_path = update_application_status(
        company=args.company,
        title=args.title,
        status=ApplicationStatus(args.status),
        path=Path(args.tracker_path),
        notes=args.notes,
        next_follow_up=args.next_follow_up,
    )
    payload = {"tracker_path": str(saved_path), "record": record.model_dump(mode="json")}
    print(json.dumps(payload, indent=2))
    return 0


def _tracker_summary(args: argparse.Namespace) -> int:
    summary = summarize_tracker(Path(args.tracker_path))
    print(summary.model_dump_json(indent=2))
    return 0


def _export_applications(args: argparse.Namespace) -> int:
    exported = export_tracker_csv(Path(args.tracker_path), Path(args.output_path))
    print(json.dumps({"csv_path": str(exported)}, indent=2))
    return 0




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="job-agent — AI-powered job search and resume tailoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Resume ---
    generate = subparsers.add_parser("generate-resumes", help="Generate LaTeX+PDF resumes for all lanes.")
    generate.add_argument("--output-dir", default="outputs/resumes")
    generate.set_defaults(func=_generate_resumes)

    # --- Job analysis ---
    analyze = subparsers.add_parser("analyze-job", help="Fetch a job URL, classify it, render tailored resume.")
    analyze.add_argument("--url", required=True)
    analyze.add_argument("--mode", default="auto", choices=[m.value for m in FetchMode])
    analyze.add_argument("--output-dir", default="outputs/jobs")
    analyze.add_argument("--use-agents", action="store_true")
    analyze.add_argument("--track", action="store_true")
    analyze.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    analyze.add_argument("--status", default="", choices=["", *[s.value for s in ApplicationStatus]])
    analyze.add_argument("--notes", default="")
    analyze.set_defaults(func=_analyze_job)

    # --- Discovery ---
    discover = subparsers.add_parser("discover-jobs", help="Extract job links from a board URL and optionally analyze.")
    discover.add_argument("--search-url", required=True)
    discover.add_argument("--mode", default="auto", choices=[m.value for m in FetchMode])
    discover.add_argument("--limit", type=int, default=25)
    discover.add_argument("--lane", default="", choices=["", *[l.value for l in Lane]])
    discover.add_argument("--analyze", action="store_true")
    discover.add_argument("--use-agents", action="store_true")
    discover.add_argument("--track", action="store_true")
    discover.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    discover.add_argument("--output-dir", default="outputs/discovery")
    discover.set_defaults(func=_discover_jobs)

    # --- Board finder ---
    boards = subparsers.add_parser("find-boards", help="Find careers board URLs for a company.")
    boards.add_argument("--url", required=True)
    boards.add_argument("--mode", default="auto", choices=[m.value for m in FetchMode])
    boards.add_argument("--output-dir", default="outputs/boards")
    boards.set_defaults(func=_find_boards)

    # --- JobSpy search ---
    search = subparsers.add_parser("search-jobs", help="Search LinkedIn/Indeed/Glassdoor via jobspy.")
    search.add_argument("--query", required=True)
    search.add_argument("--location", default="India")
    search.add_argument("--sites", default="linkedin,indeed,glassdoor")
    search.add_argument("--results", type=int, default=20)
    search.add_argument("--lane", default="", choices=["", *[l.value for l in Lane]])
    search.add_argument("--analyze", action="store_true")
    search.add_argument("--use-agents", action="store_true")
    search.add_argument("--track", action="store_true")
    search.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    search.add_argument("--output-dir", default="outputs/jobspy")
    search.set_defaults(func=_search_jobs)

    # --- Project suggestions ---
    suggest = subparsers.add_parser("suggest-projects", help="Analyze job postings and suggest projects to build.")
    suggest.add_argument("--role", required=True)
    suggest.add_argument("--job-urls", default="")
    suggest.add_argument("--job-urls-file", default="")
    suggest.add_argument("--mode", default="auto", choices=[m.value for m in FetchMode])
    suggest.add_argument("--num-suggestions", type=int, default=3)
    suggest.add_argument("--output-dir", default="outputs/gap-reports")
    suggest.set_defaults(func=_suggest_projects)

    # --- Tracker ---
    list_t = subparsers.add_parser("list-applications", help="List tracked applications.")
    list_t.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    list_t.add_argument("--status", default="", choices=["", *[s.value for s in ApplicationStatus]])
    list_t.add_argument("--lane", default="", choices=["", *[l.value for l in Lane]])
    list_t.set_defaults(func=_list_applications)

    update_t = subparsers.add_parser("update-application", help="Update application status.")
    update_t.add_argument("--company", required=True)
    update_t.add_argument("--title", required=True)
    update_t.add_argument("--status", required=True, choices=[s.value for s in ApplicationStatus])
    update_t.add_argument("--notes", default="")
    update_t.add_argument("--next-follow-up", default="")
    update_t.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    update_t.set_defaults(func=_update_application)

    tracker_sum = subparsers.add_parser("tracker-summary", help="Show tracker summary.")
    tracker_sum.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    tracker_sum.set_defaults(func=_tracker_summary)

    export_t = subparsers.add_parser("export-applications", help="Export applications to CSV.")
    export_t.add_argument("--tracker-path", default="outputs/tracker/applications.json")
    export_t.add_argument("--output-path", default="outputs/tracker/applications.csv")
    export_t.set_defaults(func=_export_applications)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except AttributeError:
        parser.print_help()
        return 1
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def _main_with_subcommand(subcommand: str) -> int:
    parser = build_parser()
    args = parser.parse_args([subcommand, *sys.argv[1:]])
    return args.func(args)


def analyze_job_main() -> int:
    return _main_with_subcommand("analyze-job")

def discover_jobs_main() -> int:
    return _main_with_subcommand("discover-jobs")

def generate_resumes_main() -> int:
    return _main_with_subcommand("generate-resumes")

def suggest_projects_main() -> int:
    return _main_with_subcommand("suggest-projects")

def find_boards_main() -> int:
    return _main_with_subcommand("find-boards")

def list_applications_main() -> int:
    return _main_with_subcommand("list-applications")

def update_application_main() -> int:
    return _main_with_subcommand("update-application")

def search_jobs_main() -> int:
    return _main_with_subcommand("search-jobs")

def tracker_summary_main() -> int:
    return _main_with_subcommand("tracker-summary")

def export_applications_main() -> int:
    return _main_with_subcommand("export-applications")
