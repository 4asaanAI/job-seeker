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
- Multi-user support. Single seeker, single operator.

## 4. Architecture

Local-first Python on macOS, SQLite store, single config file. Designed to lift
to a Linux VPS later without a rewrite: no Mac-specific paths outside the
launcher, no absolute paths in code, all secrets and tuning in `config.toml`.

```
jobpilot/
  config.toml           targets, search terms, thresholds, schedule
  profile/
    alex.yaml        structured profile, derived from resume
    base_resume.yaml    canonical resume content as data
  sources/
    ats_api.py          Workday / SuccessFactors / Greenhouse / Lever JSON
    structured.py       JSON-LD JobPosting, sitemaps, RSS
    jobspy_adapter.py   Naukri, Indeed IN, Glassdoor, Google Jobs, LinkedIn
    browser.py          Playwright fallback
    health.py           canaries, per-source health, block detection
  store/
    schema.sql
    db.py
  brain/
    base.py             AI adapter interface
    claude_code.py      subscription-backed implementation (default)
    api.py              API-key implementation (post-cloud-move)
  resume/
    tailor.py           per-job reframing
    render.py           ATS-safe PDF generation
  digest/
    html.py             local HTML digest (milestone 1 output)
  bot/                  Telegram (deferred to final milestone)
  run.py                orchestrator
```

### Data flow

```
sources -> normalize -> dedupe -> store(status=discovered)
       -> brain.score()  -> store(fit_score, reasoning)
       -> top N by score -> brain.tailor() -> resume/render -> PDF on disk
       -> digest         -> operator reviews -> (later) Telegram push
       -> her action     -> store(status=applied|skipped|deferred)
       -> stale items    -> escalating nudges
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

## 6. Data model (SQLite)

- `companies` — id, name, tier, ats_vendor, endpoint, careers_url, region, enabled
- `jobs` — id, company_id, source, external_id, title, location, description,
  url, posted_at, first_seen_at, last_seen_at, content_hash, status
- `scores` — job_id, fit_score, reasoning, model, scored_at
- `applications` — job_id, status, resume_version_id, applied_at, response_at, notes
- `resume_versions` — id, job_id, path, generated_at, ats_notes
- `source_runs` — source, run_at, canary_ok, results_count, status, notes
- `events` — append-only log of every state change, for auditability and nudges

Status vocabulary for `jobs`: `discovered -> scored -> shortlisted ->
presented -> applied | skipped | deferred | expired`.

Dedupe key: `(company_id, normalized_title, location)` plus a content hash, so
the same role appearing on Naukri and on the company's Workday is one row with
multiple source references.

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

**Rendering (ATS-safe).** Her current PDF exhibits character-detachment under
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

## 8. AI adapter

`brain/base.py` defines exactly four operations:

```python
score(job, profile) -> (float, str)      # fit 0-100, plain-language reasoning
tailor(base_resume, job) -> ResumeDraft
cover_letter(job, profile) -> str
interview_prep(job, profile) -> str      # later milestone
```

Nothing outside `brain/` knows how these are served.

**`claude_code.py` (default).** Work is batched and handed to a scheduled
Claude Code session that reads the store, performs the batch, and writes back.
Consequences the operator has accepted:

- Draws on the same subscription quota as his other projects; scheduled
  off-peak, batch size capped in config.
- Does not survive the move to a VPS. The adapter boundary exists so that
  migration is a config change.
- Cannot be handed to Alex to run herself.

**`api.py` (later).** Same interface over a paid API, with a hard monthly spend
cap in config. Estimated $3-8/month at realistic volume.

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

**M1 (today):** JobSpy discovery + ATS JSON for tier 1-2 + store + canary health
+ scoring + resume tailoring + ATS-safe render with extraction gate + local HTML
digest.

**M2:** Career-page watcher for tiers 3-4, structured-data parsing, scheduled
daily runs via launchd.

**M3:** Telegram bot — digest push, inline actions, status write-back, nudge
escalation.

**M4:** Separate specs — LinkedIn post generation; interview prep.

## 16. Open questions

1. Salary expectation and notice period — needed for scoring and for
   application forms. Not on the resume.
2. Is she open to Bangalore/Mumbai, or NCR only? Currently assuming NCR
   preferred, others considered.
3. Should the operator review every digest before she sees it (M3 onward), or
   only the first few weeks?
