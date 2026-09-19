# src/jobpilot/store/db.py
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .location import parse_location
from .models import Posting, Seeker, SeekerJob

SCHEMA = Path(__file__).parent / "schema.sql"
MAX_REASONING_CHARS = 300
# Unknown (later-milestone) statuses rank above these, as more advanced.
_STATUS_RANK = {"discovered": 0, "scored": 1, "presented": 2}

# Columns added after the original schema; migrate() adds and backfills any
# that are missing from a database created before they existed.
_LOCATION_COLUMNS = ("location_city", "location_region", "location_country")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_COMPANY_SUFFIXES = {"pvt", "ltd", "limited", "private", "inc", "plc", "llp", "india"}


def normalize_title(title: str) -> str:
    # Keep + and # so "C++ Developer" and "C# Developer" stay distinct.
    return re.sub(r"[^a-z0-9+#]+", " ", title.lower()).strip()


def normalize_company(name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).split()
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def _hash(company: str, title: str, city: str) -> str:
    # Normalisers strip "|", so the join cannot be spoofed.
    basis = "|".join((company, title, city))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def content_hash(p: Posting) -> str:
    """Identity of a real-world role, independent of which board carries it.

    Only the city component of the location goes into the hash -- a region
    or country alone (or a state code misread as a city) never does. See
    src/jobpilot/store/location.py.
    """
    city, _region, _country = parse_location(p.location)
    return _hash(normalize_company(p.company_name), normalize_title(p.title), city)


