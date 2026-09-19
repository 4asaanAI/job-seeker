# src/jobpilot/store/db.py
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Posting, Seeker, SeekerJob

SCHEMA = Path(__file__).parent / "schema.sql"
MAX_REASONING_CHARS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def normalize_location(location: str | None) -> str:
    if not location:
        return ""
    first = location.split(",")[0]
    return re.sub(r"[^a-z0-9]+", " ", first.lower()).strip()


def content_hash(p: Posting) -> str:
    """Identity of a real-world role, independent of which board carries it."""
    basis = "|".join(
        (
            re.sub(r"[^a-z0-9]+", " ", p.company_name.lower()).strip(),
            normalize_title(p.title),
            normalize_location(p.location),
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class Store:
    """All database access. No other module may call conn.execute.

    The connection is shared across threads because FastMCP runs sync tool
    functions on an anyio worker thread. A lock serialises writes; SQLite's
    own WAL mode handles durability, not Python-level concurrency.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def migrate(self) -> None:
        with self._lock:
            self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))
            self.conn.commit()

    # --- events --------------------------------------------------------
    def record_event(
        self, kind: str, detail: str,
        seeker_id: int | None = None, posting_id: int | None = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO events (seeker_id, posting_id, kind, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (seeker_id, posting_id, kind, detail[:2000], _now()),
            )
            self.conn.commit()

    # --- seekers -------------------------------------------------------
    def add_seeker(
        self, name: str, telegram_user_id: int | None, profile_path: str,
        daily_cap: int = 20,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO seekers "
                "(name, telegram_user_id, profile_path, output_token, daily_cap) "
                "VALUES (?,?,?,?,?)",
                (name, telegram_user_id, profile_path,
                 secrets.token_urlsafe(12), daily_cap),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def _row_to_seeker(self, r: sqlite3.Row) -> Seeker:
        return Seeker(
            id=r["id"], name=r["name"], telegram_user_id=r["telegram_user_id"],
            profile_path=r["profile_path"], output_token=r["output_token"],
            active=bool(r["active"]), daily_cap=r["daily_cap"],
        )

    def list_seekers(self, active_only: bool = True) -> list[Seeker]:
        sql = "SELECT * FROM seekers"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        return [self._row_to_seeker(r) for r in self.conn.execute(sql).fetchall()]

    def get_seeker(self, seeker_id: int) -> Seeker:
        row = self.conn.execute(
            "SELECT * FROM seekers WHERE id = ?", (seeker_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no seeker with id {seeker_id}")
        return self._row_to_seeker(row)

    def seeker_by_telegram_id(self, telegram_user_id: int) -> Seeker | None:
        row = self.conn.execute(
            "SELECT * FROM seekers WHERE telegram_user_id = ? AND active = 1",
            (int(telegram_user_id),),
        ).fetchone()
        return self._row_to_seeker(row) if row else None

    # --- postings ------------------------------------------------------
    def upsert_posting(self, p: Posting) -> int:
        """Dedupe by content hash across sources; record each source occurrence."""
        h = content_hash(p)
        now = _now()
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM job_postings WHERE content_hash = ?", (h,)
            ).fetchone()

            if row is None:
                cur = self.conn.execute(
                    """
                    INSERT INTO job_postings (
                        company_name, title, normalized_title, location,
                        description, canonical_url, posted_at,
                        first_seen_at, last_seen_at, content_hash
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (p.company_name, p.title, normalize_title(p.title), p.location,
                     p.description, p.url, p.posted_at, now, now, h),
                )
                posting_id = int(cur.lastrowid)
            else:
                posting_id = int(row["id"])
                # Refresh derived columns too, so they cannot drift from title.
                self.conn.execute(
                    """
                    UPDATE job_postings SET
                        last_seen_at     = ?,
                        title            = ?,
                        normalized_title = ?,
                        location         = COALESCE(?, location),
                        description      = COALESCE(?, description),
                        canonical_url    = COALESCE(canonical_url, ?),
                        posted_at        = COALESCE(?, posted_at)
                    WHERE id = ?
                    """,
                    (now, p.title, normalize_title(p.title), p.location,
                     p.description, p.url, p.posted_at, posting_id),
                )

            self.conn.execute(
                "INSERT INTO posting_sources (posting_id, source, external_id, url, seen_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(source, external_id) DO UPDATE SET seen_at = excluded.seen_at",
                (posting_id, p.source, p.external_id, p.url, now),
            )
            self.conn.commit()
            return posting_id

    # --- seeker/posting links -----------------------------------------
    def link_posting(self, seeker_id: int, posting_id: int) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO seeker_jobs (seeker_id, posting_id, first_seen_at) "
                "VALUES (?,?,?)",
                (seeker_id, posting_id, _now()),
            )
            self.conn.commit()

    def is_linked(self, seeker_id: int, posting_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM seeker_jobs WHERE seeker_id = ? AND posting_id = ?",
            (seeker_id, posting_id),
        ).fetchone() is not None

    def seeker_jobs(self, seeker_id: int, status: str | None = None) -> list[SeekerJob]:
        sql = (
            "SELECT sj.seeker_id, sj.posting_id, sj.status, jp.title, "
            "       jp.company_name, jp.canonical_url AS url "
            "FROM seeker_jobs sj JOIN job_postings jp ON jp.id = sj.posting_id "
            "WHERE sj.seeker_id = ?"
        )
        params: list[object] = [seeker_id]
        if status is not None:
            sql += " AND sj.status = ?"
            params.append(status)
        sql += " ORDER BY sj.posting_id"
        return [
            SeekerJob(
                seeker_id=r["seeker_id"], posting_id=r["posting_id"],
                status=r["status"], title=r["title"],
                company_name=r["company_name"], url=r["url"],
            )
            for r in self.conn.execute(sql, params).fetchall()
        ]
