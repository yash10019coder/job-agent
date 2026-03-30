# job-agent

Automated job search tool: job scraping, ATS scoring, resume tailoring, and application tracking.

## Features

- **Job Discovery**: Search LinkedIn, Indeed, Glassdoor + scrape company career pages
- **ATS Scoring**: Keyword matching and job fit analysis
- **Resume Tailoring**: Auto-generate tailored resumes per job
- **Application Tracking**: Track all applications with status
- **PDF Generation**: LaTeX-based professional resumes

## Quick Start

See [INSTALL.md](INSTALL.md) for full setup instructions.

```powershell
# Install
git clone <repo-url>
cd job-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .

# Configure
cp .env.example .env
# Edit .env - add OPENROUTER_API_KEY (free at openrouter.ai/keys)

# Search jobs
.venv\Scripts\python.exe -m job_agent search-jobs --query "Backend Engineer" --location "India" --sites "linkedin"

# Generate resumes
.venv\Scripts\python.exe -m job_agent generate-resumes --output-dir outputs/resumes
```

## Project Structure

```
job-agent/
├── src/job_agent/           # Source code
│   ├── cli.py              # CLI commands
│   ├── scraping.py         # Job scraping (JobSpy + Scrapling)
│   ├── tailoring.py        # Resume tailoring + ATS scoring
│   ├── latex.py           # Resume PDF generation
│   └── tracker.py          # Application tracking
├── data/
│   └── resume_inventory.json   # Your resume data (edit this!)
├── resume/
│   └── template.tex.j2        # LaTeX template
├── tools/
│   └── tectonic/               # Bundled PDF engine
├── tests/                      # Test suite
└── outputs/                    # Generated resumes
```

## Customization

Edit `data/resume_inventory.json` with your information:
- Contact details
- Education
- Work experience
- Projects
- Skills

## License

MIT
