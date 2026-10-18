"""Set up a new seeker from a resume file, with no Telegram and no hand-written YAML.

    python -m jobpilot.onboard --name "Pat Example" --resume ~/Downloads/resume.pdf \
        --role "Product Manager" --location "India" --terms "product manager,technical product manager" \
        --sources indeed_in,foundit_in,himalayas_in --min-pay-note "20 lakh a year or more"

It copies the resume into data/seekers/<name>/ (git-ignored), extracts its text to resume_source.txt (the file every resume
fact is later checked against), writes a starter profile.yaml, registers the seeker, and prints the environment lines for an MCP
session. It never overwrites an existing folder and makes no network calls.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

import yaml

from .paths import jobpilot_home
from .resume import extract_source_text
from .store.db import Store

MIN_TEXT = 200


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "seeker"


def onboard(store: Store, home: Path, *, name: str, resume: Path, role: str, location: str, terms: list[str],
            sources: list[str], notes: list[str] | None = None, min_score: float = 70.0) -> dict:
    resume = Path(resume).expanduser()
    if not resume.is_file():
        raise ValueError(f"resume file not found: {resume}")
    text = extract_source_text(resume).strip()
    if len(text) < MIN_TEXT:
        raise ValueError("the text of that file could not be read (a scanned image?). Use a PDF or Word file with selectable text.")
    rel = Path("data") / "seekers" / slug(name)
    folder = home / rel
    if folder.exists():
        raise ValueError(f"{rel} already exists; pick another name or remove it first")
    if not terms or not sources:
        raise ValueError("give at least one search term and one source")
    folder.mkdir(parents=True)
    try:
        shutil.copy2(resume, folder / f"uploaded_resume{resume.suffix.lower()}")
        (folder / "resume_source.txt").write_text(text, encoding="utf-8")
        profile = {"name": name, "headline": role, "location": location, "sources": sources, "search_terms": terms,
                   "min_score": min_score, "rubric": "general", "notes": notes or []}
        (folder / "profile.yaml").write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")
        sid = store.add_seeker(name, None, str(rel / "profile.yaml"), 20)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)       # never leave half a seeker behind
        raise
    return {"seeker_id": sid, "folder": str(rel), "characters_read": len(text)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a seeker from a resume file")
    parser.add_argument("--name", required=True)
    parser.add_argument("--resume", type=Path, required=True)
    parser.add_argument("--role", required=True, help="your target role, e.g. 'Product Manager'")
    parser.add_argument("--location", required=True)
    parser.add_argument("--terms", required=True, help="comma-separated search terms")
    parser.add_argument("--sources", required=True, help="comma-separated, e.g. indeed_in,foundit_in,himalayas_in")
    parser.add_argument("--note", action="append", default=[], help="free-text facts for scoring (pay, notice period...)")
    parser.add_argument("--min-score", type=float, default=70.0)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    home = jobpilot_home()
    db = Path(args.db or os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db"))
    store = Store(db)
    try:
        store.migrate()
        out = onboard(store, home, name=args.name, resume=args.resume, role=args.role, location=args.location,
                      terms=[t.strip() for t in args.terms.split(",") if t.strip()],
                      sources=[s.strip() for s in args.sources.split(",") if s.strip()], notes=args.note,
                      min_score=args.min_score)
    except ValueError as exc:
        print(f"onboard: {exc}")
        return 2
    finally:
        store.close()
    print(f"created seeker {out['seeker_id']} in {out['folder']} ({out['characters_read']} characters of resume read)")
    print("Next: ask Claude (JobPilot connected) to save your structured resume with save_base_resume, then:")
    print(f"  export JOBPILOT_SEEKER_ID={out['seeker_id']}   # pin your MCP session to you")
    print(f"  python -m jobpilot.doctor --seeker {out['seeker_id']}   # check everything is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
