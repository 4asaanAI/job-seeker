"""Push each seeker's scored digest to their own Telegram chat."""
from __future__ import annotations

from typing import Callable

from .digest.plain import render_plain_digest
from .store.db import Store
from .telegram_bot import _log


def push_digests(store: Store, send: Callable[[int, str], None],
                 min_score: float = 70.0, limit: int = 5) -> int:
    """Send one digest per seeker with new matches; returns how many were sent.

    A notification is recorded only after its message was delivered, so a
    failed send is retried on the next run instead of being lost.
    """
    sent = 0
    for seeker in store.list_seekers():
        if not seeker.telegram_user_id:
            continue
        try:
            digest = render_plain_digest(store, seeker.id, min_score, limit,
                                         channel="telegram", record=False)
            if not digest.posting_ids:
                continue
            send(int(seeker.telegram_user_id), digest.text)
            for posting_id in digest.posting_ids:
                store.record_notification(seeker.id, posting_id, "telegram", "first")
            sent += 1
        except Exception as exc:
            _log(f"digest for seeker {seeker.id} failed: {type(exc).__name__}: {exc}")
    return sent
