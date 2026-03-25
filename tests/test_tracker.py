from pathlib import Path

from job_agent.models import ApplicationStatus, FetchMode
from job_agent.tailoring import analyze_job_url
from job_agent.tracker import list_applications, update_application_status, upsert_report


def test_tracker_upserts_and_updates_application(monkeypatch) -> None:
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

    base_dir = Path(".pytest-tracker")
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
    assert report.ats_analysis is not None
    assert report.ats_analysis.score_breakdown["ats_score"] == report.decision.ats_score
    tracker_path = base_dir / "tracker" / "applications.json"
    _, record, _ = upsert_report(report, path=tracker_path, notes="Strong backend match")
    assert record.company == "Acme"
    assert record.status is ApplicationStatus.REVIEW

    _, updated, _ = update_application_status(
        company="Acme",
        title="Backend Engineer",
        status=ApplicationStatus.APPLIED,
        path=tracker_path,
        notes="Applied via referral",
    )
    assert updated.status is ApplicationStatus.APPLIED
    assert updated.notes == "Applied via referral"

    records = list_applications(path=tracker_path, status=ApplicationStatus.APPLIED)
    assert len(records) == 1
    assert records[0].ats_score is not None
