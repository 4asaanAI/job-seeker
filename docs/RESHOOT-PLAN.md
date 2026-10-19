# Re-shoot plan: one conversation in Claude Code (Oct 19, before 10:15 AM PT)

**Goal:** one real, end-to-end story filmed in the Claude Code window. Claude Code runs JobPilot and calls Fo (MCP) for every
real-world step. Nothing is mocked. iMessage is not used: my texts to Fo never reach JobPilot (Fo's API returns only Fo's messages).

**Outcome (Oct 19):** filmed as planned up to the review draft. Paytm's Lever form blocked Fo's submit (anti-bot check), so the final clip applies to Turing on Greenhouse instead, where Fo submitted after my "submit". What was filmed and why: `docs/SUBMISSION.md` section C.

**On screen:** Claude Code in Terminal (`claude --permission-mode default`, so every `ask_fo` shows its permission prompt).
Cutaways: the JobPilot Tracker sheet and Gmail, each in a Chrome app window. Contact email for the application:
[my email].

| # | I type / see | What runs | Check before next |
|---|---|---|---|
| 1 | "Find me jobs I should apply to." Claude asks for my resume | - | prompt visible |
| 2 | I paste my resume's Google Drive link | Fo reads it; `save_base_resume` | resume facts shown, no phone on screen |
| 3 | Claude asks 3-4 narrowing questions (role, location/remote, seniority, pay floor) | `update_my_profile` | profile saved |
| 4 | "Searching..." | `run_my_search`: real boards + employer feeds, scoring | matches listed |
| 5 | Shortlist + sheet link in the chat | `ask_fo`: Fo refreshes the Tracker sheet (job, company, score, location, URL, status) | link in chat |
| 6 | Cut to the sheet: new rows read `match` | live sheet | rows visible |
| 7 | "Apply to #N." Permission prompt, approve | `ask_fo` vetted apply request: Fo fills the form, uploads the Drive PDF, stops before Submit | Fo's reply |
| 8 | Fo lists every filled field (and a screenshot if it can); I check and reply "submit"; Fo submits | `ask_fo` | receipt text |
| 9 | "Mark it applied." | `record_application` + Fo updates the sheet; cut to row: `match` -> `applied` | row reads applied |
| 10 | Gmail: the company's confirmation email, opened directly | real inbox | only that email in frame |

**Job:** the top live match whose form needs no account, code or essay. Fallback: Paytm APM (696, Lever, PDF upload).

**Videos:** V-full = one continuous recording (form item C). V-X = 2-3 min cut for X: waits trimmed and labelled
("waited 4 min"), never cut inside a step.

**Stop rules:** if a scene shows other windows, private content or an error, redo that scene only. Fo never submits without my
"submit" for exactly this job. The ledger records the real outcome, success or failure.
