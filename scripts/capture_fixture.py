"""Capture a small, redacted JobSpy response as a test fixture.

This is intentionally a live-only script. Tests must consume the captured
file and never call a job board. The default capture is Naukri India because
that source is the least represented by the existing fixtures.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EMAIL = re.compile(r"\b[A-Z0-9.%+\-_\\]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.-])?(?:\(\d{2,5}\)|\d{2,5})"
    r"[\s.-]\d{3,4}[\s.-]\d{3,4}(?!\w)"
)
INDIA_PHONE = re.compile(
    r"(?<!\d)(?:\+?91[\s.-]?)?0?[6-9]\d{4}[\s.-]?\d{5}(?!\d)"
)


def redact_text(value: str) -> tuple[str, int]:
    """Redact contact details without changing URLs or ordinary job data."""
    urls: list[str] = []

    def hold_url(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return f"\x00URL{len(urls) - 1}\x00"

    protected = re.sub(r"https?://\S+", hold_url, str(value or ""))
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[redacted]"

    protected = EMAIL.sub(replace, protected)
    protected = PHONE.sub(replace, protected)
    protected = INDIA_PHONE.sub(replace, protected)
    for index, url in enumerate(urls):
        protected = protected.replace(f"\x00URL{index}\x00", url)
    return protected, count


def redact_rows(rows: list[dict]) -> tuple[list[dict], int]:
    """Redact contact details in every string field of captured rows."""
    cleaned: list[dict] = []
    count = 0
    for row in rows:
        copy = dict(row)
        for key, value in copy.items():
            if isinstance(value, str):
                copy[key], found = redact_text(value)
                count += found
        cleaned.append(copy)
    return cleaned, count


def survivors(rows: list[dict]) -> list[str]:
    """Return obvious contact markers left in unredacted row text."""
    found: list[str] = []
    for row in rows:
        for value in row.values():
            if not isinstance(value, str):
                continue
            without_urls = re.sub(r"https?://\S+", "", value)
            if "@" in without_urls:
                found.append("@")
            if PHONE.search(without_urls) or INDIA_PHONE.search(without_urls):
                found.append("phone")
    return found


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return "" if math.isnan(value) else value
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _normalise_rows(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    rows = []
    for raw in df.fillna("").to_dict("records"):
        row = {key: _json_value(value) for key, value in raw.items()}
        rows.append(row)
    cleaned, redactions = redact_rows(rows)
    leftovers = survivors(cleaned)
    if leftovers:
        raise ValueError("redaction left contact markers: " + ", ".join(leftovers))
    print(f"redacted contact fields: {redactions}")
    return cleaned


def _validate(rows: list[dict]) -> tuple[bool, str]:
    if not rows:
        return False, "captured nothing; refusing to write an empty fixture"
    for index, row in enumerate(rows, 1):
        external_id = row.get("id")
        if external_id in (None, "") or (
            isinstance(external_id, float) and math.isnan(external_id)
        ) or str(external_id).strip().lower() == "nan":
            return False, f"row {index} has an empty or NaN id"
        if not isinstance(row.get("site"), str) or not row["site"].strip():
            return False, f"row {index} has no string site"
        posted = row.get("date_posted", "")
        if posted not in (None, "") and not isinstance(posted, str):
            return False, f"row {index} has a non-string date_posted"
    described = sum(bool(str(row.get("description") or "").strip()) for row in rows)
    if described == 0:
        return False, "captured rows have no descriptions; scoring would be title-only"
    return True, f"{described}/{len(rows)} rows have descriptions"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", choices=("naukri", "indeed_us"),
                        help="compatibility alias for the source fixture")
    parser.add_argument("--source", choices=("naukri", "indeed"))
    parser.add_argument("--location")
    parser.add_argument("--country")
    parser.add_argument("--term", default="merchandiser")
    parser.add_argument("--results", type=int, default=10)
    parser.add_argument("--hours-old", type=int, default=168)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.target == "indeed_us":
        source = "indeed"
        location = args.location or "United States"
        country = args.country or "usa"
        output = args.output or ROOT / "tests" / "fixtures" / "jobspy_indeed_us.json"
    else:
        source = args.source or "naukri"
        if source == "indeed":
            location = args.location or "United States"
            country = args.country or "usa"
            default_output = ROOT / "tests" / "fixtures" / "jobspy_indeed_us.json"
        else:
            location = args.location or "Gurgaon"
            country = args.country or "india"
            default_output = ROOT / "tests" / "fixtures" / "jobspy_naukri.json"
        output = args.output or default_output

    from jobspy import scrape_jobs

    df = scrape_jobs(
        site_name=[source], search_term=args.term,
        location=location, results_wanted=args.results,
        country_indeed=country, hours_old=args.hours_old,
        linkedin_fetch_description=True,
    )
    try:
        rows = _normalise_rows(df)
    except ValueError as exc:
        print(f"REFUSING TO WRITE: {exc}")
        return 3
    valid, message = _validate(rows)
    print(message)
    if not valid:
        return 2 if "descriptions" in message else 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"captured {len(rows)} rows -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
