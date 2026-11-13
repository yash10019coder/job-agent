"""CLI handlers for the apply engine (kept out of cli.py to respect the 500-line rule)."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from job_agent.apply.browser import BraveConfig, BraveSession
from job_agent.apply.linkedin import LINKEDIN_HOME, ensure_jobs_page, is_linkedin
from job_agent.apply.jobs import open_jobs, parse_cards
from job_agent.apply.search import DEFAULT_TITLES, JobSearch, run_search


def _config(args: argparse.Namespace) -> BraveConfig:
    return BraveConfig(port=args.port, executable=args.brave_path or None, profile_name=args.profile_name)


def add_brave_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--brave-path", default="", help="Path to the Brave executable (auto-detected)")
    parser.add_argument("--profile-name", default="job-agent", help="Name of the separate agent-only Brave profile (never your real profile)")


def brave_launch(args: argparse.Namespace) -> int:
    config = _config(args)

    async def run():
        async with BraveSession(config) as session:
            page = await session.new_tab(args.url)
            return {"launched_new_browser": session.launched, "cdp_url": config.cdp_url,
                    "profile_dir": str(config.profile_dir), "url": page.url, "title": await page.title()}

    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


def linkedin_jobs(args: argparse.Namespace) -> int:
    """Reuse an open LinkedIn tab (or open one) and make sure it is on the Jobs page."""
    config = _config(args)

    async def run():
        async with BraveSession(config) as session:
            page = next((p for p in reversed(session.context.pages) if is_linkedin(p)), None)
            created = page is None
            page = page or await session.new_tab(LINKEDIN_HOME)
            started_at = page.url
            navigated = await ensure_jobs_page(page)
            await page.bring_to_front()
            return {"reused_tab": not created, "started_at": started_at, "navigated": navigated,
                    "url": page.url, "title": await page.title()}

    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


def linkedin_search(args: argparse.Namespace) -> int:
    """Run the role/experience search in a LinkedIn tab and report how many cards loaded."""
    config = _config(args)
    search = JobSearch(
        titles=tuple(args.title) if args.title else DEFAULT_TITLES,
        location=args.location, min_years=args.min_years, max_years=args.max_years,
    )

    async def run():
        async with BraveSession(config) as session:
            page = next((p for p in reversed(session.context.pages) if is_linkedin(p)), None) \
                or await session.new_tab(LINKEDIN_HOME)
            cards = await run_search(page, search)
            return {"query": search.query, "url": page.url, "job_cards_visible": len(cards),
                    "first_cards": [c[:140] for c in cards[:5]]}

    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


def linkedin_open_jobs(args: argparse.Namespace) -> int:
    """Search, parse the result cards, then open each job in its own new tab (one by one)."""
    config = _config(args)
    search = JobSearch(
        titles=tuple(args.title) if args.title else DEFAULT_TITLES,
        location=args.location, min_years=args.min_years, max_years=args.max_years,
    )

    async def run():
        async with BraveSession(config) as session:
            page = next((p for p in reversed(session.context.pages) if is_linkedin(p)), None) \
                or await session.new_tab(LINKEDIN_HOME)
            await run_search(page, search)
            jobs = (await parse_cards(page))[: args.limit]
            results = await open_jobs(session.context, jobs, keep_open=args.keep_open)
            return {"query": search.query, "jobs_opened": len(results), "jobs": results}

    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


def apply_demo(args: argparse.Namespace) -> int:
    """POC: perceive -> resolve (Flash) -> act on a form page in Brave. Dry run: never submits."""
    from job_agent.apply.demo import render, run_demo
    from job_agent.apply.llm import LLM
    from job_agent.apply.prefill import build_facts

    root = Path(__file__).resolve().parents[3]
    facts = build_facts(root / "data" / "applicant_profile.json", root / "data" / "resume_inventory.json")
    context = json.dumps(facts, indent=1)
    url = Path(args.fixture).resolve().as_uri() if args.fixture else args.url
    if not url:
        raise SystemExit("pass --url <job/application url> or --fixture <html file>")
    llm = LLM(model=args.model or None)

    async def run():
        async with BraveSession(_config(args)) as session:
            page = await session.new_tab(url)
            result = await run_demo(page, llm, context, resume=args.resume or None, facts=facts, submit=args.submit)
            print(render(result, llm))
            if args.keep_open:
                input("\nForm left open in Brave for inspection. Press Enter to close the tab... ")
            await page.close()

    asyncio.run(run())
    return 0


def apply_steps(args: argparse.Namespace) -> int:
    """Step-by-step runner on a live job (see apply/stepper.py)."""
    from job_agent.apply.llm import LLM
    from job_agent.apply.prefill import build_facts
    from job_agent.apply.stepper import STAGES, run_steps

    root = Path(__file__).resolve().parents[3]
    facts = build_facts(root / "data" / "applicant_profile.json", root / "data" / "resume_inventory.json")
    url = Path(args.fixture).resolve().as_uri() if args.fixture else args.url
    if not url:
        raise SystemExit("pass --url <job/application url> or --fixture <html file>")
    pause = (not args.no_pause) and sys.stdin.isatty()

    async def run():
        async with BraveSession(_config(args)) as session:
            page = await session.new_tab(url)
            await run_steps(page, LLM(model=args.model or None), facts, args.resume or None, args.until, pause, args.allow_submit, args.confirm_url or None)

    asyncio.run(run())
    return 0


def apply_batch(args: argparse.Namespace) -> int:
    """Run several jobs concurrently, one Brave window each; run data goes to data/runs/<timestamp>/."""
    from job_agent.apply.batch import run_batch
    from job_agent.apply.prefill import build_facts

    root = Path(__file__).resolve().parents[3]
    facts = build_facts(root / "data" / "applicant_profile.json", root / "data" / "resume_inventory.json")
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))

    async def run():
        async with BraveSession(_config(args)) as session:
            return await run_batch(session, jobs, root / "data" / "runs", facts, args.resume or None,
                                   args.model or None, submit=args.submit, concurrency=args.concurrency)

    for r in asyncio.run(run()):
        print(f"{r['ats']:12} {r['status']:18} fields={r.get('fields', '-')} ok={r.get('ok', '-')} blocked={r.get('blocked', '-')} "
              f"failed={r.get('failed', '-')} {r['seconds']}s  {r['dir']}")
    return 0


def _survey_linkedin(args: argparse.Namespace, root: Path) -> int:
    from datetime import datetime
    from job_agent.apply.survey import run_linkedin_phase, run_survey_into, sample_linkedin

    jobs = sample_linkedin(root / "data", args.per_ats, args.limit or args.li_cap, args.seed)
    print(f"Cohort B: {len(jobs)} LinkedIn links (one at a time, slow) ...")

    async def run():
        out_dir = root / "data" / "surveys" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-linkedin")
        out_dir.mkdir(parents=True, exist_ok=True)
        async with BraveSession(_config(args)) as session:
            li = await run_linkedin_phase(session, jobs, out_dir)
            ext = [{**j, "linkedin_url": j["url"], "url": j["external_url"]} for j in li if j.get("external_url")]
            print(f"apply types: { {t: sum(1 for j in li if j.get('apply_type') == t) for t in {j.get('apply_type') for j in li}} }; surveying {len(ext)} external pages ...")
            await run_survey_into(session, ext, out_dir, args.concurrency)
        return out_dir

    print(f"done: {asyncio.run(run())}")
    return 0


def _unclassified(dirs: list[str]) -> list[dict]:
    """Rows from earlier surveys whose final URL host does not name a known vendor (custom domains etc.)."""
    from urllib.parse import urlparse
    from job_agent.apply.fingerprint import _match

    out, seen = [], set()
    for d in dirs:
        for line in (Path(d) / "index.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            final = r.get("final_url") or r["url"]
            if _match(urlparse(final).netloc.lower()) or r["url"] in seen:
                continue
            seen.add(r["url"])
            out.append({k: r.get(k) for k in ("ats", "file", "company", "title", "url")})
    return out


def apply_reveal(args: argparse.Namespace) -> int:
    """Click Apply on already-surveyed pages (no filling, no login) to learn where the application really lives."""
    from datetime import datetime
    from job_agent.apply.reveal import run_reveal

    root = Path(__file__).resolve().parents[3]
    rows = []
    for line in (Path(args.from_survey) / "index.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        fp = r.get("fingerprint") or {}
        rows.append({"url": r["url"], "ats": r.get("ats"), "landing_vendor": fp.get("vendor"), "landing_conf": fp.get("confidence")})
    if args.limit:
        rows = rows[: args.limit]
    out_dir = root / "data" / "surveys" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-reveal")
    print(f"revealing {len(rows)} pages -> {out_dir}")

    async def run():
        async with BraveSession(_config(args)) as session:
            await run_reveal(session, rows, out_dir, args.concurrency)

    asyncio.run(run())
    print("done")
    return 0


def apply_survey(args: argparse.Namespace) -> int:
    """Read-only survey of entry pages (no model calls, no filling); output in data/surveys/<stamp>/."""
    from job_agent.apply.survey import run_survey, sample_direct

    root = Path(__file__).resolve().parents[3]
    if args.linkedin:
        return _survey_linkedin(args, root)
    if args.refingerprint:
        jobs = _unclassified(args.refingerprint)
    else:
        jobs = sample_direct(root / "data", args.per_ats, args.seed)
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"surveying {len(jobs)} pages across {len({j['ats'] for j in jobs})} ATS types ...")

    async def run():
        async with BraveSession(_config(args)) as session:
            return await run_survey(session, jobs, root / "data" / "surveys", args.concurrency)

    print(f"done: {asyncio.run(run())}")
    return 0


def apply_run(args: argparse.Namespace) -> int:
    """Run the generic application loop on many jobs (dry run only; one Brave window each); data/runs/<timestamp>/."""
    from job_agent.apply.batch import run_batch
    from job_agent.apply.prefill import build_facts

    root = Path(__file__).resolve().parents[3]
    facts = build_facts(root / "data" / "applicant_profile.json", root / "data" / "resume_inventory.json")
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))

    async def run():
        async with BraveSession(_config(args)) as session:
            return await run_batch(session, jobs, root / "data" / "runs", facts, args.resume or None, args.model or None,
                                   submit=False, concurrency=args.concurrency, engine="controller")

    for r in asyncio.run(run()):
        print(f"{(r.get('ats') or '-')[:12]:12} {r['status']:22} steps={r.get('steps', '-')} fields={r.get('fields', '-')} {r['seconds']}s  "
              f"{' > '.join(r.get('states', []))[:70]}  {r['dir']}")
    return 0


def register(subparsers) -> None:
    """Add every apply-engine subcommand to the CLI."""
    brave = subparsers.add_parser("brave-launch", help="Launch Brave (CDP) and open a new tab via Playwright.")
    brave.add_argument("--url", default="https://www.linkedin.com/jobs/", help="URL to open in the new tab")
    add_brave_args(brave)
    brave.set_defaults(func=brave_launch)

    li_jobs = subparsers.add_parser("linkedin-jobs", help="Ensure a LinkedIn tab is on the Jobs page (navigates if not).")
    add_brave_args(li_jobs)
    li_jobs.set_defaults(func=linkedin_jobs)

    for name, func, helptext in (("linkedin-search", linkedin_search, "Run the title/experience search on LinkedIn Jobs."),
                                 ("linkedin-open-jobs", linkedin_open_jobs, "Search, parse cards, open each job in a new tab.")):
        sp = subparsers.add_parser(name, help=helptext)
        sp.add_argument("--title", action="append", help="Role title (repeatable); default: SWE/Backend/SDE/MTS set")
        sp.add_argument("--location", default="India")
        sp.add_argument("--min-years", type=int, default=1)
        sp.add_argument("--max-years", type=int, default=3)
        if name == "linkedin-open-jobs":
            sp.add_argument("--limit", type=int, default=5, help="Max jobs to open this run")
            sp.add_argument("--keep-open", action="store_true", help="Leave each job tab open")
        add_brave_args(sp)
        sp.set_defaults(func=func)

    demo = subparsers.add_parser("apply-demo", help="Agentic form fill on a page (dry run unless --submit).")
    demo.add_argument("--url", default="")
    demo.add_argument("--fixture", default="", help="Local HTML file to run against instead of a URL")
    demo.add_argument("--resume", default="", help="PDF to upload into file fields")
    demo.add_argument("--model", default="", help="Model id (default: gemini-3.5-flash-lite)")
    demo.add_argument("--submit", action="store_true", help="Actually press Submit when every guard passes (default: dry run)")
    demo.add_argument("--keep-open", action="store_true", help="Leave the filled form open until you press Enter")
    add_brave_args(demo)
    demo.set_defaults(func=apply_demo)

    steps = subparsers.add_parser("apply-steps", help="Run the apply pipeline one stage at a time on a live job.")
    steps.add_argument("--url", default="")
    steps.add_argument("--fixture", default="", help="Local HTML file instead of a URL")
    steps.add_argument("--resume", default="", help="PDF to upload into resume fields")
    steps.add_argument("--model", default="", help="Model id (default: gemini-3.5-flash-lite)")
    steps.add_argument("--until", default="guards", choices=["open", "perceive", "prefill", "resolve", "fill", "guards", "submit"],
                       help="Last stage to run (default: guards, i.e. everything except Submit)")
    steps.add_argument("--no-pause", action="store_true", help="Run straight through without waiting between stages")
    steps.add_argument("--confirm-url", default="", help="Non-interactive submit confirmation: must equal the posting URL (otherwise submit is refused)")
    steps.add_argument("--allow-submit", action="store_true", help="Enable the real Submit stage (still asks you to type 'submit')")
    add_brave_args(steps)
    steps.set_defaults(func=apply_steps)

    batch = subparsers.add_parser("apply-batch", help="Run many jobs concurrently (dry run unless --submit); logs to data/runs/.")
    batch.add_argument("--jobs", required=True, help="JSON list of {ats, company, title, url}")
    batch.add_argument("--resume", default="", help="PDF to upload into resume fields")
    batch.add_argument("--model", default="")
    batch.add_argument("--concurrency", type=int, default=5)
    batch.add_argument("--submit", action="store_true", help="Press Submit when every guard passes (default: dry run)")
    add_brave_args(batch)
    batch.set_defaults(func=apply_batch)

    survey = subparsers.add_parser("apply-survey", help="Read-only survey of job entry pages per ATS (data/surveys/).")
    survey.add_argument("--per-ats", type=int, default=8, help="(--linkedin: use ~3)")
    survey.add_argument("--seed", type=int, default=7)
    survey.add_argument("--limit", type=int, default=0, help="Only the first N sampled pages (smoke test)")
    survey.add_argument("--concurrency", type=int, default=5)
    survey.add_argument("--refingerprint", nargs="+", metavar="DIR", help="Re-survey the unclassified pages of these survey dirs, with fingerprints")
    survey.add_argument("--linkedin", action="store_true", help="Cohort B: LinkedIn-sourced links (needs a logged-in agent profile)")
    survey.add_argument("--li-cap", type=int, default=60, help="Max LinkedIn pages opened (kept small on purpose)")
    add_brave_args(survey)
    survey.set_defaults(func=apply_survey)

    reveal = subparsers.add_parser("apply-reveal", help="Click Apply on surveyed pages (no fill/login) to find where the application lives.")
    reveal.add_argument("--from-survey", required=True, help="Survey dir with index.jsonl (needs fingerprints)")
    reveal.add_argument("--limit", type=int, default=0)
    reveal.add_argument("--concurrency", type=int, default=4)
    add_brave_args(reveal)
    reveal.set_defaults(func=apply_reveal)

    run = subparsers.add_parser("apply-run", help="Generic application loop on many jobs (dry run only); logs to data/runs/.")
    run.add_argument("--jobs", required=True, help="JSON list of {url, company?, title?, ats?}")
    run.add_argument("--resume", default="", help="PDF to upload into resume fields")
    run.add_argument("--model", default="")
    run.add_argument("--concurrency", type=int, default=3)
    add_brave_args(run)
    run.set_defaults(func=apply_run)
