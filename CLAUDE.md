# job-agent — Claude + Cline Configuration

## Engineering Philosophy (ALL sessions)
Elon Musk 5-step: **Question → Delete → Simplify → Accelerate → Automate**
Never optimize or automate what should be deleted.

## Agent Division of Labor

**Cline is the PRIMARY agent** — free with MiniMax, handles all AI work.

| Component | Who Handles | Cost |
|-----------|-------------|------|
| **Cline** | AI analysis, code, debugging, resume updates | Free |
| **job-agent** | Scraping, deduplication, ATS scoring, lane matching | Free |
| **OpenRouter** | Not used (Cline handles everything) | $0 |

## How It Works

**job-agent** (Python) handles:
- Job scraping (jobspy: LinkedIn, Indeed, Glassdoor)
- Deduplication (URL + title matching)
- ATS scoring (keyword matching)
- Lane matching (C++ vs Fullstack)
- Resume generation (LaTeX → PDF)
- Application tracking

**Cline** handles (via built-in MiniMax):
- Complex job analysis
- Cover letter drafting
- Strategic recommendations
- Resume content improvements

## Scraping Stack
1. **jobspy** — LinkedIn, Indeed, Glassdoor, ZipRecruiter (no API key)
2. **Scrapling** — direct URL fallback

## Resume Engine
- Always **3 projects** per resume
- **Broad skills**: Languages + AI/ML + Cloud + Backend + Frameworks + Systems + DevTools
- LaTeX via tectonic (`tools/tectonic/tectonic.exe`)
- Content: `data/resume_inventory.json`

## Memory System
- qmd (global MCP): `qmd search "keyword"` before ANY file read
- Obsidian brain: `C:\Users\omkar\OneDrive\Documents\Claude-Brain\`

## Commands
```powershell
# Job search (no API key needed)
.venv\Scripts\python.exe -m job_agent search-jobs --query "Backend Engineer" --location "India" --sites "linkedin"

# Generate resumes
.venv\Scripts\python.exe -m job_agent generate-resumes --output-dir outputs/resumes

# Application tracking
.venv\Scripts\python.exe -m job_agent list-applications
.venv\Scripts\python.exe -m job_agent update-application --company "Google" --title "Backend" --status "interview"

# Run job discovery (data saved to outputs/)
.venv\Scripts\python.exe -m job_agent discover-jobs --search-url "URL" --limit 25
```

## Architecture
```
src/job_agent/
  ats.py            ← ATS keyword scoring
  tailoring.py      ← job-specific resume tailoring
  scraping.py       ← jobspy + scrapling scraper
  latex.py          ← resume generation (LaTeX → PDF)
  cli.py            ← CLI entry points
  models.py         ← Pydantic data models
  tracker.py        ← application tracking
  discovery.py      ← job board discovery
  board_finder.py   ← find careers pages
  gap_analysis.py   ← skill gap analysis
  jobspy_adapter.py ← LinkedIn/Indeed search
  inventory.py      ← resume inventory loader
  parsing.py        ← job description parsing
```

## Current Resume Scores (as of 3/25/2026)
- C++ lane: ~80 ATS
- Fullstack lane: ~81 ATS
