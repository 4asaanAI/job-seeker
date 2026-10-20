# The JobPilot dashboard

A small web app on your Mac: your pipeline as a board, a review drawer for each application, and your search profile.

## Start it

```bash
.venv/bin/python -m jobpilot.dashboard --new-token     # once: prints your login token (keep it private)
.venv/bin/python -m jobpilot.bridge --setup-code       # once, if you have not: the authenticator-app secret for approvals
.venv/bin/python -m jobpilot.dashboard                 # then open http://127.0.0.1:8770
```

It serves the seeker in `[fo] seeker_id` (or `--seeker N`). Applying from the dashboard needs `[fo] enabled`.

## From your phone

Run it with your Tailscale address so only your own devices can reach it:

```bash
.venv/bin/python -m jobpilot.dashboard --host 100.x.y.z     # your Mac's Tailscale IP (tailscale ip -4)
```

Open `http://100.x.y.z:8770` on the phone, log in with the token, and use "Add to Home Screen". Do not expose it to the
public internet (no Funnel): it is meant for your own devices.

## The screens

- **Pipeline:** your jobs as a board (matches, started, applied, interview, closed) or as a sortable table (the Board / List
  buttons). **Filters & sorting** narrow your matches by text, lowest score, work mode, place, job board, lowest salary (with a
  currency), "only jobs that state a salary", posted within 2 days / a week / 2 weeks, and "closing within 48 hours"; sort by best
  match, newest, closing soonest, highest salary (currencies compared at rough rates) or company / title A-Z. In the list, click a
  column heading to sort. Your choices are remembered in this browser. A job that does not state a fact (no salary, no work mode)
  is not removed by the filter for it.
- **To do:** email suggestions to confirm, failed submissions, follow-ups due.
- **My answers:** the ten questions nearly every form asks, in four groups. **Where can you work?** comes first: list the countries
  where you can work without sponsorship and the ones you would relocate to, and JobPilot answers "authorised to work in the
  country of the job?", "need visa sponsorship?" and "willing to relocate?" **for each job** from the job's location (by looking
  the country up, not by guessing). When a job's country is not clear (a plain "Remote"), Fo stops and asks you. "What would
  JobPilot answer?" lets you try any location. The other answers (notice period, salary expectation, current compensation, years of
  experience, LinkedIn, why this role, why leaving) are answered once; saving writes a formal, polite version for you to read, and
  forms use it only after you approve it, word for word.
  **Ask Fo to help me write it** opens a small chat: tell Fo the facts, or press "Draft it for me". Fo uses only your resume and what
  you tell it, asks for a fact it lacks, and takes no other action. Its reply goes into the answer box only when you press "Use this
  as my answer"; nothing is saved until you press Save. A question no form of yours asks for is not here; add your own at the bottom.
- **Search profile:** what JobPilot searches for and which jobs are hidden.

## What protects it

- Every page and API call needs the login token (as a session cookie derived from it; `--new-token` logs everyone out).
- Only the addresses it was started on are accepted (Host check), and API calls must carry a custom header and, when a
  browser sends one, a same-site Origin: another web page cannot drive it.
- **Approve and apply** needs the current 6-digit code from your authenticator app, used once; five wrong codes lock
  approvals until `python -m jobpilot.bridge --unlock-codes`.
- With review mode on (the default), Fo still shows you its filled-in review draft in its own chat and waits before
  submitting; with review mode off (`set_review_mode`), your code is the confirmation and Fo submits.
- One application is in flight at a time. If sending to Fo errors, the outcome is "unknown" (Fo may still be working):
  nothing is parked or retried until you record what happened. "The submit failed" parks it for one retry, with a new code.
- Job text is shown as text, never as HTML, and the page allows no outside scripts.
