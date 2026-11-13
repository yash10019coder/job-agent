"""Ground truth for vendor labels: click the Apply control (nothing is filled, no login) and record where the
application actually lives. The destination's final URL host is evidence independent of the landing page's
fingerprint, so comparing the two measures how reliable the landing fingerprint is.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from job_agent.apply.batch import blank_window
from job_agent.apply.browser import BraveSession
from job_agent.apply.gates import classify_gate
from job_agent.apply.submit import captcha_visible
from job_agent.apply.fingerprint import COLLECT_JS, HTML_MARKERS, identify
from job_agent.apply.perceive import Settler, perceive
from job_agent.apply.survey import _HostPacer

APPLY = re.compile(r"^(quick )?apply( now| today| online| for this (job|role|position)| to (this )?(job|position|role))?!?\s*[»>›]*$", re.I)


_DIALOG_CHOICE = re.compile(r"^(apply manually|apply without (a |an )?(resume|account|signing in)|continue as (a )?guest|"
                            r"continue without (a |an )?(account|signing in|resume)|start (your |the )?application|apply as (a )?guest)$", re.I)


def pick_apply(model) -> str | None:
    """The control that starts the application. An open choice dialog ("Apply Manually" / "Autofill with Resume" /
    "Use My Last Application") is answered first with the choice that needs no account or upload; otherwise the
    first exact Apply control, main content before header/footer."""
    enabled = [a for a in model.actions if not a.disabled]
    for a in enabled:
        if a.region == "dialog" and (_DIALOG_CHOICE.match(a.label.strip()) or APPLY.match(a.label.strip())):
            return a.id
    for region in ("main", "dialog"):
        for a in enabled:
            if a.region == region and APPLY.match(a.label.strip()):
                return a.id
    for a in enabled:
        if APPLY.match(a.label.strip()):
            return a.id
    return None


def dest_kind(model, url: str) -> str:
    kinds = {f.kind for f in model.fields}
    text = (model.title + " " + " ".join(model.headings) + " " + model.text_digest[:600]).lower()
    if "password" in kinds or re.search(r"sign in|log in|create (an )?account|sign up", text) and len(model.fields) <= 6:
        return "login_or_signup"
    if len(model.fields) >= 3 and any(f.kind == "email" or "email" in f.label.lower() or "name" in f.label.lower() for f in model.fields):
        return "form"
    if re.search(r"no longer|filled|expired|not found", text):
        return "closed"
    return "other"


async def reveal_one(session: BraveSession, row: dict, idx: int, out_dir: Path, pacer: _HostPacer, sem: asyncio.Semaphore) -> dict:
    rec = {"idx": idx, "url": row["url"], "ats_label": row.get("ats"), "landing_vendor": row.get("landing_vendor"),
           "landing_conf": row.get("landing_conf"), "clicked": None, "error": None}
    async with sem:
        await pacer.wait(urlparse(row["url"]).netloc.lower())
        page = None
        try:
            page = await blank_window(session)
            ctx = session.context
            try:
                await page.goto(row["url"], wait_until="domcontentloaded", timeout=30000)
            except Exception as error:
                rec["error"] = str(error).splitlines()[0][:140]
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            settler = Settler(page)
            await settler.settle()
            p = await perceive(page)
            rec["landing_kind"] = dest_kind(p.model, page.url)
            target = page
            if rec["landing_kind"] == "form":
                rec["clicked"] = "(form already visible)"
            else:
                action = pick_apply(p.model)
                if action:
                    rec["clicked"] = next(a.label for a in p.model.actions if a.id == action)
                    before_pages, before_url = set(ctx.pages), page.url
                    await p.locator(action, next(a.frame for a in p.model.actions if a.id == action)).click(timeout=5000)
                    for _ in range(100):  # up to ~10s for a popup or navigation
                        new = [x for x in ctx.pages if x not in before_pages]
                        if new or page.url != before_url:
                            break
                        await asyncio.sleep(0.1)
                    new = [x for x in ctx.pages if x not in before_pages]
                    target = new[-1] if new else page
                    rec["opened"] = "new_tab" if new else ("navigated" if page.url != before_url else "same_page")
                    try:
                        await target.wait_for_load_state("domcontentloaded", timeout=10000)
                        await target.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    await Settler(target).settle()
                else:
                    rec["opened"] = "no_apply_control_matched"
            model = (await perceive(target)).model
            try:
                collected = await target.evaluate(COLLECT_JS, HTML_MARKERS)
            except Exception:
                collected = {}
            fp = identify(target.url, [], collected)
            frame_hosts = sorted({urlparse(f.url).netloc for f in target.frames if f.url.startswith("http")})
            try:
                cap = await captcha_visible(target)
            except Exception:
                cap = False
            gate = classify_gate(model, frame_hosts, rec.get("status"), cap)
            rec.update(gate=gate["gate"], gate_notes=gate["notes"], gate_signals=gate["signals"],
                       dest_actions=[a.label for a in model.actions][:15], dest_field_labels=[f.label for f in model.fields][:12])
            (out_dir / "pages").mkdir(exist_ok=True)
            (out_dir / "pages" / f"{idx:03d}.json").write_text(json.dumps(model.model_dump(), indent=1, default=str), encoding="utf-8")
            rec.update(dest_url=target.url, dest_host=urlparse(target.url).netloc, dest_vendor=fp["vendor"], dest_conf=fp["confidence"],
                       dest_evidence=fp["evidence"][:4], dest_kind=dest_kind(model, target.url), dest_fields=len(model.fields),
                       dest_title=model.title[:90], dest_text=model.text_digest[:240])
            if target is not page:
                await target.close()
        except Exception as error:
            rec["error"] = (rec["error"] or "") + " | " + str(error).splitlines()[0][:140]
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
    with (out_dir / "reveal.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    return rec


async def run_reveal(session: BraveSession, rows: list[dict], out_dir: Path, concurrency: int = 4) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pacer, sem = _HostPacer(), asyncio.Semaphore(concurrency)
    return await asyncio.gather(*[reveal_one(session, r, i + 1, out_dir, pacer, sem) for i, r in enumerate(rows)])
