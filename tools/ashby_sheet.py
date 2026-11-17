"""One sheet for all Ashby jobs: data/ashby_sheet.csv. Rows are the source of truth for what to apply to and what happened.

  ashby_sheet.py build                    merge every Ashby dump into the sheet (keeps existing rows/statuses), then sync past outcomes
  ashby_sheet.py sync                     copy outcomes from data/autopilot/*/results.jsonl into the Status column
  ashby_sheet.py apply --count N [...]    pick the next N fitting rows with no status, run autopilot on them, write results back
  ashby_sheet.py show                     counts per status

Status: (empty)=todo | applied | unconfirmed (Submit clicked, no confirmation: check email, never auto-retried) | failed | skipped
"""
import argparse, csv, glob, json, re, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from job_agent.apply.submit import _posting_key  # noqa: E402

SHEET = ROOT / "data" / "ashby_sheet.csv"
COLS = ["URL", "Company", "Title", "Location", "Source", "Fit", "Status", "Detail", "Cost USD", "Updated at"]
INDIA = re.compile(r"india|bengal|bangalore|mumbai|hyderabad|delhi|gurgaon|gurugram|noida|pune|chennai|remote", re.I)
ENG = re.compile(r"engineer|developer|sde|software", re.I)
TOO_SENIOR_OR_OTHER = re.compile(r"manager|director|head of|\bvp\b|principal|staff|architect|intern\b|marketing|sales|recruit|designer", re.I)


def fit(title: str, location: str) -> str:
    return "yes" if INDIA.search(location or "") and ENG.search(title or "") and not TOO_SENIOR_OR_OTHER.search(title or "") else "no"


def read_sources() -> list[dict]:
    out = []
    for f in ("sde_ashby_global.json", "ashby_jobs.json"):
        p = ROOT / "data" / f
        if p.is_file():
            for j in json.load(p.open(encoding="utf-8")):
                out.append({"URL": j["job_url"], "Company": j.get("company", ""), "Title": j.get("job_title", ""), "Location": j.get("location", ""), "Source": f})
    for f in glob.glob(str(ROOT / "data" / "ashby_india_*.csv")):
        for r in csv.reader(open(f, encoding="utf-8")):
            url = next((c for c in r if "jobs.ashbyhq.com" in c), "")
            if url and len(r) >= 3:
                out.append({"URL": url, "Company": r[1], "Title": r[0], "Location": r[2], "Source": Path(f).name})
    return out


def load() -> list[dict]:
    return list(csv.DictReader(SHEET.open(encoding="utf-8"))) if SHEET.is_file() else []


def save(rows: list[dict]) -> None:
    with SHEET.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, quoting=csv.QUOTE_ALL, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def status_of(r: dict) -> str:
    if r.get("status") == "applied":
        return "applied"
    if r.get("status") == "skipped":
        return "skipped"
    return "unconfirmed" if "neither a confirmation" in str(r.get("reason", "")) else "failed"


def sync(rows: list[dict], result_files: list[str] | None = None) -> int:
    files = result_files or sorted(glob.glob(str(ROOT / "data" / "autopilot" / "*" / "results.jsonl")))
    latest = {}
    for f in files:  # folders are timestamped, later wins
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("url"):
                latest[_posting_key(r["url"])] = r
    changed = 0
    now = datetime.now().isoformat(timespec="minutes")
    for row in rows:
        r = latest.get(_posting_key(row["URL"]))
        if r and row.get("Status") != status_of(r):
            row["Status"], row["Detail"], row["Cost USD"], row["Updated at"] = status_of(r), str(r.get("reason", ""))[:200], f'{r.get("cost_usd", 0):.5f}', now
            changed += 1
    return changed


def build() -> None:
    rows = load()
    known = {_posting_key(r["URL"]) for r in rows}
    added = 0
    for j in read_sources():
        k = _posting_key(j["URL"])
        if k not in known:
            known.add(k)
            rows.append({**j, "Fit": fit(j["Title"], j["Location"]), "Status": "", "Detail": "", "Cost USD": "", "Updated at": ""})
            added += 1
    n = sync(rows)
    save(rows)
    print(f"{len(rows)} rows ({added} new), {n} statuses synced -> {SHEET}")
    show(rows)


def show(rows: list[dict] | None = None) -> None:
    rows = rows if rows is not None else load()
    counts = {}
    for r in rows:
        key = r["Status"] or ("todo (fits)" if r["Fit"] == "yes" else "todo (no fit)")
        counts[key] = counts.get(key, 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k:<16}{v}")


def apply(a: argparse.Namespace) -> None:
    rows = load()
    wanted = {""} | ({"failed"} if a.retry_failed else set()) | ({"unconfirmed"} if a.retry_unconfirmed else set())
    todo = [r for r in rows if r["Status"] in wanted and (r["Fit"] == "yes" or a.include_unfit)][: a.count]
    if not todo:
        print("nothing to apply to: no matching rows (run `build`, or loosen flags)")
        return
    jobs = [{"url": r["URL"], "company": r["Company"], "title": r["Title"], "location": r["Location"]} for r in todo]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(jobs, fh)
    cmd = [str(ROOT / ".venv" / "bin" / "python"), "-m", "job_agent", "autopilot", "--jobs", fh.name, "--resume", a.resume,
           "--concurrency", str(a.concurrency)] + ([] if a.dry_run else ["--submit"]) + (["--no-limits"] if a.no_limits else [])
    print(f"applying to {len(jobs)} jobs ({'DRY RUN' if a.dry_run else 'LIVE'})")
    subprocess.run(cmd, cwd=ROOT)
    # Re-read all outcomes (including this run's) and write them to the sheet, even if the run was interrupted.
    rows = load()
    print(f"{sync(rows)} statuses updated")
    save(rows)
    show(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("sync")
    sub.add_parser("show")
    p = sub.add_parser("apply")
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--resume", default="/home/ubuntu/Documents/resume/latex_resume/Yash_Verma_CV_Latest.pdf")
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--dry-run", action="store_true", help="fill forms but do not submit")
    p.add_argument("--no-limits", action="store_true", help="pass --no-limits to autopilot")
    p.add_argument("--include-unfit", action="store_true", help="also take rows where Fit=no")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--retry-unconfirmed", action="store_true", help="risk of duplicate applications")
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    elif a.cmd == "sync":
        rows = load(); n = sync(rows); save(rows); print(f"{n} statuses updated"); show(rows)
    elif a.cmd == "show":
        show()
    else:
        apply(a)


if __name__ == "__main__":
    main()
