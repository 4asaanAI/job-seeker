# Fo bridge: JobPilot reachable from Fo on text, email and calls

Status: design, waiting for approval. Nothing here is built yet.

## Problem

The loop in the demo works only inside Claude Code. When I text, email or call Fo directly, nothing reaches JobPilot:
Fo's API does not return my own texts (checked again on Oct 19), and Fo has no way to call JobPilot. Fo confirmed on
Oct 19 that it can call a custom MCP server on any channel, as long as the server is reachable over public HTTPS. It cannot
forward my messages to a webhook.

## Goal

From my phone I can text Fo "find me product jobs", "apply to 689" or "what's on my sheet", and Fo calls JobPilot to do it,
with the same safety rules as in Claude Code: the job-post filter, the review draft before any submit, and the hand-off when a
site blocks it.

## Design

**One extra way to start the existing server.** `python -m jobpilot.bridge` starts the same JobPilot tool surface over HTTP
(FastMCP's HTTP transport) on 127.0.0.1, pinned to one seeker. No new business logic; it reuses `build_tools`.

**Public HTTPS through Tailscale Funnel** (already installed on this Mac). Funnel gives a fixed `https://<machine>.ts.net`
address and forwards it to the local port. Moving it to a small server later changes only the address.

**A secret key.** On first start, the bridge creates a random 32-byte key in `~/.config/jobpilot/fo_bridge_key` (mode 600,
same pattern as the Fo API key). Every request must carry it; anything without it gets a 401 and is logged. I give the key
to Fo through Fo's secure link, never in chat.

**A short list of tools Fo may call.** The bridge exposes only these:

| Tool | Why Fo needs it | Limit |
|---|---|---|
| `my_matches`, `apply_candidates`, `my_status`, `my_tracker_link` | answer "what did you find" | read only |
| `refresh` (new, wraps `jobpilot.refresh`) | "find me jobs": search and score | 3 per day; starts in the background and returns at once, because it takes minutes |
| `update_my_profile` | "only remote roles" | the existing checks |
| `build_fo_apply_message` | "apply to 689" | the request carries the review-draft and hand-off rules; Fo still asks me before submitting |
| `record_application` | "it went through" | statuses limited to applied, interview, rejected, withdrawn |
| `build_fo_tracker_message` (with `posting_id`) | update one sheet row | the existing busy-lock |

Not exposed: resume editing, the web tools, cover letters, onboarding, operator tools.

**Job posts still never reach Fo raw.** Every result that carries job text goes through the same vetting as the texts JobPilot
already sends (checked titles, no descriptions, safe links).

## What stays the same, and the limits

- Fo asks me on the same channel before submitting anything. That rule lives in the apply request, not in the bridge.
- The Mac must be awake and online for Fo to reach JobPilot. If it is not, Fo says it cannot reach JobPilot. A small server
  fixes this later.
- Scoring uses the `claude` CLI on this Mac, so `refresh` only runs where that works.
- Anti-bot checks and job-board logins still stop Fo. That is the hand-off, not a bug.

## Testing

- Unit tests: a request without the key is refused; tools outside the list are not exposed; `refresh` respects its daily cap
  and returns at once; `record_application` refuses other statuses.
- Live test from my phone: text Fo "find me jobs" → matches come back; "apply to <id>" → review draft by text → "submit" → the
  sheet row updates. Each run goes in the run log.

## Rollback

Stop the bridge and turn off the Funnel (`tailscale funnel off`). Delete the key file so the old key stops working.
