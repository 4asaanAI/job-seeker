# JobPilot × Fo

[![tests](https://github.com/4asaanAI/job-seeker/actions/workflows/tests.yml/badge.svg)](https://github.com/4asaanAI/job-seeker/actions/workflows/tests.yml)

**Claude finds and judges the jobs. [Fo](https://wajo.ai) (Wajo's assistant, over MCP) does the real-world part: it texts me the matches,
applies when I say yes, and adds an interview to my calendar only when the company sends the invite.** One Claude session holds both MCP servers; you just ask in plain English.
Built for the GSD hackathon. JobPilot already existed; the Fo link, the safety layer, tailored resumes and the
unattended digest are what was built for it.

> Fo can email, call and buy, and job posts are written by strangers. So Fo never sees a job post: it receives only an id,
> a score and a vetted title, and every Fo action asks for permission first.

## Try it in three minutes (no accounts, nothing is sent)

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python scripts/injection_demo.py   # a poisoned job posting is neutralized before Fo sees it
.venv/bin/python scripts/fo_demo.py          # dry run on a fictional seeker: the exact messages Fo would get
.venv/bin/python -m pytest -q                # 500+ offline tests, including one that starts the real MCP server
```

## What is new for the hackathon

- **Fo delivery with a safety layer.** A minimal digest (id, score, vetted title), random-marker wrapper, host-pinned
  client that never follows redirects, and "delivered" only when Fo confirms. One Fo account serves exactly one seeker.
- **Two transports.** `api` (an `FO_API_KEY`, ready for when Wajo ships keys; tested with a fake Fo) and `claude`, a
  headless `claude -p` relay on your own Fo login with only Fo's send/poll tools. The relay is verified live.
- **Tailored resumes, facts-only.** `tailor_resume` ranks your own bullets and skills against a job and lists the
  job terms your resume lacks. `render_resume` can only keep and reorder what your resume already says.
- **Text Fo, JobPilot answers.** Wajo has no inbound webhook yet, but Fo's MCP can read your own conversation
  transcripts. `python -m jobpilot.inbox --once` polls them through a headless relay, accepts a strict read-only
  grammar (`jp matches`, `jp status`, `jp tailor <id>`) re-checked in Python, runs JobPilot locally and replies
  through Fo. Commands never apply, email or change a job's status. Design: [docs/superpowers/specs/2026-10-18-fo-inbox-design.md](docs/superpowers/specs/2026-10-18-fo-inbox-design.md).
- **Applying through Fo, on your yes.** `build_fo_apply_message` builds one vetted request per job (safe link, your resume text,
  "ask me before submitting", **no calendar event**). A calendar entry appears only when the company itself sends an invitation
  (`build_fo_reply_check_message`). Nothing is sent by JobPilot; every `ask_fo` passes Claude Code's permission prompt.
- **Real-server test.** The MCP server is started over stdio and driven like Claude would, including seeker pinning.

```mermaid
flowchart LR
  boards[Job boards and employer ATS] --> JP[JobPilot MCP server]
  JP -->|scored matches, ledger| Claude
  you([You]) <-->|plain English| Claude
  Claude -->|ask_fo, with your approval| Fo[Fo MCP: text, email, calendar]
  Fo --> phone([Your phone and calendar])
  JP -.->|scheduled digest: headless relay, vetted text only| Fo
```

## Honest status

| Verified live | Built and tested, not live |
|---|---|
| Claude -> Fo texts and calendar create/delete | `api` transport (Wajo has not released headless keys) |
| Headless digest through the `claude` relay | Text-to-Fo-triggers-JobPilot via webhook (not released). A polling inbox is built and tested with a fake Fo; live, Fo's transcript API did not expose my test text yet |
| Real Fo permission prompt shows the exact vetted message | |

More: [docs/FO-DEMO.md](docs/FO-DEMO.md) (demo and safety choices) · [docs/APPLYING.md](docs/APPLYING.md) (using it for your own search).

---

## Background: JobPilot itself

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
operator setup steps. A separate-process Wigolo wrapper boundary is now
supported for ordinary career pages, but no Wigolo executable is currently
configured on this machine. The MCP
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
