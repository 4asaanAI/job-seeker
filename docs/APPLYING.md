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

## 3. Apply (one job at a time, only with your yes)

1. "Prepare my application for job 12" -> `prepare_application` returns your facts-only resume as text,
   plus the job's title, company and link. Nothing is sent. Optionally "tailor it": Claude may only
   reorder your experience and pick your own skills; it cannot invent facts.
2. You read it. If you want to proceed, say so for THAT job.
3. Claude asks Fo to apply, with a request like: "Apply to <job link> for me. Use the resume text below
   exactly as written. If the application is by email, draft it and show it to me before sending. If it
   needs a web form, tell me what it asks for. Do not submit anything until I confirm." Fo may ask you
   questions; answer them in the session.
4. Once Fo confirms it was submitted, Claude calls `record_application` so the job moves to "applied"
   and stops being suggested. The 3-day and 7-day follow-ups then work from that record.

## Limits (so you are not surprised)

- Fo takes text only: it cannot attach a file. Applications go as pasted resume text, or Fo works
  through whatever form or email route the posting offers. Many postings need a web form or account.
  How well Fo handles those is up to Fo; ask it to tell you before it submits.
- The Claude permission prompt for `ask_fo` is your safety gate. Do not add `mcp__fo__*` to any allow-list.
- Job postings are written by strangers. `prepare_application` never includes the posting's description,
  and you should not paste it into a request to Fo.
- Fo can email, call and buy from your real account. Keep applications to one explicit yes each.
- The unattended daily digest through Fo needs `FO_API_KEY`; without one, use the interactive flow
  (or Telegram) for notifications.
