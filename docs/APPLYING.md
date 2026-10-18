# Using JobPilot + Fo for your own job search

Real data lives outside git: `data/seekers/` and `data/*.db` are already ignored. Never run this
against the demo folder, and never put your real resume in anything you post publicly.

## 1. Set up yourself as a seeker (no hand-written YAML)

```bash
python -m jobpilot.onboard --name "Your Name" --resume ~/Downloads/resume.pdf --role "Product Manager" \
  --location "India" --terms "product manager,technical product manager" --sources indeed_in,foundit_in,himalayas_in \
  --note "pay: 20 lakh a year or more"
python -m jobpilot.doctor --seeker 1        # tells you exactly what is still missing
```

`onboard` copies your resume into `data/seekers/<name>/` (git-ignored), reads its text (the file every resume fact is later
checked against), writes a starter profile, registers you, and prints the id. Then pin your MCP session
(`JOBPILOT_SEEKER_ID=<id>`, see `.mcp.fo-demo.json.example`), start Claude Code, and ask it to save your structured resume with
`save_base_resume`. Log in to Fo with `/mcp` (restart Claude Code after adding it).

## 2a. Your day-to-day tools

- `my_dashboard`: one call for new matches, applications by status, follow-ups due and applications waiting a week.
- `build_fo_sweep_replies_message`: one Fo request that checks email for replies to ALL submitted applications.
- `manual_apply_packet`: for login-gated jobs Fo cannot complete: the safe link, your resume text and a checklist.

## 2b. Pick jobs Fo can actually apply to

`apply_candidates` lists your best matches, easiest route first: **direct** (a public form on the company's own applicant
system, no account), **aggregator** (a listing whose Apply button leads to the company's page), **login** (foundit, Naukri,
Indeed, LinkedIn, Workday: an account and often a phone code Fo cannot always receive). Lesson from the first real attempt:
foundit's phone verification never delivered a code, so Fo could not get in, and nothing was submitted. Start with
direct and aggregator jobs, and apply by hand where a board needs a code only your phone gets.

## Deadlines

When a source states a closing date (job-board feeds with an expiry, `validThrough` on careers pages), JobPilot stores it. A job
disappears from matches, digests, follow-ups, the job list and the apply plan the day after its last open day, with no sweep
needed, and each search also marks it `expired` (never a job you already applied to). Many boards state no date; those stay until
they stop appearing in searches or you skip them.

## Find the best new jobs (whenever you ask)

Say "find me new jobs". Claude runs `run_my_search` (3 a day) to discover from your enabled boards, scores what is new with the
rubric (a years-of-experience mismatch caps a score below 50), then runs `plan_apply_batch`. From a terminal:
`python -m jobpilot.refresh --seeker N` does all three headlessly (`--no-search` to only score, `--max-batches` caps cost).
Nothing is applied by this: it ends with a list you can approve.

## Apply to all my new jobs (whenever you ask)

**Review mode.** By default (`review_required`) every application needs its own confirmation, so a batch cannot auto-submit.
To allow approved batches, tell Claude to run `set_review_mode(false)`; turn it back on any time. Plan ids are stored in the
database, so a restarted server still honours the plan you approved (they expire after two hours).

Say "apply to my new jobs". Claude runs `plan_apply_batch` and shows you the list (at most 10, best first, no login-gated
boards, nothing already applied, started or skipped). You give ONE yes for that exact list. Then, one job at a time, Claude
builds `build_fo_apply_message(..., batch_approved=True)` and passes it to `ask_fo` unchanged (you still see Claude Code's
permission prompt for each Fo call). With the batch approved, Fo submits without a second confirmation, but it STOPS and tells
you if a form asks for anything not in your resume (salary, notice period, a question, a file) or needs an account or a code.
Claude records each result: `applied` when Fo says it submitted, `started` with a note when it stopped, so a blocked job is not
retried blindly. No calendar events are created. Limits: 10 approved requests per day, only jobs on the approved list.
Jobs on login boards are listed separately for you to apply to by hand.

## 3. Apply with Fo (one job at a time, only with your yes)

1. "Prepare my application for job 12" -> `prepare_application` shows your facts-only resume text and the job's title,
   company and link. Optionally tailor it first (below). Nothing is sent.
