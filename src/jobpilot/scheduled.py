"""Bounded runner for the unattended JobPilot routine (claude CLI or Bedrock)."""
from __future__ import annotations

import argparse
import os
import stat
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
        "--tools", "", "--allowedTools", "mcp__jobpilot__list_seekers,mcp__jobpilot__discover",
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


def fo_api_key() -> str | None:
    """The Fo API key: FO_API_KEY, else the private file ~/.config/jobpilot/fo_api_key. The file is used only if it is a
    regular file only you can read (a key in a group- or world-readable file is ignored, never read)."""
    key = os.environ.get("FO_API_KEY")
    if key:
        return key
    path = Path.home() / ".config" / "jobpilot" / "fo_api_key"
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 256:
            return None
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def make_fo_client(cfg, claude_bin: str = "claude"):
    """API key when allowed and present; otherwise the headless claude relay; None = skip."""
    key = fo_api_key()
    if cfg.fo_transport in ("api", "auto") and key:
        # Google and form-filling tasks take minutes (a 82-row sheet, a whole application), so wait up to 15 minutes
        return FoClient(key, cfg.fo_url, max_wait=900.0, max_polls=40)
    if cfg.fo_transport in ("claude", "auto"):
        return ClaudeFoClient(claude_bin, cfg.fo_url)
    return None


def deliver(store: Store, cfg, home: Path, *, bot_factory, fo_factory, tracker_text_me: bool = False) -> None:
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
                try:
                    print(f"fo digests sent: {push_digests(store, send, channel='fo', seeker_ids=ids, recipient=lambda s: s.id, render=render_fo_digest)}")
                    print(f"fo follow-ups sent: {push_followups(store, send, render_fo_followups, channel='fo', seeker_ids=ids)}")
                    fo_id = cfg.fo_seeker_id
                finally:
                    if cfg.google_enabled:         # its own guard: a failed digest must not skip the sheet
                        try:
                            from .google import send_tracker
                            result = send_tracker(store, cfg.fo_seeker_id, fo_client, home=home, text_me=tracker_text_me)
                            print(f"google tracker: {result.status if result else 'nothing to record'}")
                        except Exception as exc:  # noqa: BLE001
                            print(f"google tracker failed: {type(exc).__name__}: {exc}")
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
        print(f"job lists sent: {push_job_lists(store, bot.send_job_list, skip={fo_id} if fo_id else ())}")
    except Exception as exc:  # noqa: BLE001
        print(f"telegram delivery failed: {type(exc).__name__}: {exc}")


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
    parser.add_argument("--mcp-config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None, help="config.toml to use instead of the home one")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--max-budget-usd", type=float, default=2.0)
    parser.add_argument("--deliver-only", action="store_true",
                        help="skip discovery and scoring; only push digests and follow-ups")
    args = parser.parse_args()
    if not args.deliver_only and args.mcp_config is None:
        parser.error("--mcp-config is required unless --deliver-only is given")
    from .config import load_config
    cfg = load_config(args.config)
    returncode = 0
    if not args.deliver_only:
        if cfg.llm.provider == "bedrock":
            result = run_once_bedrock(machine=args.machine, mcp_config=args.mcp_config,
                                      db_path=args.db)
        else:
            result = run_once(
                machine=args.machine, mcp_config=args.mcp_config, db_path=args.db,
                claude_bin=args.claude, max_budget_usd=args.max_budget_usd,
            )
        print(result.output, end="")
        returncode = result.returncode
    if returncode == 0:
        from .score_runner import claude_scorer, score_all
        from .telegram_bot import Bot, _find_token
        home = jobpilot_home()
        store = Store(Path(args.db or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db")))
        try:
            store.migrate()
            if not args.deliver_only:
                if cfg.llm.provider == "bedrock":
                    from .llm import bedrock_scorer
                    ask = bedrock_scorer(model=cfg.llm.scoring_model, region=cfg.llm.region,
                                         fallback_model=cfg.llm.fallback_model)
                else:
                    ask = claude_scorer(args.claude, model=cfg.scoring_model, effort=cfg.scoring_effort)
                from .bridge import scoring_lock
                with scoring_lock(home, wait=True):             # a bridge search scores the same backlog: one at a time
                    for row in score_all(store, home, ask):
                        print(f"scoring: {row}")
            deliver(
                store, cfg, home,
                bot_factory=lambda: (lambda t: Bot(t, home, store) if t else None)(_find_token(home)),
                fo_factory=lambda: make_fo_client(cfg, args.claude),
            )
        finally:
            store.close()
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
