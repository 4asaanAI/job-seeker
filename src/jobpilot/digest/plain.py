"""Plain-text digest output for chat channels such as Telegram."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..sources.base import safe_url
from ..store.db import Store


@dataclass(frozen=True)
class PlainDigest:
    text: str
    posting_ids: tuple[int, ...]
    recorded: bool


def render_plain_digest(
    store: Store,
    seeker_id: int,
    min_score: float,
    limit: int,
    *,
    channel: str = "telegram",
    record: bool = False,
    stale_after_hours: float = 24.0,
) -> PlainDigest:
    """Build a safe chat message without HTML or scraped markup."""
    seeker = store.get_seeker(seeker_id)
    since = (datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)).isoformat()
    health = store.source_health_summary(since=since)
    unhealthy = [h for h in health if h["status"] != "healthy" or h["stale"]]
    jobs = store.notifiable_postings(seeker_id, min_score, limit)

    lines = [f"JobPilot update for {seeker.name}"]
    if not health:
        lines.append("Source health: no source run recorded; an empty list is not trustworthy.")
    elif unhealthy:
        lines.append("Source health: incomplete — some sources are unavailable or stale.")
        for item in unhealthy:
            lines.append(f"- {item['source']}: {item['status']}")
    else:
        lines.append("Source health: all recorded sources healthy.")

    if not jobs:
        lines.append("No new roles cleared the configured score threshold.")
        return PlainDigest("\n".join(lines), (), False)

    lines.append(f"\n{len(jobs)} role(s) worth reviewing:")
    for index, job in enumerate(jobs, start=1):
        lines.extend([
            f"\n{index}. {job.title} — {job.company_name} ({job.fit_score:.0f}/100)",
            job.reasoning or "No reasoning recorded.",
        ])
        url = safe_url(job.url)
        if url:
            lines.append(url)
        lines.append(f"Posting ID: {job.posting_id}")
    lines.append("\nReply with the posting ID and your decision; the store is the source of truth.")

    posting_ids = tuple(job.posting_id for job in jobs)
    if record:
        for posting_id in posting_ids:
            store.record_notification(seeker_id, posting_id, channel, "first")
    return PlainDigest("\n".join(lines), posting_ids, record)
