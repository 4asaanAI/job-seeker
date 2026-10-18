"""Structured JobPosting JSON-LD source for ordinary career pages."""
from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.request import Request, urlopen

from ..config import StructuredConfig
from ..store.models import Posting
from .base import MAX_DESCRIPTION_CHARS, safe_url


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._script = False
        self._chunks: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        kind = dict(attrs).get("type", "") or ""
        if "ld+json" in kind.casefold():
            self._script = True
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script:
            self.blocks.append("".join(self._chunks))
            self._script = False
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._script:
            self._chunks.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._script:
            self._chunks.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._script:
            self._chunks.append(f"&#{name};")


def _default_fetcher(url: str, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": "JobPilot/0.4"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - config URL
        if getattr(response, "status", 200) >= 400:
            raise RuntimeError(f"structured endpoint returned HTTP {response.status}")
        return response.read().decode("utf-8", errors="replace")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or value.get("value") or ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(_text(item) for item in value)
    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def _types(item: dict[str, Any]) -> set[str]:
    value = item.get("@type", [])
    if isinstance(value, str):
        value = [value]
    result = set()
    for item in value:
        text = str(item).casefold()
        result.add(text)
        result.add(re.split(r"[/#]", text)[-1])
    return result


def _job_items(html_text: str) -> list[dict[str, Any]]:
    parser = _JsonLdParser()
    parser.feed(html_text)
    result: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if "jobposting" in _types(value):
                result.append(value)
            for key in ("@graph", "itemListElement", "mainEntity"):
                if key in value:
                    walk(value[key])

    for block in parser.blocks:
        try:
            walk(json.loads(block))
        except (TypeError, json.JSONDecodeError):
            continue
    return result


def _location(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    locations = []
    for item in values:
        if not isinstance(item, dict):
            text = _text(item)
        else:
            address = item.get("address", item)
            if isinstance(address, dict):
                text = ", ".join(
                    part for part in (
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                        address.get("addressCountry"),
                    ) if part
                )
            else:
                text = _text(address)
        if text:
            locations.append(_text(text))
    return "; ".join(dict.fromkeys(locations))


def _to_posting(item: dict[str, Any], cfg: StructuredConfig) -> Posting | None:
    url = safe_url(_text(item.get("url")))
    raw_id = item.get("identifier")
    if isinstance(raw_id, dict):          # schema.org PropertyValue: {"name": <company>, "value": <job id>}
        raw_id = raw_id.get("value") if raw_id.get("value") not in (None, "") else raw_id.get("name")
    identifier = _text(raw_id)
    if not identifier and url:
        identifier = "url:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    title = _text(item.get("title"))
    organization = item.get("hiringOrganization")
    company = _text(organization) or _text(cfg.company_name)
    if not identifier or not title or not company:
        return None
    return Posting(
        company_name=company,
        source=cfg.name,
        external_id=identifier,
        title=title,
        location=_location(item.get("jobLocation")) or None,
        description=_text(item.get("description"))[:MAX_DESCRIPTION_CHARS] or None,
        url=url,
        posted_at=_text(item.get("dateModified") or item.get("datePosted")) or None,
    )


class JsonLdSource:
    """Fetch one configured career page and locally search its JobPosting JSON-LD."""

    def __init__(
        self,
        cfg: StructuredConfig,
        fetcher: Callable[[str, float], str] | None = None,
    ):
        self.cfg = cfg
        self.name = cfg.name
        self._fetcher = fetcher or _default_fetcher
        self._rows: list[dict[str, Any]] | None = None
        self._postings: list[Posting] | None = None

    def _load(self) -> tuple[list[dict[str, Any]], list[Posting]]:
        if self._rows is None or self._postings is None:
            rows = _job_items(self._fetcher(
                self.cfg.endpoint, self.cfg.fetch_timeout_seconds
            ))
            postings = [
                posting for row in rows
                if (posting := _to_posting(row, self.cfg)) is not None
            ]
            self._rows, self._postings = rows, postings
        return self._rows, self._postings

    def canary(self) -> tuple[bool, int, int]:
        rows, postings = self._load()
        return len(postings) >= self.cfg.canary_min_results, len(rows), len(postings)

    def search(self, term: str, location: str | None) -> tuple[list[Posting], int]:
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
