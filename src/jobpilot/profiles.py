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
