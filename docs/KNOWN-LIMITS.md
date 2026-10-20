# Known limits (from two independent code reviews, 2026-10-18)

Fixed in the same pass: JSON-LD job ids collapsing to the company name (jobs were silently dropped); spreadsheet formula
injection in the `/list` export; seeker text becoming an option in the Wigolo wrapper call; an empty onboarding variable
opening the operator tools; the Fo relays inheriting a project's tool approvals (now a neutral working directory plus an
explicit deny list); "delivered" through the headless relay now needs a real Fo tool result of `complete` in the session
transcript; stricter `clean()` (domains, long numbers, payment and credential words, dotted spellings); the polling inbox
now locks, prunes by age, answers each command once, and no longer echoes job-description keywords.

Also fixed later: the employer-feed lookup cap is now a real 24-hour cap (counted from stored events); tailoring keywords skip generic posting words; one broken profile no longer stops discovery for everyone (reported as `skipped_seekers`), and profile YAML is written atomically.

Fixed after a Sonnet 5.5 audit: instruction text hidden in apply links (links now allow only plain URL characters and no
instruction-like words); the unattended routine now holds only `list_seekers` and `discover`; closed or finished jobs no longer
reach scoring; the 7-day expiry now fires after a nudge; `clean()` keeps real titles such as "Systems Engineer" or "Node.js
Developer" and cuts long ones instead of withholding them; batch approval is bound to a server-issued `plan_id`; the headless
relay must have sent exactly the vetted message; one failing inbox command no longer blocks the others; and apply candidates
exclude finished jobs before the top-N cut.

Fixed from the second audit (all 25 numbered findings): application statuses only move forward (a late `applied` can no longer undo
an `interview`), `response_at` is written, `applied_at` is set only for real applications, and notes are kept; an extended deadline
revives a job that expired only because of the old date; employer-feed lookups share one global daily cap and rotate fairly;
short skills must be real words in the source ("Go" is not "Google"); a text resume fails closed if it would drop a required
fact; Telegram job lists skip the Fo seeker; `prepare_application` drops unsafe links and refuses closed jobs; raw SQL now takes
the store lock; inbox matches leave out finished jobs; and the vacuous MCP tests now assert real behaviour.
`review_required` is a reserved column that nothing enforces; applying is gated by approved plans and per-call permission prompts.

Fixed in the last pass (every limit that code alone can fix): the JavaScript-rendering retry in `web_fetch` is gone; auto-found
employer feeds still serve everyone who names that employer but are no longer advertised in the source catalog; two seekers can
no longer share a profile folder (enforced in the store, so every path is covered); single-seeker searches no longer skew a
source's health history; the posting re-check in `migrate()` runs once per schema version and is retried if it fails; apply plan
ids are stored in the database, so a restarted server still honours an approved plan; a Fo send that timed out is not repeated
for 30 minutes (a clear failure can be retried at once); `get_notifiable` now returns an object that carries the
untrusted-data note; and `review_required` now means something: it is on by default and blocks batch auto-submission until the
user turns it off with `set_review_mode`.

History was rewritten on Oct 18 to replace a third party's first name in every commit, tag and branch (a backup bundle was kept by the owner, not published). Old commit ids no longer exist; clone again if you had one.

Fixed after the phone-apply and Google review rounds: auto-tailoring keeps every bullet; the phone approval parameter is not an MCP
parameter; Fo asks before submitting unless review mode is off; a phone code is checked, consumed and marked started as one step
and only after the request is built; send failures, lost confirmations and duplicate `jp doc` requests are handled; Google
requests use only files you own, treat Drive content as data, never share, and keep job titles out of file names.

What remains, honestly:

