# job-agent — Development Notes

## Quick Commands

```powershell
# Search jobs
.venv\Scripts\python.exe -m job_agent search-jobs --query "Backend Engineer" --location "India" --sites "linkedin"

# Analyze & tailor
.venv\Scripts\python.exe -m job_agent analyze-job --url "URL" --track --use-agents

# Generate resumes
.venv\Scripts\python.exe -m job_agent generate-resumes --output-dir outputs/resumes

# Track applications
.venv\Scripts\python.exe -m job_agent list-applications
.venv\Scripts\python.exe -m job_agent tracker-summary

# Run tests
.venv\Scripts\python.exe -m pytest tests/ -v
```

## Architecture

```
src/job_agent/
  cli.py            # CLI entry points
  models.py         # Pydantic models
  scraping.py       # Job scraping (JobSpy + Scrapling)
  parsing.py        # Job description parsing
  tailoring.py      # Resume tailoring + ATS scoring
  latex.py         # Resume PDF generation
  tracker.py       # Application tracking
  board_finder.py  # Company career page discovery
  inventory.py     # Resume inventory loader
```

## Key Rules

- **NEVER delete** `data/resume_inventory.json` or `data/applications.json`
- **NEVER commit** secrets or `.env`
- Keep files under 500 lines
