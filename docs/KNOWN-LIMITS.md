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

What remains, honestly:

| Area | Limit | Why it cannot be closed here |
|---|---|---|
| `web_fetch` DNS rebinding (Telegram bot only) | We check the host, then Wigolo resolves it again; a malicious DNS server could change its answer in between. | Closing it needs an egress proxy that refuses private ranges, or pinning the connection inside Wigolo. Do not expose the web tools to untrusted users. |
| `ask_fo` is not idempotent | Fo's MCP has no idempotency key, so a send that times out may still complete. The 30-minute guard makes a duplicate unlikely, not impossible. | It needs support from Fo's API. |
