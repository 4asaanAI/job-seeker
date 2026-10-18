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

## 3. Trial run first (nothing recorded, nothing irreversible)

Each step has a pass condition. No step sends an application.

| # | Trial | Pass if |
|---|---|---|
| T1 | Focus guard: bring Messages forward, confirm it is the frontmost app, type text into the box without sending, clear it | the text is in Messages and not in VS Code |
| T2 | Layout: Terminal window over the Messages sidebar, conversation pane visible on the right; screenshot | no other chat visible anywhere in the region |
| T3 | Sheet in an app-style Chrome window (no tabs, no bookmarks), signed in, scroll and switch to the Summary tab | rows and chart visible, no login page |
| T4 | Claude Code session driven from Terminal: open, send a prompt, read the answer, approve a prompt, exit | the answer is read back correctly |
| T5 | Read-only Fo probe of the real application page (nothing filled or submitted) | Fo says whether an account or code is needed and what the form asks |
| T6 | A direct API refresh of the sheet takes under 2 minutes | time measured |
| T7 | Judge path in a fresh clone: install, tests, fictional-data demo, doctor | all green |

## 4. The take (V2): one unedited recording, region = Terminal over the Messages sidebar + the Fo thread

Narration is optional; on-screen captions are added only as separate text in the post, not inside the video.

1. Messages thread visible. I ask Fo: "Can you send me the link to my JobPilot Tracker sheet?" Fo texts it. (about 1 min)
2. Terminal: `python -m jobpilot.refresh --deliver` searches Indeed, foundit and Himalayas for my real profile, scores, and Fo texts
   the new matches with the sheet link, then a one-line sheet update. (about 5 min over the API)
3. Terminal: Claude Code with the JobPilot and Fo tools. "Prepare my application for the chosen job and show what you would send,
   without my contact details." The `ask_fo` permission prompt shows the exact vetted message (the safety moment).
4. I approve. Fo opens the company's page, fills the form from my resume, and asks me to confirm. I confirm. Fo submits.
5. JobPilot records the application; Fo refreshes the sheet row and texts the update line with the link. I open the sheet
   in a clean Chrome window and show the row.
6. End on the sheet and the text. Total is long; V1 is the plain 2-minute excerpt around steps 4-5.

If the application cannot be completed (account or code required), the take shows that live: Fo stops safely and says why, which is
the "user stays in control" criterion. We then pick another job; the failed attempt stays in the run count.

## 5. Safety checks before anything is posted

- Review every frame of V1 and V2. Only the Terminal and Fo's thread may appear.
- Do Not Disturb on. VS Code not frontmost. No other chats, no API keys, no calendar.
- No phone number on screen: the Messages header shows the contact name "Fo".
- The Fo API key is stored only in `~/.config/jobpilot/fo_api_key` (mode 600). Rotate it after the hackathon.
