# GSD hackathon submission

Answers for the form (items A to G) and the X post.

## A. X post link
https://x.com/itsmanyusin/status/2112237686383890898

## X post

> JobPilot x Fo MCP, my real job search: Claude finds and scores jobs; via the Fo MCP, Fo keeps a Google Sheet with every link, fills the application and sends me a review draft. Nothing goes out without my OK.

## B. Emails
My email (in the form only), and the consent box ticked.

## C. Video
- **X clip (2:12):** `out/video/jobpilot-fo-X.mp4`. Cut from real takes. Each trimmed wait is labelled with its real length. Nothing is
  sped up.
- **Full version:** `out/video/jobpilot-fo-full.mp4`. The search take and the Turing take, played in real time. Two kinds of
  redaction: a Discord window and a code editor came to the front during waits and are blacked out, and the apply request is blurred
  while it shows my phone number.

## D. Write-up

**Who it is for.** People looking for a job who want help with the repetitive part: finding openings, keeping track of them and
filling in applications, while they stay in charge of what gets sent. I am the first user, with my own resume and my own Fo account.

**What I built at the event (Oct 17-19).**
- The connection between JobPilot and Fo, through the Fo MCP in Claude Code and through Wajo's API for unattended runs.
- A filter between job posts and Fo. Fo never reads a job post's raw text. It gets a job id, a score and a checked title, so a post
  cannot slip instructions to an agent that can email, call and buy.
- A Google Sheet that Fo keeps in my Drive: every match with its status and link (116 rows on Oct 19).
- Applying through Fo. Fo fills the form from my resume, stops at any question my resume does not answer, and sends me a review
  draft. It submits only after I say "submit". If a site blocks it, it gives me the page link and the values so I can finish.
- A run log that records every real run, including the ones that failed.

**What existed before.** JobPilot: job search across boards and company career pages, scoring against my profile, and the resume
tools. It was built from September 2026.

**What is mocked or hard-coded.** Nothing in the video. The profile, resume, job boards, sheet and Fo account are all real. A
made-up job seeker exists only for tests. Two limits: an agent cannot get through Paytm's anti-bot check, and Fo cannot read the
texts I send it by iMessage. Answers to questions my resume does not cover come from me. For Turing I wrote my project answer,
Claude suggested a cleaner wording, and I approved it.

## E. Build note

**Tools.** Claude Code with two MCP servers: JobPilot (search, scoring, the sheet, application requests) and Fo (`ask_fo`,
`get_fo_reply`, `list_fo_conversations`, `get_fo_conversation`).

**What I added on top.** The filter between job posts and Fo, the application requests with the review step and the hand-off, the
sheet updates, and the run log.

**One thing that did not work.** Paytm's application form rejected every submit from Fo with an anti-bot check: 7 attempts over
two days. Fo filled the form each time and stopped without sending anything. My own submission from the hand-off did not go
through either, so that job is recorded as withdrawn.

## F. Run trace
`docs/run-trace-turing.md`: every tool call from the filmed Turing application, in order. `docs/run-trace.md` has the search and
sheet session. Emails and phone numbers are masked.

## G. Run count
18 runs, 12 succeeded, 6 failed (`docs/run-count.md`, generated from the run log). Fo submitted two applications to Turing. Paytm
blocked Fo every time. One early test against Turing's form submitted before it should have.
