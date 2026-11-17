"""Write autopilot outcomes into the jobs CSV: adds 'Apply URL', 'Status', 'Detail', 'Updated at' columns, matched by posting identity.
Usage: update_sheet.py <csv> <resolved.json> <autopilot results.jsonl> [more results.jsonl ...]"""
import csv, json, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from job_agent.apply.submit import _posting_key

def main(csv_path, resolved_path, *result_paths):
    resolved = json.load(open(resolved_path, encoding="utf-8"))
    latest = {}
    for rp in result_paths:  # later files win
        for line in open(rp, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            latest[_posting_key(r["url"])] = r
    p = Path(csv_path)
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    cols = ["Apply URL", "Status", "Detail", "Updated at"]
    fields = list(rows[0].keys()) if rows else []
    fields += [c for c in cols if c not in fields]
    now = datetime.now().isoformat(timespec="minutes")
    for row, job in zip(rows, resolved):
        r = latest.get(_posting_key(job["url"]))
        row["Apply URL"] = job["url"]
        if r:
            row["Status"], row["Detail"], row["Updated at"] = r.get("status", ""), str(r.get("detail", ""))[:200], now
        else:
            row.setdefault("Status", "")
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    done = sum(1 for r in rows if r.get("Status"))
    print(f"{done}/{len(rows)} rows have a status")

if __name__ == "__main__":
    main(*sys.argv[1:])
