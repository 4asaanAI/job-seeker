"""Official ATS JSON sources used by the R4 discovery layer.

The endpoint is operator configuration, never model input. Each source fetches
its endpoint once per source instance, normalizes vendor-specific records into
the existing Posting contract, and filters locally for each seeker query.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Callable
from urllib.request import Request, urlopen

from ..config import ATSConfig
from ..store.models import Posting
from .base import MAX_DESCRIPTION_CHARS, safe_url


def _default_fetcher(url: str, timeout: float) -> object:
    request = Request(url, headers={"User-Agent": "JobPilot/0.3"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - config URL
        if getattr(response, "status", 200) >= 400:
            raise RuntimeError(f"ATS endpoint returned HTTP {response.status}")
        return json.load(response)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(_text(item) for item in value)
    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def _nested(item: dict, *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return ""


def _items(payload: object, vendor: str) -> list[dict]:
    if vendor == "greenhouse":
        value = payload.get("jobs", []) if isinstance(payload, dict) else []
    elif vendor == "lever":
        value = payload if isinstance(payload, list) else []
    elif vendor == "workday":
        if isinstance(payload, dict):
            data = payload.get("data")
            nested = data.get("jobPostings", []) if isinstance(data, dict) else []
            value = payload.get("jobPostings", nested)
        else:
            value = []
    else:  # SuccessFactors commonly wraps OData records under d.results.
        if isinstance(payload, dict):
            data = payload.get("d")
            nested = data.get("results", []) if isinstance(data, dict) else []
            value = payload.get("results", nested)
        else:
            value = []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _to_posting(item: dict, cfg: ATSConfig) -> Posting | None:
    vendor = cfg.vendor
    if vendor == "greenhouse":
        external_id = _nested(item, "id", "requisition_id")
        title = _nested(item, "title", "name")
        location = _nested(item.get("location", {}), "name") if isinstance(item.get("location"), dict) else _nested(item, "location")
        url = _nested(item, "absolute_url", "url")
        description = _nested(item, "content", "description")
        posted_at = _nested(item, "updated_at", "created_at")
    elif vendor == "lever":
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        external_id = _nested(item, "id", "lever_id")
        title = _nested(item, "text", "title")
        location = _nested(categories, "location")
        url = _nested(item, "hostedUrl", "applyUrl", "url")
        description = _nested(item, "descriptionPlain", "description")
        posted_at = _nested(item, "createdAt", "updatedAt")
    elif vendor == "workday":
        external_id = _nested(item, "jobPostingId", "externalPath", "id")
        title = _nested(item, "title", "jobTitle")
        location = _nested(item, "locationsText", "location")
        url = _nested(item, "externalUrl", "url")
        description = _nested(item, "jobDescription", "description", "bulletFields")
        posted_at = _nested(item, "postedOn", "postedDate", "updatedDate")
    else:
        external_id = _nested(item, "jobPostingId", "jobReqId", "externalCode", "id")
        title = _nested(item, "jobTitle", "title", "name")
        location = _nested(item, "jobLocation", "location", "locations")
        url = _nested(item, "jobUrl", "externalUrl", "url")
        description = _nested(item, "jobDescription", "description")
        posted_at = _nested(item, "postingDate", "createdAt", "lastModifiedDate")

    external_id = _text(external_id)
    title = _text(title)
    company = _text(cfg.company_name)
    if not external_id or not title or not company:
        return None
    description = _text(description)[:MAX_DESCRIPTION_CHARS] or None
    return Posting(
        company_name=company,
        source=cfg.name,
        external_id=external_id,
        title=title,
        location=_text(location) or None,
        description=description,
        url=safe_url(_text(url)),
        posted_at=_text(posted_at) or None,
    )


class ATSJsonSource:
    """One configured employer ATS endpoint implementing the source contract."""

    def __init__(
        self,
        cfg: ATSConfig,
        fetcher: Callable[[str, float], object] | None = None,
    ):
        self.cfg = cfg
        self.name = cfg.name
        self._fetcher = fetcher or _default_fetcher
        self._rows: list[dict] | None = None
        self._postings: list[Posting] | None = None

    def _load(self) -> tuple[list[dict], list[Posting]]:
        if self._rows is None or self._postings is None:
            payload = self._fetcher(self.cfg.endpoint, self.cfg.fetch_timeout_seconds)
            rows = _items(payload, self.cfg.vendor)
            postings = [
                posting for row in rows
                if (posting := _to_posting(row, self.cfg)) is not None
            ]
            self._rows, self._postings = rows, postings
        return self._rows, self._postings

    def canary(self) -> tuple[bool, int, int]:
        """Fetch the configured endpoint and require normalized jobs."""
        rows, postings = self._load()
        return len(postings) >= self.cfg.canary_min_results, len(rows), len(postings)

    def search(self, term: str, location: str | None) -> tuple[list[Posting], int]:
        """Filter the cached official ATS response for one seeker query."""
        rows, postings = self._load()
        wanted = (term or "").casefold().strip()
        place = (location or "").casefold().strip()
        found = []
        for posting in postings:
            haystack = " ".join((posting.title, posting.company_name,
                                  posting.description or "")).casefold()
            if wanted and wanted not in haystack:
                continue
            if place and place not in (posting.location or "").casefold():
                continue
            found.append(posting)
        return found, len(rows)
