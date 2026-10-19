# Run count

Counted from `data/runs.jsonl`, written by each real run or recorded right after it (`python -m jobpilot.runlog record`), not typed into this file by hand.

| Task | Runs | Succeeded | Failed |
|---|---|---|---|
| `apply_handoff` | 1 | 0 | 1 |
| `apply_live` | 5 | 2 | 3 |
| `apply_rehearsal` | 1 | 0 | 1 |
| `refresh_deliver` | 5 | 5 | 0 |
| `refresh_tracker` | 6 | 5 | 1 |

Total: 18 run(s), 12 succeeded, 6 failed.

Failed runs (kept, not hidden):
- 2026-10-18T22:44:13+00:00 `apply_rehearsal`: cause=rehearsal run against a live form, company=Turing, error=form submitted early when Enter was pressed in a dropdown; resume was not entered; an incomplete application was sent, job=1
- 2026-10-19T02:24:25+00:00 `apply_live`: ats=Lever, attempts=5, cause=Lever rejected every submit: 'There was an error verifying your application' (bot check), company=Paytm, job=696, role=Associate Product Manager - Travel, submitted=no
- 2026-10-19T15:13:07+00:00 `apply_live`: ats=Lever, cause=Lever verification rejected Fo's submit; nothing sent, company=Paytm, fo_asked_before_submit=yes, started=2026-10-19T05:35Z, take=night-1
- 2026-10-19T15:13:08+00:00 `apply_live`: ats=Lever, cause=anti-bot check blocked Fo's submit twice; Fo stopped and handed over link and values; nothing sent by Fo, company=Paytm, review_draft=yes, started=2026-10-19T14:52Z, take=final
- 2026-10-19T15:13:08+00:00 `apply_handoff`: company=Paytm, detail=user's own submission on Lever did not go through (corrected after the user said so); record set to withdrawn, started=2026-10-19T15:01Z, take=final
- 2026-10-19T16:21:23+00:00 `refresh_tracker`: links=full (hex ids kept), take=final, why=Turing 689 applied, Paytm 696 withdrawn

