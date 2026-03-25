from pathlib import Path

import pytest

from job_agent.board_finder import find_boards
from job_agent.discovery import discover_jobs
from job_agent.models import FetchMode, Lane
from job_agent.scraping import FetchResult, ScraplingClient


class _FakeResponse:
    def __init__(self, url: str, text: str, status: int) -> None:
        self.url = url
        self.text = text
        self.status = status


def test_discover_job_links_from_board_page(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <a href="https://boards.greenhouse.io/acme/jobs/123">Backend Engineer</a>
        <a href="/acme/jobs/456">Platform Engineer</a>
        <a href="https://example.com/about">About</a>
      </body>
    </html>
    """

    def fake_fetch(self, url, mode=FetchMode.AUTO):
        return FetchResult(url="https://boards.greenhouse.io/acme", html=html, mode=FetchMode.STATIC)

    monkeypatch.setattr(ScraplingClient, "fetch", fake_fetch)
    client = ScraplingClient()
    links = client.discover_job_links("https://boards.greenhouse.io/acme")
    assert links == [
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://boards.greenhouse.io/acme/jobs/456",
    ]


def test_discover_jobs_filters_to_lane(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <a href="https://jobs.lever.co/deviceco/abc">C++ Systems Engineer</a>
      </body>
    </html>
    """
    job_html = """
    <html>
      <body>
        <div class="posting-headline"><h2>C++ Systems Engineer</h2></div>
        <div class="posting-categories">
          <div class="sort-by-location">Remote</div>
        </div>
        <div class="content">
          <p>Requirements: C++, Linux, multithreading, D-Bus.</p>
        </div>
      </body>
    </html>
    """

    def fake_fetch(self, url, mode=FetchMode.AUTO):
        if "deviceco/abc" in url:
            return FetchResult(url=url, html=job_html, mode=FetchMode.STATIC)
        return FetchResult(url=url, html=html, mode=FetchMode.STATIC)

    monkeypatch.setattr(ScraplingClient, "fetch", fake_fetch)
    report = discover_jobs(
        search_url="https://jobs.lever.co/deviceco",
        mode=FetchMode.AUTO,
        output_dir=Path(".pytest-tmp") / "discovery",
        lane_filter=Lane.CXX,
        analyze=False,
    )
    assert len(report.discovered_jobs) == 1
    assert report.discovered_jobs[0].chosen_lane is Lane.CXX


def test_placeholder_lever_url_is_rejected() -> None:
    client = ScraplingClient()
    with pytest.raises(ValueError, match="Invalid Lever URL"):
        client.discover_job_links("https://jobs.lever.co/company")


def test_placeholder_greenhouse_url_is_rejected() -> None:
    client = ScraplingClient()
    with pytest.raises(ValueError, match="Invalid Greenhouse URL"):
        client.discover_job_links("https://boards.greenhouse.io/example")


def test_lever_404_has_actionable_message(monkeypatch) -> None:
    def fake_get(url, verify=False):
        return _FakeResponse(url=url, text="<html>missing</html>", status=404)

    client = ScraplingClient()
    monkeypatch.setattr(client._static, "get", fake_get)
    with pytest.raises(ValueError, match="Try the company's own careers site"):
        client.fetch("https://jobs.lever.co/netflix", mode=FetchMode.STATIC)


def test_discover_job_links_follows_jobs_hub(monkeypatch) -> None:
    landing_html = """
    <html>
      <body>
        <a href="/careers/search">View Open Roles</a>
      </body>
    </html>
    """
    careers_html = """
    <html>
      <body>
        <a href="/?id=7563&jvi=o5vLVfw0">Senior Backend Engineer</a>
      </body>
    </html>
    """

    def fake_fetch(self, url, mode=FetchMode.AUTO):
        if "search" in url:
            return FetchResult(url=url, html=careers_html, mode=FetchMode.STATIC)
        return FetchResult(url="https://jobs.netflix.com/", html=landing_html, mode=FetchMode.STATIC)

    monkeypatch.setattr(ScraplingClient, "fetch", fake_fetch)
    client = ScraplingClient()
    links = client.discover_job_links("https://jobs.netflix.com/")
    assert links == ["https://jobs.netflix.com/?id=7563&jvi=o5vLVfw0"]


def test_discover_job_links_probes_common_careers_paths(monkeypatch) -> None:
    landing_html = "<html><body><p>Welcome</p></body></html>"
    careers_html = """
    <html>
      <body>
        <a href="/jobs/backend-platform-engineer">Backend Platform Engineer</a>
      </body>
    </html>
    """

    def fake_fetch(self, url, mode=FetchMode.AUTO):
        if url.endswith("/careers"):
            return FetchResult(url=url, html=careers_html, mode=FetchMode.STATIC)
        return FetchResult(url="https://company.example", html=landing_html, mode=FetchMode.STATIC)

    monkeypatch.setattr(ScraplingClient, "fetch", fake_fetch)
    client = ScraplingClient()
    links = client.discover_job_links("https://company.example")
    assert links == ["https://company.example/jobs/backend-platform-engineer"]


def test_find_boards_extracts_external_board_candidates(monkeypatch) -> None:
    html = """
    <html>
      <body>
        <a href="https://boards.greenhouse.io/acme">Careers</a>
        <script>window.jobsBoard = "https://jobs.lever.co/acme";</script>
      </body>
    </html>
    """

    def fake_fetch(self, url, mode=FetchMode.AUTO):
        return FetchResult(url=url, html=html, mode=FetchMode.STATIC)

    monkeypatch.setattr(ScraplingClient, "fetch", fake_fetch)
    report = find_boards("https://acme.example", FetchMode.AUTO, Path(".pytest-tmp") / "boards")
    assert [candidate.source for candidate in report.candidates[:2]] == ["greenhouse", "lever"]
