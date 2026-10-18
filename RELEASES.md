# JobPilot releases

Each release is working software. `main` is always the last released state.
The deployed instance runs a tag, never `main`.

## Fallback alert (set up before R1, runs permanently)

A free Naukri saved-search email alert, independent of all JobPilot code.

**This needs a Naukri account, so the seeker or the operator must do it — Claude
does not create accounts or enter credentials.**

1. Sign in at naukri.com (the seeker's own account is best: results are tuned to
   their profile).
2. Search: keyword `merchandiser`, location `Gurgaon`. Repeat for
   `senior merchandiser` and `product development merchandiser`.
3. Set Freshness to "Last 7 days" and Experience to 4 years.
4. Save the search and enable the email alert (daily or weekly).
5. Optionally add a second alert for `Delhi / NCR` — Naukri treats it as a
   separate location from Gurgaon.

Record the date it was switched on here, so the baseline comparison at R3 has
a start point.

- Enabled on: _(fill in)_

## v0.1.0 - R1 Discovery core

Operator-run on the Mac (Python 3.12.11, conda env `jobpilot`). Nothing sends a message to a seeker in
this release; notifications, scoring and the digest start in R2. Tag message: "R1 Discovery core - full
suite green".

### What shipped
- Store (`store/db.py`, SQLite WAL): one posting per real role across boards, identity by
  `(source, external_id)` first and a company/title/city hash second; seeker-scoped links, scores and
  a notification ledger that suppresses a repeat `first` notification per (seeker, posting).
- Per-site JobSpy adapter, one (site, market) pair per source, with descriptions, a URL-scheme
  allowlist and a canary that carries no recency filter.
- Health classification (healthy / degraded / blocked / error) recorded in `source_runs` on every run.
  A drop below 25% of the trailing median, including a drop to zero against a busy history, marks the
  source degraded; zero against an empty history stays healthy. A source error with an empty message
  still counts as an error.
- Discovery pipeline: isolated per site, shared scraping across seekers, seeker profiles
  (`sources` required), target-company matching.
- Pinned dependency: `python-jobspy` at commit fda080a373e8226f3fd60635323f5da9af9892b1.

### Gate evidence
- Full suite: `/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python -m pytest -q` -> 138 passed.
- Real JobSpy capture: `tests/fixtures/jobspy_indeed_us.json`, 10 live Indeed US rows (redacted for
  emails and phones inside the capture script). Verified against real data: id, site, date_posted
  types, description populated 10/10, one row with no company correctly rejected (9 postings).
- Indeed India location probe (Gurgaon): 10 live rows, id/site/date types as expected; locations are
  state level only ("HR, IN", "UP, IN"); 2 of 10 rows had no company and are rejected.
- No live end-to-end run. `run_discovery` has never run against a real site; the live smoke run is R3.
  Only the fixture captures and the location probe touched the network.

### Known gaps in this release (each is loud or contained)
1. **Naukri was not validated against real data.** The Naukri capture was blocked by recaptcha (HTTP
   406) on three calls on 2026-09-19 and was not retried. `tests/fixtures/jobspy_naukri.json` is a
   hand-written stand-in: its id and location shape agree with the pinned JobSpy source, but the
   mapping of description, `date_posted` and real location labels ("Gurgaon/Gurugram", multi-city
   labels) is unproven. If Naukri keeps refusing, its canary reports BLOCKED or ERROR, never a quiet
   market. ONE cool-down retry is approved, but only after Indian phone formats ("98765 43210",
   "(+91) 98765-43210", "09876543210", "91-11-4567-8901", bare 10-digit numbers) are added to the
   redaction and the fail-closed guard in `scripts/capture_fixture.py`, with tests.
2. **KNOWN GAP, R2 Task 1, hard gate: Indeed India dedupe.** Indeed India locations are state level
   ("HR, IN", "UP, IN") and are read as a city, so the same job on Indeed and on Naukri or LinkedIn is
   stored as two postings, and two cities in one state can merge into one. No digest goes to any
   seeker until this is fixed (a parsed city/region/country model). Nothing is sent in R1.
3. **A listing that changes from a country-only location to a specific city can pull other listings of
   the same company and title into one posting.** Narrow trigger; fixed together with item 2.
4. **Rows with no company are discarded**: about 20% of real Indeed India rows had no company and are
   rejected by the adapter. The count is not yet recorded in the run notes.
5. **Not yet exercised:** the independent Naukri fallback email alert (Enabled on: not yet), Windows
   deployment, the MCP server, the smoke script, scoring and the digest (R2 and R3).

### Public interface changes
None from an earlier release (this is the first).

## v0.2.0 - R2 Judgement and digest

Released locally on 2026-09-19 as annotated tag `v0.2.0` at commit `80df9d9`.

### What shipped
- Per-seeker scoring rubrics with safe, path-keyed rubric caching and profile notes.
- An HTML digest renderer sourced from the scored notification ledger, including source-health and stale-source warnings.
- Per-seeker `review_required` migration support for the upcoming operator approval gate.
- A city/region/country location model for cross-board identity, with explicit protection against shared-row rewrites and wrong merges.
- Self-repairing database migration: interrupted location backfills are retried, hashes are recomputed, and parser-induced collisions preserve source keys and seeker history through the normal merge path.
- Region ambiguity is conservative: a state-only sighting adopts only when exactly one city is known in that region; two known cities remain separate.

