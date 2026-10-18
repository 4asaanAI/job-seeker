"""Preflight: tell a first-time user exactly what to fix before the first search. Read-only; no network.

    python -m jobpilot.doctor [--seeker N]
Exit code 1 when something needs fixing.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .paths import jobpilot_home
from .store.db import Store


def checks(store: Store, home: Path, seeker_id: int | None) -> list[tuple[bool, str, str]]:
    """(ok, what was checked, how to fix)."""
    out: list[tuple[bool, str, str]] = []
    out.append((sys.version_info[:2] == (3, 12), f"Python {sys.version_info.major}.{sys.version_info.minor}",
                "use Python 3.12 (see pyproject.toml)"))
    out.append((shutil.which("claude") is not None, "claude CLI on PATH (scoring and the Fo relay need it)",
                "install Claude Code and run `claude` once to log in"))
    try:
        from .config import load_config
        cfg = load_config()
        out.append((True, "config.toml parses", ""))
        if cfg.fo_enabled:
            out.append((cfg.fo_seeker_id is not None, "[fo] has a seeker_id", "set seeker_id under [fo] in config.toml"))
    except Exception as exc:  # noqa: BLE001
        out.append((False, "config.toml parses", f"fix config.toml: {type(exc).__name__}: {exc}"))
    seekers = store.list_seekers()
    out.append((bool(seekers), "at least one seeker exists", "python -m jobpilot.onboard --help"))
    if seeker_id is not None:
        seeker = next((s for s in seekers if s.id == seeker_id), None)
        out.append((seeker is not None, f"seeker {seeker_id} exists", "check the id with list_seekers"))
        if seeker:
            profile_path = Path(seeker.profile_path)
            profile_path = profile_path if profile_path.is_absolute() else home / profile_path
            folder = profile_path.parent
            try:
                from .profiles import load_profile
                load_profile(profile_path)
                out.append((True, "profile.yaml loads", ""))
            except Exception as exc:  # noqa: BLE001
                out.append((False, "profile.yaml loads", f"fix {profile_path}: {exc}"))
            out.append(((folder / "resume_source.txt").is_file(), "your resume text is on file (resume_source.txt)",
                        "re-run onboard with --resume, or add the file"))
            base = folder / "base_resume.yaml"
            out.append((base.is_file(), "structured resume saved (base_resume.yaml)",
                        "ask Claude to save it with save_base_resume"))
            if base.is_file():
                try:
                    from .resume import load_base_resume
                    load_base_resume(base)
                    out.append((True, "base_resume.yaml is valid", ""))
                except Exception as exc:  # noqa: BLE001
                    out.append((False, "base_resume.yaml is valid", str(exc)))
            unscored = len(store.unscored_jobs(seeker_id, 1000))
            out.append((True, f"{unscored} job(s) waiting to be scored", ""))
            errs = store.event_count_since(seeker_id, "scoring_error", 24)
            out.append((errs == 0, "no scoring errors in the last 24 hours",
                        "run python -m jobpilot.score_runner and read the error; often a logged-out claude CLI"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that JobPilot is ready to use")
    parser.add_argument("--seeker", type=int, default=None)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    home = jobpilot_home()
    store = Store(Path(args.db or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db")))
    try:
        store.migrate()
        results = checks(store, home, args.seeker)
    finally:
        store.close()
    bad = 0
    for ok, what, fix in results:
        print(("OK   " if ok else "FIX  ") + what + ("" if ok else f"  ->  {fix}"))
        bad += not ok
    print("\nAll good." if not bad else f"\n{bad} thing(s) to fix.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
