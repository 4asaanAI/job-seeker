# Final demo plan: GSD hackathon submission

Deadline: **Mon Oct 19, 10:15 AM Pacific** (the kickoff deck, "Due on Monday, Oct 19 at 10:15 AM Pacific Time"). Community voting
and the Discord form close Tue Oct 20, 10:15 AM PT. Scoring: 50% judges, 50% community.

## 1. What the rules ask for, and where each item comes from

| Rule (from the kickoff deck) | Our answer | Status |
|---|---|---|
| Uses the Fo MCP and API | Both: Fo MCP in Claude Code and the relay; direct Wajo API key transport (verified live) | done |
| X: quote-repost Wajo's post with demo video and short write-up | `docs/SUBMISSION.md` has the post text; video V1 | to post (you) |
| Form C: video that "shows the key action live or in one unedited take" | V2, one continuous unedited recording; V1 is a plain excerpt of it (no speed-up, no cuts inside) | to record |
| Form D: who it is for, what was built at the event, what existed before, what is mocked or hard-coded | `docs/SUBMISSION.md` section D | drafted |
| Form E: build note (tools, what we added, one thing that did not work) | `docs/SUBMISSION.md` section E | drafted |
| Form F: run trace, the log of one real run with the agent's tool calls | `docs/run-trace.md`, captured from the real run in V2 | to capture |
| Form G: run count (how many times, how many succeeded) | `docs/run-count.md`, counted from the real logs by `scripts/run_count.py` | to generate |
| "Built mainly during the event" | JobPilot predates the event and the write-up says so; the Fo layer, safety layer, sheet record, letters, setup were built Oct 17-19 and are diffable | stated honestly |
| Hardware bonus | not applicable | none |

## 2. Score map: what the video and write-up must make obvious

| Criterion (points) | What we show |
|---|---|
| Gets real things done (25) | One real task end to end on my real profile: search real boards, Fo texts the matches, Fo applies on my yes, Fo keeps the Google Sheet record and texts me its link |
| Pushes the frontier (25) | A living job record kept by an assistant in my own Drive; applications filed from a vetted request; facts-only resume and letter |
| Trust by design (20) | Job posts never reach Fo raw; every `ask_fo` passes a permission prompt; Fo asks before submitting; no calendar event until a company sends one; daily caps; "only files I own"; the injection demo |
| Agent craft (20) | Tools built on top of the Fo MCP; the run count shows it works more than once; 890+ tests |
| Real need (10) | Job search is a daily chore; the sheet and texts replace the spreadsheet and the checking |

## 3. Trial runs done before recording (nothing submitted)

| # | Trial | Result |
|---|---|---|
| T1 | Focus guard: refuse to type unless the target app is frontmost | works: it aborted once when VS Code held focus instead of typing into it |
| T2 | Layout: Terminal covering the Messages sidebar, only the Fo conversation visible beside it | works; old Terminal windows must be closed first (done) |
| T3 | Sheet in an app-style Chrome window, signed in | works: header, banded rows, status dropdowns, Tracker and Summary tabs; dismiss Google's promo banner first |
| T4 | Claude Code in Terminal driven by keystrokes, answer read back | works; start it with `--permission-mode default` so the Fo permission prompt is shown |
| T5 | Read-only Fo probe of real application forms | Anthropic (essay, visa, consents: poor fit 20-35), Turing (resume only, no account, no code), Zeta and Meesho (CTC, notice period answers) |
| T6 | Direct Fo API | works: 25-60 s per call; long Google Doc tasks exceed the client's 3-minute wait |
| T7 | Judge path in a fresh clone | install, 897 tests, fictional demo all pass |
| T8 | Real search with the new employer feeds on a copy of my data | Meesho PM I 72, Zeta PM II 70, Paytm APM 70, Turing PM 60; Turing's direct form link is now chosen over the Indeed link (bug fixed) |
| T9 | Apply request, rehearsed with "do not submit" | see the rehearsal notes in the run ledger |

Known limits found: attachments sent from this Mac to Fo do not deliver ("Not Delivered"), and making a PDF through a Google Doc
takes minutes, so the application pastes the resume text where the form allows it (`resume_file=False`).

## 4. The take (V2): one unedited recording, region = Terminal over the Messages sidebar + the Fo thread

Chosen application: Turing, "Product Manager" (remote, India), Greenhouse, no account, no code, fields all from the resume.
Nothing is submitted without my explicit go before the take; the take then submits live.

1. Terminal: `python -m jobpilot.refresh --deliver`. Real search of Indeed, foundit, Himalayas and the employer feeds (Turing, Zeta,
   Meesho, Paytm, Mindtickle), scoring, Fo texts the new matches with the sheet link, then a one-line sheet update. (about 6 min)
2. Messages: I text Fo "Can you send me the link to my JobPilot Tracker sheet?" and Fo replies with it. (about 1 min)
3. Terminal: Claude Code started with `claude --permission-mode default`. "Prepare my application for job 1 and show what you would
   send, without my contact details; paste my resume, no PDF." The `ask_fo` permission prompt shows the exact vetted message.
4. I approve. Fo opens Turing's page, fills the form from my resume, and asks me to confirm. I confirm. Fo submits.
5. "Record it as applied and update my tracker." JobPilot records the application; Fo refreshes the sheet and texts the update.
6. Chrome app window: the sheet with the new "applied" row and the Summary tab. End.
V1 is a plain 2-minute excerpt of V2 (no speed-up, no cuts inside); the full take is the unlisted link.

If the form blocks Fo (a captcha, a code), the take shows that live: Fo stops safely and says why. That attempt stays in the run count.

## 5. Safety checks before anything is posted

- Review every frame of V1 and V2. Only the Terminal and Fo's thread may appear.
- Do Not Disturb on. VS Code not frontmost. No other chats, no API keys, no calendar.
- No phone number on screen: the Messages header shows the contact name "Fo".
- The Fo API key is stored only in `~/.config/jobpilot/fo_api_key` (mode 600). Rotate it after the hackathon.
