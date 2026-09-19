# JobPilot releases

Each release is working software. `main` is always the last released state.
The deployed instance runs a tag, never `main`.

## Fallback alert (set up before R1, runs permanently)

A free Naukri saved-search email alert, independent of all JobPilot code.

**This needs a Naukri account, so Alex or the operator must do it — Claude
does not create accounts or enter credentials.**

1. Sign in at naukri.com (Alex's own account is best: results are tuned to
   her profile).
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

_Not yet released._

## v0.3.0 - R3 Claude-driven on the desktop

_Not yet released._
