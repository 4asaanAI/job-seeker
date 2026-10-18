"""Push each seeker's scored digest to their own chat channel (Telegram or Fo)."""
from __future__ import annotations

from typing import Callable

from .digest.plain import render_plain_digest
from .store.db import Store
from .telegram_bot import _log


def _telegram_recipient(seeker) -> int | None:
    return int(seeker.telegram_user_id) if seeker.telegram_user_id else None


PENDING_WINDOW_HOURS = 0.5


def _unconfirmed_send(store: Store, seeker_id: int, channel: str) -> bool:
    """True when an earlier Fo send timed out or was cut off and may still complete: wait before sending again."""
    if channel == "telegram":
        return False
    pending = store.recent_event_details(seeker_id, "fo_send_pending", PENDING_WINDOW_HOURS, 1)
    if not pending:
        return False
    done = store.recent_event_details(seeker_id, "fo_send_done", PENDING_WINDOW_HOURS, 1)
    return not done or done[0][1] < pending[0][1]


def _mark_pending(store: Store, seeker_id: int, channel: str, ids) -> None:
    if channel != "telegram":
        store.record_event("fo_send_pending", ",".join(str(i) for i in ids), seeker_id=seeker_id)


def _mark_resolved(store: Store, seeker_id: int, channel: str, outcome: str) -> None:
    if channel != "telegram":
        store.record_event("fo_send_done", outcome, seeker_id=seeker_id)


def _require_one_seeker(channel: str, seeker_ids) -> None:
    """One Fo account belongs to one person: never fan out to it."""
    if channel != "telegram" and (seeker_ids is None or len(seeker_ids) != 1):
        raise ValueError(f"channel {channel!r} needs exactly one seeker id")


def push_digests(store: Store, send: Callable[[int, str], object],
                 min_score: float = 70.0, limit: int = 5, *,
                 channel: str = "telegram", seeker_ids=None, recipient=None,
                 render=render_plain_digest) -> int:
    """Send one digest per seeker with new matches; returns how many were sent.

    A notification is recorded only after its message was delivered (send
    returned without raising), so a failed send is retried on the next run.
    """
    _require_one_seeker(channel, seeker_ids)
    recipient = recipient or _telegram_recipient
    sent = 0
    for seeker in store.list_seekers():
        if seeker_ids is not None and seeker.id not in seeker_ids:
            continue
        to = recipient(seeker)
        if to is None:
            continue
        try:
            digest = render(store, seeker.id, min_score, limit,
                            channel=channel, record=False)
            if not digest.posting_ids:
                continue
            if _unconfirmed_send(store, seeker.id, channel):
                _log(f"digest for seeker {seeker.id} skipped: an earlier send is still unconfirmed")
                continue
            _mark_pending(store, seeker.id, channel, digest.posting_ids)
            try:
                ref = send(to, digest.text)
            except Exception as exc:
                if "(working)" not in str(exc):          # a clear failure: nothing was sent, so retry freely
                    _mark_resolved(store, seeker.id, channel, "failed")
                raise
            _mark_resolved(store, seeker.id, channel, "delivered")
            for posting_id in digest.posting_ids:
                store.record_notification(seeker.id, posting_id, channel, "first",
                                          ref if isinstance(ref, str) else None)
            sent += 1
        except Exception as exc:
            _log(f"digest for seeker {seeker.id} failed: {type(exc).__name__}: {exc}")
    return sent


def push_followups(store: Store, send: Callable[[int, str], object],
                   render: Callable[[list[dict]], str], *,
                   channel: str, seeker_ids, limit: int = 5) -> int:
    """One batched follow-up message per seeker; recorded only after delivery."""
    _require_one_seeker(channel, seeker_ids)
    sent = 0
    for seeker_id in seeker_ids:
        try:
            items = store.pending_followups(seeker_id, limit)
            if not items:
                continue
            if _unconfirmed_send(store, seeker_id, channel):
                _log(f"follow-ups for seeker {seeker_id} skipped: an earlier send is still unconfirmed")
                continue
            _mark_pending(store, seeker_id, channel, [i["posting_id"] for i in items])
            try:
                ref = send(seeker_id, render(items))
            except Exception as exc:
                if "(working)" not in str(exc):
                    _mark_resolved(store, seeker_id, channel, "failed")
                raise
            _mark_resolved(store, seeker_id, channel, "delivered")
            for item in items:
                store.record_notification(seeker_id, item["posting_id"], channel,
                                          item["kind"], ref if isinstance(ref, str) else None)
            sent += 1
        except Exception as exc:
            _log(f"follow-ups for seeker {seeker_id} failed: {type(exc).__name__}: {exc}")
    return sent


def push_job_lists(store: Store, send_list: Callable[[int, int, bool], bool], skip=()) -> int:
    """Send each linked seeker their spreadsheet when new jobs arrived today (not those served through Fo)."""
    sent = 0
    for seeker in store.list_seekers():
        if not seeker.telegram_user_id or seeker.id in skip:
            continue
        uid = int(seeker.telegram_user_id)
        try:
            sent += bool(send_list(uid, uid, True))
        except Exception as exc:
            _log(f"job list for seeker {seeker.id} failed: {type(exc).__name__}: {exc}")
    return sent
