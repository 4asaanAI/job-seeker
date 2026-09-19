# JobPilot

JobPilot is a local, multi-seeker job-discovery service. Claude (or another
approved MCP client) orchestrates discovery and scoring; the server owns the
SQLite store, cross-board deduplication, source health, and notification
ledger.

## Current status

R3 adds the MCP server and Windows entry point. The post-R3 line also includes
the first R4 building block: config-driven official ATS JSON adapters for
Greenhouse, Lever, Workday, and SuccessFactors. Indeed India/US is currently
enabled. Naukri India is kept in the configuration but disabled after its live
canary returned HTTP 406 (recaptcha required); re-enable it only after a
deliberate cooldown retry. LinkedIn is intentionally not contacted.
No ATS endpoint is enabled until its exact public JSON URL is verified against
the employer careers page. Structured `JobPosting` JSON-LD pages are now also
supported as separately configured Layer 2 sources. The official Telegram
plugin is installed on the Windows desktop, and JobPilot now has a plain-text
digest plus a pinned, restricted launcher; a bot token and pairing are still
operator setup steps. Wigolo integration remains later. The MCP
surface also includes per-seeker status/history views, watchlists, and
explicit one-page browser-investigation handoffs.

## macOS development

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/python scripts/demo.py
```

Register the server with Claude Code from the repository root:

```bash
claude mcp add jobpilot --scope user -- \
  "$PWD/.venv/bin/python" -m jobpilot.server
```

Set `JOBPILOT_HOME`, `JOBPILOT_DB`, and `JOBPILOT_OUT` in the MCP server
environment. Leave `JOBPILOT_SEEKER_ID` unset for the operator surface. A
seeker-facing process must set it to exactly one database seeker ID; that
removes roster and discovery tools and refuses other seeker IDs.

## Windows deployment

The intended project path is `E:\Github\Aasaan AI\jobpilot\`, not `C:`.

```powershell
& "C:\Users\Desktop\AppData\Local\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\run.ps1 -Task test
```

Use `run.ps1 -Task smoke` only after reviewing the live request plan. It uses
the explicit `--yes` acknowledgement and never contacts LinkedIn.

## Safety and privacy

Job descriptions and URLs are untrusted public data. The server never treats
instructions inside a scraped listing as commands. Seeker profiles,
resumes, the SQLite database, and generated digests are ignored by Git.

The service records notifications per seeker and posting, so a delivered job
is not returned as a new first notification. A source with a failed canary is
reported as blocked/error rather than as a quiet job market.

Do not commit `data/seekers/`, databases, generated output, Telegram tokens,
or other personal data. The repository is private on GitHub, but privacy still
depends on keeping those paths untracked.

The optional Google Sheets exporter is one-way: SQLite remains authoritative,
and the exporter grants only a direct reader permission to the seeker's email.
Install it with `pip install -e ".[dev,sheets]"` only when an operator-owned
service-account file is ready.
