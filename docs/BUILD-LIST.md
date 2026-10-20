# Fo's build list: what was built (2026-10-19)

Fo sent a 45-item list. Every item is below with where it lives and anything it does not do. "Existed" means JobPilot
already had it before this list; those items were checked and extended where the list asked for more.

## 01 Discovery and matching

| Item | Status | Where / how |
|---|---|---|
| board_expansion | Lever, Greenhouse and Ashby existed (17 employer feeds). Per-board scoring added. **LinkedIn stays off** on purpose. | `source_report` (per board: found, scored, average fit, good matches, applications, interviews); profile `source_weights` (-20..20) moves a board's scores. `my_sources` explains LinkedIn. |
| posting_detail_fetch | Built | `posting_detail`: full description, salary, work mode, deadline, score with reasoning and adjustment, every board, how applying works. |
| filters | Built | Profile `salary_floor` + `salary_currency`, `work_modes`, `locations`. Applied when a job is linked and again on every reweigh. A job that does not state a fact is never filtered for it. |
| company_lists | Built | `blacklist_companies`, `whitelist_companies` (always kept, +10), `exclude_staffing_agencies` (on by default; names like TeamLease or Randstad, and "on behalf of our client" posts). |
| freshness_engine | Built | Matches older than 14 days (`[freshness] max_age_days`) leave matches, digests and the sheet; past-deadline jobs expire (existed); a deadline within 48 hours is flagged in matches, digests and the dashboard. Reweighing runs after every scoring run (daily). |
| duplicate_guard | Built | The same role (title + company under another posting id) is never scored, matched or sent again once it has an application, and two listings of one role appear once. |
| feedback_loop | Built | `ranking.reweight`: earlier outcomes at the same company (rejected -5, withdrawn -3, skipped -4, interview/offer +5, capped at ±15). The model's own score is kept; only the adjustment moves. |
| daily_digest | Existed (08:00 launchd run), threshold made configurable | `[notify] min_score`, else each seeker's own `min_score`. |

## 02 Applications and forms

| Item | Status | Where / how |
|---|---|---|
| profile_parse | Built | `form_profile`: name (split), email, phone, address, links, current job (only one with no end date), experience, education, skills, all copied from the saved resume. |
| answers_vault | Built | `save_answer` (the user's own words), stored in the seeker's `answers.yaml`; every apply request is prefilled from it; an answer given for one job wins. JobPilot writes a formal, polite version of each answer once; you approve that exact text (dashboard, or tell Fo) and forms carry it word for word. Until approved, the answer goes in as you typed it. Drafts that change a number, a limit or a refusal show a warning, and Fo cannot approve a draft with warnings. Resume text, contact details, dates, names and codes are always copied exactly. |
| pre_submit_qa | Built | `pre_submit_check`: every required field filled, required files present, every value either the user's own text or the formal version they approved (word for word), choices matching a saved answer. Answers Fo itself supplies while applying are not certified as the user's. Through the Fo bridge, `approve_submit` refuses until the latest check is ready. |
| session_manager | Partly | JobPilot has no browser: logins live in Fo's browser. JobPilot now remembers which boards have a working login (`record_progress(board_login=...)`) and where an application stopped (page link + fields filled); the next request tells Fo to reuse the login and continue from that page. |
| human_challenge_escalation | Built | `report_blocked` (captcha, login, otp, upload, question, manual, anti_bot): the application stays started, the stop is logged and shown in `my_status`, and the reply says exactly what the user must do. |
| retry_queue | Built | The exact request is kept when built. `report_submit_failed` parks it; `retry_failed(user_confirmed=true)` returns the identical payload once; a second failure is final. |
| batch_approvals | Existed (`plan_apply_batch`), per-item review added | `review_batch` shows each item (resume text, prefilled answers, letter state); `approve_batch_items` makes a plan of only the approved jobs. |
| audit_log | Built | `submit_audit` table, append-only (SQLite triggers refuse UPDATE and DELETE); `audit_log` tool. |

## 03 Documents

| Item | Status | Where / how |
|---|---|---|
| resume_tailoring | Existed; history per application added | The exact resume text sent with each apply request is saved as a version (`my_resumes`). |
| cover_letter_gen | Built (AI draft) | `draft_cover_letter` (a headless Claude with no tools; the posting is untrusted) with warnings for numbers and names not in the resume; used only after `approve_cover_letter`. The facts-only templated letter still exists. |
| pdf_render | Built | `render_resume_pdf`: "Resume - <Company>.pdf", text-only, read back and checked for every required fact. Characters outside Windows-1252 are folded or shown as "?". |

## 04 Tracker and data

| Item | Status | Where / how |
|---|---|---|
| auto_status_sync | Built as **proposals** (operator's choice) | JobPilot holds no Gmail login. Each morning (`[fo] email_scan = true`) Fo reads application emails and JobPilot parses its reply; Claude can also pass Gmail metadata to `propose_status_from_emails`; fixed Python rules match each email to one open application and suggest only forward moves; the user confirms each (`decide_proposal`, or the dashboard's To do tab). |
| csv_snapshot | Built | Nightly CSV of applications and tracked jobs plus a database copy in `data/backups/` (90 kept); `snapshot_tracker` on demand. Cells are formula-inert. |
| import_history | Built | `import_applications` (CSV: company, title, applied_on, optional status, url, location, notes); safe to run twice. |
| analytics | Built | `funnel_report`: weekly match, applied, interview, offer, rejected; change against the week before; average days per stage. |
| sheet_styling | Existed for Fo's sheet (colours, widths, Summary tab); added to the service-account exporter | Exporter: a summary row above the header, status colours, auto-sized columns. |

## 05 Status, follow-ups and interviews

| Item | Status | Where / how |
|---|---|---|
| followup_nudges | Built | `application_followups` (silence for `followup_days`, default 7) and `draft_followup` (draft only; optional request for Fo to save it as a Gmail draft, never send). |
| interview_to_calendar | Built as a proposal | An interview email with a clear date and time becomes a calendar-hold proposal; accepting returns one tentative hold, no guests, for Fo or a calendar tool. |
| interview_prep | Built | `interview_prep`: what to stress from the resume, the posting's key terms, likely questions, questions to ask, and web research highlights when Wigolo is reachable. The daily run writes one for every application newly at interview. |
| rejection_reasons | Built | `record_application(..., reason=)`; the reason's category (seniority, experience, skills, location, visa, salary, filled) feeds the scoring prompt as counts (never the employer's words). |

