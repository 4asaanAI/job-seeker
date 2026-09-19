"""Guarded live smoke test for approved non-LinkedIn sources.

This is deliberately separate from pytest. It contacts public job boards and
should be run only from an interactive terminal after reviewing the plan.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobpilot.config import load_config  # noqa: E402
from jobpilot.paths import jobpilot_home  # noqa: E402
from jobpilot.pipeline import SeekerTarget, run_discovery  # noqa: E402
from jobpilot.profiles import load_companies, load_profile  # noqa: E402
from jobpilot.sources.jobspy_source import JobSpySource  # noqa: E402
from jobpilot.store.db import Store  # noqa: E402

APPROVED_SOURCES = frozenset({"naukri_in", "indeed_in", "indeed_us"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true",
                        help="required acknowledgement; no prompt is bypassed")
    args = parser.parse_args(argv)
    if not args.yes:
        print("Refusing live requests without --yes. Review the command first.")
        return 2

    home = jobpilot_home()
    cfg = load_config()
    store = Store(cfg.data_dir / "jobpilot.db")
    try:
        store.migrate()
        store.sync_companies(load_companies(home / "data" / "companies.csv"))
        seekers = store.list_seekers()
        if not seekers:
            print("No active seekers are configured.")
            return 2

        targets = []
        for seeker in seekers:
            path = Path(seeker.profile_path)
            if not path.is_absolute():
                path = home / path
            targets.append(SeekerTarget(seeker.id, load_profile(path)))

        sources = [
            JobSpySource(site, min_request_gap_seconds=cfg.min_request_gap_seconds)
            for site in cfg.sites.values()
            if site.enabled and site.name in APPROVED_SOURCES
        ]
        skipped = sorted(
            site.name for site in cfg.sites.values()
            if site.enabled and site.name not in APPROVED_SOURCES
        )
        if skipped:
            print("Skipped sources (not approved for live smoke): " + ", ".join(skipped))

        results = run_discovery(store, sources, targets)
        worst = 0
        for result in results:
            print(
                f"{result.source:<12} {result.status.value:<9} "
                f"canary_ok={result.canary_ok} canary={result.canary_count} "
                f"raw={result.raw_count} found={len(result.postings)}"
            )
            print(f"  {result.notes}")
            if result.status.value in ("blocked", "error"):
                worst = 1

        for seeker in seekers:
            print(f"linked to {seeker.name}: {len(store.seeker_jobs(seeker.id))}")
        return worst
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
