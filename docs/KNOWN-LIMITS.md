# Known limits (from two independent code reviews, 2026-10-18)

Fixed in the same pass: JSON-LD job ids collapsing to the company name (jobs were silently dropped); spreadsheet formula
injection in the `/list` export; seeker text becoming an option in the Wigolo wrapper call; an empty onboarding variable
opening the operator tools; the Fo relays inheriting a project's tool approvals (now a neutral working directory plus an
explicit deny list); "delivered" through the headless relay now needs a real Fo tool result of `complete` in the session
transcript; stricter `clean()` (domains, long numbers, payment and credential words, dotted spellings); the polling inbox
now locks, prunes by age, answers each command once, and no longer echoes job-description keywords.

Also fixed later: the employer-feed lookup cap is now a real 24-hour cap (counted from stored events); tailoring keywords skip generic posting words; one broken profile no longer stops discovery for everyone (reported as `skipped_seekers`), and profile YAML is written atomically.

Not fixed, with the honest impact:

| Area | Limit | Impact |
|---|---|---|
| `web_fetch` (Telegram bot only) | Host is checked once; the fetcher resolves it again (DNS rebinding), and a JS-rendering retry can run page scripts that reach private addresses. | Needs an egress proxy that refuses private ranges. Do not expose the web tools to untrusted users. |
| Shared employer feeds | Feeds found for one seeker are stored globally and listed to all seekers. | Reveals which employers someone targets; stored names are seeker-supplied. |
| Profile folders | Two profiles in one folder share resume, rubric and uploads. | Operator error only; the bot creates one folder per seeker. |
| Source health | A single-seeker search records counts used for the "degraded" median. | Can mislabel a source as degraded. |
| Schema migration | `migrate()` re-parses postings on every server start. | Slower starts with a large database. |
| `ask_fo` is not idempotent | A send that times out may still complete; the next run can duplicate a text. | A duplicate text, never a lost digest. |
| Untrusted-data notes | A few tools (`get_notifiable`, `my_history`, `get_followups`) return scraped text without the note. | The model is still told scraped text is untrusted elsewhere. |

Git history still contains a third party's first name from early commits; it was removed from the current tree.
