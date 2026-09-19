# JobPilot — Design Spec

**Date:** 2026-09-19
**Status:** Awaiting review
**Owner:** Abhimanyu Singh (operator) — built on behalf of Test Seeker (job seeker)

## 1. Context

Test Seeker is a Senior Merchandiser (Product Development) at Acme Exports
Pvt. Ltd., [redacted], living in Gurugram. She wants to move from the export
house side to the **brand / buying office side** of apparel sourcing.

She is non-technical. She will never run a command or read a log. Abhimanyu
operates the system on her behalf. She struggles to sustain a job search on her
own, so the system must reduce the effort of applying to near zero and nudge
proactively.

### Her profile (from resume, 2026-09-19)

| Field | Value |
|---|---|
| Current role | Senior Merchandiser - Product Development, Acme Exports ([redacted]) |
| Prior | Merchandiser, Acme Exports ([redacted]); Management Trainee ([redacted]) |
| Total experience | ~4 years, single employer |
| Accounts handled | **Ralph Lauren (USA)**, **Marks & Spencer (UK)** |
| Domain | Product development, costing & margin, TNA / critical path, sampling & approvals, vendor management, buyer communication |
| Education | [redacted], [redacted], [redacted], [redacted] (confirmed) |
| Location | Gurugram, India |
| Contact | [redacted], [redacted] |

**Ralph Lauren and M&S experience is the strongest targeting signal in the
profile.** Both maintain sourcing/buying offices in the Delhi NCR region. Job
scoring weights account adjacency heavily.

## 2. Goals

1. Find brand-side / buying-office merchandising roles she would not otherwise see.
2. Produce a tailored, ATS-safe resume per role, ready before she is asked to act.
3. Track every company seen, scored, applied to, and responded to.
4. Nudge proactively, and never confuse "no jobs" with "we are blocked".

## 3. Non-goals (this spec)

- Automated submission of applications. Workday and SuccessFactors require a
  per-company account; account creation and credential entry are out of scope
  permanently. The system prepares; a human submits.
- LinkedIn post generation and interview prep. Separate later specs, reading
  from the same store.
- Export-house-to-export-house roles, D2C, and retail category roles. Explicitly
  descoped by the operator.
- Public or self-service signup. Seekers are added by the operator by hand.

## 4. Architecture

**Revised 2026-09-19 after investigation.** The system is not a standalone
platform. Claude is the orchestrator; open-source MCP servers are the hands.
This removes most of the bespoke code from the original design.

### Deployment target

