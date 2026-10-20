# Fo's build list (2026-10-19)

Fo sent a 45-item list. This maps each item to where it lives, what existed already, and the decisions taken.

Decisions from the operator (2026-10-19):
- LinkedIn stays off (their terms forbid scraping; accounts get banned). `my_sources` says so plainly.
- Gmail status sync only PROPOSES a change; the user confirms each one. Rules are fixed Python patterns, no model reads mail.
- Cover letters: an AI-written draft from the posting and the resume, which the user must edit/approve before use.
- Dashboard: listens on 127.0.0.1 by default; `--host` (a Tailscale address) plus a login token for the phone.

## The five invariants and where each is enforced

| Invariant | Enforced in |
|---|---|
| (No apply limits: the daily cap and per-board gap were removed at the user's request on 2026-10-19.) | |
| Nothing is submitted without the user's explicit OK | `_apply_core` (yes / plan_id / phone code), bridge `approve_submit` (TOTP), dashboard approve (TOTP), `retry_failed` needs `user_confirmed=True` |
| One application in flight at a time | `Store.in_flight` + `_apply_core` refuses a second; cleared by an outcome, `report_blocked`, `cancel_application`, or after 2 h |
| Only the user's own facts go into form answers (reworded formally by Fo, at the user's request on 2026-10-19) | answers vault is written only by the user; JobPilot writes a formal version of each answer once and the user approves that exact text (a model's rewording cannot be proven fact-for-fact, so Fo copies approved text exactly); `pre_submit_check` accepts a value only if it is the user's text or their approved formal version; AI cover letters are used only once the user approves the final text |
| Stale tracker rows are never deleted | nothing deletes `applications`, `seeker_jobs` or tracker rows; freshness only hides rows from matches; `submit_audit` is append-only by trigger |
| No silent retry after a failure | `retry_queue`: one retry, same payload, only with the user's OK; every failure is logged in `submit_audit` and shown by `my_status` |

## Item map

01 Discovery: `ranking.py` (facts, filters, staffing, feedback), `Store.reweight`, freshness window, duplicate guard in the
match queries, `posting_detail`, `source_report` (per-board scoring), daily digest threshold.
02 Applications: `vault.py` (form fields + answers), `pre_submit_check`, `board_sessions`/`record_progress`, `report_blocked`,
`retry_queue`, batch review, `submit_audit`.
03 Documents: `documents.py` (PDF writer, AI letter drafts), resume versions for every apply.
04 Tracker: `tracking.py` (email proposals, CSV snapshot, import, funnel), exporter styling.
05 Follow-ups: `followups.py` (application follow-up drafts, calendar hold proposals, interview prep), rejection reasons.
06 Reliability: `reliability.py` (stable error codes, token health, cooldowns, next run), extended `my_status`.
07 Frontend: `dashboard/` (Starlette app + one HTML page + PWA manifest), notifications via Fo text or email.
