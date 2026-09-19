# JobPilot v0.3/R4 usage

The repository is now on `main` with the R3 MCP server. The supported operator
interface is Claude Code (or another MCP client); there is no browser UI yet.
Telegram delivery remains a later, separately secured integration.

R4 also provides no-network-in-tests adapters for official employer ATS JSON
endpoints and ordinary career pages containing `JobPosting` JSON-LD. ATS JSON
supports `greenhouse`, `lever`, `workday`, and `successfactors`. Both endpoint
types are operator configuration, not model or seeker input; JobPilot does not
infer an API URL from a careers page.

## 1. Verify the safe offline demo

From the repository root:

```bash
/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python -m pytest -q -p no:cacheprovider
/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python scripts/demo.py
```

The demo uses a throwaway database and captured Indeed rows. It does not make
network requests, touch real seeker profiles, or send Telegram messages.

## 2. Run the guarded live smoke test

Use a real Terminal window, not a non-interactive shell:

```bash
/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python scripts/demo.py --live
```

This path is deliberately limited to the enabled Indeed sources, caps the
request plan, waits between calls, stops on a detected block, and deletes its
throwaway database afterward. It is a smoke test, not the production R2 run:
it does not call an LLM for real scoring and it does not send notifications.

## 3. Exercise the R2 scoring and digest flow

R2 exposes the handoff between discovery, scoring, and digest rendering:

1. Create a `Store` and call `migrate()`.
2. Load a seeker profile with `load_profile()` and register it with
   `Store.add_seeker()`.
3. Build `JobSpySource` objects for the profile's configured sources and call
   `run_discovery()`.
4. Call `build_scoring_batch(store, seeker_id, profile, limit)`. This returns
   the seeker-scoped rubric, profile facts, and unscored jobs for Claude (or
   another approved scorer). Scraped descriptions are untrusted data; the
   returned `note` is part of the safety contract.
5. Write each model-approved score with `store.record_score(...)`.
6. Render the HTML digest with `render_digest(...)`. By default it records the
   delivered `first` notifications, so the same job is not returned again.

The generated file is under the configured output directory:

```text
out/seekers/<seeker-id>-<output-token>/digest-<timestamp>.html
```

Open that HTML file in a browser to inspect the current digest. The
`review_required` flag is stored per seeker; an operator approval workflow for
Telegram rollout remains a later channel-integration step.

## 4. Start the MCP server through Claude Code

Install the declared dependencies, then register the local stdio server:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
claude mcp add jobpilot --scope user -- \
  "$PWD/.venv/bin/python" -m jobpilot.server
