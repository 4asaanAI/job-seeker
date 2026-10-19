# Fo bridge: JobPilot reachable from Fo on text, email and calls

Status: built on Oct 19 after a security review and an architecture review of this design, then a security review of the
code. The section "As built" at the end lists where the code differs from the text above.

## Problem

The loop in the demo works only inside Claude Code. When I text, email or call Fo directly, nothing reaches JobPilot:
Fo's API does not return my own texts (checked again on Oct 19), and Fo has no way to call JobPilot. Fo confirmed on Oct 19
that it can call a custom MCP server on any channel if the server is reachable over public HTTPS with a stored key. It cannot
forward my messages to a webhook.

## Goal

From my phone I text Fo "find me product jobs", "apply to 689" or "what's on my sheet", and Fo calls JobPilot. The safety rules
stay as strong as in Claude Code: job posts never reach Fo raw, nothing is submitted without my approval, and a blocked site
ends in a hand-off.

## Scope: one user, opt-in

Pinned to my seeker (id 1). One key, held only by my Fo account. Runs on my Mac, off until I turn it on. Other JobPilot users
keep Claude Code, the daily run and Telegram; nothing about them changes.

## The one big change from the first draft: Fo cannot approve itself

In Claude Code, I approve each Fo action before Fo gets it. Through the bridge, Fo would both ask me and act, and anything Fo
reads (a recruiter email, a web page) could steer it. So:

- **Submitting needs a code from my phone.** `build_fo_apply_message` through the bridge always builds a review-only request.
  To submit, Fo must call `approve_submit(posting_id, code)` with a 6-digit code that JobPilot texts to my phone (reusing the
  existing phone-approval flow, `_phone_approval_ok`). Codes are single-use, tied to one job and expire in 30 minutes. Fo
  never sees a code it did not get from me.
- **Batch approval does not exist on the bridge.** `batch_approved`, `plan_id`, `phone_code`, `set_review_mode` and
  `plan_apply_batch` are not exposed.

## Design

**Server.** `python -m jobpilot.bridge` starts a new FastMCP server (fastmcp 4.0.5, pinned in pyproject to `>=4.0,<4.1`) with
`transport="http"`, `stateless_http=True`, `json_response=True`, at `127.0.0.1:8765/mcp`. It registers tools from an
explicit list, never by filtering `build_tools` (which would bring along `web_fetch`, resume editing and more). Each tool is a
thin wrapper with no `seeker_id` parameter; the pinned id is bound inside. At startup it refuses to run unless the config's Fo
seeker id equals the pin.

**Public address.** Tailscale Funnel publishes only the `/mcp` path (`tailscale funnel --set-path /mcp 8765`). The server sets
`allowed_hosts=["<machine>.ts.net", "127.0.0.1"]` (otherwise FastMCP answers 421), rejects any request with an `Origin` header,
sends no CORS headers and caps request bodies at 64 KB. `tailscale serve status` is checked so nothing else is published. The
hostname appears in public certificate logs, so scanning is expected from day one.

**Key.** A `TokenVerifier` subclass compares the `Authorization: Bearer` key with `hmac.compare_digest`. The key is read from
`~/.config/jobpilot/fo_bridge_key` on every request (deleting or rotating it revokes access at once). It is created with
`os.open(O_CREAT|O_EXCL, 0o600)` and `secrets.token_urlsafe(32)`, never accepted in a query string, never logged. `--rotate`
makes a new one. A Starlette middleware logs every 401 and blocks an IP after 10 failures in 10 minutes. A config switch
`[bridge] enabled` is checked on every request as an off switch.

**Tools Fo may call, and what they return.**

