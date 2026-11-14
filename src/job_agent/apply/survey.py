"""Read-only survey of job-entry pages: load each URL, record what is there. No model calls, nothing is filled or clicked.

Output (data/surveys/<stamp>/): pages/<n>-<ats>.json (record + full page model), index.jsonl (one summary row
per page), summary.json. This is the evidence the page-state taxonomy and classifier are built from.
"""
from __future__ import annotations

import asyncio
import glob
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from job_agent.apply.batch import blank_window
from job_agent.apply.fingerprint import COLLECT_JS, HTML_MARKERS, identify
from job_agent.apply.browser import BraveSession
from job_agent.apply.perceive import Settler, perceive
from job_agent.apply.submit import captcha_visible

AGGREGATORS = ("linkedin.", "indeed.", "glassdoor.", "ziprecruiter.")
APPLY_WORDS = re.compile(r"\b(apply|application|i'?m interested|start|easy apply)\b", re.I)
HOST_GAP_S = 3.0


def sample_direct(data_dir: Path, per_ats: int, seed: int = 7) -> list[dict]:
    """Up to `per_ats` direct (non-aggregator) URLs from each per-ATS file, one per company where possible."""
    rng = random.Random(seed)
    out = []
    for f in sorted(glob.glob(str(data_dir / "*_jobs.json")) + glob.glob(str(data_dir / "sde_*.json"))):
        name = Path(f).stem
        ats = re.sub(r"^(sde_|india_)|(_jobs|_global|_india)$", "", name)
        if ats in {"linkedin", "indeed"}:
            continue
        try:
            rows = json.load(open(f))
            rows = rows if isinstance(rows, list) else rows.get("jobs", [])
        except Exception:
            continue
        direct = [r for r in rows if isinstance(r, dict) and (r.get("job_url") or "").startswith("http")
                  and not any(a in urlparse(r["job_url"]).netloc.lower() for a in AGGREGATORS)]
        rng.shuffle(direct)
        picked, seen = [], set()
        for r in direct:
            key = (r.get("company") or urlparse(r["job_url"]).netloc).lower()
            if key not in seen:
                seen.add(key)
                picked.append(r)
            if len(picked) >= per_ats:
                break
        out += [{"ats": ats, "file": name, "company": r.get("company"), "title": r.get("job_title"), "url": r["job_url"]} for r in picked]
    return out


class _HostPacer:
    def __init__(self) -> None:
        self.next_at: dict[str, float] = {}
        self.lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at.get(host, 0.0))
            self.next_at[host] = at + HOST_GAP_S
        if at > now:
            await asyncio.sleep(at - now)


