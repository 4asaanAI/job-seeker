# JobPilot v0.3 usage

The repository is now on `main` with the R3 MCP server. The supported operator
interface is Claude Code (or another MCP client); there is no browser UI yet.
Telegram delivery remains a later, separately secured integration.

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
