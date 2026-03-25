# Ruflo Setup

This repo is prepared for Ruflo/claude-flow style orchestration, but the actual upstream init command requires Node.js and npm.

## What is already prepared

- A project-specific [CLAUDE.md](C:/Users/omkar/OneDrive/Desktop/job-agent/CLAUDE.md) for swarm coordination
- A bootstrap script: [tools/setup-ruflo.ps1](C:/Users/omkar/OneDrive/Desktop/job-agent/tools/setup-ruflo.ps1)
- Verification commands and repo-specific agent roles tailored to this project

## Local Setup

1. Install Node.js so `node`, `npm`, and `npx` are available.
2. From the repo root, run:

```powershell
.\tools\setup-ruflo.ps1
```

That runs:

```powershell
npx claude-flow@alpha init --verify --pair --force
```

## Why this mode

For this repo, `--verify --pair` is the best default because it matches the real needs:

- researcher/coder/tester/reviewer specialization
- verification-first behavior for resume truthfulness
- concurrent work for scraping, ATS scoring, and tracker features
- cleaner token usage by splitting bounded tasks instead of doing everything in one context

## Current Limitation

In the current environment, Node.js/npm are not available, so the upstream Ruflo init command could not be executed directly here.
