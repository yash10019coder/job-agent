"""CLI handlers for the apply engine (kept out of cli.py to respect the 500-line rule)."""
from __future__ import annotations

import argparse
import os
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
                                   args.model or None, submit=args.submit, concurrency=args.concurrency, keep_open=args.keep_open)

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


def _auth_kit(facts: dict, allow_signup: bool = False):
    """Account handling for runs: OS keyring for passwords, Gmail (if connected) for emailed codes. None if neither exists."""
    from job_agent.apply.auth import default_store
    from job_agent.apply.gate_flow import AuthKit
    from job_agent.apply.mailcode import default_code_provider

    store, provider = default_store(), default_code_provider()
    print(f"accounts: keyring {'ready' if store else 'NOT available (no accounts will be created)'}; "
          f"Gmail {'connected' if provider else 'NOT connected (emailed codes will be skipped: run gmail-auth)'}; "
          f"account creation {'ON' if allow_signup else 'off'}")
    return AuthKit.from_facts(facts, store, provider, allow_signup)


def gmail_auth(args: argparse.Namespace) -> int:
    from job_agent.apply.mailcode import CLIENT_PATH, gmail_authorize

    path = Path(args.client_secret) if args.client_secret else CLIENT_PATH
    if not path.exists():
        raise SystemExit(f"No OAuth client file at {path}.\nOne-time setup: in Google Cloud Console create a project, enable the Gmail API, "
                         "create an OAuth client of type 'Desktop app', add your Gmail address as a test user, download the JSON and pass it with --client-secret.")
    print("token saved to", gmail_authorize(path))
    return 0


def semantic_label(args: argparse.Namespace) -> int:
    from job_agent.apply import semantic
    from job_agent.apply.semantic_bank import BANK

    states = sorted(BANK)
    print("States:", ", ".join(states), "\n(press Enter to skip a page)")

    def ask(text: str) -> str:
        print("\n" + text[:700])
        return input("state> ").strip().lower()

    print(f"labelled {semantic.label_queue(states, ask)} page(s); the rest stay queued")
    return 0


def creds_list(args: argparse.Namespace) -> int:
    from job_agent.apply.auth import default_store

    store = default_store()
    if store is None:
        raise SystemExit("no usable OS keyring")
    for site in store.sites():
        print(site)
    return 0


def creds_forget(args: argparse.Namespace) -> int:
    from job_agent.apply.auth import default_store

    store = default_store()
    if store is None:
        raise SystemExit("no usable OS keyring")
    store.forget(args.site)
    print("forgot", args.site)
    return 0


def _parallel(text: str) -> int:
    n = int(text)
    if not 1 <= n <= 10:
        raise argparse.ArgumentTypeError("concurrency must be between 1 and 10")
    return n


def apply_run(args: argparse.Namespace) -> int:
    if getattr(args, "semantic", False):
        os.environ["JOB_AGENT_SEMANTIC"] = "1"
    """Run the generic application loop on many jobs (dry run only; one Brave window each); data/runs/<timestamp>/."""
    from job_agent.apply.batch import run_batch
    from job_agent.apply.prefill import build_facts

    root = Path(__file__).resolve().parents[3]
    facts = build_facts(root / "data" / "applicant_profile.json", root / "data" / "resume_inventory.json")
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))

    auth = _auth_kit(facts, args.allow_signup)

    async def run():
        async with BraveSession(_config(args)) as session:
            return await run_batch(session, jobs, root / "data" / "runs", facts, args.resume or None, args.model or None,
                                   submit=False, concurrency=args.concurrency, engine="controller", keep_open=args.keep_open, auth=auth)

    for r in asyncio.run(run()):
        print(f"{(r.get('ats') or '-')[:12]:12} {r['status']:22} steps={r.get('steps', '-')} fields={r.get('fields', '-')} {r['seconds']}s  "
              f"{' > '.join(r.get('states', []))[:70]}  {r['dir']}")
    return 0


