# Using JobPilot + Fo for your own job search

Real data lives outside git: `data/seekers/` and `data/*.db` are already ignored. Never run this
against the demo folder, and never put your real resume in anything you post publicly.

## 1. Set up yourself as a seeker

1. Create `data/seekers/<you>/profile.yaml` (see `scripts/fo_demo.py` for the fields: name, headline,
   location, sources, search_terms, min_score).
2. Give Claude your resume as a PDF or Word file and ask it to save it. It calls `save_base_resume`,
   which writes `data/seekers/<you>/base_resume.yaml` and checks every fact against your file.
3. Register yourself (`add_seeker`), then pin the session to you: set `JOBPILOT_SEEKER_ID=<your id>`
   and copy `.mcp.fo-demo.json.example` to `.mcp.json`, pointing `JOBPILOT_DB` at `data/jobpilot.db`.
4. Log in to Fo with `/mcp` (restart Claude Code after adding it).

## 2. Find and judge jobs

"Run my search" -> `run_my_search`; "what are my best matches?" -> `my_matches`. Scoring uses the
`[scoring]` model in `config.toml` (use `haiku` to save credits).

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

## 4. Calendar: only when the company writes back

JobPilot never puts anything on your calendar when you apply. Later, "check for a reply from <company>" calls
`build_fo_reply_check_message`: Fo looks in your email, and ONLY if the company itself sent an interview invitation or a
scheduling link does it add that event, with exactly the details they sent. No such message means Fo does nothing and
says so. If it added an event, Claude calls `record_application(status="interview")`.

## Tailoring your resume to a job

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

- Fo takes text only: it cannot attach a file. It cannot complete phone or email codes it cannot read (the request tells it to stop). Applications go as pasted resume text, or Fo works
  through whatever form or email route the posting offers. That is Fo's own capability and is not verified here;
  the first real application is a supervised pilot. Many postings need a web form or account.
  How well Fo handles those is up to Fo; ask it to tell you before it submits.
- The Claude permission prompt for `ask_fo` is your safety gate. Do not add `mcp__fo__*` to any allow-list.
- Job postings are written by strangers. `prepare_application` never includes the posting's description,
  and you should not paste it into a request to Fo.
- Fo can email, call and buy from your real account. Keep applications to one explicit yes each.
- The unattended daily digest through Fo needs `FO_API_KEY`; without one, use the interactive flow
  (or Telegram) for notifications.
