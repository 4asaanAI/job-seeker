"""Bounded Claude Code runner for the unattended JobPilot routine."""
from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .fo import (ClaudeFoClient, FoClient, child_env, fo_sender, render_fo_digest,
                 render_fo_followups)
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
            env=child_env(),
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


def make_fo_client(cfg, claude_bin: str = "claude"):
    """API key when allowed and present; otherwise the headless claude relay; None = skip."""
    key = os.environ.get("FO_API_KEY")
    if cfg.fo_transport in ("api", "auto") and key:
        return FoClient(key, cfg.fo_url)
    if cfg.fo_transport in ("claude", "auto"):
        return ClaudeFoClient(claude_bin, cfg.fo_url)
    return None


def deliver(store: Store, cfg, home: Path, *, bot_factory, fo_factory) -> None:
    """Push digests and follow-ups. Each channel is guarded so one cannot block the other.

    A seeker served through Fo is skipped on Telegram: the "already told"
    ledger ignores the channel, so two channels would starve each other.
    """
    from .notify import push_digests, push_followups, push_job_lists
    fo_id = None
    if cfg.fo_enabled:
        try:
            fo_client = fo_factory()
            if cfg.fo_seeker_id is None:
                raise ValueError("[fo] enabled needs seeker_id: one Fo account serves one seeker")
            if fo_client is None:
                print("fo: skipped, no Fo transport available (set FO_API_KEY, or use transport = claude)")
            else:
                if not any(s.id == cfg.fo_seeker_id for s in store.list_seekers()):
                    raise ValueError("[fo] seeker_id is not an active seeker")
                send = fo_sender(fo_client)
                ids = {cfg.fo_seeker_id}
                print(f"fo digests sent: {push_digests(store, send, channel='fo', seeker_ids=ids, recipient=lambda s: s.id, render=render_fo_digest)}")
                print(f"fo follow-ups sent: {push_followups(store, send, render_fo_followups, channel='fo', seeker_ids=ids)}")
                fo_id = cfg.fo_seeker_id
        except Exception as exc:  # noqa: BLE001 - reported, Telegram still runs
            print(f"fo delivery failed: {type(exc).__name__}: {exc}")
    try:
        bot = bot_factory()
        if bot is None:
            print("telegram: skipped, no bot token on this machine")
            return
        def to_telegram(seeker):
            if seeker.id == fo_id or not seeker.telegram_user_id:
                return None
            return int(seeker.telegram_user_id)
        print(f"digests sent: {push_digests(store, bot.send, recipient=to_telegram)}")
        print(f"job lists sent: {push_job_lists(store, bot.send_job_list)}")
    except Exception as exc:  # noqa: BLE001
        print(f"telegram delivery failed: {type(exc).__name__}: {exc}")


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
        from .config import load_config
        from .score_runner import claude_scorer, score_all
        from .telegram_bot import Bot, _find_token
        home = jobpilot_home()
        cfg = load_config()
        store = Store(Path(args.db or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db")))
        try:
            store.migrate()
            for row in score_all(store, home, claude_scorer(
                    args.claude, model=cfg.scoring_model, effort=cfg.scoring_effort)):
                print(f"scoring: {row}")
            deliver(
                store, cfg, home,
                bot_factory=lambda: (lambda t: Bot(t, home, store) if t else None)(_find_token(home)),
                fo_factory=lambda: make_fo_client(cfg, args.claude),
            )
        finally:
            store.close()
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