### Gate evidence
- Full suite: `python -m pytest -q -p no:cacheprovider` -> **269 passed**.
- The high-risk location/identity redesign was independently reviewed; all findings carried into R2 were either fixed with regression tests or explicitly documented as accepted scope trade-offs.
- `main` is fast-forwarded to the tagged R2 commit. No `origin` remote is configured in this checkout, so nothing has been pushed.

### Current operator interface
- Offline safety demo: `python scripts/demo.py`.
- Guarded live smoke test: `python scripts/demo.py --live` (Indeed only; requires a real terminal and typed confirmation).
- R2 scoring and digest are Python library interfaces. There is not yet a UI, Telegram bot, or MCP server; those are R3 work.

## v0.3.0 - R3 Claude-driven on the desktop

Released on 2026-09-19 as annotated tag `v0.3.0` after the Windows deployment
and MCP registration gate.

### What shipped
- FastMCP stdio server with operator and seeker tool surfaces.
- Process-level `JOBPILOT_SEEKER_ID` pinning and verified Telegram-sender
  resolution, preventing cross-seeker roster or job-history access.
- Operator discovery, source-health, scoring, notification, and HTML-digest
  tools with docstrings exposed to the MCP client.
- Windows `run.ps1`, an MCP configuration example, live smoke wrapper, and
  redacted fixture-capture script.
- LinkedIn source entries disabled and excluded from the live server path;
  JobPilot does not contact LinkedIn.
- Naukri India is currently disabled after the Windows canary returned HTTP
  406 (`recaptcha required`); this is an explicit source-health decision, not
  a claim that Naukri has no jobs.

### Gate evidence so far
- Full offline suite: **283 passed**.
- Windows Python 3.12 suite: **283 passed**.
- Guarded Windows smoke: Indeed India **healthy**, 34 normalized results and
  30 links for the seeker. Naukri returned HTTP 406 (`recaptcha required`) and was
  deliberately disabled; it is not presented as a quiet market. No LinkedIn
  requests were made.
- Claude Code MCP registration: `jobpilot` user-scoped server **connected**
  with the pinned Windows interpreter and project paths.
- Repository: `https://github.com/4asaanAI/job-seeker`.

### Operator handoff
- In a new Claude Code session on Windows, ask Claude to call `list_seekers`
  and then `source_health` through the connected `jobpilot` MCP server.
- Run `discover` only when you want a fresh live search; the operator surface
  is intentionally separate from a pinned seeker-facing process.

## Main after v0.3.0 - M2 interface and R4 ATS groundwork

The post-R3 main line adds the seeker-scoped introspection and watchlist tools:
`my_status`, `my_sources`, `my_history`, `add_target_company`,
`investigate_posting`, `verify_still_open`, and `check_company_careers`.
The browser tools are honest handoffs to Claude's one-page browser; they do not
pretend that the MCP server itself performed a live verification.

Gate evidence for the interface groundwork: **287 offline tests passed**.
Wigolo, the resume engine, Telegram transport, and Google Sheets remain
subsequent milestones.

The next R4 increment now adds the vendor-neutral official ATS JSON contract:
config validation, Greenhouse/Lever/Workday/SuccessFactors normalization,
one-response-per-source caching, local term/location filtering, URL safety, and
discovery-pipeline health isolation. No production endpoint is enabled by
default because the exact employer tenant URLs still need operator verification.

Gate evidence: **300 offline tests passed**. No live ATS endpoint was contacted.

The following R4/R5 increment adds structured `JobPosting` JSON-LD career-page
sources, a facts-only structured resume loader, ATS-safe DOCX rendering with a
blocking DOCX/PDF extraction gate, per-seeker resume-version records, and a
plain-text digest for chat delivery. The Windows checkout also has a dedicated
Telegram launcher using the installed official Telegram plugin, strict MCP
configuration, a pinned seeker process, and no built-in tools. No bot token or
Telegram sender was configured automatically.

Gate evidence: **314 offline tests passed**. No live structured-page endpoint,
resume from a real seeker, or Telegram message was used by the test suite.

The M3 channel/scheduling increment adds a plain-text Telegram digest path,
machine-run ledger, bounded non-persistent Claude scheduler, an explicit
JobPilot-only MCP scheduled config, and a credential-free one-way Google Sheets
exporter with direct sharing semantics. The official Telegram plugin was
installed and enabled on Windows, but its bot token remains intentionally
unconfigured.

Gate evidence: **319 offline tests passed**. No Google service-account or
Telegram credential was committed or exercised.

The R4 discovery boundary is complete in code with a separate-process Wigolo
adapter. It accepts an operator-configured wrapper contract, uses argv without
shell execution, applies the same canary/health isolation as other sources,
and keeps the AGPL component outside this repository. The current machine did
not expose a `wigolo` executable, so no live Wigolo source is enabled.

Gate evidence: **322 offline tests passed**. No crawl process was started.

The final workflow increment adds seeker-controlled job status transitions,
application records tied to validated resume versions, one-time follow-up
nudge/expiry candidates, and notification suppression for completed/skipped
roles.

Gate evidence: **324 offline tests passed**.
