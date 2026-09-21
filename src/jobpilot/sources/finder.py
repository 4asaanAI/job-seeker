"""Find an employer's public job feed from its name alone.

Probes the three public ATS APIs that need no key (Greenhouse, Lever, Ashby)
with slugs derived from the name. A feed is accepted only when it is
identifiably that employer and has at least one live job: Greenhouse reports
the board's company name, which must match; Lever and Ashby do not, so their
slug must equal the normalised name exactly. Anything else is "not found" --
a wrong employer's jobs are worse than none.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .ats_json import _default_fetcher

SUFFIXES = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
            "pvt", "plc", "gmbh", "hq", "the", "technologies"}
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def _words(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", name.casefold().replace("&", " and "))
    return [w for w in words if w not in SUFFIXES]


def normalise(name: str) -> str:
    return "".join(_words(name))


def slugs(name: str) -> list[str]:
    words = _words(name)
    if not words:
        return []
    return list(dict.fromkeys(["".join(words), "-".join(words)]))


def _jobs(vendor: str, payload: object) -> int:
    if vendor == "lever":
        return len(payload) if isinstance(payload, list) else 0
    return len(payload.get("jobs") or []) if isinstance(payload, dict) else 0


def find_feed(
    company: str,
    fetch: Callable[[str, float], object] = _default_fetcher,
    timeout: float = 15.0,
) -> dict | None:
    """Return {vendor, slug, endpoint, company_name, jobs} or None."""
    wanted = normalise(company)
    if len(wanted) < 2:
        return None

    def probe(vendor: str, slug: str) -> dict | None:
        try:
            if vendor == "greenhouse":
                board = fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout)
                name = str(board.get("name") or "") if isinstance(board, dict) else ""
                if normalise(name) != wanted:
                    return None
            elif slug.replace("-", "") != wanted:
                return None
            endpoint = ENDPOINTS[vendor].format(slug=slug)
            count = _jobs(vendor, fetch(endpoint, timeout))
        except Exception:  # 404, timeout, bad JSON: this guess is simply wrong
            return None
        if count < 1:
            return None
        return {"vendor": vendor, "slug": slug, "endpoint": endpoint,
                "company_name": " ".join(company.split()), "jobs": count}

    guesses = [(vendor, slug) for vendor in ENDPOINTS for slug in slugs(company)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = [hit for hit in pool.map(lambda g: probe(*g), guesses) if hit]
    # Greenhouse is the only name-verified match, so it wins a tie.
    return found[0] if found else None
