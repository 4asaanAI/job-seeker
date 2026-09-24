"""Bounded runner for the unattended JobPilot routine (claude CLI or Bedrock)."""
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


def run_once_bedrock(*, machine: str, mcp_config: Path, db_path: Path | None = None,
                     brain=None) -> ScheduledResult:
    """Same routine as run_once, driven by the Bedrock brain instead of the
    claude CLI. The tool process gets exactly the env from --mcp-config."""
    import asyncio
    import json
    import tempfile
    home = jobpilot_home()
    db = Path(db_path or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db"))
    store = Store(db)
    store.migrate()
    run_id = store.start_machine_run(machine)
    try:
        server = json.loads(Path(mcp_config).read_text(encoding="utf-8"))["mcpServers"]["jobpilot"]
        env = {**{k: v for k, v in os.environ.items()
                  if k in ("PATH", "PYTHONPATH", "SYSTEMROOT", "HOME", "USERPROFILE", "TEMP", "TMP",
                           "JOBPILOT_WIGOLO_URL", "JOBPILOT_WIGOLO_TOKEN_FILE",
                           "JOBPILOT_SEARXNG_URL")},
               **server.get("env", {})}
        if brain is None:
            from .config import load_config
            from .llm import BedrockBrain
            llm = load_config().llm
            brain = BedrockBrain(Path(tempfile.mkdtemp(prefix="jobpilot-sched-")),
                                 bot_model=llm.bot_model, fallback_model=llm.fallback_model,
                                 region=llm.region)
        output, _ = asyncio.run(brain._turn(
            env, str(home), "You run JobPilot's unattended daily routine. Use only the tools.",
            [], PROMPT))
        store.finish_machine_run(run_id, "completed")
        return ScheduledResult(run_id, 0, output)
    except Exception as exc:  # noqa: BLE001 - recorded in the ledger, then reported
        store.finish_machine_run(run_id, "error")
        return ScheduledResult(run_id, 1, f"{type(exc).__name__}: {exc}")
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
    from .config import load_config
    cfg = load_config()
    if cfg.llm.provider == "bedrock":
        result = run_once_bedrock(machine=args.machine, mcp_config=args.mcp_config,
                                  db_path=args.db)
    else:
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
            if cfg.llm.provider == "bedrock":
                from .llm import bedrock_scorer
                ask = bedrock_scorer(model=cfg.llm.scoring_model, region=cfg.llm.region,
                                     fallback_model=cfg.llm.fallback_model)
            else:
                ask = claude_scorer(args.claude, model=cfg.scoring_model, effort=cfg.scoring_effort)
            for row in score_all(store, home, ask):
                print(f"scoring: {row}")
            bot = Bot(_load_token(home), home, store)
            print(f"digests sent: {push_digests(store, bot.send)}")
            print(f"job lists sent: {push_job_lists(store, bot.send_job_list)}")
        finally:
            store.close()
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
