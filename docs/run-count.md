# Run count

Counted from `data/runs.jsonl`, written by each real run (not typed by hand).

| Task | Runs | Succeeded | Failed |
|---|---|---|---|
| `apply_live` | 1 | 1 | 0 |
| `apply_rehearsal` | 1 | 0 | 1 |
| `refresh_deliver` | 4 | 4 | 0 |

Total: 6 run(s), 5 succeeded, 1 failed.

Failed runs (kept, not hidden):
- 2026-10-18T22:44:13+00:00 `apply_rehearsal`: cause=rehearsal run against a live form, company=Turing, error=form submitted early when Enter was pressed in a dropdown; resume was not entered; an incomplete application was sent, job=1

