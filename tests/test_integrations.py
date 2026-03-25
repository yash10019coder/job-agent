import sys
import types
from pathlib import Path

from job_agent.jobspy_adapter import search_jobs
from job_agent.models import FetchMode, Lane
from job_agent.tailoring import analyze_job_url
from job_agent.tracker import export_tracker_csv, summarize_tracker, upsert_report


def test_search_jobs_uses_jobspy_results(monkeypatch) -> None:
    class FakeFrame:
        def to_dict(self, orient):
            assert orient == "records"
            return [
                {
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "location": "Remote",
                    "job_url": "https://acme.example/jobs/backend-engineer",
                    "description": "Requirements: Python, APIs, microservices, SQL.",
                    "site": "indeed",
                }
            ]

    fake_module = types.SimpleNamespace(
        scrape_jobs=lambda **kwargs: FakeFrame(),
    )
    monkeypatch.setitem(sys.modules, "jobspy", fake_module)
    report = search_jobs(
        search_term="backend engineer",
        location="India",
        sites=["indeed"],
        results_wanted=5,
        output_dir=Path(".pytest-jobspy"),
        lane_filter=Lane.FULLSTACK_BACKEND,
        analyze=False,
    )
    assert len(report.results) == 1
    assert report.results[0].company == "Acme"


def test_tracker_summary_and_export(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <div class="posting-headline"><h2>Backend Engineer</h2></div>
        <div class="posting-categories">
          <div class="sort-by-company">Acme</div>
          <div class="sort-by-location">Remote</div>
        </div>
        <div class="content">
          <p>Requirements: Python, microservices, APIs, SQL.</p>
          <p>Preferred: Docker, Kubernetes.</p>
        </div>
      </body>
    </html>
    """

    from job_agent.scraping import FetchResult, ScraplingClient
    import job_agent.tailoring as tailoring_module

    def fake_fetch(self, url, mode=FetchMode.AUTO):
        return FetchResult(url=url, html=html, mode=FetchMode.STATIC)

    base_dir = Path(".pytest-tracker-summary")
    if base_dir.exists():
        for path in sorted(base_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            else:
                path.rmdir()
        base_dir.rmdir()

    monkeypatch.setattr(ScraplingClient, "fetch", fake_fetch)
    monkeypatch.setattr(tailoring_module, "compile_tex_to_pdf", lambda tex_path, job_dir: job_dir / "resume.pdf")
    report = analyze_job_url("https://jobs.lever.co/acme/backend", FetchMode.AUTO, base_dir / "jobs")
    tracker_path = base_dir / "tracker" / "applications.json"
    upsert_report(report, path=tracker_path, notes="Shortlist this")

    summary = summarize_tracker(tracker_path)
    assert summary.total == 1
    assert summary.by_status

    csv_path = export_tracker_csv(tracker_path, base_dir / "tracker" / "applications.csv")
    assert csv_path.exists()
    assert "Acme" in csv_path.read_text(encoding="utf-8")
