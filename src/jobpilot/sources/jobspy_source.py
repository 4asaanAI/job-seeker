# src/jobpilot/sources/jobspy_source.py
from __future__ import annotations

import random
import time
from typing import Any, Callable

from ..config import SiteConfig
from ..store.models import Posting
from .base import MAX_DESCRIPTION_CHARS, safe_url


def _default_scraper(**kwargs: Any) -> list[dict]:
    from jobspy import scrape_jobs

    df = scrape_jobs(**kwargs)
    if df is None or len(df) == 0:
        return []
    return df.fillna("").to_dict("records")


def _default_sleeper(seconds: float) -> None:
    time.sleep(seconds)


class JobSpySource:
    """One instance per source (a site in a market, e.g. indeed_us).

    cfg.name is the source key used for health records; cfg.site is the
    JobSpy site name passed to the scraper.

    scrape_jobs collects its futures with future.result() and no try/except,
    so a single failing scraper aborts a multi-site call and discards every
    other site's results. One site per call keeps failures contained.
    """

    def __init__(
        self,
        cfg: SiteConfig,
        scraper: Callable[..., list[dict]] | None = None,
        sleeper: Callable[[float], None] | None = None,
        min_request_gap_seconds: float = 4.0,
    ):
        self.cfg = cfg
        self.name = cfg.name
        self._scraper = scraper or _default_scraper
        self._sleep = sleeper or _default_sleeper
        self._gap = min_request_gap_seconds

    def _pause(self) -> None:
        """Jittered delay. Politeness is what keeps the source available."""
        self._sleep(self._gap + random.uniform(0, self._gap / 2))

    def _scrape(
        self, term: str, location: str | None, wanted: int, hours_old: int | None
    ) -> list[dict]:
        self._pause()
        kwargs: dict[str, Any] = {
            "site_name": [self.cfg.site],
            "search_term": term,
            "results_wanted": wanted,
            "country_indeed": self.cfg.country_indeed,
            "linkedin_fetch_description": self.cfg.fetch_descriptions,
        }
        if location:
            kwargs["location"] = location
        if hours_old is not None:
            kwargs["hours_old"] = hours_old
        return self._scraper(**kwargs)

    @staticmethod
    def _to_posting(row: dict, fallback_site: str) -> Posting | None:
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        company = str(row.get("company") or "").strip()
        if not external_id or not title or not company:
            return None

        description = str(row.get("description") or "").strip() or None
        if description and len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS]

        return Posting(
            company_name=company,
            source=str(row.get("site") or fallback_site),
            external_id=external_id,
            title=title,
            location=str(row.get("location") or "").strip() or None,
            description=description,
            url=safe_url(str(row.get("job_url") or "")),
            posted_at=str(row.get("date_posted") or "").strip() or None,
        )

    def _normalise(self, rows: list[dict]) -> list[Posting]:
        return [p for p in (self._to_posting(r, self.cfg.site) for r in rows) if p]

    def search(self, term: str, location: str | None) -> tuple[list[Posting], int]:
        """Returns (postings, raw_row_count).

        The raw count matters: raw > 0 with zero postings means the site
        answered but our mapping is broken, which is a different failure
        from the site going dark.
        """
        rows = self._scrape(term, location, self.cfg.results_wanted, self.cfg.hours_old)
        return self._normalise(rows), len(rows)

    def canary(self) -> tuple[bool, int, int]:
        """Returns (ok, raw_count, normalized_count).

        Deliberately carries no recency filter and no location: the broadest
        query that is still meaningful. It runs through the same normalisation
        as a real search, so a column rename fails the canary instead of
        leaving it green while search() silently returns nothing.

        Raises on transport failure so the caller can distinguish "answered
        with nothing" from "the call blew up".
        """
        c = self.cfg.canary
        rows = self._scrape(c.search_term, c.location, c.results_wanted, None)
        normalized = self._normalise(rows)
        ok = len(normalized) >= c.min_results
        return ok, len(rows), len(normalized)
