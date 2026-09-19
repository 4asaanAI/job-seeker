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

# Captured ads are public but can carry recruiter emails and phone numbers.
# Redaction happens here, before the file is written, so a re-run cannot
# re-commit raw contact details. Email includes markdown-escaped "\_".
EMAIL = re.compile(r"(?:[\w.+-]|\\_)+@[\w-]+(?:\.[\w-]+)+")
# +CC numbers, and NANP style 1-800-865-7580 / (800) 865-7580 / 800.865.7580.
# A trailing "." or "," is allowed on purpose: "call 1-800-865-7580." is a phone.
PHONE = re.compile(
    r"(?<![\w/.-])(?:\+\d{1,3}[ .-]?\(?\d{2,4}\)?[ .-]?\d{3,4}[ .-]?\d{3,5}"
    r"|(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4})(?![\w-])"
)
# Stricter fail-closed check run on the redacted output: any "@", or any
# grouped digit run that looks phone-like, aborts the write.
LOOSE_PHONE = re.compile(r"(?<![\w/.-])\+?\(?\d{2,4}\)?[ .-]\d{2,4}[ .-]\d{3,4}(?![\w-])")
URL_KEYS = ("url", "url_direct", "logo")
REDACTED = "[redacted]"


def redact_text(text: str) -> tuple[str, int]:
    text, n_email = EMAIL.subn(REDACTED, text)
    text, n_phone = PHONE.subn(REDACTED, text)
    return text, n_email + n_phone


def redact_rows(rows: list[dict]) -> tuple[list[dict], int]:
    total = 0
    out = []
    for r in rows:
        clean = {}
        for k, v in r.items():
            if isinstance(v, str) and not k.endswith(URL_KEYS):
                v, n = redact_text(v)
                total += n
            clean[k] = v
        out.append(clean)
    return out, total


def survivors(rows: list[dict]) -> list[str]:
    found = []
    for r in rows:
        for k, v in r.items():
            if isinstance(v, str) and not k.endswith(URL_KEYS):
                found += ["@" for _ in re.finditer("@", v)]
                found += LOOSE_PHONE.findall(v)
    return found


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "naukri"
    target = TARGETS[name]
    from jobspy import scrape_jobs  # lazy: redaction is importable offline

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

    rows, n_redacted = redact_rows(rows)
    left = survivors(rows)
    if left:
        print(f"REFUSING TO WRITE: {len(left)} email/phone candidate(s) survive "
              f"redaction: {left}. Extend the patterns, do not hand-edit.")
        return 3

    target["out"].write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    with_desc = sum(1 for r in rows if str(r.get("description") or "").strip())
    print(f"captured {len(rows)} rows -> {target['out']}")
    print(f"redacted {n_redacted} email/phone value(s)")
    print(f"rows with a non-empty description: {with_desc}/{len(rows)}")
    print("columns:", sorted(rows[0].keys()))
    if with_desc == 0:
        print("\nWARNING: no descriptions. Scoring cannot work on titles alone.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
