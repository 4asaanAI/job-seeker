"""Separate-process boundary for an operator-provided Wigolo JSON wrapper.

Wigolo is AGPL-3.0 and is intentionally not imported, vendored, or bundled.
The configured executable must implement the small argv/stdout contract below:
it accepts ``--url URL --query TERM [--location LOCATION] --format json`` and
prints either a JSON list or ``{"jobs": [...]}`` to stdout.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime
from typing import Any, Callable

from ..config import WigoloConfig
from ..paths import jobpilot_home
from ..store.models import Posting
from .base import MAX_DESCRIPTION_CHARS, safe_url


def _posting(row: dict[str, Any], cfg: WigoloConfig) -> Posting | None:
    external_id = str(row.get("external_id") or row.get("id") or "").strip()
    title = str(row.get("title") or "").strip()
    company = str(row.get("company_name") or row.get("company") or cfg.company_name).strip()
    url = safe_url(str(row.get("url") or row.get("job_url") or ""))
    if not external_id and url:
        external_id = "url:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    if not external_id or not title or not company:
        return None
    description = str(row.get("description") or "").strip() or None
    if description:
        description = description[:MAX_DESCRIPTION_CHARS]
    posted_at = str(row.get("posted_at") or row.get("date_posted") or "").strip() or None
    return Posting(
        company_name=company,
        source=cfg.name,
        external_id=external_id,
        title=title,
        location=str(row.get("location") or "").strip() or None,
        description=description,
        url=url,
        posted_at=posted_at,
    )


class WigoloSource:
    """Run an external crawl command without importing or shelling through it."""

    def __init__(self, cfg: WigoloConfig, runner: Callable[..., Any] | None = None):
        self.cfg = cfg
        self.name = cfg.name
        self._runner = runner or subprocess.run

    def _run(self, term: str, location: str | None) -> tuple[list[Posting], int]:
        home = str(jobpilot_home())
        base = [part.replace("{python}", sys.executable).replace("{home}", home)
                for part in self.cfg.command]
        command = [*base, "--url", self.cfg.endpoint,
                   "--query", term, "--format", "json"]
        if location:
            command.extend(["--location", location])
        completed = self._runner(
            command, capture_output=True, text=True, check=False,
            timeout=self.cfg.fetch_timeout_seconds,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Wigolo wrapper exited {completed.returncode}: "
                f"{(completed.stderr or '').strip()[:500]}"
            )
        try:
            payload = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Wigolo wrapper returned invalid JSON") from exc
        rows = payload if isinstance(payload, list) else payload.get("jobs", [])
        if not isinstance(rows, list):
            raise RuntimeError("Wigolo wrapper JSON must contain a jobs list")
        postings = [
            posting for row in rows if isinstance(row, dict)
            if (posting := _posting(row, self.cfg)) is not None
        ]
        return postings, len(rows)

    def canary(self) -> tuple[bool, int, int]:
        postings, raw = self._run(self.cfg.canary_term, self.cfg.canary_location)
        return len(postings) >= self.cfg.canary_min_results, raw, len(postings)

    def search(self, term: str, location: str | None) -> tuple[list[Posting], int]:
        return self._run(term, location)