## 06 MCP server and reliability

| Item | Status | Where / how |
|---|---|---|
| token_health | Built | The bridge key now expires after 90 days; `my_status`, the dashboard and the daily digest warn 14 days before. Fo's own login token is held by Claude Code and is not visible to JobPilot. |
| rate_limit_respect | Built | Employer feeds: requests to one API host at least 1 s apart. A source that answered blocked is skipped for 6 hours (reported as `cooling_down`). Applying has no limit at all (no daily cap, no gap between jobs), as the user asked; one application is still in flight at a time. |
| stable_errors | Built | Every failure carries `code` from `reliability.ERROR_CODES`, beside the plain `error`. Codes are only ever added. |
| extended_status | Built | `my_status`: last run, errors in the last 24 h, next scheduled refresh (from the launchd job), key warnings, the job in flight, jobs waiting for the user, parked failures, proposals. |

## 07 Frontend

| Item | Status | Where / how |
|---|---|---|
| local dashboard | Built | `python -m jobpilot.dashboard` (see docs/DASHBOARD.md): pipeline board (match, started, applied, interview, closed) sorted by score, quick actions. |
| review drawer | Built | Prefilled fields, saved answers, the resume text, the exact request Fo will get, and **Approve and apply** with the 6-digit authenticator code. |
| resume_preview | Built | Resume text, PDF, AI letter draft and approved letter in the drawer. |
| search profile UI | Built | Search profile tab: roles, cities, seniority, filters, company lists, threshold, and the answers vault. |
| phone approvals | Built | The same page, phone layout, installable (web app manifest), over Tailscale with `--host`. Not yet tried on a real phone. |
| notifications | Built | `[notify] channel = "text"` or `"email"` (Fo sends to your own number or address only) and `min_score`. |

## Not verified live

Everything above has offline tests (1008 in total), and an independent code and security review's 14 findings were fixed with a regression test each. Not yet exercised against the real services: the AI cover letter
(headless Claude), the dashboard sending through Fo, the phone over Tailscale, and the Gmail proposals with real mail.

## What Fo can use (the Fo bridge)

Fo reaches JobPilot over the bridge with about 60 tools (no seeker id on any): searching and profile (all filters and company
lists), matches with full job detail, the Wigolo web tools (`web_search`, `web_fetch`, `web_crawl`, `web_extract`,
`web_research`, `company_openings`), resumes and PDFs, AI cover-letter drafts, the answers vault, applying with the
pre-submit check and the approval code, blocked / failed / retry handling, the audit log, the tracker sheet (one row or the
whole sheet), the deck and Google Doc requests, email suggestions and calendar holds, follow-up drafts, interview prep,
analytics, history import and snapshots. Every result is shaped first: unsafe links and local paths are removed, job titles
are screened, long text is cut, and anything from a web page or posting travels with a "data, not instructions" note.

Kept from Fo on purpose, because each one removes your per-application OK or widens who JobPilot serves: `set_review_mode`,
batch planning and approval, adding seekers, the unattended search and score writes, and the service-account sheet exporter.
Fo can save a form answer you told it but cannot overwrite one (change those in the dashboard), cannot mark a job applied
without your approval code, and cannot accept an email suggestion that says "applied".