Everything runs on **DESKTOP-G47CCOU** ("abhimanyu-desktop"), Windows 10 Pro,
reachable from the Mac as `ssh pc` over Tailscale. Verified 2026-09-19 to have
claude.exe 2.1.277, bun, node v24.11.1, git 2.53, and Python 3.12 under
`%LOCALAPPDATA%\Programs\Python\Python312\`. No new software needed.

Machine constraints carried over from `Machines/windows-desktop.md`:

- `where python` resolves to `C:\Python314` first. **Pin the 3.12 path
  explicitly**; never rely on PATH.
- PowerShell 5.1 reads UTF-8 `.ps1` as ANSI. **Generated scripts must be
  ASCII-only.**
- Git operations set `GCM_INTERACTIVE=Never` to avoid the invisible-prompt hang.
- **C: is BitLocker-encrypted with a pre-boot password and no TPM.** Any reboot
  halts the machine until the password is typed physically. This is the primary
  availability risk, mitigated but not solved by the fallback below.
- **Nothing is installed on C:.** Drive survey 2026-09-19: C: 100.2 GB free,
  D: 183.2 GB, E: 377.7 GB. The project, its virtualenv, the store and all
  generated documents live at `E:\Github\Aasaan AI\jobpilot\`, alongside the
  existing repos. **Caveat:** Claude Code's own state (`~/.claude/`, the plugin
  config and the Telegram access list) necessarily stays under
  `C:\Users\Desktop\.claude\`. It is a few KB and cannot be relocated.
  Moving the project off C: solves the space problem; it does not affect the
  pre-boot password problem, which is a property of the OS volume.

### Two entry points, both Claude

1. **Scheduled routine** - wakes on a cron, runs discovery, scores, tailors,
   writes the digest, exits. Needs no persistent session.
2. **Live Telegram channel** - `claude --channels plugin:telegram@claude-plugins-official`
   held open on the desktop. Alex DMs the bot; Claude answers in chat.

### Hands (MCP servers)

- **JobSpy MCP** - aggregator search across Naukri, Indeed IN, Glassdoor,
  Google, LinkedIn. Evaluate `borgius/jobspy-mcp-server` (MIT) first; it is a
  Node wrapper shelling out to the Python library, so if integration is fiddly,
  fall back to a thin FastMCP wrapper over `python-jobspy` directly.
- **JobPilot MCP** - built here. ATS JSON fetchers (Workday, SuccessFactors,
  Greenhouse, Lever), the SQLite store, canaries and source health, resume
  rendering with the extraction gate, application tracking.
- **Validation gate** - the seven blocking checks from `leelakrishna288/ai-career-agent`
  (Apache-2.0) ported into JobPilot MCP with attribution, rather than a
  dependency on an unproven repo.

### Telegram

The official Anthropic plugin (`telegram@claude-plugins-official`, free, open
source) rather than a custom bot. Access policy set to `allowlist` with
Alex's numeric Telegram ID only. Claude gets `reply` (with file attachments
up to 50MB, which is how tailored resumes reach her), `react`, `edit_message`.
Known limitation: the bot has no message history or search, so all durable
state must live in the JobPilot store, never in the chat.

### How the system remembers

The Telegram plugin has no message history and no search, and Claude itself
starts every scheduled run cold. **Neither is the memory layer, and neither
needs to be.** All continuity lives in the JobPilot SQLite store, which is
strictly better than chat history for this purpose: it is queryable, it
survives restarts and machine moves, and it can be audited when something looks
wrong.

The specific worry this addresses - re-notifying her about the same job every
day - is handled by a first-class `notifications` table. A job is eligible for
notification only if no delivered row exists for it, or if a deliberate
re-surfacing rule fires (the 3-day nudge in section 9). "We already told her
about this on this date" is stored as a fact, not inferred from a transcript.

MemPalace is already installed on the desktop and remains the place for
conversational and cross-session context. Job state does not go there; it goes
in the store, because job state needs exact queries, not semantic recall.

### Availability: desktop primary, Mac fallback

The desktop is primary. **The Mac is the fallback, not merely a watchdog.** If
the desktop has not completed a run inside its expected window - most likely
because a reboot left it at the BitLocker prompt - the Mac's scheduled run
executes the pipeline itself and alerts Abhimanyu that it did so. Alex's
digests keep arriving through an outage rather than stopping silently.

This requires shared state across two machines. The store is therefore
canonical on the desktop at `E:\Github\Aasaan AI\jobpilot\`, and
synchronised through a **private git repo holding a SQL dump** - the same
pattern already proven by the memory vault. Each run pulls before starting and
pushes after finishing. The data is append-mostly and every row is keyed by
`(source, external_id)`, so conflicts are rare and resolvable by timestamp.

**Open decision for the operator:** whether her resume and profile go in that
private repo alongside the job state. Keeping them there makes the Mac fallback
self-sufficient. Keeping them out means the fallback can discover and score but
not tailor. Section 13's rule stands either way: this never goes near the
ClaudeMemory vault, which syncs to a shared remote.

### Data flow

```
scheduled routine (desktop)
  -> JobSpy MCP + JobPilot MCP ATS fetchers
  -> normalize, dedupe, store(status=discovered)
  -> canary check per source -> source_runs
  -> Claude scores        -> store(fit_score, reasoning)
  -> Claude tailors top N -> validation gate -> render -> extraction gate -> file
  -> digest
  -> Telegram push (M3) -> her action -> store
  -> stale items -> escalating nudges
