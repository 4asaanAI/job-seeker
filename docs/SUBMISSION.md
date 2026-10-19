# GSD hackathon submission pack

Everything the form asks for (A to G), in one place. Items marked **after the recording** are filled from the real take.

## A. X link of the quote-repost
After recording: link of my quote-repost of Shivani's post (Mon Oct 19, deadline 10:15 AM PT).

## X post (under 280 characters)

> JobPilot x Fo, my real job search: Claude finds and scores jobs; Fo keeps a Google Sheet of them with links, fills the application and sends me a review draft. Nothing goes out without my OK, and if a site blocks bots, Fo hands me the link. github.com/4asaanAI/job-seeker

Reply thread (one point per reply): (1) the live take, unedited; (2) the safety design: a poisoned job post is neutralized before
Fo sees it, every Fo action asks first; (3) what is real and what is not, linking the Honest status table; (4) thanks to @wajo_ai.

## C. Video
- **X clip: `out/video/jobpilot-fo-X.mp4`, 2:05.** Cut from the real takes below; every trimmed wait is labelled with its
  real length ("17 min, trimmed"). No speed-up. It shows: Fo reading my resume from Drive, Claude's narrowing questions, a live
  search of real boards, Fo keeping the Google Sheet (every job with its link), a Fo permission prompt, then a second
  application: Fo fills Turing's Greenhouse form, stops at three questions my resume cannot answer, I answer them, Fo sends the
  review draft, I say "submit", and Fo submits ("Thank you for applying"; the confirmation email reached my inbox).
- **Full takes (form item C), unedited apart from redactions:** `out/video/take3-full.mp4` (34 min: resume, search, sheet,
  Paytm review draft) and `out/video/take7-full.mp4` (25 min: choosing the job, Turing, submit). Redactions in the published
  long version: a Discord window that came to the front during a wait is blacked out, and the apply request is blurred while
  it shows my resume's contact line. Link: **add after upload**.
- Also filmed: Paytm's Lever form blocking Fo's submit with an anti-bot check, and Fo handing me the link and values. My own
  Paytm submission did not go through; the record says withdrawn, not applied.

## D. Write-up

**Who it is for.** Anyone running a job search who wants an assistant that does the chasing but cannot be steered by strangers'
job posts. I am the first user: my own profile, my own resume, my own Fo account.

**What was built at the event (Oct 17-19).** Everything that connects JobPilot to Fo, and the layer that makes it safe:
- Fo client over the MCP and, with a Wajo API key, the direct API (both verified live); a headless relay; "delivered" only when Fo
  confirms.
- Vetted messages: Fo gets an id, a score and a vetted title, never a raw job post; random-marker fences; daily caps; no calendar
  entry until a company sends an invite.
- A living job record: Fo created the "JobPilot Tracker" Google Sheet in my Drive (82 real jobs), JobPilot saves its link, opens it
  by id, shows "Your job tracker: <link>" in every digest, and Fo texts the link when I ask. Statuses are kept current and never
  deleted (match, started, applied, interview, rejected, offer, deadline passed, stale).
- Applying through Fo from a vetted request, with my own typed answers for questions my resume does not cover. Before anything is
  submitted Fo sends a review draft (every field, the page link) and waits for my "submit"; if it is stuck on a step only a person
  can do (a captcha, a verification), it stops and hands me that page's link and the exact values, asks me for any one-time
  code, and continues when I say the step is done. After I confirm, JobPilot records the application and Fo marks the row
  applied in the sheet, with the date and the job's link. A live check through Fo found that Himalayas hides its Apply button behind an account, so that route is now a login
  board.
- Facts-only resume tailoring, cover letters, a resume builder, a one-command setup, a run ledger that counts every real run.
- 900+ tests; a fresh clone from GitHub passes them and runs the fictional-data demo.

**What existed before.** JobPilot itself: job discovery from boards and employer feeds, de-duplication, scoring, the resume engine,
the MCP server and a Telegram bot, built from September 2026. The diff for the hackathon:
https://github.com/4asaanAI/job-seeker/compare/bf935c7...main

**What is mocked or hard-coded.** Nothing in the video is mocked: real profile, real boards, real Fo account. The fictional seeker
"Avery Demo" exists only for the dry-run demo and tests, so judges can run it without my data. Not live yet: the PDF upload into a
form and company-sent interview invites. Not possible today: answering `jp ...` texts typed in iMessage (see E). My application
answers to non-resume questions are typed by me, not generated.

## E. Build note

**Tools the agent uses.** Claude Code with two MCP servers: JobPilot (50+ tools: discover, score, tailor, plan and build applications,
the job record) and Fo (`ask_fo`, `get_fo_reply`, `list_fo_conversations`, `get_fo_conversation`); plus the Fo API key transport
for unattended runs.

**What we added on top.** The vetting layer between job posts and Fo, the apply request builder, the sheet record and link
handling, the run ledger and trace, and the tests that start the real MCP server and attack it with poisoned input.

**Things that did not work.** (0) Paytm's Lever form rejects Fo's submit with an anti-bot check every time (7 attempts over two
days, all in the run count). Fo filled every field and stopped safely; I pressed Submit myself. Fo also cannot share a link into
its own browser session (it said so when asked), so a hand-off means the public form link plus the exact values, not Fo's filled
page. And a first recording of this take was unusable: Terminal had inherited VS Code's `COLORTERM=truecolor` and drew Claude
Code's colours as green and red blocks.
**One mistake.** (1) While rehearsing, I let a "do not submit" rehearsal run against Turing's real
form; Fo pressed Enter in a dropdown and the form submitted without my resume. It is in the run count as a failure, every apply
request now forbids the Enter key in fields, and the real application was then redone properly (Turing now has both; the note
says so). (2) I wanted to text Fo `jp matches` in iMessage and have JobPilot answer. Fo's transcript API never
returns the user's own SMS or iMessage texts, only Fo's replies: checked live, and the polling inbox answered 0 commands in 15+
polls. It needs Wajo's inbound-reply webhook. What works instead: Fo answers by text when I ask it for the sheet link, and
JobPilot pushes updates to me.

## F. Run trace
`docs/run-trace.md`: every tool call of the filmed Claude Code session for the search, the sheet and the Paytm review draft
(81 calls). The Turing application's calls are in the same format in `docs/run-trace-turing.md`. Emails and phone numbers masked.

## G. Run count
`docs/run-count.md`, generated from the ledger (`python -m jobpilot.runlog`): **17 runs, 11 succeeded, 6 failed.** Applications:
Turing twice submitted by Fo (Oct 18 and the filmed Oct 19 one); Paytm blocked by Lever's anti-bot check on every Fo attempt,
and my own hand-off submission there did not go through; one early Turing rehearsal that submitted too soon. Failures are
listed, not hidden.
