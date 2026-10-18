"""Find the best new jobs for a seeker, on demand: discover, score what is new, then list what Fo can apply to.

    python -m jobpilot.refresh                 # for [fo] seeker_id (or --seeker N); search + score + plan
    python -m jobpilot.refresh --no-search     # only score the backlog and show the plan

Nothing is applied here: the plan is a read-only list; applying is a separate, approved step (docs/APPLYING.md).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .paths import jobpilot_home
from .store.db import Store


def refresh(store: Store, home: Path, seeker_id: int, *, discover, ask, plan, search: bool = True,
            max_batches: int = 5) -> dict:
    """discover(seeker_id) -> dict; ask = scoring callable; plan(seeker_id) -> dict. All injected for tests."""
    from .profiles import load_profile
    from .score_runner import score_backlog
    result: dict = {"seeker_id": seeker_id}
    if search:
        found = discover(seeker_id)
        result["sources"] = [{"source": s["source"], "status": s["status"], "found": s["found"]}
                             for s in found.get("sites", [])]
        result["skipped"] = found.get("skipped_seekers", {})
    seeker = store.get_seeker(seeker_id)
    path = Path(seeker.profile_path)
    path = path if path.is_absolute() else home / path
    result["scoring"] = score_backlog(store, seeker_id, load_profile(path), path, ask, max_batches=max_batches)
    result["plan"] = plan(seeker_id)
    return result


def main() -> int:
    from .config import load_config
    from .score_runner import claude_scorer
    from .server import build_tools
    parser = argparse.ArgumentParser(description="Search, score and list the best jobs to apply to")
    parser.add_argument("--seeker", type=int, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--no-search", action="store_true")
    parser.add_argument("--max-batches", type=int, default=5, help="scoring batches of 20 jobs (cost cap)")
    args = parser.parse_args()
    cfg = load_config(args.config)
    sid = args.seeker or cfg.fo_seeker_id
    if sid is None:
        print("refresh: pass --seeker N or set [fo] seeker_id")
        return 2
    home = jobpilot_home()
    store = Store(Path(args.db or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db")))
    try:
        store.migrate()
        try:
            store.get_seeker(sid)
        except ValueError as exc:
            print(f"refresh: {exc}. Create one with: python -m jobpilot.onboard --help")
            return 2
        tools = build_tools(store, cfg.out_dir, operator_mode=True)
        out = refresh(store, home, sid, discover=lambda s: tools["discover"]([s]),
                      ask=claude_scorer(args.claude, model=cfg.scoring_model, effort=cfg.scoring_effort),
                      plan=lambda s: tools["plan_apply_batch"](s, 10), search=not args.no_search,
                      max_batches=args.max_batches)
        for row in out.get("sources", []):
            print(f"source {row['source']}: {row['status']}, {row['found']} found")
        print(f"scoring: {out['scoring']}")
        if not out["plan"].get("ok", True):
            print(f"plan: {out['plan'].get('error')}")
        items = out["plan"].get("items", [])
        print(f"{len(items)} job(s) Fo can plausibly apply to:")
        for i in items:
            print(f"  id {i['posting_id']} ({i['score']:.0f}) {i['title'][:50]}, {i['company'][:30]} [{i['route']}]")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
