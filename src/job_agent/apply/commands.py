"""CLI handlers for the apply engine (kept out of cli.py to respect the 500-line rule)."""
from __future__ import annotations

import argparse
import asyncio
import json
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
