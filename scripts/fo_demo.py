"""Fo x JobPilot demo with fully fictional data: no real resume, phone or profile.

    python scripts/fo_demo.py            dry run: builds the demo data, prints exactly what
                                         would be sent to Fo, sends nothing
    python scripts/fo_demo.py --send     really sends the digest and a follow-up through Fo
                                         (needs FO_API_KEY; asks you to type: yes)
    python scripts/fo_demo.py --dir PATH keep the demo data somewhere else

It leaves a database you can point a Claude session at (the exports are printed at
the end), so the interactive half of the demo reads the same fictional seeker.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobpilot import notify  # noqa: E402
from jobpilot.fo import (FO_URL, FoClient, fo_sender, render_fo_digest,  # noqa: E402
                         render_fo_followups, wrap)
from jobpilot.store.db import Store  # noqa: E402
from jobpilot.store.models import Posting  # noqa: E402

PROFILE = """\
name: Avery Demo
headline: Senior business analyst, retail and e-commerce
location: Austin, TX
sources: [indeed_us]
search_terms: [business analyst, analytics manager]
min_score: 70
"""

RESUME = """\
name: Avery Demo
headline: Senior business analyst, retail and e-commerce
contact: [avery.demo@example.com, Austin TX]
summary: Analyst who turns messy sales data into decisions retail teams act on.
experience:
  - id: northwind
    employer: Northwind Retail (fictional)
    title: Senior Business Analyst
    dates: 2022 - present
    bullets:
      - Built weekly sell-through dashboards used by 40 store managers.
      - Cut markdown waste 12% by modelling price elasticity per category.
  - id: contoso
    employer: Contoso Stores (fictional)
    title: Business Analyst
    dates: 2019 - 2022
    bullets:
      - Automated month-end reporting, saving 20 analyst hours a month.
education:
  - id: ut
    institution: Example State University
    degree: BBA, Business Analytics
    dates: 2015 - 2019
skills: [SQL, Python, Tableau, Forecasting, A/B testing]
"""

JOBS = [  # title, company, score, reasoning (canned: demo scoring is bypassed)
    ("Senior Business Analyst", "Fabrikam Commerce", 92, "SQL and retail analytics match the resume closely."),
    ("Analytics Manager, Merchandising", "Tailspin Brands", 88, "Merchandising analytics and team lead scope fit."),
    ("Pricing Analyst", "Adventure Outfitters", 81, "Price elasticity work lines up with a past result."),
    ("Data Entry Clerk", "Litware Services", 55, "Below the seniority and pay this seeker wants."),
]
FOLLOW_UP = ("Business Intelligence Lead", "Wingtip Retail", 84,
             "BI leadership with retail focus.")


MARKER = ".fo-demo"


def seed(base: Path, keep: bool = False) -> tuple[Store, int, list[int]]:
    if base.exists():
        if not (base / MARKER).exists() and any(base.iterdir()):
            raise SystemExit(f"{base} is not a Fo demo folder (no {MARKER} file): refusing to delete it")
        if keep and (base / MARKER).exists():
            store = Store(base / "jobpilot.db")
            store.migrate()
            return store, store.list_seekers()[0].id, []
        shutil.rmtree(base)
    seeker_dir = base / "seekers" / "avery"
    seeker_dir.mkdir(parents=True)
    (base / MARKER).write_text("created by scripts/fo_demo.py\n")
    (seeker_dir / "profile.yaml").write_text(PROFILE, encoding="utf-8")
    (seeker_dir / "base_resume.yaml").write_text(RESUME, encoding="utf-8")
    store = Store(base / "jobpilot.db")
    store.migrate()
    sid = store.add_seeker("Avery Demo", None, str(seeker_dir / "profile.yaml"), 20)
    store.record_source_run("indeed_us", True, 20, 50, 50, "healthy", "demo fixture")

    def add(n: int, title: str, company: str, score: float, why: str) -> int:
        pid = store.upsert_posting(Posting(
            company_name=company, source="indeed_us", external_id=f"demo-{n}",
            title=title, location="Austin, TX", description="Fictional demo posting.",
            url=f"https://example.com/jobs/demo-{n}", posted_at="2026-10-17"))
        store.link_posting(sid, pid)
        store.record_score(sid, pid, float(score), why, "demo")
        return pid

    ids = [add(i, *job) for i, job in enumerate(JOBS, 1)]
    # One role Fo already told the seeker about four days ago: it is due a follow-up.
    old = add(99, *FOLLOW_UP)
    store.record_notification(sid, old, "fo", "first")
    long_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=4)).isoformat()
    store.conn.execute("UPDATE notifications SET delivered_at = ?", (long_ago,))
    store.conn.commit()
    return store, sid, ids


def show(title: str, text: str) -> None:
    print(f"\n=== {title} ===\n{text}")


def main(argv=None, *, input_fn=input, client_factory=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--send", action="store_true", help="really send through Fo")
    ap.add_argument("--dir", type=Path, default=ROOT / "out" / "fo-demo")
    ap.add_argument("--keep", action="store_true",
                    help="reuse the existing demo folder (keeps a running Claude session's database)")
    args = ap.parse_args(argv)

    store, sid, _ = seed(args.dir, args.keep)
    try:
        digest = render_fo_digest(store, sid, 70.0, 5)
        followups = store.pending_followups(sid)
        show("Digest message for Fo", digest.text)
        show("Follow-up message for Fo", render_fo_followups(followups))
        show("What Fo actually receives (digest)", wrap(digest.text))

        if args.send:
            key = os.environ.get("FO_API_KEY")
            if not key:
                print("\nFO_API_KEY is not set: nothing sent.")
                return 1
            if input_fn("\nSend these two messages to your real Fo account? Type yes: ").strip() != "yes":
                print("Nothing sent.")
                return 1
            client = (client_factory or FoClient)(key, FO_URL)
            send = fo_sender(client)
            ids = {sid}
            n = notify.push_digests(store, send, channel="fo", seeker_ids=ids,
                                    recipient=lambda s: s.id, render=render_fo_digest)
            m = notify.push_followups(store, send, render_fo_followups, channel="fo", seeker_ids=ids)
            print(f"\nsent through Fo: {n} digest(s), {m} follow-up message(s)")
            if n + m < 2:
                print("Something did not complete: see the log lines above; nothing failed was recorded.")
                return 1
        else:
            print("\nDry run: nothing was sent to Fo.")
        print(f"\nPoint a Claude session at this data:\n  export JOBPILOT_DB='{args.dir / 'jobpilot.db'}'"
              f"\n  export JOBPILOT_PYTHON='{sys.executable}'"
              f"\n  export JOBPILOT_SEEKER_ID={sid}\n  export JOBPILOT_OUT='{args.dir / 'out'}'")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
