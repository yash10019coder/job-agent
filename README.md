# job-agent

`job-agent` generates two one-page resume variants from a shared inventory and uses Scrapling to fetch and normalize job descriptions before classifying, scoring, and tailoring the right resume lane.

## What it does

- Generates `resume_cxx` and `resume_fullstack_backend` from one structured inventory
- Keeps shared facts identical across variants while changing emphasis, ordering, and supporting proof points
- Fetches job URLs with Scrapling using static, stealth, and dynamic modes
- Discovers job posting links from Greenhouse, Lever, and company board/search pages
- Follows one hop into company-hosted careers hubs like "View Open Roles" and probes common careers paths when needed
- Finds likely external ATS boards from company homepages and careers pages
- Normalizes company, Greenhouse, and Lever pages into one `JobPosting` shape
- Produces a `TailoringDecision`, ATS-style match score, optional specialized-agent notes, tailored LaTeX, and a Markdown report per job
- Tracks discovered and applied roles in a local application tracker with history, summaries, and CSV export
- Supports broader optional ingestion through JobSpy-backed search

## Quickstart

```powershell
.venv\Scripts\python.exe -m pip install -e .
job-agent generate-resumes
job-agent analyze-job --url "https://boards.greenhouse.io/<company-slug>/jobs/<job-id>"
job-agent discover-jobs --search-url "https://jobs.lever.co/<company-slug>" --analyze --use-agents
job-agent discover-jobs --search-url "https://jobs.netflix.com/" --analyze --use-agents
job-agent find-boards --url "https://company.com"
job-agent analyze-job --url "https://company.com/careers/backend-role" --track
job-agent list-applications
job-agent tracker-summary
job-agent export-applications
job-agent search-jobs --query "backend engineer" --location "India" --sites "google,linkedin,indeed"
```

To enable broader JobSpy search locally:

```powershell
.venv\Scripts\python.exe -m pip install python-jobspy
```

If that install fails on Python 3.13, use a Python 3.11 or 3.12 virtualenv for the JobSpy-backed search flow.

Replace `<company-slug>` and `<job-id>` with real values. Literal placeholders like `jobs.lever.co/company`
or `boards.greenhouse.io/example` return 404s from the job board itself.

## Layout

- `data/resume_inventory.json`: structured evidence, skills, metrics, and lane metadata
- `resume/template.tex.j2`: single-column LaTeX template for both resume variants
- `src/job_agent/`: renderer, models, scraping, parsing, board finding, scoring, tracking, and CLI
- `tests/`: regression coverage for variant generation and job normalization
- `CLAUDE.md` and `docs/ruflo-setup.md`: repo-level swarm/orchestration setup for Ruflo/claude-flow style workflows
