"""A ledger of real runs: what ran, each Fo tool call with its timing, and whether it worked.

Every on-demand run appends one JSON line to data/runs.jsonl. `python -m jobpilot.runlog` turns the ledger into the two things
the hackathon form asks for: a run count (how many runs, how many succeeded) and a run trace (the tool calls of the latest run).
Nothing private is stored: Fo messages are cut to 160 characters of already-vetted text, and no key or resume text is ever written.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .paths import jobpilot_home


def ledger_path(home: Path | None = None) -> Path:
    return Path(home or jobpilot_home()) / "data" / "runs.jsonl"


def record_run(kind: str, ok: bool, steps: dict, trace: list[dict] | None = None, home: Path | None = None) -> dict:
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "ok": bool(ok),
             "steps": steps, "fo_calls": list(trace or [])}
    path = ledger_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def read_runs(home: Path | None = None) -> list[dict]:
    path = ledger_path(home)
    runs: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return runs
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and "kind" in item and "ok" in item:
            runs.append(item)
    return runs


def run_count(runs: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in runs:
        row = out.setdefault(str(r["kind"]), {"runs": 0, "succeeded": 0})
        row["runs"] += 1
        row["succeeded"] += 1 if r["ok"] else 0
    return out


def render_count(runs: list[dict]) -> str:
    counts = run_count(runs)
    lines = ["# Run count", "", "Counted from `data/runs.jsonl`, written by each real run (not typed by hand).", "",
             "| Task | Runs | Succeeded | Failed |", "|---|---|---|---|"]
    for kind, row in sorted(counts.items()):
        lines.append(f"| `{kind}` | {row['runs']} | {row['succeeded']} | {row['runs'] - row['succeeded']} |")
    total = sum(r["runs"] for r in counts.values())
    won = sum(r["succeeded"] for r in counts.values())
    lines += ["", f"Total: {total} run(s), {won} succeeded, {total - won} failed."]
    failed = [r for r in runs if not r["ok"]]
    if failed:
        lines += ["", "Failed runs (kept, not hidden):"] + [
            f"- {r['at']} `{r['kind']}`: " + ", ".join(f"{k}={v}" for k, v in r["steps"].items()) for r in failed]
    return "\n".join(lines) + "\n"


def render_trace(run: dict) -> str:
    lines = [f"# Run trace: `{run['kind']}` at {run['at']}", "", f"Outcome: {'succeeded' if run['ok'] else 'FAILED'}", "",
             "## Steps", ""] + [f"- {k}: {v}" for k, v in run["steps"].items()] + ["", "## Fo tool calls (in order)", ""]
    if not run["fo_calls"]:
        lines.append("(none)")
    for i, c in enumerate(run["fo_calls"], 1):
        lines.append(f"{i}. `{c['tool']}` status={c['status']} {c['seconds']}s, {c.get('chars', 0)} chars sent")
        if c.get("message_head"):
            lines.append(f"   - sent: {c['message_head']!r}")
        if c.get("reply_head"):
            lines.append(f"   - Fo replied: {c['reply_head']!r}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["record"]:                  # python -m jobpilot.runlog record <kind> ok|fail [key=value ...]
        if len(argv) < 3 or argv[2] not in ("ok", "fail"):
            print("usage: python -m jobpilot.runlog record <kind> ok|fail [key=value ...]")
            return 2
        steps = dict(item.split("=", 1) for item in argv[3:] if "=" in item)
        record_run(argv[1], argv[2] == "ok", steps)
        print(f"recorded {argv[1]}: {argv[2]}")
        return 0
    runs = read_runs()
    if not runs:
        print("No runs recorded yet.")
        return 1
    print(render_count(runs))
    print(render_trace(runs[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
