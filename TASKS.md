# job-agent Tasks

## Sprint 2026-03-W13 (Current)

### Backlog
- [ ] Analyze DoorDash job from LinkedIn search | Owner: Claude | Est: 15min
- [ ] Apply to Microsoft LinkedIn (91.6 ATS) | Owner: Claude | Est: 30min
- [ ] Apply to Microsoft Careers (90.8 ATS) | Owner: Claude | Est: 30min
- [ ] Apply to Nutanix (83.2 ATS) | Owner: Claude | Est: 30min
- [ ] Apply to Cisco (80.0 ATS) | Owner: Claude | Est: 30min
- [ ] Set up Gemini CLI for bulk scoring | Owner: Cline | Est: 1h
- [ ] Set up pilot-mcp for browser layer | Owner: Cline | Est: 1h
- [ ] Configure Telegram notification bot | Owner: Cline | Est: 30min

### In Progress
- [ ] Apply to Microsoft LinkedIn (91.6 ATS) | Owner: Claude | Est: 30min

### Done
- [x] MCP servers restored (qmd, obsidian-brain, github, filesystem)
- [x] Agent skill files created (coordinator, cline, gemini, pilot)
- [x] CLAUDE.md rewritten with project-specific config
- [x] Multi-agent architecture setup
- [x] pilot-mcp added to .mcp.json (needs restart)
- [x] TASKS.md restructured with scrum format

### Discussions
> **Claude (2026-03-26):** Consider adding Naukri.com adapter - huge Indian market. Worth prioritizing over DoorDash?
> 
> **Claude (2026-03-26):** Gemini CLI setup blocked - need API key configured. Flagging Omkar.
>
> **Claude (2026-03-26):** Suggestion: Separate Claude Code sessions per agent to avoid context bleeding (learned from showmyspace issues)

---

## Historical Context

### ATS Boost: 87.8 → 91.6 (+3.8 points!)
With semantic skill aliasing and enhanced resume content

### Resume Optimized for One Page
- Streamlined to 6 skill categories (Languages, Backend, Cloud, Databases, Frameworks, Systems)
- Removed redundant skills (e.g., merged APIs into Backend)
- Richer bullets with stronger action verbs (Led, Architected, Engineered, Delivered, Built)

### Rich Cloud TV Device Experience
Updated Samsung role with detailed bullets:
- Cloud TV Device (CTD): Java Spring Boot migration, 40% more connections, sub-200ms API
- Notification Service: Real-time WebSocket, 100% API call reduction, 30% efficiency
- Remote Management: C++, 10K+ devices, zero security incidents
- WebRTC File Sharing: 80% bandwidth reduction, 3x performance

---

## Job Analysis Results

| Job | ATS Score | Lane | Status |
|-----|-----------|------|--------|
| **Microsoft (LinkedIn)** | **91.6** 🏆 | Fullstack | ⏳ Pending |
| **Microsoft (Careers)** | **90.8** | Fullstack | ⏳ Pending |
| **Nutanix** | **83.2** | Fullstack | ⏳ Pending |
| **Cisco** | **80.0** | Both | ⏳ Pending |
| **PayPal** | **45.0** | Fullstack | ⚠️ Low match |

### Stale/Problematic Links
- ❌ Cisco #2 (410 Gone)
- ❌ Fidelity (404)
- ❌ Nvidia (Workday - JS rendering)
- ❌ BrowserStack (Workday - manual)

### Fresh Jobs Found (LinkedIn Search)
- Microsoft 365 Copilot Chat (91.6 ATS)
- Microsoft Dynamics 365 BC
- Microsoft OneLake/Azure Data
- DoorDash (needs analysis)

---

## 🧪 Testing Commands

```powershell
# Generate resume (fullstack_backend)
.venv\Scripts\python.exe -m job_agent generate-resumes --output-dir outputs/resumes

# Analyze job
.venv\Scripts\python.exe -m job_agent analyze-job --url "URL" --mode auto --track

# List applications
.venv\Scripts\python.exe -m job_agent list-applications

# Search fresh jobs
.venv\Scripts\python.exe -m job_agent search-jobs --query "Software Engineer" --location "India" --sites "linkedin" --results 15
```

---

## Multi-Agent System

| Agent | Skill | Status |
|-------|-------|--------|
| Claude Code | `skills/job-agent-coordinator/` | ✅ Active |
| Cline | `skills/job-agent-cline/` | ✅ Configured |
| Gemini CLI | `skills/job-agent-gemini/` | ⏳ Setup pending |
| pilot-mcp | `skills/job-agent-pilot/` | ⏳ Setup pending |

---

*Last updated: 2026-03-26 by Claude Code (multi-agent setup)*
