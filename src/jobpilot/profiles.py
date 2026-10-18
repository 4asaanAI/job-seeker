# src/jobpilot/profiles.py
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# sources is required with no default: "every enabled source" would send a
# US seeker through Naukri.
REQUIRED = ("name", "headline", "location", "search_terms", "sources")


@dataclass(frozen=True)
class Profile:
    name: str
    headline: str
    location: str
    sources: list[str]
    accounts: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    target_companies: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    min_score: float = 70.0
    notes: list[str] = field(default_factory=list)
    also_search: list[str] = field(default_factory=list)
    # Names a shared rubric under data/rubrics/<rubric>.md, used only when this
    # seeker has no data/seekers/<name>/rubric.md of their own (see scoring.py).
    rubric: str | None = None


@dataclass(frozen=True)
class CompanyRow:
    name: str
    tier: int
    ats_vendor: str
    careers_url: str
    region: str


def load_profile(path: Path) -> Profile:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    missing = [f for f in REQUIRED if not raw.get(f)]
    if missing:
        raise ValueError(f"profile {path} missing required fields: {', '.join(missing)}")
    return Profile(
        name=raw["name"],
        headline=raw["headline"],
        location=raw["location"],
        sources=list(raw["sources"]),
        accounts=list(raw.get("accounts") or []),
        search_terms=list(raw["search_terms"]),
        target_companies=list(raw.get("target_companies") or []),
        exclude_keywords=list(raw.get("exclude_keywords") or []),
        min_score=float(raw.get("min_score", 70)),
        notes=list(raw.get("notes") or []),
        also_search=list(raw.get("also_search") or []),
        rubric=raw.get("rubric") or "general",
    )


def load_companies(path: Path) -> list[CompanyRow]:
    rows: list[CompanyRow] = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("enabled") or "1").strip() != "1":
                continue
            rows.append(
                CompanyRow(
                    name=r["name"].strip(),
                    tier=int(r["tier"]),
                    ats_vendor=(r.get("ats_vendor") or "").strip(),
                    careers_url=(r.get("careers_url") or "").strip(),
                    region=(r.get("region") or "").strip(),
                )
            )
    return rows


def seeker_min_score(profile_path: str | Path, home: Path) -> float:
    """The seeker's own score threshold. Never raises: a missing or broken profile, or a value that is not a finite
    number, gives 70; the result is kept between 50 and 100 so a stray 0 cannot dump every scored posting."""
    try:
        path = Path(profile_path)
        path = path if path.is_absolute() else Path(home) / path
        if not path.is_file() or path.stat().st_size > 1_000_000:       # never read a device, a FIFO or a huge file
            return 70.0
        value = float(load_profile(path).min_score)
    except Exception:  # noqa: BLE001 - any trouble reading a profile means the default
        return 70.0
    return min(100.0, max(50.0, value)) if value == value and abs(value) != float("inf") else 70.0
