from scrapling import Selector

from job_agent.models import Lane, SourceType
from job_agent.parsing import parse_job
from job_agent.tailoring import build_decision


def test_greenhouse_job_normalizes() -> None:
    html = """
    <html>
      <body>
        <div id="app_body">
          <h1>Software Engineer, Backend</h1>
          <div class="company-name">Acme Cloud</div>
          <div class="location">Remote</div>
          <div id="content">
            <p>Requirements: Python, microservices, REST APIs, SQL.</p>
            <p>Preferred: Kubernetes, Docker.</p>
          </div>
        </div>
      </body>
    </html>
    """
    selector = Selector(html, url="https://boards.greenhouse.io/acme/jobs/123")
    job = parse_job(selector, "https://boards.greenhouse.io/acme/jobs/123")
    assert job.source is SourceType.GREENHOUSE
    assert job.title == "Software Engineer, Backend"
    assert "python" in job.required_skills
    assert "kubernetes" in job.preferred_skills


def test_lever_job_routes_to_cxx() -> None:
    html = """
    <html>
      <body>
        <div class="posting-headline"><h2>C++ Systems Engineer</h2></div>
        <div class="posting-categories">
          <div class="sort-by-company">Device Labs</div>
          <div class="sort-by-location">Bengaluru</div>
        </div>
        <div class="content">
          <p>Requirements: C++, Linux, multithreading, D-Bus, systems programming.</p>
        </div>
      </body>
    </html>
    """
    selector = Selector(html, url="https://jobs.lever.co/devicelabs/abc")
    job = parse_job(selector, "https://jobs.lever.co/devicelabs/abc")
    decision = build_decision(job)
    assert decision.chosen_lane is Lane.CXX
    assert decision.final_recommendation in {"apply", "review"}


def test_low_fit_job_is_skipped() -> None:
    html = """
    <html>
      <body>
        <main>
          <h1>Sales Operations Specialist</h1>
          <p>Requirements: CRM operations, Salesforce, quota planning, revenue operations.</p>
        </main>
      </body>
    </html>
    """
    selector = Selector(html, url="https://company.example/jobs/sales-ops")
    job = parse_job(selector, "https://company.example/jobs/sales-ops")
    decision = build_decision(job)
    assert decision.final_recommendation == "skip"


def test_company_job_uses_json_ld_fallback() -> None:
    html = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Backend Platform Engineer",
            "description": "Requirements: Python, microservices, APIs, SQL. Preferred: Kubernetes, Docker.",
            "hiringOrganization": {"name": "Example Corp"},
            "jobLocation": {
              "@type": "Place",
              "address": {
                "@type": "PostalAddress",
                "addressLocality": "Remote",
                "addressCountry": "India"
              }
            }
          }
        </script>
      </head>
      <body>
        <main><h1>Ignore Me</h1></main>
      </body>
    </html>
    """
    selector = Selector(html, url="https://careers.example.com/backend-platform")
    job = parse_job(selector, "https://careers.example.com/backend-platform")
    assert job.company == "Example Corp"
    assert job.title == "Backend Platform Engineer"
    assert "python" in job.required_skills