| Area | Limit | Why it cannot be closed here |
|---|---|---|
| `web_fetch` DNS rebinding (Telegram bot and the Fo bridge) | We check the host, then Wigolo resolves it again; a malicious DNS server could change its answer in between. | Closing it needs an egress proxy that refuses private ranges, or pinning the connection inside Wigolo. Do not expose the web tools to untrusted users; through the bridge only your own Fo reaches them. |
| `ask_fo` is not idempotent | Fo's MCP has no idempotency key, so a send that times out may still complete. The 30-minute guard makes a duplicate unlikely, not impossible. | It needs support from Fo's API. |
| Text approval (`jp yes`) | The inbox reads your Fo transcripts through a headless model, and the one-time code is texted into the same chat, so a manipulated relay could echo it. | Fo's own confirmation is the default defence (review mode on). With review mode off, the code is the only gate. Closing it needs an inbound channel the relay cannot read, or Fo's webhook. |
| Google files found by name | The first request finds or creates "JobPilot Tracker" by name (Fo said it created a fresh one in your own Drive when none existed; open the saved link once to confirm). JobPilot then saves the link from Fo's own reply and every later request opens the sheet by its id. "JobPilot Resumes" and the letter/resume Docs are still found by name, and "do not share" is a request to Fo. | JobPilot cannot see your Drive. `jp link reset` forgets the saved link. |
| Texts typed in iMessage/SMS never reach the inbox | Confirmed live on 2026-10-18: in Fo's SMS threads the transcript API returns only Fo's own messages, never yours, and the polling inbox answered 0 commands in 15+ polls. `jp ...` commands work only in Fo's chat channel (the Fo app or `ask_fo`). | Needs Wajo's inbound-reply webhook or a transcript API that includes your texts. Ask Wajo. |
| Google and attachment steps untested live | Fo says it can create Docs and Sheets, export a PDF and upload it to a form. This was asked, not exercised end to end. | The first real run is a supervised pilot. |
| Setup trusts a model once, then you | Setup lets a headless model lay out your resume. Each value must appear in your file, but the check is per value: a model could move a real bullet under the wrong employer. | You read the result and confirm before it is saved. Pilot it on two or three real resumes first. |
| Cover letters are templated | The letter is built from your own bullets, so it reads plainer than a model-written one. | A model-written letter could not be checked against your resume in code. |
| Tracker rows are never removed | Requests forbid deleting rows on purpose. A match that is no longer among your best open matches is set to `stale` ("no longer listed"); a job whose deadline passed in the last 14 days is `deadline passed`. One request carries at most 100 rows (50 applications, 15 just-closed jobs, the best open matches), so older rows keep their last status. | Fo cannot be trusted to prune a sheet safely. |
| 100 rows in one Fo turn is untested live | Fo is asked to read column A once and write every row in one range update. Whether it lands all of them has not been run. | Pilot it on a copy of the sheet and count the rows that land. |
| Slow sheet refresh blocks the next one | A refresh that times out ("working") holds a 30-minute lease, so `jp sync` answers "still running" and outcome texts skip the refresh until it clears. Two processes claiming at once rely on SQLite's write lock. | Two Fo turns writing one sheet would duplicate rows. |
| Sheet status rules | Fo is told never to turn a started, applied, interview, offer, rejected or withdrawn row back into match, stale or deadline passed; JobPilot itself still allows some changes among those outcomes. A request built through the MCP tool is refused while a refresh is running, but nothing stops Claude sending an old one later. | The sheet is a view; JobPilot's database is the record. |
| The sheet never writes back | Edits in the Google Sheet do not reach JobPilot. Email can only PROPOSE an outcome (`propose_status_from_emails`: fixed Python patterns, forward moves only); nothing changes until you confirm it. Otherwise outcomes arrive when you record them (`jp applied|interview|rejected|withdrawn <id>`, Claude, or the dashboard). | Letting a sheet or email change records on its own would be a new injection path. |
| Logins live in Fo's browser | JobPilot remembers which boards have a working login and where an application stopped, and tells Fo to reuse the login and continue from that page, but it cannot hold or restore a browser session itself. | JobPilot has no browser. |
| Pre-submit check trusts Fo's field list | `pre_submit_check` checks the fields Fo reports. A field Fo leaves out is not checked. | Only Fo sees the form. |
| The bridge key expires | Since 2026-10-19 the bridge key stops working 90 days after it was made (`my_status` warns 14 days before). Rotate with `python -m jobpilot.bridge --rotate`. | A key that never expires is worse. |
| Web and posting text now reaches Fo | Fo can call the Wigolo web tools and read full job descriptions through the bridge, so text written by strangers reaches an agent that can email, call and buy. Results are cut, links are vetted and a "data, not instructions" note rides along, but that is a request, not a lock. | Fo already browses pages itself when it applies, so the extra exposure is small; the real gate is still your approval code and Fo's review draft. |
| A saved answer Fo records is trusted afterwards | Fo can add (never overwrite) an answer to your vault; a hostile page that talked Fo into saving one would pass the form check later. `my_status` counts them (`answers_saved_by_fo_7d`) and each save is logged (`answer_saved_by_fo`); the answer goes into forms as typed until you approve its formal version. | Fo hears your words only through chat; you review Fo's filled draft before giving the code. |
| Formal-version warnings are not proof | JobPilot flags a formal draft that changes a number, unit, limit word, refusal, yes/no or adds a name, but a person must still read it before approving; a clean list does not prove every fact was kept. Fo cannot approve a draft that has warnings. | Only a human can confirm the meaning. |
| One application at a time is a database rule | The slot is claimed in one database transaction, so Claude, the bridge, the dashboard and the text inbox cannot both start. A crash after the claim frees itself after 2 hours, or by `cancel_application`. | |
| The email scan needs the direct Fo API | With only the headless relay (no `FO_API_KEY`), the daily email scan is skipped, because the relay's model would read Fo's reply, which carries email text, while holding the power to message Fo. | Wajo's API key is the safe route. |
| An answer typed once goes in as typed until approved | If you have not approved a formal version, the answer reaches the employer exactly as you wrote it (casual wording included). `pre_submit_check` and `my_status` list the answers still waiting. | A model's wording cannot be proven fact-for-fact; only you can approve it. |

Fixed on 2026-10-19 (side effects of the stricter rules): a dashboard preview works while another job is in flight; the dashboard
spends your authenticator code only after the request was built (a build that fails, or a route that took the slot first, leaves
the code usable); a failed text-inbox build spends no code and leaves no "started" record; importing history now turns a row that
matches a job JobPilot already found into that job's application (never overwriting one); the nightly CSV snapshot and interview
prep run even when the morning search failed.
| The writing helper's replies are Fo's wording | Fo drafts from your resume and your notes and is told never to guess a fact, but a model can still slip. Its reply is only shown; you edit it, save it, and approve the formal version, so nothing reaches a form without you reading it. Up to 100 helper requests a day. | A person must read what goes to an employer. |