def autopilot(args: argparse.Namespace) -> int:
    if getattr(args, "semantic", False):
        os.environ["JOB_AGENT_SEMANTIC"] = "1"
    """Unattended application run: fit gate, liveness check, rate limits, generic loop, report in data/autopilot/<ts>/."""
    from datetime import datetime

    from job_agent.apply.autopilot import jobs_from_dumps, run_autopilot
    from job_agent.apply.batch import new_window
    from job_agent.apply.llm import LLM
    from job_agent.apply.prefill import build_facts

    root = Path(__file__).resolve().parents[3]
    facts = build_facts(root / "data" / "applicant_profile.json", root / "data" / "resume_inventory.json")
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8")) if args.jobs else jobs_from_dumps(root / "data")
    if args.limit:
        jobs = jobs[: args.limit]
    auth = _auth_kit(facts, args.submit or args.allow_signup)
    out_dir = root / "data" / "autopilot" / datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"autopilot: {len(jobs)} jobs, {'LIVE: will submit' if args.submit else 'dry run'}, cap {args.max_per_day}/day -> {out_dir}")

    async def run():
        async with BraveSession(_config(args)) as session:
            return await run_autopilot(jobs, lambda u: new_window(session, u), lambda: LLM(model=args.model or None), facts, args.resume or None,
                                       out_dir, submit=args.submit, max_per_day=args.max_per_day, concurrency=args.concurrency,
                                       pace_s=(args.pace / 2, args.pace), auth=auth, check_fit=not args.no_fit_check)

    results = asyncio.run(run())
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"done: {counts}; report: {out_dir / 'report.md'}")
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
    batch.add_argument("--keep-open", action="store_true", help="Leave each job window open for inspection (default: close it)")
    batch.add_argument("--concurrency", type=_parallel, default=5)
    batch.add_argument("--submit", action="store_true", help="Press Submit when every guard passes (default: dry run)")
    add_brave_args(batch)
    batch.set_defaults(func=apply_batch)

    survey = subparsers.add_parser("apply-survey", help="Read-only survey of job entry pages per ATS (data/surveys/).")
    survey.add_argument("--per-ats", type=int, default=8, help="(--linkedin: use ~3)")
    survey.add_argument("--seed", type=int, default=7)
    survey.add_argument("--limit", type=int, default=0, help="Only the first N sampled pages (smoke test)")
    survey.add_argument("--concurrency", type=_parallel, default=5)
    survey.add_argument("--refingerprint", nargs="+", metavar="DIR", help="Re-survey the unclassified pages of these survey dirs, with fingerprints")
    survey.add_argument("--linkedin", action="store_true", help="Cohort B: LinkedIn-sourced links (needs a logged-in agent profile)")
    survey.add_argument("--li-cap", type=int, default=60, help="Max LinkedIn pages opened (kept small on purpose)")
    add_brave_args(survey)
    survey.set_defaults(func=apply_survey)

    reveal = subparsers.add_parser("apply-reveal", help="Click Apply on surveyed pages (no fill/login) to find where the application lives.")
    reveal.add_argument("--from-survey", required=True, help="Survey dir with index.jsonl (needs fingerprints)")
    reveal.add_argument("--limit", type=int, default=0)
    reveal.add_argument("--concurrency", type=_parallel, default=4)
    add_brave_args(reveal)
    reveal.set_defaults(func=apply_reveal)

    run = subparsers.add_parser("apply-run", help="Generic application loop on many jobs (dry run only); logs to data/runs/.")
    run.add_argument("--jobs", required=True, help="JSON list of {url, company?, title?, ats?}")
    run.add_argument("--resume", default="", help="PDF to upload into resume fields")
    run.add_argument("--model", default="")
    run.add_argument("--allow-signup", action="store_true", help="Let the dry run create accounts on real sites (default: off)")
    run.add_argument("--keep-open", action="store_true", help="Leave each job window open for inspection (default: close it)")
    run.add_argument("--concurrency", type=_parallel, default=3)
    run.add_argument("--semantic", action="store_true", help="Also classify page meaning with local embeddings (closed/dead/confirmation/code/bot wording)")
    add_brave_args(run)
    run.set_defaults(func=apply_run)

    auto = subparsers.add_parser("autopilot", help="Unattended: apply to every live, fitting job in a list (dry run unless --submit).")
    auto.add_argument("--jobs", default="", help="JSON list of {url, title, company, location}; default: every direct link in the job dumps")
    auto.add_argument("--resume", default="", help="PDF to upload into resume fields")
    auto.add_argument("--submit", action="store_true", help="Actually submit when every guard passes (default: dry run)")
    auto.add_argument("--allow-signup", action="store_true", help="Create accounts even without --submit (with --submit it is on)")
    auto.add_argument("--max-per-day", type=int, default=20)
    auto.add_argument("--concurrency", type=_parallel, default=2)
    auto.add_argument("--pace", type=float, default=60.0, help="Max seconds of gap between application starts (human-like pacing; half to full)")
    auto.add_argument("--limit", type=int, default=0, help="Only the first N jobs (smoke test)")
    auto.add_argument("--model", default="")
    auto.add_argument("--no-fit-check", action="store_true", help="Apply to every link given, without judging whether the job suits the profile")
    auto.add_argument("--semantic", action="store_true", help="Also classify page meaning with local embeddings (closed/dead/confirmation/code/bot wording)")
    add_brave_args(auto)
    auto.set_defaults(func=autopilot)

    ga = subparsers.add_parser("gmail-auth", help="One-time read-only Gmail consent so the agent can read emailed verification codes.")
    ga.add_argument("--client-secret", default="", help="Path to the OAuth client JSON (Desktop app); default ~/.config/job-agent/gmail_client_secret.json")
    ga.set_defaults(func=gmail_auth)
    sl = subparsers.add_parser("semantic-label", help="Label pages the classifier could not place; they become examples for --semantic.")
    sl.set_defaults(func=semantic_label)
    cl = subparsers.add_parser("creds-list", help="List the sites the agent holds an account for (never prints passwords).")
    cl.set_defaults(func=creds_list)
    cf = subparsers.add_parser("creds-forget", help="Forget the stored login for one site.")
    cf.add_argument("site")
    cf.set_defaults(func=creds_forget)
