# Job record in the tracker sheet: design

Status: proposed, 2026-10-18. Goal: Fo keeps one living record of every good job JobPilot finds, and keeps each row's status
current (found, started, applied, interview, offer, rejected, withdrawn, deadline passed), with no manual bookkeeping
beyond telling JobPilot the outcome of an application.

## What exists (verified in code)

- `google.tracker_rows` sends: the newest 30 applications, then up to 10 open matches scoring 70 or more
  (`store.best_matches(sid, 25, open_only=True)`; jobs past `valid_through` or not seen for 45 days are excluded).
- Header: `ID | Job | Company | Status | Fit score | Applied on | Last updated`. Fo updates the row whose ID matches or adds one,
  never deletes or reorders. Rows with Status `match` whose ID is not in the request are set to `stale`.
- Refreshed by the 8:00 daily run when `[fo]` and `[google]` are on (`scheduled.py` calls `google.send_tracker`), by `jp sync`,
  and best-effort after `jp yes`.
- Outcomes (`applied`, `interview`, `rejected`, `withdrawn`, `offer`) reach the sheet only after JobPilot records them
  (`jp applied|interview|rejected|withdrawn <id>`, Claude's `record_application`, or `jp yes`).

## Gaps this design closes

1. Only the best 10 open matches are recorded, so most found jobs never appear.
2. A job whose deadline passed disappears from the request, so its row becomes `stale`, which also means "fell out of the
   top 10". Nothing says "deadline passed", and no deadline is shown.
3. No "first seen" date.
4. Outcome commands do not refresh the sheet, so it can lag a day.
5. The deck and Summary tab ignore expired jobs.

## Design

### Data: one query, one row builder

`Store.tracked_jobs(seeker_id, min_score, open_limit, closed_limit, closed_days) -> list[dict]` returns, for jobs linked to the
seeker with a score at or above `min_score` and not skipped:
`posting_id, title, company_name, fit_score, seeker_status, first_seen_at (seeker_jobs), valid_through, closed (bool)`.
- Open: `valid_through IS NULL OR valid_through >= date('now')` and seen in the last 45 days; ordered by score then newest;
  capped at `open_limit`.
- Closed: `valid_through < date('now')` and `valid_through >= date('now', -closed_days)`; ordered by `valid_through` descending;
  capped at `closed_limit`. These are the "deadline passed" rows. Jobs the seeker applied to are handled by the applications
  list, not here.
`min_score` comes from the seeker's profile (`profiles.Profile.min_score`, default 70), not a constant.

`tracker_rows(apps, tracked)` returns one row per posting id, applications first, then open matches, then closed ones:
- Status: the application status if there is an application; else `expired` for a closed job; else `match`.
- A `started` application older than 7 days whose posting is now closed keeps `started` (the user may still have applied;
  JobPilot does not guess).
- Cells stay vetted by `clean()` and formula-inert by `_inert()`.

### Sheet contract

Header becomes `ID | Job | Company | Status | Fit score | Applied on | Last updated | First seen | Deadline` (two new columns at
the END, so an existing sheet keeps its data). The request tells Fo: if row 1 lacks the new columns, add them to the right; never
move existing columns; blank cell = unknown, keep what the sheet has; dates `YYYY-MM-DD`.
`stale` rule stays and is narrowed: a row with Status `match` whose ID is not between the markers becomes `stale`; a row whose ID IS
between the markers takes the status given (so `match` becomes `expired` when the deadline passes). `expired` and `stale` are
never reverted by Fo on its own; the next request decides.

### Volume and size

- Caps: applications 100 (was 30), open matches 100, closed 30 (last 14 days) = at most 230 rows, about 230 x 130 bytes = 30 KB.
- The message goes in ONE request (Fo's own tool limit is unknown). To stay safe, rows are chunked at 60 rows per request when
  the total exceeds 100 rows: chunk 1 carries the header text and the stale rule is applied ONLY on the final chunk, so a chunk
  never marks other chunks' rows stale. Chunks are sent in order; if one fails the rest are not sent and the next refresh
  resends everything (full state each time, so it is idempotent).
- Each refresh costs one Fo turn per chunk. Daily run: counts against nothing else. `jp sync`: existing `phone_google` cap (10/day)
  counts requests, not chunks.

### Freshness

- Daily run (unchanged trigger).
- After `jp applied|interview|rejected|withdrawn <id>`, best effort, if Google is on and a Fo client exists: refresh the sheet,
  limited to 5 per day (separate event `phone_tracker`) so a burst of outcome texts cannot spam Fo; the outcome is recorded
  regardless and the reply does not wait for or depend on the refresh result.
- The `tracked_jobs` query computes "closed" from `valid_through` at read time, so it does not depend on
  `expire_past_deadlines` having run.

### Deck and Summary

`deck_inputs` counts: applications by status, plus `match` (open, at or above min_score, true count) and `expired`.

## Review amendments (supersede the sections above wherever they differ)

Three reviews (adversarial, five-voice roundtable, correctness) all found the same defect in the first draft: every Fo send
starts a fresh conversation, so a "stale only on the final chunk" rule would mark every earlier chunk's rows stale on every run.

- R1. NO CHUNKING. One request, at most 100 rows: up to 50 newest applications, then up to 15 recently closed jobs (deadline within
  the last 14 days, newest first), then open matches filling the rest. Rows beyond the cap stay on the sheet as they were; an
  open match that no longer fits is set to `stale` by the existing rule, whose meaning is now "no longer listed".
- R2. Open list parity with `best_matches`: seeker job status not in (skipped, applied, expired) and no application row of any
  status. The stored status `expired` set by the 7-day nudge is NOT a deadline and never becomes a closed row. A closed row needs
  `valid_through < today`, status not skipped/applied, and no application row. Today's date is passed in as a parameter
  (local date), never `date('now')` inside the query, so tests are deterministic.
- R3. Sheet status for a closed job is `deadline passed` (own colour, orange). `expired` stays in the dropdown for old rows;
  `stale` is grey and means "no longer listed". Statuses are never moved backwards by Fo: a row already applied, interview, offer,
  rejected or withdrawn is never set to match, deadline passed or stale.
- R4. Fo maps cells by header NAME. If First seen or Deadline is missing from row 1, add them after the last used header; never
  write into a column whose header is not in the list. Read column A once, write all rows in one range update, and report the
  final total number of data rows. A note on the Summary tab says edits in the sheet do not reach JobPilot; to record an outcome
  text `jp applied|interview|rejected|withdrawn <id>`. Summary gains "new since yesterday" and "closing within 7 days" counts.
- R5. Lease. `Store.claim_tracker(seeker_id, minutes=30)` atomically checks and records `tracker_pending`; `send_tracker` returns
  a `busy` result when a refresh started in the last 30 minutes has not finished. `tracker_done` is recorded on complete or
  failed, left open on `working` (a timeout). All triggers share it (daily run, `jp sync`, outcome refresh, MCP tool users call
  `send_tracker` only through these paths).
- R6. The refresh after outcome commands (and after `jp yes`) is ONE refresh at the end of `poll_once`, after every reply of that
  poll was sent, only when Google is on and a Fo client exists, at most 5 per day (event `phone_tracker`, recorded before the
  send), under the lease. Failures are swallowed; the outcome is already recorded.
- R7. `profiles.seeker_min_score(profile_path, home)` never raises, returns a finite value clamped to 50..100, default 70. Used by
  `tracker_rows`, the MCP tool, the daily run, `jp sync` and `deck_inputs`.
- R8. Tracker cells for title and company are screened for sheet-operation phrases ("mark ... rejected", "make sheet public",
  share/publish/permission/overwrite) in addition to `clean()`; a hit becomes `[withheld]`.
- R9. The daily run sends the tracker in its own try block so a failed digest cannot skip it.
- R10. `deck_inputs` uses the same query: a true count of open matches and a `deadline passed` count (no capped list).
- R11. KNOWN-LIMITS records: a 100-row request has not been run live (pilot it on a copy of the sheet and count the rows that
  land); `working` timeouts hold the lease for 30 minutes; the two-process claim relies on SQLite.
- R12. Dropped from the first draft: chunking, the separate `phone_tracker` row cap, the "started older than 7 days" rule.

## Security

- Nothing new reaches Fo except title, company, score, dates and ids, all through the existing vetting, between random markers,
  with `_RULES`. Job descriptions and URLs are never sent. Fo never reads email for the sheet; replies are still reported by
  `jp replies` and recorded by the user.
- Fo is told again: only the one sheet, owned by the user, no sharing, rows are data.

## Edge cases the implementation must handle

Deadline = today (open until the end of the day); deadline extended (job returns to open: status goes back to `match` because
the request carries `match`); same posting seen under two ids (separate rows, by design); posting without a score (not recorded);
score exactly at min_score (included); `min_score` unreadable profile (fall back to 70); a seeker with no jobs (nothing to send);
an application whose posting row is missing (skipped by the join); clock/timezone (dates compared in UTC as stored); titles with
tabs or newlines (collapsed by `clean`); rows beyond caps (oldest closed first dropped; they stay on the sheet as they were);
Fo timing out mid-chunk; sheet header edited by the user; two refreshes at once (daily run and `jp sync`: each is full state, last
write wins, no row is lost because Fo never deletes); Google off (nothing is sent); no Fo transport (skipped with a message).

## Out of scope

Reading replies from email automatically; deleting rows; per-user column customisation; any write path from Fo back to
JobPilot's database.

## Tests

Store query (open, closed window, boundaries, skipped, below threshold, applications excluded from open/closed lists);
row builder (status precedence, `started` kept after close, caps, inertness); message text (new columns, stale rule only on the
final chunk, rules present); chunking (counts, order, stop on failure); inbox (`jp applied` triggers one refresh, cap of 5, no
refresh when Google is off, outcome recorded even if the refresh fails); deck counts; daily run path.