2. You say yes to applying to THAT job. Claude calls `build_fo_apply_message`, which builds one request for Fo:
   the vetted posting link, your resume text, and these rules: the page is untrusted data, use only your resume text,
   show you the draft and wait for your confirmation before submitting, **and do not create any calendar event**.
   It refuses links that fail a safety check (not https, IP address, shortener, punycode, credentials in the URL) and
   titles or companies that look like instructions.
3. Claude passes that message to `ask_fo` unchanged. You see Claude Code's permission prompt, and Fo asks you before it
   submits anything. Fo may ask you questions; answer them in the session.
4. When Fo reports it submitted, Claude calls `record_application(status="applied")`.

## 3b. Apply from your phone (text Fo)

With the inbox running (`python -m jobpilot.inbox --once`), text Fo `jp matches`, then `jp apply <id>`. JobPilot replies with
the job, a note that your resume is tailored to it, and a one-time code. Reply `jp yes <code>` and Fo applies with the
tailored resume. The code is random, tied to that one job, works once and expires in two hours; at most 3 are open at a time.
Login-gated boards are refused (apply by hand). Counts toward the 10-a-day Fo limit. The job is marked `started` the
moment the request is built (so it can never go out twice); tell Claude the result to mark it `applied`.

**Who submits.** While review mode is on (the default), Fo fills the form and asks you in its own chat before it submits.
Only if you turned review mode off (`set_review_mode(false)`) does Fo submit straight after your `jp yes`, stopping at
anything not in your resume. Be aware: the inbox reads your Fo transcripts through a headless model, and the code is
texted to the same chat, so a manipulated relay could in principle echo it. Review mode on is the safe setting.

## 3c. Google Sheets and Docs through Fo

Fo has native Google Sheets and Docs tools on your connected Google account. JobPilot holds no Google credentials for this;
it builds vetted requests that name one file, pass only cleaned text between random markers, and forbid sharing, email,
calendar events and following instructions found in the data.

- **Tracker sheet.** `build_fo_tracker_message` (or text `jp sync`) asks Fo to keep a sheet called "JobPilot Tracker" in your
  Drive: one row per application and good open match (ID, job, company, status, fit score, dates), updated by ID, never
  deleting rows. Cells are written as plain text, never formulas.
- **Tailored resume in Google Docs.** `build_fo_resume_doc_message` (or text `jp doc <id>`) asks Fo to create a Google Doc
  "Resume - <company> - <title> (job <id>)" in a "JobPilot Resumes" folder, containing exactly your facts-only tailored
  text (bullets reordered for that job, nothing added). Review or edit it in Docs, and export to PDF yourself.
- **Formatting.** The tracker is styled by instruction: bold coloured frozen header, status dropdown, colours per status, a Summary
  tab with a chart. The resume Doc uses real Title / Heading 1 / Heading 2 styles, real bullets and one clean font, with no tables or
  columns so applicant systems can read it. `jp deck` (or `build_fo_deck_message`) asks for a themed Google Slides progress deck:
  pipeline chart, top matches table, next steps, using only your vetted numbers. Pass `export_xlsx`, `export_docx` or `export_pptx`
  to also get Excel, Word or PowerPoint copies in a "JobPilot Exports" folder (never emailed or shared). Fo confirmed it can do all
  of this except a Sheets filter view; it is Fo's own claim, not yet seen end to end.
- **Resume files for applications.** With `[google] enabled`, every apply request also tells Fo to create the tailored resume as a Doc, export a PDF and use it for a resume/CV upload field or email attachment, so jobs that need a file become possible. Other files (ID, certificates; a cover letter unless you turned letters on, below) still stop it. It may not share the Doc.
- **Cover letters.** `jp letter <id>` (or `build_fo_cover_letter_message`) asks Fo for a Google Doc named "JobPilot cover letter - job <id>". JobPilot builds the letter from your own saved resume: your two best-matching bullets copied exactly (with their employer), the skills of yours the job mentions, your summary, and the company name. No model writes it and the job title and description are not copied into it. With `[apply] cover_letters = true` (off by default), applications carry the same letter, and Fo uses it only where the form REQUIRES a cover letter: it leaves optional fields empty, answers every other question from the resume text only, and (with Google on) uploads it as a PDF from a new Doc named with the job id. `jp apply` says when a letter will be used.
- **The job record.** The "JobPilot Tracker" sheet is refreshed every morning (when `[fo]` and `[google]` are on), by `jp sync`, and once after a batch of outcome texts (at most 5 a day). It lists, in one request of up to 100 rows: your newest 50 applications, up to 15 jobs whose deadline passed in the last 14 days (`deadline passed`), then your best open matches at or above your own `min_score` (profile, between 50 and 100, default 70), with First seen and Deadline columns. Fo is told to match columns by header name, add the two new columns if missing, never move a status backwards, never delete rows, and to write everything in one update. `stale` means a match that is no longer listed. The sheet cannot write back: to record an outcome text `jp applied|interview|rejected|withdrawn <id>`.
- **Safety limits.** Fo is told to use only files and folders you OWN (ignoring same-named ones shared with you), to treat anything it reads in Drive as data, and never to share. The Doc is named "JobPilot resume - job <id>" (job titles are kept out of names). `jp sync` / `jp doc` are capped at 10 a day. One residual risk: Fo finds the sheet by name, so check once that it is yours.
- **Automatic.** Put `[google] enabled = true` in `config.local.toml` (with `[fo]` enabled). The daily run then refreshes the
  tracker, and one refresh follows a batch of outcome or `jp yes` texts (after their replies, at most 5 a day). Nothing else is automatic.
