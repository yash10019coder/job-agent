# Auto-apply rules

How the engine in `src/job_agent/apply/` behaves, for one job and for many. Written 2026-11-16 from what was actually run.
Items marked **(uncommitted)** exist in the working tree but are not committed yet.

## 1. Rules that apply to every run (single or multi)

### What the agent will never do
- Never solve a captcha. A visible challenge (hCaptcha, Cloudflare, "I'm not a robot" reCAPTCHA) stops the job as `blocked_captcha` / needs-human. The invisible reCAPTCHA badge does not count.
- Never touch SSO ("Sign in with Google" etc.).
- Never log in more than once per site (no retry, to avoid lockouts). A site where an account already exists is handed to a human.
- Never guess a required answer. A required field with no honest answer (profile fact, derived fact, `answer_policy`, EEO decline) is `BLOCKED`, and the job is not submitted.
- Never submit unless `--submit` is given (dry run is the default everywhere).
- Never press Submit when any guard fails (see below).
- Never delete `data/resume_inventory.json` or `data/applications.json`; never commit `.env` or secrets.

### Guards before every Submit (`submit.check_ready`)
All must pass, otherwise the job is `blocked_guard` and nothing is sent:
1. Every filled answer succeeded; no failed fills; no low-confidence answers.
2. No required field is still empty; no field shows an error.
3. No page alert is showing; no visible captcha.
4. Exactly one enabled Submit control (zero or several = ambiguous = refuse).
5. The posting was not already submitted (see "Same job" below).
6. Rate limiter allows it (unless `--no-limits`).

### After Submit
- `submitted` only when a confirmation is detected (thank-you text or a `/confirmation|thank|submitted|success|applied` URL). No confirmation is never assumed to be success: it is reported as `unknown_outcome` ("stuck").
- Site rejection with field errors becomes `error_validation`. The same errors three times in a row stop the job. A server-side "try again later" error gets one patient retry (40 s), never more, to avoid duplicates.
- **Emailed security code** (any vendor): if a new code field appears after Submit (labeled "Security code" / "verification code" / "one-time" / OTP, or an unlabeled row of 4 to 10 single-character boxes next to "security code" text), the agent reads the newest matching email from Gmail, types it, re-runs the same guards, and presses Submit once more. One attempt only.
  - Code retrieval uses read-only Gmail (`gmail_token.json`) and only accepts mail that arrived after the click.
  - The click, email and second click are serialised per company (a lock), so two parallel applications to the same company cannot take each other's code.
  - No email in time, or no Gmail connection, leaves the job for a human.
- A wrong code is reported as a site rejection and is not retried.

### Same job = same Greenhouse job id
The duplicate key is `greenhouse:<id>` taken from `?gh_jid=<id>` or `/jobs/<id>`, so a company-page link and a `job-boards.greenhouse.io` link for the same job are one posting. Other sites use host + path (query ignored). The check reads `outputs/apply/submissions.jsonl` (entries with status `submitted`).
Known limits: applications made by hand or elsewhere are not known unless logged there; the tracker and Gmail are not consulted.

### Form-filling behaviour
- Standard fields come from `data/applicant_profile.json` + `resume_inventory.json` without the model (identity, location, links, years of experience, policy answers).
- The model is Gemini Flash-Lite (`gemini-3.5-flash-lite`, Vertex key `GEMINI_API_KEY` in `.env`). Long option lists: the model states the answer in words and the widget is matched afterwards.
- Date boxes asked as separate parts ("Start date year / month / day"): an answer that means "now" becomes today's year / month / day. **(uncommitted)**
- Dropdown options are read in one atomic pass (a re-rendering list used to cause an 8 s timeout and a false "could not fill: Country"). **(uncommitted)**
- Account creation happens only with `--submit` or `--allow-signup`; the password is saved to the OS keyring before the signup is submitted and is never logged.

