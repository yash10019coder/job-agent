# job-agent Installation Guide

Automated job search tool: scraping, ATS scoring, resume tailoring, and application tracking.

## Prerequisites

- **Python 3.11+** (3.13 supported, but JobSpy may need 3.11/3.12)
- **Git** (for cloning)
- **LaTeX distribution** or use bundled Tectonic (included)

## Quick Setup

```powershell
# 1. Clone the repo
git clone <repo-url>
cd job-agent

# 2. Create virtual environment
python -m venv .venv

# 3. Activate venv
.venv\Scripts\Activate.ps1  # PowerShell
# OR
source .venv/Scripts/activate  # Git Bash

# 4. Install dependencies
pip install -e .

# 5. Copy and configure environment
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY (free at openrouter.ai/keys)
```

## System Dependencies

### Windows

No additional dependencies required. Tectonic (bundled) handles PDF generation.

### macOS / Linux

```bash
# Ubuntu/Debian
sudo apt-get install build-essential python3-dev

# For JobSpy LinkedIn/Indeed scraping:
sudo apt-get install chromium-browser
```

## Configuration

Edit `.env` with your settings:

```env
# Required for AI-powered job analysis
OPENROUTER_API_KEY=sk_or_v1_your_key_here

# Optional: Notion integration
NOTION_API_TOKEN=your_notion_integration_secret
```

### Getting OpenRouter API Key (Free)

1. Visit [openrouter.ai/keys](https://openrouter.ai/keys)
2. Sign up (free)
3. Copy your API key
4. Uses Qwen3 model - zero cost

## Usage

### Job Search
```powershell
# Search jobs on LinkedIn/Indeed/Glassdoor
.venv\Scripts\python.exe -m job_agent search-jobs --query "Backend Engineer" --location "India" --sites "linkedin" --results 20

# Analyze a job posting
.venv\Scripts\python.exe -m job_agent analyze-job --url "https://jobs.company.com/role/123"

# Discover jobs from company career pages
.venv\Scripts\python.exe -m job_agent discover-jobs --search-url "https://jobs.lever.co/company" --analyze
```

### Resume Generation
```powershell
# Generate resumes from inventory
.venv\Scripts\python.exe -m job_agent generate-resumes --output-dir outputs/resumes

# List tracked applications
.venv\Scripts\python.exe -m job_agent list-applications

# Export applications to CSV
.venv\Scripts\python.exe -m job_agent export-applications
```

### Auto-Apply (Beta)
```powershell
# Auto-apply to jobs (requires browser automation setup)
.venv\Scripts\python.exe -m job_agent auto-apply --url "URL" --resume outputs/resumes/resume.pdf
```

## Project Structure

```
job-agent/
├── src/job_agent/          # Source code
│   ├── cli.py             # CLI commands
│   ├── scraping.py         # Job scraping
│   ├── tailoring.py        # Resume tailoring
│   ├── ats.py             # ATS scoring
│   ├── latex.py           # Resume PDF generation
│   └── tracker.py         # Application tracking
├── data/
│   ├── resume_inventory.json  # YOUR resume data (edit this!)
│   └── applications.json      # Application history
├── resume/
│   └── template.tex.j2        # LaTeX template
├── tools/
│   └── tectonic/               # Bundled PDF engine
├── tests/                      # Test suite
└── outputs/                    # Generated resumes
```

## Customizing Your Resume

Edit `data/resume_inventory.json` with your information:

```json
{
  "contact": {
    "name": "Your Name",
    "email": "your@email.com",
    "phone": "+91-...",
    "linkedin": "https://linkedin.com/in/...",
    "github": "https://github.com/..."
  },
  "education": { ... },
  "skills": [ ... ],
  "roles": [ ... ],
  "projects": [ ... ]
}
```

## Troubleshooting

### JobSpy Install Fails on Python 3.13
Use Python 3.11 or 3.12 for JobSpy functionality:
```powershell
py -3.11 -m venv .venv
```

### LinkedIn Scraping Not Working
JobSpy uses free scraping. Rate limits may apply. Use `--use-agents` flag for AI-assisted analysis.

### PDF Generation Fails
Ensure `tools/tectonic/tectonic.exe` exists. If missing, install LaTeX:
- Windows: [MiKTeX](https://miktex.org/download)
- macOS: `brew install mactex`

## Development

```powershell
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
.venv\Scripts\python.exe -m pytest tests/ -v
```

## License

MIT
