---
name: job-agent
description: AI-powered job search automation with ATS scoring, resume tailoring, and application tracking
trigger: job search, resume, ATS, job application, career
version: 1.0.0
---

# Job Agent Skill

Specialized skill for automated job search, resume generation, and application tracking.

## Capabilities

### Resume Generation
- Generate ATS-optimized resumes in LaTeX/PDF format
- Create multiple lane variants (C++ backend, Fullstack)
- Always produces 3 relevant projects per resume
- Tailor content based on job requirements

### Job Discovery
- Search LinkedIn, Indeed, Glassdoor via jobspy
- Discover jobs from company career boards (Greenhouse, Lever)
- Find careers pages from company URLs
- Analyze job postings for ATS compatibility

### Application Tracking
- Track applied jobs with status
- Store notes, follow-ups, salary info
- Export to CSV for spreadsheet analysis
- Summarize application pipeline

### ATS Scoring
- Keyword matching against job descriptions
- Lane matching (C++ vs Fullstack)
- Content quality analysis
- Gap identification for missing skills

## Usage

```powershell
# Generate resumes
job-agent generate-resumes --output-dir outputs/resumes

# Search jobs
job-agent search-jobs --query "Backend Engineer" --location "India" --sites "linkedin"

# Analyze a job
job-agent analyze-job --url "https://..." --track --use-agents

# Track applications
job-agent list-applications
job-agent update-application --company "Google" --title "SRE" --status "interview"
```

## Configuration

- Resume inventory: `data/resume_inventory.json`
- LaTeX template: `resume/template.tex.j2`
- Tracker DB: `outputs/tracker/applications.json`

## Key Principles

1. **Always 3 projects** - Resume shows exactly 3 projects, selected by relevance
2. **ATS-first** - Optimize for keyword matching before aesthetics
3. **Lane-based** - Separate C++ and Fullstack targeting strategies
4. **Track everything** - Every application should be logged

## Data Structure

Resume inventory JSON contains:
- Contact info (name, email, phone, location)
- Education (degree, institution, year)
- Skills (categorized: Languages, AI/ML, Cloud, Backend, etc.)
- Projects (title, tech stack, description, metrics)
- Experience (company, role, dates, bullets)
