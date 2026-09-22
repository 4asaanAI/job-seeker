"""Bounded Claude Code runner for the unattended JobPilot routine."""
from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import jobpilot_home
from .store.db import Store


PROMPT = """Run the JobPilot daily routine using only the JobPilot MCP tools.

1. Call list_seekers.
2. Call discover for all active seekers.
3. Do not score jobs or build digests: scoring and delivery run separately
   after this session.

Do not use shell, file, browser, or unrelated MCP tools. Report source health
and any failures; never describe a blocked source as an empty job market.
"""


@dataclass(frozen=True)
class ScheduledResult:
    run_id: int
    returncode: int
    output: str


def run_once(
    *,
    machine: str,
    mcp_config: Path,
    db_path: Path | None = None,
    claude_bin: str = "claude",
    max_budget_usd: float = 2.0,
    runner=subprocess.run,
) -> ScheduledResult:
    """Run one isolated Claude session and close the machine-run ledger."""
    home = jobpilot_home()
    db = Path(db_path or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db"))
    store = Store(db)
    store.migrate()
    run_id = store.start_machine_run(machine)
    command = [
        claude_bin, "--print", "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", str(mcp_config),
        "--tools", "", "--allowedTools", "mcp__jobpilot__*",
        "--max-budget-usd", str(max_budget_usd), PROMPT,
    ]
    try:
        completed = runner(
            command, cwd=str(home), capture_output=True, text=True, check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        status = "completed" if completed.returncode == 0 else "error"
        store.finish_machine_run(run_id, status)
        return ScheduledResult(run_id, int(completed.returncode), output)
    except Exception:
        store.finish_machine_run(run_id, "error")
        raise
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded JobPilot routine")
    parser.add_argument("--machine", default=os.environ.get("JOBPILOT_MACHINE", "local"))
    parser.add_argument("--mcp-config", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    args = parser.parse_args()
    result = run_once(
        machine=args.machine, mcp_config=args.mcp_config, db_path=args.db,
        claude_bin=args.claude, max_budget_usd=args.max_budget_usd,
    )
    print(result.output, end="")
    if result.returncode == 0:
        from .notify import push_digests, push_job_lists
        from .telegram_bot import Bot, _load_token
        home = jobpilot_home()
        store = Store(Path(args.db or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db")))
        try:
            store.migrate()
            from .score_runner import claude_scorer, score_all
            from .config import load_config
            cfg = load_config()
            for row in score_all(store, home, claude_scorer(
                    args.claude, model=cfg.scoring_model, effort=cfg.scoring_effort)):
                print(f"scoring: {row}")
            bot = Bot(_load_token(home), home, store)
            print(f"digests sent: {push_digests(store, bot.send)}")
            print(f"job lists sent: {push_job_lists(store, bot.send_job_list)}")
        finally:
            store.close()
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
