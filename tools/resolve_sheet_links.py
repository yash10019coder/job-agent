"""Resolve the aggregator detail links in a jobs CSV to the real 'Apply on ...' URL; writes <csv stem>.resolved.json for autopilot --jobs."""
import csv, json, re, sys, html
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

APPLY = re.compile(r'<a[^>]*class="[^"]*jobs-action is-primary[^"]*"[^>]*href="([^"]+)"|<a[^>]*href="([^"]+)"[^>]*class="[^"]*jobs-action is-primary', re.S)

def resolve(url: str) -> str:
    try:
        page = urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25).read().decode("utf-8", "replace")
    except Exception as e:
        return ""
    m = APPLY.search(page)
    return html.unescape(next(g for g in m.groups() if g)) if m else ""

def main(path: str) -> None:
    p = Path(path)
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    with ThreadPoolExecutor(6) as ex:
        urls = list(ex.map(resolve, [r["Offerfy detail"] for r in rows]))
    jobs = []
    for r, u in zip(rows, urls):
        company, _, rest = r["Company / location"].partition(" · ")
        jobs.append({"url": u, "company": company, "title": r["Job title"], "location": rest.rsplit(" · ", 1)[0], "detail": r["Offerfy detail"]})
    out = p.with_suffix(".resolved.json")
    out.write_text(json.dumps(jobs, indent=1), encoding="utf-8")
    print(f"{sum(bool(j['url']) for j in jobs)}/{len(jobs)} resolved -> {out}")

if __name__ == "__main__":
    main(sys.argv[1])
