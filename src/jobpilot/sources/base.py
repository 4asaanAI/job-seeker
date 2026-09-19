# src/jobpilot/sources/base.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from ..store.models import Posting

MAX_DESCRIPTION_CHARS = 16_000
ALLOWED_URL_SCHEMES = ("http", "https")


class SourceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    ERROR = "error"


def safe_url(value: str | None) -> str | None:
    """Reject any scheme other than http(s).

    Job URLs are scraped from the open internet and later rendered into an
    href that a seeker is invited to click. Autoescaping protects the
    attribute's syntax, not its scheme.
    """
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        return None
    return value.strip()


@dataclass(frozen=True)
class SourceResult:
    source: str
    status: SourceStatus
    canary_ok: bool
    canary_count: int = 0
    raw_count: int = 0
    postings: list[Posting] = field(default_factory=list)
    latency_ms: int | None = None
    notes: str = ""