class Store:
    """All database access. No other module may call conn.execute.

    The connection is shared across threads because FastMCP runs sync tool
    functions on an anyio worker thread. One RLock guards every read and
    write, so a reader never sees another thread's uncommitted rows. Writes
    run in `with self.conn:`, which commits on success and rolls back on error.
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
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))
            self._migrate_location_columns()
            self._add_missing_columns("seekers", {"review_required": "INTEGER NOT NULL DEFAULT 1"})

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> list[str]:
        """Add any of `columns` (name -> SQL type/default) missing from `table`.

        schema.sql's CREATE TABLE IF NOT EXISTS gives a fresh database every
        column for free; an existing database needs them added by hand.
        `table` and the keys of `columns` are always internal constants, never
        caller-supplied, so building the ALTER TABLE text is safe. Returns the
        names actually added, so a caller can backfill just those.
        """
        existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        added = [c for c in columns if c not in existing]
        for column in added:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {columns[column]}")
        return added

    def _migrate_location_columns(self) -> None:
        """Add location_city/region/country to a database that predates them,
        and parse each existing row's free-text `location` into them -- or
        every old row stays a city-less identity forever.
        """
        added = self._add_missing_columns(
            "job_postings", {c: "TEXT NOT NULL DEFAULT ''" for c in _LOCATION_COLUMNS}
        )
        if not added:
            return
        for row in self.conn.execute("SELECT id, location FROM job_postings").fetchall():
            city, region, country = parse_location(row["location"])
            self.conn.execute(
                "UPDATE job_postings SET location_city=?, location_region=?, "
                "location_country=? WHERE id=?",
                (city, region, country, row["id"]),
            )

    # --- events --------------------------------------------------------
    def record_event(
        self, kind: str, detail: str,
        seeker_id: int | None = None, posting_id: int | None = None,
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO events (seeker_id, posting_id, kind, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (seeker_id, posting_id, kind, detail[:2000], _now()),
            )

    # --- seekers -------------------------------------------------------
    def add_seeker(
        self, name: str, telegram_user_id: int | None, profile_path: str,
        daily_cap: int = 20,
    ) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO seekers "
                "(name, telegram_user_id, profile_path, output_token, daily_cap) "
                "VALUES (?,?,?,?,?)",
                (name, telegram_user_id, profile_path,
                 secrets.token_urlsafe(12), daily_cap),
            )
            return int(cur.lastrowid)

    def _row_to_seeker(self, r: sqlite3.Row) -> Seeker:
        return Seeker(
            id=r["id"], name=r["name"], telegram_user_id=r["telegram_user_id"],
            profile_path=r["profile_path"], output_token=r["output_token"],
            active=bool(r["active"]), daily_cap=r["daily_cap"],
            review_required=bool(r["review_required"]),
        )

    def list_seekers(self, active_only: bool = True) -> list[Seeker]:
        sql = "SELECT * FROM seekers"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY id"
        with self._lock:
            return [self._row_to_seeker(r) for r in self.conn.execute(sql).fetchall()]

    def get_seeker(self, seeker_id: int) -> Seeker:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM seekers WHERE id = ?", (seeker_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"no seeker with id {seeker_id}")
        return self._row_to_seeker(row)

    def seeker_by_telegram_id(self, telegram_user_id: int) -> Seeker | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM seekers WHERE telegram_user_id = ? AND active = 1",
                (int(telegram_user_id),),
            ).fetchone()
        return self._row_to_seeker(row) if row else None

    # --- postings ------------------------------------------------------
    def _resolve_hash(self, company: str, title: str, city: str, region: str) -> str:
        """Content hash for a sighting whose (source, external_id) is UNKNOWN.

        A known key never comes here: its posting is already decided by its own
        key (see `upsert_posting`), so neither fallback below may reassign it.
        """
        h = _hash(company, title, city)
        if city:
            # An exact-city row wins; only fall back to a city-less row when none
            # exists, or a re-sighting of a known city listing would fold into it.
            cityless = _hash(company, title, "")
            for candidate in (h, cityless):
                if self.conn.execute(
                    "SELECT 1 FROM job_postings WHERE content_hash = ?", (candidate,)
                ).fetchone():
                    return candidate
            return h
        # No city on this sighting: adopt the role if exactly one city-specific
        # row exists for it; several cities is ambiguous, so stay separate.
        # Trade-off: if only one city is known so far, a city-less sighting of a
        # genuinely different-city role merges into it (Ruling 12).
        rows = [
            r for r in self.conn.execute(
                "SELECT company_name, content_hash, location_city, location_region "
                "FROM job_postings WHERE normalized_title = ?", (title,)
            ) if normalize_company(r["company_name"]) == company
        ]
        # A known region narrows the field first: a state-level sighting must
        # not be refused as ambiguous just because a DIFFERENT state's city is
        # also known for the same role (this is the fix for I1/the design debt
        # -- without it, "HR, IN" and a known Pune row would leave two cities
        # known and the sighting would stay separate for no good reason).
        if region:
            same_region = [r for r in rows if r["location_city"] and r["location_region"] == region]
            if len(same_region) == 1:
                return same_region[0]["content_hash"]
        all_hashes = [r["content_hash"] for r in rows]
        return all_hashes[0] if len(all_hashes) == 1 else h

    def _file_key(self, posting_id: int, p: Posting, now: str) -> None:
        self.conn.execute(
            "INSERT INTO posting_sources (posting_id, source, external_id, url, seen_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(source, external_id) DO UPDATE SET "
            "posting_id = excluded.posting_id, url = excluded.url, seen_at = excluded.seen_at",
            (posting_id, p.source, p.external_id, p.url, now),
        )

    def _file_sighting(
        self, p: Posting, company: str, title: str,
        city: str, region: str, country: str, now: str,
    ) -> int:
        """File a sighting with no row of its own yet: a brand-new key, or a
        known key just detached from a row it no longer belongs on (see
        `upsert_posting`). Resolves by content hash and may fold into an
        existing row; never touches any OTHER key already on that row."""
        h = self._resolve_hash(company, title, city, region)
        # `_resolve_hash` can return an EXISTING row's hash that this
        # sighting never actually confirmed (a city-less or region-narrowed
        # adoption). Only when the resolved hash IS this sighting's own
        # city-hash does its city/region/country get to update the row --
        # otherwise a fallback adoption would silently overwrite a role's
        # real (possibly still-unknown) identity with a stranger's, exactly
        # the disagreement the old free-text guard existed to catch.
        own_hash = _hash(company, title, city)
        if h == own_hash:
            gate_city, gate_region, gate_country = city, region, country
        else:
            gate_city, gate_region, gate_country = "", "", ""
        posting_id = int(self.conn.execute(
            """
            INSERT INTO job_postings (
                company_name, title, normalized_title, location,
                location_city, location_region, location_country,
                description, canonical_url, posted_at,
                first_seen_at, last_seen_at, content_hash
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(content_hash) DO UPDATE SET
                last_seen_at     = excluded.last_seen_at,
                title            = excluded.title,
                location         = CASE
                    WHEN ? != '' OR COALESCE(job_postings.location, '') = ''
                    THEN COALESCE(excluded.location, job_postings.location)
                    ELSE job_postings.location END,
                location_city    = CASE WHEN ? != '' THEN excluded.location_city
                                    ELSE job_postings.location_city END,
                location_region  = CASE WHEN ? != '' THEN excluded.location_region
                                    ELSE job_postings.location_region END,
                location_country = CASE WHEN ? != '' THEN excluded.location_country
                                    ELSE job_postings.location_country END,
                description      = COALESCE(excluded.description, job_postings.description),
                canonical_url    = COALESCE(job_postings.canonical_url, excluded.canonical_url),
                posted_at        = COALESCE(excluded.posted_at, job_postings.posted_at)
            RETURNING id
            """,
            (p.company_name, p.title, title, p.location,
             gate_city, gate_region, gate_country,
             p.description, p.url, p.posted_at, now, now, h,
             city, gate_city, gate_region, gate_country),
        ).fetchone()["id"])
        self._file_key(posting_id, p, now)
        return posting_id

    def upsert_posting(self, p: Posting) -> int:
        """One row per role across boards; one posting_sources row per board listing.

        (source, external_id) is the primary identity: a listing a board edits
        keeps its posting id, so seeker_jobs, scores and notifications follow it.
        The content hash is secondary: it merges the same role seen on another
        board, and if an edited listing becomes a role already known, the two
        postings are merged.

        A known key is resolved by three rules, because a wrong merge destroys
        a seeker's saved job and its score:
          A. the sighting stays on the posting its key already names;
          B. its effective location is the more specific of the sighting's and
             the stored one, so a country-only sighting loses no city;
          C. it merges only when a different posting owns that exact hash --
             and if the posting being left behind carries ANOTHER key too,
             that key is detached and re-filed on its own instead of being
             dragged into the merge (Rule C would otherwise rewrite a row a
             different key still depends on; see the R2 location redesign).
        An unknown key keeps the hash path of `_resolve_hash`.
        """
        company = normalize_company(p.company_name)
        title = normalize_title(p.title)
        city, region, country = parse_location(p.location)
        now = _now()
        with self._lock, self.conn:
            known = self.conn.execute(
                "SELECT posting_id FROM posting_sources WHERE source = ? AND external_id = ?",
                (p.source, p.external_id),
            ).fetchone()
            if known is None:
                return self._file_sighting(p, company, title, city, region, country, now)

            # Rule A: this posting is this key's, whatever else exists.
            posting_id = int(known["posting_id"])
            stored = self.conn.execute(
                "SELECT location_city, location_region, location_country, content_hash "
                "FROM job_postings WHERE id = ?", (posting_id,)
            ).fetchone()
            stored_city = stored["location_city"]
            # Rule B: a sighting with no city token adds no location
            # information, so the stored city and its rendering both stay;
            # a sighting that has one wins.
            eff_city = city if (city or not stored_city) else stored_city
            h = _hash(company, title, eff_city)

            if h != stored["content_hash"]:
                # This sighting's own facts (city and/or title) would move
                # the row's identity away from what it currently is. If the
                # row is shared with another key, moving it -- in place, or
                # by merging into an existing collision -- would rewrite
                # that bystander's identity out from under it. Detach
                # instead: only this key moves, whether or not a
                # destination already exists.
                other_key_exists = self.conn.execute(
                    "SELECT 1 FROM posting_sources WHERE posting_id = ? "
                    "AND NOT (source = ? AND external_id = ?)",
                    (posting_id, p.source, p.external_id),
                ).fetchone() is not None
                if other_key_exists:
                    self.conn.execute(
                        "DELETE FROM posting_sources WHERE source = ? AND external_id = ?",
                        (p.source, p.external_id),
                    )
                    return self._file_sighting(p, company, title, city, region, country, now)

            # Rule C: merge only on an exact collision — the edited listing
            # has become the same company, title and city as another row.
            other = self.conn.execute(
                "SELECT id FROM job_postings WHERE content_hash = ? AND id != ?",
                (h, posting_id),
            ).fetchone()
            if other is not None:
                self._merge_postings(keep_id=int(other["id"]), drop_id=posting_id)
                posting_id = int(other["id"])

            if city or not stored_city:
                eff_location, eff_region, eff_country = p.location, region, country
            else:
                eff_location = None
                eff_region, eff_country = stored["location_region"], stored["location_country"]

            self.conn.execute(
                """
                UPDATE job_postings SET
                    last_seen_at     = ?,
                    company_name     = ?,
                    title            = ?,
                    normalized_title = ?,
                    content_hash     = ?,
                    location         = COALESCE(?, location),
                    location_city    = ?,
                    location_region  = ?,
                    location_country = ?,
                    description      = COALESCE(?, description),
                    canonical_url    = COALESCE(canonical_url, ?),
                    posted_at        = COALESCE(?, posted_at)
                WHERE id = ?
                """,
                (now, p.company_name, p.title, title, h, eff_location,
                 eff_city, eff_region, eff_country, p.description,
                 p.url, p.posted_at, posting_id),
            )
            self._file_key(posting_id, p, now)
            return posting_id

    def _merge_postings(self, keep_id: int, drop_id: int) -> None:
        """Fold drop into keep. Caller holds the lock and the transaction."""
        c = self.conn
        c.execute("UPDATE posting_sources SET posting_id = ? WHERE posting_id = ?",
                  (keep_id, drop_id))
        c.execute(
            "UPDATE job_postings SET first_seen_at = MIN(first_seen_at, "
            "(SELECT first_seen_at FROM job_postings WHERE id = ?)) WHERE id = ?",
            (drop_id, keep_id))

        # seeker_jobs: one row per seeker, keeping the more advanced status.
        for r in c.execute("SELECT * FROM seeker_jobs WHERE posting_id = ?",
                           (drop_id,)).fetchall():
            mine = c.execute(
                "SELECT status FROM seeker_jobs WHERE seeker_id = ? AND posting_id = ?",
                (r["seeker_id"], keep_id)).fetchone()
            if mine is None:
                c.execute("INSERT INTO seeker_jobs (seeker_id, posting_id, status, "
                          "first_seen_at) VALUES (?,?,?,?)",
                          (r["seeker_id"], keep_id, r["status"], r["first_seen_at"]))
            elif _STATUS_RANK.get(r["status"], 3) > _STATUS_RANK.get(mine["status"], 3):
                c.execute("UPDATE seeker_jobs SET status = ? "
                          "WHERE seeker_id = ? AND posting_id = ?",
                          (r["status"], r["seeker_id"], keep_id))
        c.execute("DELETE FROM seeker_jobs WHERE posting_id = ?", (drop_id,))

        # scores: one row per seeker, keeping the later scored_at.
        for r in c.execute("SELECT * FROM scores WHERE posting_id = ?", (drop_id,)).fetchall():
            mine = c.execute(
                "SELECT scored_at FROM scores WHERE seeker_id = ? AND posting_id = ?",
                (r["seeker_id"], keep_id)).fetchone()
            if mine is not None and mine["scored_at"] >= r["scored_at"]:
                c.execute("DELETE FROM scores WHERE seeker_id = ? AND posting_id = ?",
                          (r["seeker_id"], drop_id))
                continue
            if mine is not None:
                c.execute("DELETE FROM scores WHERE seeker_id = ? AND posting_id = ?",
                          (r["seeker_id"], keep_id))
            c.execute("UPDATE scores SET posting_id = ? WHERE seeker_id = ? AND posting_id = ?",
                      (keep_id, r["seeker_id"], drop_id))

        # applications: one row per seeker; an applied one beats one that is not.
        for r in c.execute("SELECT * FROM applications WHERE posting_id = ?",
                           (drop_id,)).fetchall():
            mine = c.execute(
                "SELECT applied_at FROM applications WHERE seeker_id = ? AND posting_id = ?",
                (r["seeker_id"], keep_id)).fetchone()
            if mine is not None and (mine["applied_at"] or not r["applied_at"]):
                c.execute("DELETE FROM applications WHERE seeker_id = ? AND posting_id = ?",
                          (r["seeker_id"], drop_id))
                continue
            if mine is not None:
                c.execute("DELETE FROM applications WHERE seeker_id = ? AND posting_id = ?",
                          (r["seeker_id"], keep_id))
            c.execute("UPDATE applications SET posting_id = ? "
                      "WHERE seeker_id = ? AND posting_id = ?",
                      (keep_id, r["seeker_id"], drop_id))

        for table in ("notifications", "events", "resume_versions"):
            c.execute(f"UPDATE {table} SET posting_id = ? WHERE posting_id = ?",
                      (keep_id, drop_id))
        c.execute("DELETE FROM job_postings WHERE id = ?", (drop_id,))

    # --- seeker/posting links -----------------------------------------
    def link_posting(self, seeker_id: int, posting_id: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO seeker_jobs (seeker_id, posting_id, first_seen_at) "
                "VALUES (?,?,?)",
                (seeker_id, posting_id, _now()),
            )

    def is_linked(self, seeker_id: int, posting_id: int) -> bool:
        with self._lock:
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
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            SeekerJob(
                seeker_id=r["seeker_id"], posting_id=r["posting_id"],
                status=r["status"], title=r["title"],
                company_name=r["company_name"], url=r["url"],
            )
            for r in rows
        ]

    # --- scores --------------------------------------------------------
    def record_score(
        self, seeker_id: int, posting_id: int, fit_score: float,
        reasoning: str, model: str | None = None,
    ) -> None:
        if not 0 <= float(fit_score) <= 100:
            raise ValueError(f"fit_score must be between 0 and 100, got {fit_score}")
        clean = re.sub(r"https?://\S+", "[link removed]", reasoning or "")
        clean = " ".join(clean.split())[:MAX_REASONING_CHARS]

        with self._lock, self.conn:
            if not self.is_linked(seeker_id, posting_id):
                raise ValueError(
                    f"posting {posting_id} is not linked to seeker {seeker_id}; "
                    "refusing to write a score that no query would ever see"
                )
            self.conn.execute(
                """
                INSERT INTO scores (seeker_id, posting_id, fit_score, reasoning, model, scored_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(seeker_id, posting_id) DO UPDATE SET
                    fit_score = excluded.fit_score,
                    reasoning = excluded.reasoning,
                    model     = excluded.model,
                    scored_at = excluded.scored_at
                """,
                (seeker_id, posting_id, float(fit_score), clean, model, _now()),
            )
            self.conn.execute(
                "UPDATE seeker_jobs SET status = 'scored' "
                "WHERE seeker_id = ? AND posting_id = ? AND status = 'discovered'",
                (seeker_id, posting_id),
            )

    def unscored_jobs(self, seeker_id: int, limit: int) -> list[SeekerJob]:
        limit = max(0, int(limit))  # SQLite treats a negative LIMIT as unlimited
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT sj.seeker_id, sj.posting_id, sj.status, jp.title,
                       jp.company_name, jp.canonical_url AS url
                FROM seeker_jobs sj
                JOIN job_postings jp ON jp.id = sj.posting_id
                LEFT JOIN scores s
                       ON s.seeker_id = sj.seeker_id AND s.posting_id = sj.posting_id
                WHERE sj.seeker_id = ? AND s.posting_id IS NULL
                ORDER BY sj.posting_id
                LIMIT ?
                """,
                (seeker_id, limit),
            ).fetchall()
        return [
            SeekerJob(
                seeker_id=r["seeker_id"], posting_id=r["posting_id"],
                status=r["status"], title=r["title"],
                company_name=r["company_name"], url=r["url"],
            )
            for r in rows
        ]

    def posting_description(self, posting_id: int) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT description FROM job_postings WHERE id = ?", (posting_id,)
            ).fetchone()
        return row["description"] if row else None

    # --- notifications -------------------------------------------------
    def record_notification(
        self, seeker_id: int, posting_id: int, channel: str, kind: str,
        message_ref: str | None = None,
    ) -> None:
        if kind not in ("first", "nudge", "expiry"):
            raise ValueError(f"unknown notification kind: {kind}")
        with self._lock, self.conn:
            if not self.is_linked(seeker_id, posting_id):
                raise ValueError(
                    f"posting {posting_id} is not linked to seeker {seeker_id}; "
                    "refusing to record a notification that would suppress a job "
                    "the seeker has not been shown"
                )
            self.conn.execute(
                "INSERT INTO notifications "
                "(seeker_id, posting_id, channel, kind, delivered_at, message_ref) "
                "VALUES (?,?,?,?,?,?)",
                (seeker_id, posting_id, channel, kind, _now(), message_ref),
            )
            self.conn.execute(
                "UPDATE seeker_jobs SET status = 'presented' "
                "WHERE seeker_id = ? AND posting_id = ? AND status = 'scored'",
                (seeker_id, posting_id),
            )

    def notifiable_postings(
        self, seeker_id: int, min_score: float, limit: int
    ) -> list[SeekerJob]:
        limit = max(0, int(limit))  # SQLite treats a negative LIMIT as unlimited
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT sj.seeker_id, sj.posting_id, sj.status, jp.title,
                       jp.company_name, jp.canonical_url AS url,
                       s.fit_score, s.reasoning
                FROM seeker_jobs sj
                JOIN job_postings jp ON jp.id = sj.posting_id
                JOIN scores s
                  ON s.seeker_id = sj.seeker_id AND s.posting_id = sj.posting_id
                WHERE sj.seeker_id = ?
                  AND s.fit_score >= ?
                  AND NOT EXISTS (
                        SELECT 1 FROM notifications n
                        WHERE n.seeker_id = sj.seeker_id
                          AND n.posting_id = sj.posting_id
                          AND n.kind = 'first'
                  )
                ORDER BY s.fit_score DESC, sj.posting_id
                LIMIT ?
                """,
                (seeker_id, min_score, limit),
            ).fetchall()
        return [
            SeekerJob(
                seeker_id=r["seeker_id"], posting_id=r["posting_id"],
                status=r["status"], title=r["title"],
                company_name=r["company_name"], url=r["url"],
                fit_score=r["fit_score"], reasoning=r["reasoning"],
            )
            for r in rows
        ]

    # --- source health -------------------------------------------------
    def record_source_run(
        self, source: str, canary_ok: bool, canary_count: int, raw_count: int,
        results_count: int, status: str, notes: str = "",
        latency_ms: int | None = None, http_status_summary: str | None = None,
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO source_runs (source, run_at, canary_ok, canary_count, "
                "raw_count, results_count, latency_ms, http_status_summary, status, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (source, _now(), int(canary_ok), canary_count, raw_count,
                 results_count, latency_ms, http_status_summary, status, notes[:2000]),
            )

    def recent_counts(self, source: str, limit: int) -> list[int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT results_count FROM source_runs WHERE source = ? AND status = 'healthy' "
                "AND results_count > 0 "
                "ORDER BY id DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        return [int(r["results_count"]) for r in rows]

    def source_health_summary(self, since: str | None = None) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT source, run_at, canary_ok, canary_count, raw_count, "
                "       results_count, status, notes "
                "FROM source_runs WHERE id IN "
                "(SELECT MAX(id) FROM source_runs GROUP BY source) "
                "ORDER BY source"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # A source whose last run predates the window is stale, not absent.
            d["stale"] = bool(since is not None and d["run_at"] < since)
            out.append(d)
        return out

    # --- companies -----------------------------------------------------
    def sync_companies(self, rows: list) -> int:
        with self._lock, self.conn:
            for r in rows:
                self.conn.execute(
                    "INSERT INTO companies (name, tier, ats_vendor, careers_url, region) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "  tier = excluded.tier, ats_vendor = excluded.ats_vendor, "
                    "  careers_url = excluded.careers_url, region = excluded.region",
                    (r.name, r.tier, r.ats_vendor, r.careers_url, r.region),
                )
        return len(rows)

    def match_company(self, company_name: str) -> int | None:
        """Match a board's company string to a target employer.

        Boards list 'Marks & Spencer Reliance India Pvt Ltd'; the target list
        says 'Marks & Spencer'. Whole-word substring matching on a normalised form is
        crude but right for a curated list of fewer than a hundred names.
        """
        needle = re.sub(r"[^a-z0-9]+", " ", (company_name or "").lower()).strip()
        if not needle:
            return None
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, name FROM companies WHERE enabled = 1"
            ).fetchall()
        for row in rows:
            target = re.sub(r"[^a-z0-9]+", " ", row["name"].lower()).strip()
            if target and f" {target} " in f" {needle} ":
                return int(row["id"])
        return None

    def attach_company(self, posting_id: int, company_id: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE job_postings SET company_id = ? WHERE id = ?",
                (company_id, posting_id),
            )
