"""Autopilot over local fixture pages: the right jobs are applied to, everything else is skipped with a reason, nobody waits."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.autopilot import is_live, run_autopilot
from job_agent.apply.prefill import build_facts
from job_agent.rate_limiter import RateLimiter
from test_apply_perceive import BRAVE, RESUME, ROOT, FakeLLM

FIX = ROOT / "tests/fixtures/multistep"
FACTS = build_facts(ROOT / "data/applicant_profile.json", ROOT / "data/resume_inventory.json")
pytestmark = pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists(), reason="Brave not installed")


def url(name):
    return (FIX / name).resolve().as_uri()


def job(name, title="Backend Engineer", location="Bengaluru, India", company=None):
    return {"url": url(name), "title": title, "location": location, "company": company or name}


def go(jobs, submit=False, max_per_day=20, live_check=None):
    tmp = Path(tempfile.mkdtemp())

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
            context = await browser.new_context()

            async def open_page(u):
                page = await context.new_page()
                await page.goto(u)
                return page
            kwargs = {"live_check": live_check} if live_check else {}
            results = await run_autopilot(jobs, open_page, FakeLLM, FACTS, RESUME, tmp / "out", submit=submit, max_per_day=max_per_day, concurrency=1,
                                          limiter=RateLimiter(storage_path=tmp / "rl.jsonl"), log_path=tmp / "log.jsonl", pace_s=(0, 0),
                                          click_wait_s=1.5, **kwargs)
            open_after = len(context.pages)
            await browser.close()
            return results, open_after
    results, open_after = asyncio.run(run())
    return {r.url.rsplit("/", 1)[-1]: r for r in results}, tmp, open_after


def test_applies_to_the_good_job_and_skips_everything_else_with_a_reason():
    # (all file:// URLs share one empty host, which the same-company rule treats as one company: apply last)
    res, tmp, open_after = go([
        job("login_gate.html", company="gate"),                      # account wall -> skipped, nobody waits
        job("job_dead.html", company="dead"),                        # redirects to the board -> closed
        job("decoy_newsletter.html", title="Sales Director", company="sales"),   # wrong role: never opened
        job("job_page.html"),                                        # fits, completes -> applied
        job("job_page.html"),                                        # duplicate
    ], submit=True)
    rows = [json.loads(line) for line in (tmp / "out" / "results.jsonl").read_text().splitlines()]
    assert sum(r["status"] == "applied" for r in rows) == 1 and sum("duplicate" in r["reason"] for r in rows) == 1
    assert res["login_gate.html"].status == "skipped" and "needs a human" in res["login_gate.html"].reason
    assert res["job_dead.html"].status == "skipped" and "closed" in res["job_dead.html"].reason
    assert res["decoy_newsletter.html"].status == "skipped" and "does not fit" in res["decoy_newsletter.html"].reason
    assert open_after == 0                                           # every window was closed
    report = (tmp / "out" / "report.md").read_text()
    assert "LIVE (submits)" in report and "Applied (1)" in report
    assert json.loads((tmp / "log.jsonl").read_text().splitlines()[-1])["status"] == "submitted"


def test_daily_cap_stops_further_applications():
    res, _, _ = go([job("job_page.html", company="a"), job("job_newtab.html", company="b")], submit=True, max_per_day=1)
    statuses = sorted(r.status for r in res.values())
    assert statuses == ["applied", "skipped"] and any("daily cap" in r.reason for r in res.values())


def test_dry_run_sends_nothing_and_reports_ready():
    res, tmp, _ = go([job("job_page.html")])
    assert res["job_page.html"].status == "ready"
    assert not (tmp / "log.jsonl").exists() or "submitted" not in (tmp / "log.jsonl").read_text()


def test_dead_links_are_skipped_before_a_browser_is_opened():
    async def dead(u):
        return False, "HTTP 404"
    res, _, _ = go([job("job_page.html")], live_check=dead)
    assert res["job_page.html"].status == "skipped" and res["job_page.html"].reason.startswith("dead link")


def test_liveness_probe_treats_local_files_as_live():
    assert asyncio.run(is_live(url("job_page.html")))[0]


def test_a_vague_location_is_skipped_unless_the_job_page_says_india():
    res, _, _ = go([job("job_page.html", location="Hybrid")])
    only = res["job_page.html"]
    assert only.status == "skipped" and "not verified as India" in only.reason
