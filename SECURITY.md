# Security Policy — job-agent

## 🛡️ Safety is Job 1

This document defines security guardrails for installing skills, MCP tools, and agents.

---

## Skill Installation Checklist

Before installing ANY skill, verify:

### 1. Source Verification
- [ ] Source repo has >100 stars OR >100 installs
- [ ] Reputable source (official org, well-known developer)
- [ ] README and documentation present
- [ ] Recent commits (active maintenance)

### 2. Security Scan (REQUIRED)
- [ ] Run AIDefence scan: `aidefence_scan` with full description
- [ ] Check for threats: malicious code, secret exfiltration, overreaching permissions
- [ ] Verify PII detection: no credential harvesting

### 3. Permission Audit
Skills run with **FULL agent permissions**. Watch for:
- ❌ File system write access beyond project
- ❌ Network calls to unknown hosts
- ❌ Credential/API key access requests
- ❌ Shell command execution
- ✅ Sandboxed execution preferred

### 4. Install Verification
- [ ] Install with `--dry-run` if available
- [ ] Check npm/package install logs
- [ ] Verify files installed match expected
- [ ] Test in isolated environment first

---

## MCP Tool Safety

### Before Adding MCP Tools:
1. Verify tool source and reputation
2. Check required permissions
3. Audit network access
4. Review data handling

### Red Flags:
- Tools requesting excessive permissions
- Unknown network destinations
- Credential access without clear purpose
- File system access beyond project scope

---

## Data Protection Rules

### Protected Data (NEVER expose):
- API keys (`OPENROUTER_API_KEY`, etc.)
- User credentials
- `data/resume_inventory.json`
- `data/applications.json`
- `.env` files

### Allowed Operations:
- Read project files for analysis
- Write to `outputs/` directory
- Modify source code in `src/`
- Update `data/resume_inventory.json` (with user consent)

---

## Quarterly Audit Checklist

- [ ] Review installed skills for updates
- [ ] Remove unused skills
- [ ] Check for deprecated tools
- [ ] Update security rules as needed

---

## Incident Response

If a security issue is detected:

1. **IMMEDIATE**: Remove suspicious skill/tool
2. **ASSESS**: Check what data was accessible
3. **ROTATE**: Change any exposed credentials
4. **REPORT**: Document incident in project

---

## Quick Commands

```powershell
# Security scan a skill before install
# Use AIDefence MCP: aidefence_scan

# Check installed skills
npx skills list

# Remove suspicious skill
npx skills remove <skill-name>
```

---

**Last Updated:** 2026-03-26