```

## 5. Discovery: reliability is the primary requirement

The operator's stated fear: bot protection causes the scraper to return
nothing, or only low-quality listings, and nobody notices. The design treats
**undetected blindness as the top-priority failure mode.**

### 5.1 Source layers, in order of trust

**Layer 1 — ATS JSON APIs (primary).** For each target employer, query the
endpoint their own careers page calls. Workday exposes a JSON jobs endpoint per
tenant; SuccessFactors, Greenhouse and Lever publish documented job APIs. These
are intended to be read, are not adversarial to clients, and return complete
structured records. Most brand-side employers in scope use one of these.

**Layer 2 — Structured data.** `JobPosting` JSON-LD embedded in career pages
(published so listings appear in Google Jobs), plus sitemaps and RSS/Atom
feeds. Stable and cheap to parse.

**Layer 3 — Aggregators via JobSpy** (`pip install python-jobspy`, MIT).
Covers Naukri, Indeed (country_indeed=india), Glassdoor, Google Jobs, LinkedIn.
Treated as supplementary; expected to degrade. LinkedIn runs at low frequency
and never authenticated as her.

**Layer 4 — Playwright fallback.** Only for targets that cannot be served by
layers 1-3. Slow and heavy; used sparingly and cached aggressively.

### 5.2 Target company list

A curated CSV of brand-side employers with a sourcing/buying presence in India,
seeded around her accounts and expanding outward:

- **Tier 1 (account adjacency):** Ralph Lauren, Marks & Spencer
- **Tier 2 (US/UK sourcing offices in NCR/Bangalore):** PVH, Levi's, Gap,
  Walmart Sourcing, Target Sourcing, Next, Primark, Tesco/F&F, ASOS,
  Tommy Hilfiger, Calvin Klein, Columbia, VF Corp
- **Tier 3 (buying agents / sourcing intermediaries):** Li & Fung, Impactiva,
  MGF Sourcing, Triburg, William E Connor, AMA Herbst
- **Tier 4 (Europe/fast fashion):** Inditex, H&M, C&A, Bestseller, S.Oliver

Each row records: company, ATS vendor, endpoint/tenant, careers URL, region,
tier, enabled flag. The list is config, not code, so it grows without changes
to the pipeline.

### 5.3 Blindness detection (the canary system)

Every source defines a **canary query** whose result is known to be nonzero
under healthy conditions (e.g. Naukri, "merchandiser", Gurgaon).

On every run, per source:

1. Run the canary before the real queries.
2. `canary == 0` -> mark source `BLOCKED`, do not trust its zero results,
   escalate to the operator.
3. Record `results_count`, `http_status_summary`, `latency`, `canary_ok` in a
   `source_runs` table.
4. Compare against the trailing 7-run median. A drop below 25% of median marks
   the source `DEGRADED` and raises a warning.
5. Any `429` or captcha signature -> exponential backoff, and the source is
   flagged rather than silently retried into a hard ban.

**Rules that follow from this:**

- Zero results from a source is never reported as "no jobs" unless the canary
  passed. Otherwise it is reported as "source unavailable".
- Every digest carries an explicit source health line. A quiet week and a blind
  week must never look the same to the reader.
- If **all** Layer 1 and Layer 2 sources are healthy and still return nothing,
  that is a genuine quiet period and is stated as such.

### 5.4 Polite client behaviour

Conditional requests (ETag / If-Modified-Since), local response caching,
jittered delays between requests, realistic and honest User-Agent, per-source
concurrency limits, per-source exponential backoff. The objective is to remain
a well-behaved client indefinitely rather than to extract maximum volume once.

### 5.5 Quality floor

To prevent "only bad openings are shown": every job is scored, and the digest
reports both the count found and the count above the fit threshold. If 40 jobs
were found and none cleared the bar, the digest says exactly that, rather than
padding the list with poor matches to look productive.

## 5A. Multi-user

Added 2026-09-19. Alex is the first seeker, not the only one. Several of the
operator's friends need the same mechanism, so the system is multi-tenant from
the start rather than retrofitted later.

### Model

A **seeker** is a person being helped. Each seeker has their own profile, base
resume, target company list, search terms, scoring weights, Telegram identity
and notification state. There is one **operator** (Abhimanyu), who owns the
machine, the account and the data.

The critical normalisation: **a job posting is global; a seeker's relationship
to it is not.** Two seekers targeting Gurugram merchandising roles will see the
same listing, and it must be fetched, parsed and stored once.

- `job_postings` - global, deduped by `(source, external_id)` and content hash.
  Fetched once, shared by all seekers.
- `seeker_jobs` - per seeker: fit score, reasoning, status, notification state,
  tailored resume reference. This is where `scores`, `applications` and
  `notifications` are keyed.

This also saves real quota: scoring is per seeker, but scraping and parsing -
the slow, rate-limited part - happens once for everyone.

### Isolation (mandatory)

Multiple seekers sharing one Claude session and one store creates a genuine
leakage risk: Claude serving one person while holding another's data in
context, and mentioning it. The following are hard requirements, not
guidelines:

1. Every MCP tool that touches seeker data takes an explicit `seeker_id` and
   scopes every query by it. There is no "current seeker" global.
2. In the Telegram channel, `seeker_id` is derived **only** from the verified
   numeric sender ID. It is never inferred from message content, and never
   accepted from the message itself. A request to "show me Priya's jobs" is
   refused regardless of who asks.
3. An unrecognised sender is dropped, per the `allowlist` policy.
4. Scheduled runs process one seeker at a time, in separate batches, so one
   seeker's listings cannot appear in another's digest.
5. Generated documents are written to per-seeker directories, never a shared
   output folder.

### Consequences the operator should weigh

- **Quota multiplies with seekers.** Scraping is shared, but scoring, tailoring
  and every Telegram conversation are per person, on his subscription, against
  the same pool as Layaa and eduflow. A per-seeker daily cap is set in config,
  and the config caps total daily work as well.
- **Several third parties are now using his account.** Anthropic ships
  multi-user allowlisting in the official plugin, so this is a contemplated
  setup, but the account, the budget and what happens on it remain his.
- **He is now holding several people's personal data** - resumes, contact
  details, salary expectations, job-search activity their employers would not
  want visible. The private sync repo must stay private, and each seeker should
  be told plainly that their data sits on his machine and that he can see it.

### Onboarding a seeker

Adding a person is config plus one resume, not code: a row in `seekers`, a
profile YAML, a base resume, a target list (usually inherited from a shared
template and adjusted), and their Telegram ID added to the allowlist.

## 6. Data model (SQLite)

**Global (shared by all seekers):**

- `companies` - id, name, tier, ats_vendor, endpoint, careers_url, region, enabled
- `job_postings` - id, company_id, source, external_id, title, location,
  description, url, posted_at, first_seen_at, last_seen_at, content_hash
- `source_runs` - source, run_at, canary_ok, results_count, status, notes
- `machine_runs` - machine, started_at, finished_at, status. Drives the Mac
  fallback decision

**Per seeker:**

- `seekers` - id, name, telegram_user_id, profile_path, active, daily_cap
- `seeker_targets` - seeker_id, company_id or search_term, weight
- `seeker_jobs` - seeker_id, posting_id, status, first_seen_at
- `scores` - seeker_id, posting_id, fit_score, reasoning, model, scored_at
- `applications` - seeker_id, posting_id, status, resume_version_id, applied_at,
  response_at, notes
- `resume_versions` - id, seeker_id, posting_id, path, format, generated_at,
  validation_report, extraction_ok
- `notifications` - seeker_id, posting_id, channel, delivered_at,
  kind (first|nudge|expiry), message_ref. Prevents duplicate notification and
  makes nudge scheduling explicit
- `events` - append-only log of every state change, scoped by seeker

Status vocabulary for `seeker_jobs`: `discovered -> scored -> shortlisted ->
presented -> applied | skipped | deferred | expired`.

Dedupe for `job_postings`: `(company_id, normalized_title, location)` plus a
content hash, so the same role appearing on Naukri and on the company's Workday
is one row with multiple source references - fetched once, scored per seeker.

**Every per-seeker table carries `seeker_id` and every query filters on it.**
This is enforced in the store layer, not left to callers.

## 7. Resume engine

**Input:** `base_resume.yaml` — her resume as structured data, not a PDF.

**Per job:** `brain.tailor(base_resume, job)` reframes export-side language into
buying-office language. This is a genuine translation, not keyword insertion:
"buyer PO execution and TNA follow-up" becomes "critical path ownership and
supplier capability management". Ralph Lauren and M&S experience is surfaced
first whenever the target is a US/UK brand.

**Hard rule:** the tailoring layer may reframe, reorder and re-emphasise. It
may not invent employers, dates, titles, metrics or skills. Fabrication on a
resume is a firing offence in the industry and a non-negotiable constraint in
this system. Every generated resume is diffed against the base, and any new
factual claim is flagged for human review before the file is written.

**Rendering: DOCX primary, PDF on demand.** Workday and Taleo parse .docx more
reliably than PDF, because the text is already text with no glyph-positioning
layer to misread. Her current PDF's character detachment is exactly that class
of failure. DOCX is the default deliverable; PDF is generated only when a portal
requires it, and faces the same extraction gate.

**Rendering rules (ATS-safe).** Her current PDF exhibits character-detachment under
text extraction ("S ENIOR MERCHANDISER", "C osting"). Workday and Taleo will
parse it the same way, and Taleo uses literal exact matching, so this plausibly
suppresses keyword matches today. Requirements for the renderer:

- Single column, no text boxes, no tables for layout, no headers/footers
- Standard embedded fonts, no artificial letter-spacing
- Section headings from a conventional vocabulary (Experience, Education, Skills)
- Dates in a single consistent parseable format
- **Verification gate:** every generated PDF is re-extracted with `pypdf` and
  the extracted text is asserted to contain her name, current title, both
  account names, and all employer names as contiguous strings. A PDF that
  fails re-extraction is never delivered.

The last point is the real test of the resume engine, and it is a pass/fail
gate, not a warning.

Free manual spot-check: https://ats-screener.vercel.app (browser-only, files
are parsed client-side).

## 8. AI layer

There is no AI adapter abstraction in the revised design, because there is no
separate application calling a model. **Claude is the orchestrator**, reached
either by the scheduled routine or by the live Telegram channel, and calls MCP
tools directly.

Consequences the operator has accepted:

- All of Alex's usage draws on Abhimanyu's Claude subscription quota, shared
  with Layaa, eduflow and cockroach work. Batch sizes capped in config; the
  routine runs off-peak.
- She is using his account. Anthropic ships multi-user allowlisting in the
  official plugin, so this is a contemplated setup, but the account and the
  budget remain his responsibility.
- She cannot run this herself without her own account.

If the system later moves to a Linux VPS, the scheduled routine becomes either
Claude Code authenticated on that box, or API calls with a hard spend cap. The
MCP servers are unaffected either way, which is what keeps the migration cheap.

## 9. Anti-procrastination design

Notifications alone are a weak lever. The design removes activation energy and
adds gentle accountability instead.

1. **Nothing to start, only something to approve.** The tailored resume exists
   before she is told the job exists.
2. **One tap.** Apply / Skip / Tomorrow. Skipped items go quiet permanently.
3. **Escalation on silence, not on volume.** An item left undecided for 3 days
   is re-surfaced once, more directly. Undecided after 7 days, it expires and
   says so. The system never accumulates a guilt-inducing backlog.
4. **Volume cap.** At most 5 roles per digest. A list of 30 is a reason to close
   the app.
5. **Operator summary.** A weekly line to Abhimanyu: found / shortlisted /
   presented / applied. A friend noticing is stronger than any bot.

## 10. Error handling

- Sources fail independently. LinkedIn being rate-limited must not prevent
  Naukri results from reaching the digest.
- Partial runs still produce a digest, annotated with what was missed.
- The AI layer failing means jobs stay at `discovered` and are picked up on the
  next run; no job is lost, no duplicate scoring.
- All failures write to `source_runs` / `events`. Nothing fails silently.
- The resume re-extraction gate is blocking: a failing PDF is not delivered.

## 11. Testing

- Parser tests run against **saved fixture payloads**, never live network.
  Fixtures captured once per source and committed.
- Resume rendering tested by round-trip extraction assertion (section 7).
- Dedupe tested with the same role from three sources.
- Canary logic tested with simulated zero-result and 429 responses.
- Scoring is not unit-tested for output values; it is smoke-tested for schema
  and range.

**Verification gate for milestone completion:** the fixture suite passes and a
real run against live sources produces a digest with all Layer 1 sources
healthy.

## 12. Costs

| Item | Cost |
|---|---|
| JobSpy, pypdf, Playwright, SQLite | Free, MIT/BSD |
| AI layer (milestone 1) | Existing Claude subscription; consumes shared quota |
| AI layer (post-cloud) | ~$3-8/month, hard cap in config |
| Hosting (now) | Free, runs on this Mac |
| Hosting (later) | ~Rs 400-800/month VPS |
| Telegram bot | Free |
| Residential proxies | Optional, ~$2-15/GB. Not purchased without evidence of need |

## 13. Risks and constraints

- **Terms of service.** Layer 3 scraping of Naukri/LinkedIn/Indeed is against
  those sites' terms. Layers 1 and 2 are not. The design leans on 1 and 2 as
  primary partly for this reason. LinkedIn is never accessed with her account.
- **Data.** Her personal data and resume live in this repo. It must not be
  pushed to any public remote, and must not be placed in the ClaudeMemory
  vault, which syncs to GitHub. `.gitignore` excludes generated resumes and the
  database by default.
- **jobseek data licence.** If any jobseek-derived data is used later, its job
  data is CC BY-NC 4.0 — acceptable for personal use, not for anything
  commercial.
- **Single-employer resume.** Four years at one company is her main structural
  weakness for brand-side roles. The tailoring layer should address it
  positively (depth of account ownership) rather than ignore it.

## 14. Repository reuse assessment

Investigated 2026-09-19:

| Repo | Decision | Basis |
|---|---|---|
| speedyapply/JobSpy | **Adopt as dependency** | MIT, pip-installable, natively supports Naukri and India. Directly reusable |
| colophon-group/jobseek | **Reject** | Not a library. Requires Postgres, Redis, Typesense, S3, Next.js, Playwright. Operating it for ~60 companies costs more than a focused watcher |
| sunnypatell/ats-screener | **Reject as dependency, keep as manual tool** | SvelteKit browser app on Firebase calling Gemini/Groq. No importable scoring library |
| Gsync/jobsync | **Borrow schema concepts only** | Full Next.js app with its own auth and AI layer; disproportionate for one user |
| Resume-Matcher, resume-forge, ResumeLM | **Reject** | Their value is an LLM prompt chain, already available natively |
| Auto-apply bots (EasyApplyBot et al.) | **Reject** | Built for US tech ATS and LinkedIn Easy Apply. Wrong geography and wrong systems, with real account risk |

## 15. Milestones

Shipped incrementally; each milestone is usable on its own.

**M1 (now):** JobPilot MCP skeleton + multi-seeker store + JobSpy MCP wired up
+ canary health + target company list + scoring + local digest. Alex is
seeker #1; the schema and tools are multi-seeker from the first commit, because
retrofitting tenancy into a store is far more expensive than starting with it.

**M2:** ATS JSON fetchers for tiers 1-2, structured-data parsing, resume
tailoring with the validation gate, DOCX renderer + extraction gate.

**M3:** Telegram channel on the desktop allowlisted to Alex, scheduled
routine, Mac watchdog.

**M4:** Separate specs - LinkedIn post generation; interview prep.

## 17. Scope addendum (2026-09-19)

Four capabilities requested after the M1 plan was finalised. None change M1.

### 17.1 Claude's built-in browser as an investigation tool (M2)

Claude Code ships a real browser pane. It is the right tool for **targeted
investigation** and the wrong tool for bulk discovery:

- Every page visit returns page text or a screenshot into the context window.
  Scanning hundreds of listings that way costs orders of magnitude more quota
  than an HTTP fetch, per run, per seeker, forever.
- It is seconds per page against milliseconds for a JSON call.

Legitimate uses, added as MCP tools in M2:

- `investigate_posting(posting_id)` - open one listing and read it in full when
  the scraped description is thin or a score looks wrong.
- `check_company_careers(company_id)` - open one employer's careers page,
  useful precisely where they have no usable ATS JSON endpoint.
- `verify_still_open(posting_id)` - confirm a role has not closed before a
  seeker spends effort on it.

Rule: the browser answers questions about **one** thing. Discovery stays on
ATS JSON and JobSpy.

### 17.2 Asking the system about itself (M2)

Seekers should be able to ask what is happening. This is nearly free given
Claude plus MCP, but the tools must be seeker-scoped and read-only:

- `my_status(seeker_id)` - jobs found, scored, shown, applied this week.
- `my_sources(seeker_id)` - which sources ran and whether any were blocked,
  in plain language.
- `my_history(seeker_id, query)` - what she was shown and when.

These obey section 5A isolation: no roster, no other seeker's data, no
operator internals. A seeker asking about another person is refused.

**Watchlist requests** are supported: "watch H&M careers" adds a company to
that seeker's targets. `add_target_company(seeker_id, name)` writes to
`seeker_targets`, and discovery picks it up on the next run.

**Watching named HR or recruiter profiles is out of scope, permanently.**
Building a file on individual people scrapes third parties who never consented,
is specifically prohibited by LinkedIn, and creates a dossier of named
individuals on the operator's machine. The supported alternative is the
recruiter contact an employer chooses to publish on its own job advert, which
is already in the posting.

### 17.3 Third-party scraping APIs (evaluated, not adopted)

Apify hosts LinkedIn actors. Verified 2026-09-19: the free plan gives **$5 of
monthly credits**, and across LinkedIn actors 1,000 job records cost roughly
**$0.28 to $5.00** depending on actor and plan. At this project's volume the
free credit would plausibly cover it.

Not adopted, for three reasons:

1. **It does not change the terms position.** Apify is a host; scraping
   LinkedIn through it still breaches LinkedIn's terms. It moves the IP
   address, not the legality.
2. **It is not where her jobs are.** Brand-side employers post to their own
   Workday and SuccessFactors tenants, which M2 reads directly - free,
   permitted, and more complete than any LinkedIn scrape.
3. **It adds a paid external dependency** for the weakest source.

Recorded as a fallback with real numbers if ATS coverage proves insufficient.
Revisit only with evidence, not in advance.

### 17.5 Wigolo as the crawl layer (R4)

`KnockOutEZ/wigolo` is already installed and in use on DESKTOP-G47CCOU as
LayaaOS's main search-and-crawl path, with SearXNG as its fallback
([[Projects/Layaa AI/open-source-tooling-shortlist]]). It gives agents web
search, fetching and crawling on the operator's own machine with no API keys
and no per-query cost.

**Adopted for R4**, where it fills the exact gap this design has: tier-3 and
tier-4 employers and buying agents with no usable ATS JSON endpoint. Those are
generic career pages, which is crawling - and the LayaaOS sweep recorded that
crawling had no service configured at all before Wigolo.

Division of labour, so nothing is used for the wrong job:

| Source | Tool | Why |
|---|---|---|
| Naukri, Indeed, LinkedIn, Glassdoor | JobSpy | Structured board results, already parsed |
| Workday / SuccessFactors / Greenhouse / Lever employers | ATS JSON (R4) | Authoritative, complete, permitted, no parsing guesswork |
| Employers with only an ordinary careers page | **Wigolo** | Real crawling, free, already running on the target machine |
| One page needing JS interaction or a human-eye check | Claude's built-in browser | Interactive; too expensive for bulk |

**Licence boundary, carried over from the vault's warning.** Wigolo is
**AGPL-3.0**. It is safe to *run* as a separate tool and unsafe to *embed* in
anything sold. JobPilot is a personal tool and is not sold, so this is not a
problem here - but the boundary is kept anyway, because the same machine hosts
commercial work: **JobPilot calls Wigolo as a separate process over its own
interface and never vendors, imports or bundles its code.** Same treatment as
[[Decisions/dify-licence]] and [[Decisions/gitnexus-licence]].

**Cost:** none. It runs locally and is already provisioned. It also removes any
remaining argument for Apify (§17.3).

### 17.6 Seekers are not all in apparel (decided 2026-09-19)

The other seekers work in **different fields entirely**. Everything domain-specific
therefore becomes per-seeker data, not one shared file. Decided before R2 was
built, so this is a design change rather than a retrofit.

**What moves per seeker:**

| Was | Becomes |
|---|---|
| `data/scoring_rubric.md` (apparel brand-side, shared) | `data/seekers/<name>/rubric.md` per seeker |
| `data/companies.csv` (20 apparel employers, shared) | `data/seekers/<name>/companies.csv`, falling back to a shared `data/companies-<domain>.csv` |
| `min_score`, exclusions in one profile | unchanged - already per seeker |

**What stays shared, because it is domain-neutral:** everything in `sources/`,
`store/`, `health.py`, the digest, the notification ledger, the canary rules,
and the tenancy enforcement. The machinery does not care what industry a job is
in; only the rubric and the target list do.

**Onboarding a seeker in a new field** is therefore: a profile YAML, a rubric
markdown file, a company CSV, and a row in `seekers`. No code change. That is
the test of whether this split is correct - if adding a seeker in a new field
requires touching Python, the boundary is in the wrong place.

**Rubric contract.** Every rubric, whatever the field, must contain: score
bands, an explicit list of what to score 0-39 regardless of title match, what
to weight heavily for that person, the reasoning format, and the untrusted-text
warning. The apparel rubric written for Alex is the worked example.

### 17.7 Digest review gate (decided 2026-09-19)

For the first two to three weeks after Telegram goes live, digests queue for the
operator's approval before reaching the seeker, while the rubric is still being
tuned. After that they go direct. Implemented as a per-seeker `review_required`
flag on `seekers`, defaulting to 1 and cleared per seeker when the operator is
satisfied - not a global mode, because seekers will be onboarded at different
times and a later seeker still needs the early-weeks gate.

### 17.8 Seekers search in different countries (decided 2026-09-19)

One seeker searches India; another searches the United States. The design so far
hard-wired India: every site carried `country_indeed = "india"`, and Naukri only
covers India. That would have returned Indian jobs to a US seeker, or nothing,
with the run reporting healthy.

**The source becomes the unit of (site, market), not just site.** A source key
names both, for example `naukri_in`, `indeed_in`, `indeed_us`, `linkedin_us`,
`glassdoor_us`. Each has its own canary, its own health history and its own
failure boundary, exactly as before. A canary that passes for Indeed India says
nothing about Indeed US, so they must not share one.

**Each seeker declares the sources they use** in their profile (`sources:`),
and it is required, never defaulted. A default of "everything enabled" would
quietly send a US seeker through Naukri and an India seeker through Indeed US.
Discovery runs a source only if at least one active seeker subscribes to it,
and runs each search with the location of the seekers who subscribed.

**What this does not change:** the store, health rules, digest, notification
ledger and tenancy are market-neutral already.

**Consequences for a US seeker worth knowing now:**

- **Coverage is thinner from JobSpy alone.** Naukri, the source that works well
  for India, has no US equivalent here. In the US the boards are Indeed,
  LinkedIn and Glassdoor; LinkedIn is the most useful and the least reliable
  without proxies, and JobSpy's Google Jobs and ZipRecruiter scrapers were
  reported broken (issue #302, open when checked). Expect LinkedIn to be the
  source the canary reports on most often.
- **Employer applicant-tracking systems matter more, not less.** Many US
  employers post through Greenhouse, Lever, Ashby, Workday and similar systems
  whose job boards are published as public JSON. The R4 ATS fetchers are
  therefore the main path for US seekers, and I would treat R4 as more urgent
  than the plan currently implies.
- **Work authorisation is the deciding factor for a non-citizen searching in
  the US**, and nothing in the system knows it. A seeker who needs employer
  sponsorship should not be shown roles that say "no sponsorship". Whether a
  seeker needs sponsorship is a fact only that seeker can supply; it is
  recorded as an optional profile field and read by their rubric. It is never
  inferred from a name, a nationality or where someone studied.

### 17.4 Google Sheets as the seeker-facing view (M3)

Each seeker gets a Google Sheet showing every job found, its score, its status
and what has been applied to. Telegram hands her the link. She reads and
annotates it in a tool she already knows, with no app to learn.

**Architecture: one-way, database to sheet.** The SQLite store stays the single
source of truth; the sheet is a rendered view, rebuilt on each run. Status
changes happen through Telegram buttons, which write to the store. Two-way sync
would create a second source of truth and a merge problem for no real gain.
One exception: a `Notes` column she owns, read back and stored against the job.

**Access, and why not per-seeker OAuth.** Asking every seeker to complete a
Google OAuth flow is friction that defeats the point, and credential entry is
something Claude will not do. Instead: a **Google service account owned by the
operator** creates one sheet per seeker and shares it **directly to that
seeker's email address** - not "anyone with the link", which would make a
person's job search readable by anyone who saw the URL.

**Cost:** the Sheets API is free for standard use. Two caveats verified
2026-09-19: quotas are 300 read requests/minute per project and 60/minute per
user, and a **service account counts as a single user**, so all traffic shares
that 60/minute ceiling. Google has stated that exceeding quota is planned to
incur Cloud billing charges later in 2026. This project writes a few times per
day per seeker, which is far below the ceiling, but batch writes rather than
per-row calls so this stays true as seekers are added.

**Data note:** the sheet duplicates job-search history into the operator's
Google account. That is a second copy outside the machine, subject to that
account's security. Worth stating to each seeker alongside the disclosure
section 5A already requires.

## 16. Open questions

1. Salary expectation and notice period — needed for scoring and for
   application forms. Not on the resume.
2. Is she open to Bangalore/Mumbai, or NCR only? Currently assuming NCR
   preferred, others considered.
3. Should the operator review every digest before she sees it (M3 onward), or
   only the first few weeks?