```

The operator surface provides `list_seekers`, `add_seeker`, `discover`, and
`source_health`, plus the scoring and digest tools. Ask Claude to run
`discover`, then request a scoring batch, record scores, and build a digest.
The server resolves relative profile paths from `JOBPILOT_HOME`, not from the
client's current directory.

The seeker surface also provides `my_status`, `my_sources`, `my_history`, and
`add_target_company`. `investigate_posting`, `verify_still_open`, and
`check_company_careers` return a single public URL and an explicit browser
handoff; they do not bulk-crawl pages or claim that a listing is open without
Claude's targeted browser check.

For a seeker-facing process, set `JOBPILOT_SEEKER_ID` to one database seeker
ID. That process exposes no roster or discovery tools and refuses requests for
other seeker IDs. `resolve_telegram_sender` is the only supported mapping from
a verified numeric Telegram sender to a seeker; Telegram transport itself is
not included yet.

## 5. Windows entry point

From the deployed checkout on the Windows desktop:

```powershell
.\run.ps1 -Task test
.\run.ps1 -Task smoke
.\run.ps1 -Task serve
```

`smoke` is a live public-site check and requires the explicit `--yes` guard.
Naukri is currently disabled after a 406 recaptcha response; Indeed is the
active India/US smoke source. The run skips LinkedIn by policy.
`capture_fixture.py` is also live-only and must not be run in a loop; it
refuses empty, malformed, or description-free data.

## 6. Enable an official ATS endpoint

1. Verify the employer's public careers page and the exact JSON endpoint it
   calls. Do not paste a careers-page URL into `endpoint` unless it is actually
   a JSON response.
2. Add a block to `config.toml`, following the commented example. Use a unique
   source key such as `ralph_lauren_workday`, set `enabled = true`, and choose a
   canary floor of at least one normalized posting.
3. Add that exact source key to the intended seeker's `sources` list. A source
   is skipped for seekers who do not subscribe to it.
4. Run the offline tests, then run one guarded discovery and inspect
   `source_health`. A transport error, empty canary, or malformed vendor payload
   is recorded as error/blocked rather than reported as an empty market.

Each configured endpoint is fetched once per source instance and filtered
locally for the seeker's terms and location. Tests inject a fetcher and never
make network requests.

For a JSON-LD career page, use `[structured_sources.<key>]` with
`company_name`, an HTML `endpoint`, `enabled`, and a canary floor. Add the same
source key to the seeker's profile. A page with no valid `JobPosting` records
is blocked/error rather than reported as a quiet employer.

For a career page that needs crawling, use `[wigolo_sources.<key>]` with an
operator-provided `command` argv list. JobPilot invokes that command as a
separate process with `--url`, `--query`, optional `--location`, and
`--format json`; stdout must be a list or `{ "jobs": [...] }`. JobPilot does
not import, vendor, or bundle Wigolo. No Wigolo command is enabled by default.

## 7. Resume output and Telegram

Create a private `base_resume.yaml` under `data/seekers/<name>/`. The schema is
structured facts (`experience`, `education`, `skills`, and optional
`required_facts`); the renderer accepts only IDs and exact skill values from
that file. It cannot accept free-form model claims. Through MCP, call
`resume_facts`, then `render_resume` for a linked posting. The returned DOCX is
written under `out/` and is recorded in `resume_versions` only after the
extraction gate passes. PDF validation is available through `pypdf`, but PDF
conversion is intentionally not automatic.

The Windows checkout contains `.mcp.telegram.json.example` and
`scripts/telegram.ps1`. To configure the channel:

1. Copy the example to `.mcp.telegram.json` and review the pinned seeker ID.
2. In Claude Code, install/enable `telegram@claude-plugins-official` and run
   `/telegram:configure <BotFather token>`. The token is stored outside Git in
   the configured Telegram state directory.
3. Start `.scripts\telegram.ps1`. It uses only the pinned JobPilot MCP and
   Telegram `reply`/`react`/`edit_message` tools; built-in tools and unrelated
   MCP servers are disabled.
4. Pair the intended sender, then switch Telegram policy to `allowlist` and
   confirm the numeric ID matches the seeker's `telegram_user_id` in JobPilot.

For a digest, call `build_plain_digest(record=false)`, send its returned
`text`, and call `record_notification` only after the Telegram reply succeeds.
The HTML digest is never sent through Telegram.

After a seeker responds, use `set_job_status` for `shortlisted`, `skipped`, or
`deferred`, and `record_application` for `started`, `applied`, `interview`,
`rejected`, or `offer`. `my_applications` and `get_followups` are scoped to the
same seeker; follow-ups are one-time three-day nudges or seven-day expiry
records, not repeated daily notifications.

## 8. Scheduled routine and Sheets view

Copy `.mcp.scheduled.json.example` to `.mcp.scheduled.json` on the desktop,
then run `.un.ps1 -Task scheduled`. The runner starts a fresh Claude session
with no built-in tools, only the JobPilot MCP, no session persistence, and a
default `$2` API budget. Each attempt is recorded in `machine_runs`; a failed
Claude process is recorded as `error` rather than disappearing.

For the optional Sheets view, install `.[sheets]`, set
`JOBPILOT_GOOGLE_SERVICE_ACCOUNT` to an operator-owned service-account JSON
file outside Git, and call the operator-only `sync_google_sheet` tool with the
seeker's email. The exporter creates or updates a single tab, writes the full
seeker-scoped view in one pass, and shares it directly as a reader. It never
uses an anyone-with-the-link permission. A spreadsheet with multiple visible
tabs requires an explicit `sheet_title`.
