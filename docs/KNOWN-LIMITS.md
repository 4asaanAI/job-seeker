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

Not fixed, with the honest impact:

| Area | Limit | Impact |
|---|---|---|
| `web_fetch` (Telegram bot only) | Host is checked once; the fetcher resolves it again (DNS rebinding), and a JS-rendering retry can run page scripts that reach private addresses. | Needs an egress proxy that refuses private ranges. Do not expose the web tools to untrusted users. |
| Shared employer feeds | Feeds found for one seeker are stored globally and listed to all seekers. | Reveals which employers someone targets; stored names are seeker-supplied. |
| Profile folders | Two profiles in one folder share resume, rubric and uploads. | Operator error only; the bot creates one folder per seeker. |
| Source health | A single-seeker search records counts used for the "degraded" median. | Can mislabel a source as degraded. |
| Schema migration | `migrate()` re-checks every posting on each start by design, so a half-finished upgrade repairs itself. | Slower starts with a large database. |
| Plan ids live in the server process | A restarted MCP server forgets plan ids; ask for a new plan. | A minor inconvenience, and the safe default. |
| `ask_fo` is not idempotent | A send that times out may still complete; the next run can duplicate a text. | A duplicate text, never a lost digest. |
| Untrusted-data notes | `get_notifiable` returns a bare list, so it cannot carry the note. | The model is told scraped text is untrusted by the other tools and the system prompt. |

Git history still contains a third party's first name from early commits; it was removed from the current tree.