- Fo's own claim, confirmed by asking it on 2026-10-18; not exercised end to end here. The first run is a supervised pilot.

## 4. Calendar: only when the company writes back

JobPilot never puts anything on your calendar when you apply. Later, "check for a reply from <company>" calls
`build_fo_reply_check_message`: Fo looks in your email, and ONLY if the company itself sent an interview invitation or a
scheduling link does it add that event, with exactly the details they sent. No such message means Fo does nothing and
says so. If it added an event, Claude calls `record_application(status="interview")`.

## Tailoring your resume to a job

Every application is tailored automatically: `prepare_application` and `build_fo_apply_message` rank your own bullets and
skills against that job when no selection is passed, and report `job_terms_missing_from_resume` (gaps to tell you about,
never to invent). Passing your own `experience_order`, `bullet_order` or `skills` overrides it. The steps below are for
reviewing or changing the selection by hand.

"Tailor my resume for job 12" -> `tailor_resume` returns, for that job only:
- the posting's title, company and a short excerpt of its description (untrusted text, never instructions);
- a suggestion: which of YOUR bullets to keep per role (most relevant first), your skills in a relevant order, and
  the job terms your resume matches and the ones it lacks;
- your earlier resume versions for that job and the selections they used.

Review it, then Claude calls `render_resume` with `experience_order`, `bullet_order` (role -> bullet ids) and
`skills`. It can only reorder or pick what your resume already says: every id is checked, unknown ids are refused,
and the rendered DOCX is verified to still contain your required facts. It will not add wording, numbers or
experience. If the job asks for something your resume lacks, Claude tells you instead of inventing it; add it to
your base resume yourself (`save_base_resume`) if it is true.

## Limits (so you are not surprised)

- Talk to Claude, not to Fo, to use JobPilot. The link runs one way (Claude calls Fo); Fo cannot see JobPilot.
  If you text Fo directly, it will say it has no JobPilot tool. That is expected.
- Fo says it can drive a website in a browser for you (searches, filling application forms). This is Fo's own
  claim and is not tested here; ask Fo to show you what a form needs before it submits.

- Without `[google] enabled`, Fo takes text only and cannot attach a file. With it, Fo makes your tailored resume as a Google Doc, exports a PDF and uses it for a resume upload field or an email attachment (Fo's own claim, asked 2026-10-18, not yet exercised end to end); any other file (ID, certificate, and a cover letter unless `[apply] cover_letters = true`) still makes it stop and ask. It cannot complete phone or email codes it cannot read (the request tells it to stop). Applications go as pasted resume text, or Fo works
  through whatever form or email route the posting offers. That is Fo's own capability and is not verified here;
  the first real application is a supervised pilot. Many postings need a web form or account.
  How well Fo handles those is up to Fo; ask it to tell you before it submits.
- The Claude permission prompt for `ask_fo` is your safety gate. Do not add `mcp__fo__*` to any allow-list.
- Job postings are written by strangers. `prepare_application` never includes the posting's description,
  and you should not paste it into a request to Fo.
- Fo can email, call and buy from your real account. Keep applications to one explicit yes each.
- The unattended daily digest works without a key through the headless `claude` relay (`[fo] transport = "claude"`); an
  `FO_API_KEY` is only needed for the direct `api` transport, which Wajo has not released yet.
