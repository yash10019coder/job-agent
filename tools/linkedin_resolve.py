"""Resolve LinkedIn job links to the page the Apply button leads to, using the logged-in "job-agent" browser profile.

  linkedin_resolve.py check                              is the agent browser logged in to LinkedIn?
  linkedin_resolve.py run MASTER.csv --count N           resolve the next N LinkedIn rows that have no status and no result yet
  linkedin_resolve.py export MASTER.csv OUT.csv          write MASTER rows + a "Resolved apply URL" column (from the cache)

Results are cached in data/linkedin_resolved.json (url -> {resolved, result, at}) so reruns never repeat a job.
Slow on purpose: 2-3 s between jobs, and it stops at the first sign of a block (login wall, 429, captcha).
Result values: external (resolved holds the company page) | easy_apply (stays on LinkedIn) | closed | no_apply_button | blocked
"""
import argparse, asyncio, csv, json, random, sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
CACHE = ROOT / "data" / "linkedin_resolved.json"
JS_FIND = """() => {
  // Only the main job's own buttons count: the sidebar job cards also say "Easy Apply" and must not be read.
  const label = e => (e.getAttribute('aria-label') || e.innerText || '');
  const els = [...document.querySelectorAll('a,button')].filter(e => e.offsetParent !== null);
  const ext = els.find(e => e.tagName === 'A' && /apply on company website/i.test(label(e)));
  const easy = els.find(e => /^easy apply\\b/i.test(label(e).trim()));
  const closed = /no longer accepting|no longer available|not (?:currently )?accepting applications/i.test((document.querySelector('main') || document.body).innerText.slice(0, 3000));
  return {ext: !!ext, easy: !!easy, closed, href: ext ? ext.href : ''};
}"""


def load_cache() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.is_file() else {}


def save_cache(c: dict) -> None:
    CACHE.write_text(json.dumps(c, indent=1), encoding="utf-8")


def logged_in(page) -> bool:
    return not any(s in page.url for s in ("/login", "/authwall", "/checkpoint", "/uas/"))


async def check() -> int:
    from job_agent.apply.browser import BraveSession
    async with BraveSession() as s:
        page = await s.new_tab("https://www.linkedin.com/feed/")
        await asyncio.sleep(2)
        ok = logged_in(page) and "feed" in page.url
        print("logged in" if ok else f"NOT logged in (landed on {page.url})")
        await page.close()
        return 0 if ok else 1


async def resolve_one(s, url: str) -> tuple[str, str]:
    page = await s.new_tab(url)
    decoded = ""
    try:
        await asyncio.sleep(random.uniform(2.5, 4))
        if not logged_in(page):
            return "blocked", "login wall"
        if "429" in (await page.title()):
            return "blocked", "429"
        info = await page.evaluate(JS_FIND)
        if not info["ext"]:
            if info["easy"]:
                return "easy_apply", ""
            return ("closed", "") if info["closed"] else ("no_apply_button", "")
        decoded = unquote(parse_qs(urlsplit(info["href"]).query).get("url", [""])[0])  # the destination LinkedIn's redirect points at
        async with page.expect_popup(timeout=15000) as popup:
            await page.evaluate("""() => { const e = [...document.querySelectorAll('a')].find(x => x.offsetParent !== null && /apply on company website/i.test(x.getAttribute('aria-label') || x.innerText || '')); e.click(); }""")
        new = await popup.value
        try:
            await new.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(1.5)
        final = new.url
        await new.close()
        if "linkedin.com" in final or final.startswith("about:"):  # redirect not finished: use the destination LinkedIn's own link names
            final = decoded or final
        return "external", final
    except Exception as e:  # no popup: same-tab navigation, or the click did nothing
        if "linkedin.com" not in page.url:
            return "external", page.url
        return ("external", decoded) if decoded else ("no_apply_button", type(e).__name__)
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def run(master: str, count: int, workers: int = 3) -> None:
    from job_agent.apply.browser import BraveSession
    cache = load_cache()
    rows = [r for r in csv.DictReader(open(master, encoding="utf-8")) if "linkedin.com/jobs/view" in r["URL"] and not r["Status"] and r["URL"] not in cache]
    todo = rows[:count]
    print(f"{len(rows)} LinkedIn rows left; doing {len(todo)} with {workers} at a time")
    queue = iter(enumerate(todo, 1))
    stop = asyncio.Event()

    async def worker(s) -> None:
        for i, r in queue:  # the shared iterator hands each job to exactly one worker
            if stop.is_set():
                return
            try:
                result, resolved = await resolve_one(s, r["URL"])
            except Exception as e:  # browser hiccup: skip this job without caching it, so a rerun retries it
                print(f"[{i}] skipped (not saved): {type(e).__name__}", flush=True)
                continue
            if result == "blocked":
                stop.set()
                print(f"[{i}] STOP: {resolved}. Nothing saved for this job.", flush=True)
                return
            cache[r["URL"]] = {"result": result, "resolved": resolved if result == "external" else "", "at": datetime.now().isoformat(timespec="minutes")}
            save_cache(cache)
            print(f"[{i}/{len(todo)}] {r['Company']} | {result} | {resolved[:90]}", flush=True)
            await asyncio.sleep(random.uniform(2, 3))

    async with BraveSession() as s:
        await asyncio.gather(*(worker(s) for _ in range(workers)))


def export(master: str, out: str) -> None:
    cache = load_cache()
    rows = list(csv.DictReader(open(master, encoding="utf-8")))
    cols = list(rows[0].keys()) + ["Resolved apply URL"]
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            c = cache.get(r["URL"], {})
            r["Resolved apply URL"] = c.get("resolved") or ({"easy_apply": "(LinkedIn Easy Apply)", "closed": "(closed)", "no_apply_button": "(no apply button)"}.get(c.get("result"), ""))
            w.writerow(r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("run"); p.add_argument("master"); p.add_argument("--count", type=int, default=50); p.add_argument("--workers", type=int, default=3)
    p = sub.add_parser("export"); p.add_argument("master"); p.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "check":
        sys.exit(asyncio.run(check()))
    elif a.cmd == "run":
        asyncio.run(run(a.master, a.count, a.workers))
    else:
        export(a.master, a.out)