### Browser rules
- Separate Brave profile `job-agent` on CDP port 9222. Never the real profile (cloning it broke the user's logins).
- One window per job. Parallelism is hard-capped at **10** (`MAX_PARALLEL`) whatever is asked.
- Pages opened by one job only count if that job's page opened them (jobs share a browser context).

### Costs
- A finished job writes `cost_usd` on its final trace line; killed jobs write nothing, so totals under-count. Typical applied job about $0.006; a captcha-blocked Okta job about $0.04.
- Only totals are stored, not per-call costs.

## 2. Single job

Three ways, from safest to most automatic. All use the same engine and guards above.

| Goal | Command | Notes |
|---|---|---|
| Look at each stage | `apply-steps --url <URL> [--until open\|perceive\|prefill\|resolve\|fill\|guards\|submit]` | Pauses after each stage in your Brave tab. Real submit needs `--allow-submit` and you type `submit`. Non-interactive submit also needs `--confirm-url <exact posting URL>`, otherwise refused. |
| Fill one form | `apply-demo --url <URL> --resume <PDF> [--submit] [--keep-open]` | Dry run unless `--submit`. `--keep-open` leaves the filled form open until you press Enter. |
| Full engine on one link | `autopilot --jobs one.json --resume <PDF> --submit` | `one.json` = `[{"url","company","title","location"}]`. Handles hops, login/signup, emailed codes, confirmation. This is how the Databricks test was run. |

Single-job rules:
- Use a dry run first when the form type is new.
- A one-job list still goes through duplicate, liveness and guard checks.
- Use a list that contains only the job(s) the user named.

## 3. Many jobs (autopilot)

```
.venv/bin/python -m job_agent autopilot --jobs <list.json> --resume <PDF> \
    --submit --concurrency 5 [--keep-open --keep-max 15] [--no-limits]
```
Without `--jobs` it uses every direct (non-aggregator) link in `data/*_jobs.json`.
Related commands: `apply-batch` (many jobs, dry run unless `--submit`, logs to `data/runs/`), `apply-run` (generic loop, dry run only).

### Order of checks per job
1. Duplicate in the list, then **fit gate**, then **already applied**, then **liveness** (HTTP probe: 404/410, or a redirect to the board with `?error=true` = dead). All happen before any window opens.
2. Vague locations ("Hybrid", empty) pass the gate but are checked against the job page, which must mention India.
3. Daily cap, then rate limiter, then a random pace delay between starts.
4. Open a window, run the generic loop (hop through Apply links up to 3 times, classify each page, fill, guards, submit, confirm).
5. Close or keep the window (below).

### Fit gate (default on; `--no-fit-check` or `--no-limits` turn it off)
- Title must look like software / backend / full-stack / AI-ML / platform / data / DevOps / SRE (plus any `job_filters` in the profile).
- Rejected title words: principal, staff, distinguished, director, head of, VP, manager, intern, trainee, sales, marketing, recruiter, support, customer, designer, analyst, QA, SDET, test, hardware, embedded, network, solutions, forward deployed, lead, new grad, and similar.
- Location must be India (or an Indian city) or open remote; a clearly foreign location is rejected.

### Rate limits (default on; all off with `--no-limits`)
- 1 application per company per 24 h (shared boards use the first path segment as the company).
- 4 per hour. Per day: Greenhouse 15, LinkedIn 20, Lever 15, Ashby 15, Workday 10, Indeed 25.
- 12 h grace period after a limit is hit.
- Autopilot's own `--max-per-day` (default 20) and `--pace` (default 60 s max gap).

### `--no-limits` means
No rate limiter, no daily cap, no pacing, no fit gate. **Still on:** concurrency cap of 10, duplicate / already-applied / dead-link skips, and every guard, captcha rule and account rule in section 1. **(uncommitted)**

### Windows (`--keep-open`, `--keep-max N`) **(uncommitted)**
- Without `--keep-open` every window closes after its job.
- With it, only jobs that failed or need a human keep their window, and only the newest `--keep-max` (default 15) stay; the oldest closes first. Applied, skipped, dead and closed jobs always close.
- Memory: each window is heavy. At concurrency 10 with every window kept, memory reached about 21 GB of 31 GB; use 5 and the cap.

### Outputs
- `data/autopilot/<time>/<n>/trace.jsonl`: per-step trace of each job (`final` line has outcome and cost).
- `data/autopilot/<time>/results.jsonl` and `report.md`: **only written when the whole run finishes.** A run that is killed leaves traces but no report.
- `outputs/apply/submissions.jsonl`: the submit log and the duplicate check. Append to it; never replace it.
- Sheet: `tools/resolve_sheet_links.py <csv>` turns aggregator links into real apply links (`*.resolved.json`); `tools/update_sheet.py <csv> <resolved.json> <results.jsonl...>` writes `Apply URL`, `Status`, `Detail`, `Updated at` into the CSV.

### Status words
`applied`, `unknown_outcome` (clicked Submit, no confirmation seen), `blocked` (guard or fill failure), `needs_human` (captcha, account, unrecognised page), `skipped` (fit, duplicate, rate limit, by instruction), `dead_link`, `closed`.

## 4. Operating rules (learned the hard way)
- Run exactly the list the user named. Never restart a whole batch to "apply a fix"; to retry two jobs, pass a two-link list.
- Never use a bare `pkill -f` containing the command text from inside the same shell; it kills the shell. Find the PID with `ps`, then `kill <pid>`.
- Before a restart, say what will be interrupted: forms being filled are lost.
- Sites known to be captcha-gated (Okta) are skipped on purpose; do not spend model calls on them.
- `unknown_outcome` does not mean "not sent". MongoDB sends the application but its confirmation page is not recognised. Check the mailbox for the company's "received your application" email before retrying, and log confirmed sends in `submissions.jsonl` so they are not applied to twice.
- A resume PDF must be named explicitly; the path is checked before any browser work.
- Job links go stale within days; liveness is checked first.

## 5. Known gaps
- MongoDB confirmation page is not recognised (jobs end as `unknown_outcome`). Fix idea: confirm by finding the company's "received your application" email after the click.
- Okta: captcha-gated, always blocked.
- Elastic company pages: "more than 3 Apply hops".
- Waymo: two jobs ended `needs_human`.
- Per-call cost is not logged; killed runs lose their cost record.
- Duplicate check does not see applications made outside this tool.