| Tool | Returns / does | Guard |
|---|---|---|
| `my_matches`, `apply_candidates` (bridge versions) | job id, score, checked title and company (`safe_title_company`), route. Never the link, location or the scorer's notes | items that fail the check are dropped |
| `my_status`, `my_tracker_link`, `refresh_status` | counts, the sheet link, refresh progress | read only |
| `refresh` | starts search + scoring in a background thread, returns at once | one at a time (lock), counted as `manual_search` so the daily cap of 3 is shared with `run_my_search`; a file lock shared with the 08:00 daily run; wall-clock timeout; never delivers texts |
| `update_my_profile` | changes search terms, headline, location | headline cleaned; cannot remove every source; returns before/after; every change logged for undo |
| `build_fo_apply_message` | a review-only apply request | writes a `started` row in the same lock as the duplicate check, so two channels cannot start the same job twice |
| `approve_submit` | releases the submit for one job | needs the phone code |
| `record_application` | applied / interview / rejected only | forward moves only; `applied` only if a `started` row exists; never `withdrawn`; the previous status is logged |
| `update_tracker_row` | one-row sheet update (from today's fix) | takes the sheet lease, released when done or after 30 minutes |

The internal `refresh` uses a private operator-mode tool set that is never registered, calls discovery only for the pinned
seeker and scores with the neutral prompt. Tool descriptions are written for Fo ("do this yourself; confirm with the user on
the same channel"), not for Claude.

**Errors and storage.** SQLite runs in WAL mode with a busy timeout. Wrappers catch database errors and return fixed messages
with no paths or internals.

**Audit log.** One line per call in `data/bridge.log`: time, tool, arguments with answers and notes redacted, ok or error, and
the client IP. Never the key.

**Running it.** A `com.jobpilot.bridge` LaunchAgent with KeepAlive, a full PATH and HOME (so the `claude` CLI works), and log
rotation. `doctor` checks the port, tailscaled, the Funnel and the key file. An unauthenticated `/health` route returns "ok"
and nothing else. If the bridge is down, Funnel returns 502, and Fo's instructions say: "tell me JobPilot is offline; do not
retry or guess."

## Open questions for Wajo (asked before building)

1. Does Fo connect with streamable HTTP or SSE?
2. Does it send the stored key as `Authorization: Bearer`?
3. Are Fo's outgoing IP addresses published? If so, the bridge accepts only those.

## Limits

- The Mac must be awake and online. A small server removes this later.
- Anti-bot checks and job-board logins still stop Fo. That is the hand-off, by design.

## Tests

- HTTP: 401 without the key or with a wrong key; 200 with the key and the Funnel Host; 421 for other hosts; a request with an
  Origin header is rejected; the tool list equals the allowlist exactly.
- Pinning: no exposed tool takes a seeker id; startup refuses a mismatched config.
- Vetting: a seeded hostile posting (title, company, scorer notes carrying instructions) never appears in any bridge output.
- Approval: `build_fo_apply_message` never yields a submit-ready request; `approve_submit` refuses a wrong, reused or expired code.
- Concurrency: two parallel apply calls for one job, exactly one succeeds; a second `refresh` is refused; the file lock blocks
  scoring while the daily run holds it; the shared daily cap counts both tools.
- Records: `record_application` refuses `withdrawn`, backward moves, and `applied` without a `started` row.
- Errors: a locked database returns a fixed error, not a crash.
- Live, each in the run log: text, email and a call to Fo; a test during the 08:00 run; a refresh across a Mac sleep and wake;
  the bridge down.

## Rollback

`python -m jobpilot.bridge --rotate` (old key stops working at once), set `[bridge] enabled = false`, `tailscale funnel off`,
unload the LaunchAgent.

## As built (Oct 19), and why it differs

- **Approval codes come from an authenticator app, not a text.** JobPilot's only way to text me is through Fo, so a texted
  code would pass through the agent it is meant to check. The bridge uses time-based codes (RFC 6238) from my authenticator
  app instead. A code is single use, accepted only for the one application waiting for approval (one at a time, for two
  hours), and five wrong codes lock approval until I run `--unlock-codes` on the Mac.
- **The off switch is a file** (`data/bridge.off`), checked on every request, instead of a config flag.
- **Status `offer`** is allowed after `interview`. `applied` also needs an approval code for that job.
- **Host check:** only the public Funnel hostname is accepted. FastMCP's own check is off by default, so the bridge enforces
  it itself and turns FastMCP's on as well.
- **Client address** is uvicorn's (Funnel connects from 127.0.0.1); the bridge never trusts a forwarded-for header itself.
- **Logs** are private files, rotated at 5 MB, with the request path escaped so it cannot forge lines.
- **The daily run waits at most an hour** for a bridge search to release the scoring lock, then skips scoring for that day.
- **Not done:** a Wajo IP allowlist (Wajo publishes none) and `doctor` checks for the bridge.