async def survey_one(session: BraveSession, job: dict, idx: int, out_dir: Path, pacer: _HostPacer, sem: asyncio.Semaphore) -> dict:
    rec = {**job, "idx": idx, "hops": [], "status": None, "error": None}
    started = time.monotonic()
    async with sem:
        await pacer.wait(urlparse(job["url"]).netloc.lower())
        page = None
        try:
            page = await blank_window(session)
            hops: list[str] = []
            page.on("framenavigated", lambda fr: hops.append(fr.url) if fr == page.main_frame and (not hops or hops[-1] != fr.url) else None)
            popups: list[str] = []
            page.on("popup", lambda p: popups.append(p.url))
            req_hosts: list[str] = []
            page.on("request", lambda rq: req_hosts.append(urlparse(rq.url).netloc.lower()))
            try:
                resp = await page.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
                rec["status"] = resp.status if resp else None
            except Exception as error:
                rec["error"] = str(error).splitlines()[0][:160]
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await Settler(page).settle()
            model = (await perceive(page)).model
            try:
                collected = await page.evaluate(COLLECT_JS, HTML_MARKERS)
            except Exception:
                collected = {}
            rec["fingerprint"] = identify(page.url, req_hosts, collected)
            rec["request_hosts"] = [h for h, _ in __import__("collections").Counter(req_hosts).most_common(15)]
            frame_hosts = sorted({urlparse(f.url).netloc for f in page.frames if f.url.startswith("http") and f != page.main_frame})
            apply_actions = [a.label for a in model.actions if APPLY_WORDS.search(a.label)]
            rec.update(
                final_url=page.url, title=model.title, hops=hops, popups=popups, frame_hosts=frame_hosts,
                n_fields=len(model.fields), field_kinds=sorted({f.kind for f in model.fields}),
                n_required=sum(f.required for f in model.fields), has_file=any(f.kind == "file" for f in model.fields),
                has_email_field=any(f.kind == "email" or "email" in f.label.lower() for f in model.fields),
                n_actions=len(model.actions), apply_actions=apply_actions[:8], headings=model.headings[:5],
                captcha=await captcha_visible(page), unparsed=len(model.unparsed), text_chars=len(model.text_digest))
            (out_dir / "pages").mkdir(parents=True, exist_ok=True)
            (out_dir / "pages" / f"{idx:03d}-{job['ats']}.json").write_text(
                json.dumps({"record": rec, "model": model.model_dump()}, indent=1, default=str), encoding="utf-8")
        except Exception as error:
            rec["error"] = (rec["error"] or "") + " | crash: " + str(error).splitlines()[0][:160]
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
    rec["seconds"] = round(time.monotonic() - started, 1)
    with (out_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


async def run_survey(session: BraveSession, jobs: list[dict], base: Path, concurrency: int = 5) -> Path:
    out_dir = base / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    return await run_survey_into(session, jobs, out_dir, concurrency)


async def run_survey_into(session: BraveSession, jobs: list[dict], out_dir: Path, concurrency: int = 5) -> Path:
    pacer, sem = _HostPacer(), asyncio.Semaphore(max(1, min(int(concurrency), 10)))
    results = await asyncio.gather(*[survey_one(session, j, i + 1, out_dir, pacer, sem) for i, j in enumerate(jobs)])
    (out_dir / "summary.json").write_text(json.dumps({"pages": len(results), "errors": sum(bool(r["error"]) for r in results)}, indent=1))
    return out_dir


# ---- Cohort B: LinkedIn-sourced links -------------------------------------------------------------
def sample_linkedin(data_dir: Path, per_ats: int, cap: int, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for f in sorted(glob.glob(str(data_dir / "*_jobs.json")) + glob.glob(str(data_dir / "sde_*.json"))):
        name = Path(f).stem
        ats = re.sub(r"^(sde_|india_)|(_jobs|_global|_india)$", "", name)
        try:
            rows = json.load(open(f))
            rows = rows if isinstance(rows, list) else rows.get("jobs", [])
        except Exception:
            continue
        li = [r for r in rows if isinstance(r, dict) and "linkedin.com/jobs/view" in (r.get("job_url") or "")]
        rng.shuffle(li)
        out += [{"ats": ats, "file": name, "company": r.get("company"), "title": r.get("job_title"), "url": r["job_url"]} for r in li[:per_ats]]
    rng.shuffle(out)
    return out[:cap]


async def run_linkedin_phase(session: BraveSession, jobs: list[dict], out_dir: Path) -> list[dict]:
    """Open each LinkedIn job one at a time (slow, human-like gaps), read the apply type and external link.
    Never clicks anything. Stops if LinkedIn shows a login/challenge wall."""
    from job_agent.apply.jobs import detect_apply_type

    results, page = [], await blank_window(session)
    try:
        for i, job in enumerate(jobs, 1):
            rec = {**job, "idx": i}
            try:
                await page.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector("main", timeout=8000)
                except Exception:
                    pass
                url = page.url
                if re.search(r"/(login|authwall|checkpoint|uas)/?", url):
                    rec.update(apply_type="login_wall", linkedin_url=url)
                    results.append(rec)
                    print(f"LinkedIn shows a login/challenge wall at job {i}: stopping Cohort B to protect the session")
                    break
                apply_type, external = await detect_apply_type(page)
                rec.update(apply_type=apply_type, external_url=external, linkedin_url=url)
            except Exception as error:
                rec["error"] = str(error).splitlines()[0][:160]
            results.append(rec)
            with (out_dir / "linkedin_phase.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
            await asyncio.sleep(random.uniform(4.0, 9.0))
    finally:
        await page.close()
    return results
