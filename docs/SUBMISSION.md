# GSD hackathon submission pack

Everything the form asks for (A to G), in one place. Items marked **after the recording** are filled from the real take.

## A. X link of the quote-repost
After recording: link of my quote-repost of Shivani's post (Mon Oct 19, deadline 10:15 AM PT).

## X post (under 280 characters)

> JobPilot x Fo: my real job search, run end to end. Claude finds and scores jobs; Fo texts me, keeps a living Google Sheet record
> (with the link in every update), and applies on my yes. Job posts never reach Fo raw. Open source: github.com/4asaanAI/job-seeker

Reply thread (one point per reply): (1) the live take, unedited; (2) the safety design: a poisoned job post is neutralized before
Fo sees it, every Fo action asks first; (3) what is real and what is not, linking the Honest status table; (4) thanks to @wajo_ai.

## C. Video
- **V2: one continuous, unedited recording (about 37 minutes)** of the real cycle: a live search of Indeed, foundit, Himalayas and
  five employer feeds, scoring, Fo texting the matches with the sheet link and a sheet update, me asking Fo for the link and Fo
  replying, then a real application to Turing's "Product Manager" role through the Fo MCP (Claude Code shows the permission
  prompt), Fo filling the form and asking before it submits, my "yes", Greenhouse's "Thank you for applying", and the application
  recorded in my tracker. Only the end of the file was cut (the recorder started capturing other windows); nothing inside it was
  edited. Link: **add after upload**.
- **V1: a plain 2-minute excerpt of V2** for X (cut from the start and end only; no speed-up, no cuts inside).

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
- Applying through Fo from a vetted request, with my own typed answers for questions my resume does not cover, and Fo asking before
  it submits. A live check through Fo found that Himalayas hides its Apply button behind an account, so that route is now a login
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

**One thing that did not work, and one mistake.** (1) While rehearsing, I let a "do not submit" rehearsal run against Turing's real
form; Fo pressed Enter in a dropdown and the form submitted without my resume. It is in the run count as a failure, every apply
request now forbids the Enter key in fields, and the real application was then redone properly (Turing now has both; the note
says so). (2) I wanted to text Fo `jp matches` in iMessage and have JobPilot answer. Fo's transcript API never
returns the user's own SMS or iMessage texts, only Fo's replies: checked live, and the polling inbox answered 0 commands in 15+
polls. It needs Wajo's inbound-reply webhook. What works instead: Fo answers by text when I ask it for the sheet link, and
JobPilot pushes updates to me.

## F. Run trace
`docs/run-trace.md`: the Claude Code session that prepared and sent the Turing application through the Fo MCP, plus the unattended
search-and-text run with its Fo API calls (emails, phone numbers and the sheet id masked).

## G. Run count
`docs/run-count.md`: generated from the ledger (`python -m jobpilot.runlog`), counting every real run and how many succeeded:
6 runs, 5 succeeded, 1 failed (the rehearsal above, kept in the count).
