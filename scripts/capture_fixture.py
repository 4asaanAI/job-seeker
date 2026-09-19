"""Capture a real JobSpy response as a test fixture.

Never hand-write a fixture. v1 invented a description field the API does not
populate without linkedin_fetch_description=True, and the invented fixture
made a critical defect invisible to the whole test suite.

Usage: python scripts/capture_fixture.py [naukri|indeed_us]

One live call per run. JobSpy logs scraper errors (a 406 "recaptcha required"
from Naukri, a 403 or 429) and returns an empty frame instead of raising, so
an empty capture is treated as a possible block: stop, do not retry.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jobspy import scrape_jobs

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

TARGETS = {
    "naukri": dict(
        out=FIXTURES / "jobspy_naukri.json",
        kwargs=dict(site_name=["naukri"], search_term="merchandiser",
                    location="Gurgaon", country_indeed="india"),
    ),
    "indeed_us": dict(
        out=FIXTURES / "jobspy_indeed_us.json",
        kwargs=dict(site_name=["indeed"], search_term="product analyst",
                    location="United States", country_indeed="USA"),
    ),
}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w.])\+?\d[\d ()-]{8,}\d(?![\w.])")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "naukri"
    target = TARGETS[name]
    df = scrape_jobs(results_wanted=10, hours_old=168,
                     linkedin_fetch_description=True, **target["kwargs"])
    if df is None or len(df) == 0:
        print("Captured nothing. Possible block (see JobSpy log above). "
              "Do not write an empty fixture and do not retry in a loop.")
        return 1

    # Types as JobSpy delivers them, before any conversion.
    first = df.iloc[0]
    for col in ("id", "site", "date_posted", "description"):
        print(f"raw {col}: python type {type(first.get(col)).__name__}")
    print("raw id nulls:", int(df["id"].isna().sum()))

    # Same shape the adapter sees in production (_default_scraper).
    rows = df.fillna("").to_dict("records")
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "item"):  # numpy scalar -> python scalar
                v = v.item()
            if not isinstance(v, (str, int, float, bool, type(None))):
                v = str(v)
            r[k] = v

    target["out"].write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    with_desc = sum(1 for r in rows if str(r.get("description") or "").strip())
    print(f"captured {len(rows)} rows -> {target['out']}")
    print(f"rows with a non-empty description: {with_desc}/{len(rows)}")
    print("columns:", sorted(rows[0].keys()))
    blob = json.dumps(rows, ensure_ascii=False)
    print("email candidates:", sorted(set(EMAIL.findall(blob))))
    print("phone candidates:", sorted(set(PHONE.findall(blob))))
    if with_desc == 0:
        print("\nWARNING: no descriptions. Scoring cannot work on titles alone.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
