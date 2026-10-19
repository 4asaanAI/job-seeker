# src/jobpilot/store/db.py
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .location import parse_location
from .models import Posting, Seeker, SeekerJob

SCHEMA = Path(__file__).parent / "schema.sql"
MAX_REASONING_CHARS = 300
# Unknown (later-milestone) statuses rank above these, as more advanced.
_STATUS_RANK = {"discovered": 0, "scored": 1, "presented": 2}
JOB_STATUSES = {
    "discovered", "scored", "shortlisted", "presented", "applied",
    "skipped", "deferred", "expired",
}
LOCATION_REPAIR_VERSION = 1

APPLICATION_STATUSES = {
    "started", "applied", "withdrawn", "rejected", "interview", "offer",
}

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

    `with self.conn:` is not reentrant: a helper called from inside one
    write's block (`_file_sighting`, `_file_key`, `_merge_postings`,
    `_add_missing_columns`, ...) must never open its own -- it always relies
    on its caller already holding one.
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
            # The location/hash backfill re-reads every posting, so it runs once per version: user_version is only
            # raised AFTER it succeeds, so a half-finished upgrade still repairs itself on the next start. Bump
            # LOCATION_REPAIR_VERSION whenever parse_location or the content hash changes.
            if self.conn.execute("PRAGMA user_version").fetchone()[0] < LOCATION_REPAIR_VERSION:
                self._migrate_location_columns()
                self.conn.execute(f"PRAGMA user_version = {int(LOCATION_REPAIR_VERSION)}")
            self._add_missing_columns("seekers", {"review_required": "INTEGER NOT NULL DEFAULT 1"})
            self._add_missing_columns("job_postings", {"valid_through": "TEXT"})

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
        and repair every row whose parsed location or content hash is stale.

        The column DDL may survive a process failure even when the backfill
        does not.  Re-evaluate the data on every migration so a half-finished
        upgrade repairs itself.  Hashes are staged through temporary unique
        values before the new hashes are assigned: parser changes can turn
        two old identities into one, and that collision must use the normal
        merge path so source keys and seeker history survive.
        """
        self._add_missing_columns(
            "job_postings", {c: "TEXT NOT NULL DEFAULT ''" for c in _LOCATION_COLUMNS}
        )

        rows = self.conn.execute(
            "SELECT id, company_name, normalized_title, location, "
            "location_city, location_region, location_country, content_hash "
            "FROM job_postings ORDER BY id"
        ).fetchall()
        repairs = []
        for row in rows:
            city, region, country = parse_location(row["location"])
            expected_hash = _hash(
                normalize_company(row["company_name"]),
                row["normalized_title"],
                city,
            )
            if (
                (row["location_city"], row["location_region"], row["location_country"])
                != (city, region, country)
                or row["content_hash"] != expected_hash
            ):
                repairs.append((row, city, region, country, expected_hash))

        if not repairs:
            return

        # DDL is the only non-transactional part of this migration.  All
        # repairs below are inside migrate()'s surrounding transaction.
        for row, city, region, country, _expected_hash in repairs:
            self.conn.execute(
                "UPDATE job_postings SET location_city=?, location_region=?, "
                "location_country=? WHERE id=?",
                (city, region, country, row["id"]),
            )

        # Free every desired hash before assigning it.  This handles parser
        # changes that would otherwise produce a transient UNIQUE conflict.
        for row, _city, _region, _country, _expected_hash in repairs:
            self.conn.execute(
                "UPDATE job_postings SET content_hash=? WHERE id=?",
                (f"__migration__{row['id']}", row["id"]),
            )

        for row, _city, _region, _country, expected_hash in repairs:
            other = self.conn.execute(
                "SELECT id FROM job_postings WHERE content_hash = ?",
                (expected_hash,),
            ).fetchone()
            if other is not None and other["id"] != row["id"]:
                self._merge_postings(keep_id=int(other["id"]), drop_id=row["id"])
                continue
            self.conn.execute(
                "UPDATE job_postings SET content_hash=? WHERE id=?",
                (expected_hash, row["id"]),
            )

    # --- automatically found employer feeds ---------------------------
    def add_company_feed(self, name: str, vendor: str, company_name: str, endpoint: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO company_feeds VALUES (?, ?, ?, ?, ?)",
                (name, vendor, company_name, endpoint, _now()))

    def company_feeds(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM company_feeds ORDER BY name").fetchall()
        return [dict(row) for row in rows]

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

    def event_count_since(self, seeker_id: int, kind: str, hours: int) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._lock:
            return int(self.conn.execute(
                "SELECT COUNT(*) FROM events WHERE seeker_id = ? AND kind = ? "
                "AND created_at >= ?", (seeker_id, kind, since)).fetchone()[0])

    # --- seekers -------------------------------------------------------
    def add_seeker(
        self, name: str, telegram_user_id: int | None, profile_path: str,
        daily_cap: int = 20,
    ) -> int:
        with self._lock, self.conn:
            folder = os.path.dirname(profile_path.replace("\\", "/")).lower()
            for (other,) in self.conn.execute("SELECT profile_path FROM seekers").fetchall():
                if folder and os.path.dirname(other.replace("\\", "/")).lower() == folder:
                    # resume, rubric and uploads are found from the profile's folder: sharing one would share files
                    raise ValueError("that profile folder already belongs to another seeker; use a new folder")
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

    def link_telegram(self, seeker_id: int, telegram_user_id: int) -> bool:
        """Attach a Telegram account to a seeker that has none; False if refused."""
        with self._lock, self.conn:
            if self.conn.execute("SELECT 1 FROM seekers WHERE telegram_user_id = ?",
                                 (int(telegram_user_id),)).fetchone():
                return False
            cur = self.conn.execute(
                "UPDATE seekers SET telegram_user_id = ? WHERE id = ? "
                "AND telegram_user_id IS NULL AND active = 1",
                (int(telegram_user_id), seeker_id))
            return cur.rowcount == 1

    def best_matches(self, seeker_id: int, limit: int = 10, open_only: bool = False) -> list[dict]:
        """This seeker's highest-scored linked jobs, whether or not already sent."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT jp.id AS posting_id, jp.title, jp.company_name, jp.location, "
                "       jp.canonical_url AS url, s.fit_score, s.reasoning, sj.status "
                "FROM scores s JOIN seeker_jobs sj "
                "  ON sj.seeker_id = s.seeker_id AND sj.posting_id = s.posting_id "
                "JOIN job_postings jp ON jp.id = s.posting_id "
                "WHERE s.seeker_id = ? AND (jp.valid_through IS NULL OR jp.valid_through >= date('now')) "
                "AND jp.last_seen_at >= date('now', '-45 days') "
                + ("AND sj.status NOT IN ('skipped', 'applied', 'expired') AND NOT EXISTS (SELECT 1 FROM applications a "
                   "WHERE a.seeker_id = s.seeker_id AND a.posting_id = s.posting_id AND a.status != 'withdrawn') "
                   if open_only else "")
                + "ORDER BY s.fit_score DESC, s.scored_at DESC LIMIT ?",
                (seeker_id, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def merge_seekers(self, keep: int, drop: int) -> dict[str, int]:
        """Fold seeker `drop` into `keep` (same person, two profiles).

        Every per-seeker row moves to `keep`; where both have a row for the
        same key (a job scored twice) `keep`'s wins. `drop` loses its
        Telegram link to `keep` and is deactivated, not deleted.
        """
        if keep == drop:
            raise ValueError("cannot merge a seeker into itself")
        moved: dict[str, int] = {}
        with self._lock, self.conn:
            rows = {r["id"]: r for r in self.conn.execute(
                "SELECT id, telegram_user_id FROM seekers WHERE id IN (?, ?)", (keep, drop))}
            if set(rows) != {keep, drop}:
                raise ValueError("both seekers must exist")
            tables = [r["name"] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")]
            for table in tables:  # internal names from sqlite_master, not caller input
                columns = {c["name"] for c in self.conn.execute(f"PRAGMA table_info({table})")}
                if "seeker_id" not in columns or table == "seekers":
                    continue
                cur = self.conn.execute(
                    f"UPDATE OR IGNORE {table} SET seeker_id = ? WHERE seeker_id = ?", (keep, drop))
                moved[table] = cur.rowcount
                self.conn.execute(f"DELETE FROM {table} WHERE seeker_id = ?", (drop,))
            telegram = rows[keep]["telegram_user_id"] or rows[drop]["telegram_user_id"]
            self.conn.execute(
                "UPDATE seekers SET telegram_user_id = NULL, active = 0 WHERE id = ?", (drop,))
            self.conn.execute(
                "UPDATE seekers SET telegram_user_id = ?, active = 1 WHERE id = ?", (telegram, keep))
        return moved

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
        # A known region narrows the field first: a state-level sighting may
        # adopt the sole known city in that region even when a DIFFERENT
        # state's city is also known for the same role. This deliberately
        # widens adoption compared with the old all-cities ambiguity rule;
        # when two cities are known in the same region, it remains separate.
        # That is the documented trade-off for the real "HR, IN" shape: one
        # fewer duplicate when the region is informative, but no guess when
        # the region itself still contains multiple known cities.
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
            # Whether THIS sighting's own facts would move the row's identity
            # away from what it currently is. content_hash is UNIQUE, so
            # when this is False no other row can possibly own `h` either --
            # Rule C's merge below can only ever fire when this is True.
            identity_moving = h != stored["content_hash"]

            if identity_moving:
                # If the row is shared with another key, moving it -- in
                # place, or by merging into an existing collision -- would
                # rewrite that bystander's identity out from under it.
                # Detach instead: only this key moves, whether or not a
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

            eff_location = p.location if (city or not stored_city) else None
            if identity_moving:
                # The row's identity is genuinely (re)established here --
                # this sighting's own region/country are authoritative.
                eff_region, eff_country = region, country
            else:
                # Nothing about the row's identity is changing: this
                # sighting is only re-confirming or adding a rendering of
                # the SAME role. Region is second-order identity that
                # _resolve_hash reads to steer future city-less adoptions
                # (independent-review finding), so a re-sighting must only
                # ever ENRICH a blank value, never overwrite an
                # already-settled one -- a mistaken or stale region on one
                # board must not silently repaint the row and steer a
                # later, genuinely-different sighting onto it.
                eff_region = region if not stored["location_region"] else stored["location_region"]
                eff_country = country if not stored["location_country"] else stored["location_country"]

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

    def unscored_jobs(self, seeker_id: int, limit: int, max_age_days: int = 30) -> list[SeekerJob]:
        """Newest first; jobs not seen for `max_age_days` are likely closed."""
        limit = max(0, int(limit))  # SQLite treats a negative LIMIT as unlimited
        since = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT sj.seeker_id, sj.posting_id, sj.status, jp.title,
                       jp.company_name, jp.canonical_url AS url, jp.location
                FROM seeker_jobs sj
                JOIN job_postings jp ON jp.id = sj.posting_id
                LEFT JOIN scores s
                       ON s.seeker_id = sj.seeker_id AND s.posting_id = sj.posting_id
                WHERE sj.seeker_id = ? AND s.posting_id IS NULL AND jp.last_seen_at >= ?
                  AND sj.status NOT IN ('skipped', 'applied', 'expired')
                  AND (jp.valid_through IS NULL OR jp.valid_through >= date('now'))
                ORDER BY sj.posting_id DESC
                LIMIT ?
                """,
                (seeker_id, since, limit),
            ).fetchall()
        return [
            SeekerJob(
                seeker_id=r["seeker_id"], posting_id=r["posting_id"],
                status=r["status"], title=r["title"],
                company_name=r["company_name"], url=r["url"], location=r["location"],
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
                "UPDATE seeker_jobs SET status = CASE WHEN ? = 'expiry' "
                "THEN CASE WHEN status IN ('applied') THEN status ELSE 'expired' END WHEN ? = 'first' AND status = 'scored' "
                "THEN 'presented' ELSE status END "
                "WHERE seeker_id = ? AND posting_id = ?",
                (kind, kind, seeker_id, posting_id),
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
                  AND sj.status NOT IN ('skipped', 'applied', 'expired')
                  AND (jp.valid_through IS NULL OR jp.valid_through >= date('now'))
                  AND jp.last_seen_at >= date('now', '-45 days')
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

    def needs_description(self, source: str, external_id: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT p.description FROM posting_sources s JOIN job_postings p ON p.id = s.posting_id "
                "WHERE s.source = ? AND s.external_id = ?", (source, external_id)).fetchone()
        return row is not None and not row["description"]

    def set_description(self, source: str, external_id: str, text: str) -> None:
        """Fill a missing description; never overwrites one already stored."""
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE job_postings SET description = ? WHERE description IS NULL AND id = "
                "(SELECT posting_id FROM posting_sources WHERE source = ? AND external_id = ?)",
                (text, source, external_id))

    def recent_counts(self, source: str, limit: int) -> list[int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT results_count FROM source_runs WHERE source = ? AND status IN ('healthy', 'degraded') "
                "AND results_count > 0 AND notes NOT LIKE '[partial]%' "
                "ORDER BY id DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        return [int(r["results_count"]) for r in rows]

    def source_health_summary(self, since: str | None = None) -> list[dict]:
        """since must be the exact isoformat _now() produces (with the
        `+00:00` offset) -- it is compared as a plain string against
        `run_at`, so any other format silently never marks a row stale.
        The digest builds it as (now - a staleness window).isoformat().
        """
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

    # --- M2 seeker views and watchlist --------------------------------
    def add_target_company(self, seeker_id: int, name: str,
                           weight: float = 1.0) -> int:
        """Add a named employer to one seeker's watchlist and return its id."""
        clean = " ".join((name or "").split())
        if not clean:
            raise ValueError("target company name must not be empty")
        if not 0 < float(weight) <= 10:
            raise ValueError("target company weight must be between 0 and 10")
        with self._lock, self.conn:
            self.get_seeker(seeker_id)
            row = self.conn.execute(
                "SELECT id FROM companies WHERE lower(name) = lower(?)",
                (clean,),
            ).fetchone()
            if row is None:
                cur = self.conn.execute(
                    "INSERT INTO companies (name, tier, ats_vendor, careers_url, region) "
                    "VALUES (?, NULL, '', '', '')",
                    (clean,),
                )
                company_id = int(cur.lastrowid)
            else:
                company_id = int(row["id"])
            exists = self.conn.execute(
                "SELECT 1 FROM seeker_targets WHERE seeker_id = ? AND company_id = ?",
                (seeker_id, company_id),
            ).fetchone()
            if exists is None:
                self.conn.execute(
                    "INSERT INTO seeker_targets "
                    "(seeker_id, company_id, weight, added_at) VALUES (?,?,?,?)",
                    (seeker_id, company_id, float(weight), _now()),
                )
            return company_id

    def target_company_names(self, seeker_id: int) -> list[str]:
        """Return one seeker's watched company names in insertion order."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT c.name FROM seeker_targets st "
                "JOIN companies c ON c.id = st.company_id "
                "WHERE st.seeker_id = ? ORDER BY st.added_at, c.name",
                (seeker_id,),
            ).fetchall()
        return [str(r["name"]) for r in rows]

    def target_company(self, seeker_id: int, company_id: int) -> dict | None:
        """Return a company only when it belongs to this seeker's watchlist."""
        with self._lock:
            row = self.conn.execute(
                "SELECT c.id, c.name, c.ats_vendor, c.careers_url, c.region, "
                "       st.weight, st.added_at "
                "FROM seeker_targets st JOIN companies c ON c.id = st.company_id "
                "WHERE st.seeker_id = ? AND c.id = ?",
                (seeker_id, company_id),
            ).fetchone()
        return dict(row) if row else None

    def seeker_status(self, seeker_id: int) -> dict:
        """Return this seeker's weekly discovery, scoring and delivery counts."""
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self._lock:
            self.get_seeker(seeker_id)
            found = self.conn.execute(
                "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ? "
                "AND first_seen_at >= ?", (seeker_id, since)
            ).fetchone()[0]
            scored = self.conn.execute(
                "SELECT COUNT(*) FROM scores WHERE seeker_id = ? "
                "AND scored_at >= ?", (seeker_id, since)
            ).fetchone()[0]
            shown = self.conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE seeker_id = ? "
                "AND delivered_at >= ?", (seeker_id, since)
            ).fetchone()[0]
            applied = self.conn.execute(
                "SELECT COUNT(*) FROM applications WHERE seeker_id = ? AND status IN ('applied', 'interview', 'offer') "
                "AND applied_at >= ?", (seeker_id, since)
            ).fetchone()[0]
            total_jobs = self.conn.execute(
                "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?",
                (seeker_id,),
            ).fetchone()[0]
        return {
            "seeker_id": seeker_id,
            "window": "last_7_days",
            "found": int(found),
            "scored": int(scored),
            "shown": int(shown),
            "applied": int(applied),
            "total_jobs": int(total_jobs),
        }

    def seeker_history(self, seeker_id: int, query: str = "",
                       limit: int = 20) -> list[dict]:
        """Return notification history for one seeker, newest first."""
        limit = max(0, min(int(limit), 100))
        needle = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._lock:
            self.get_seeker(seeker_id)
            rows = self.conn.execute(
                "SELECT n.posting_id, n.kind, n.channel, n.delivered_at, "
                "       jp.title, jp.company_name, jp.canonical_url AS url, "
                "       GROUP_CONCAT(DISTINCT ps.source) AS sources "
                "FROM notifications n JOIN job_postings jp ON jp.id = n.posting_id "
                "LEFT JOIN posting_sources ps ON ps.posting_id = jp.id "
                "WHERE n.seeker_id = ? AND (? = '' OR jp.title LIKE ? ESCAPE '\\' "
                "   OR jp.company_name LIKE ? ESCAPE '\\' OR ps.source LIKE ? ESCAPE '\\') "
                "GROUP BY n.id ORDER BY n.delivered_at DESC, n.id DESC LIMIT ?",
                (seeker_id, query.strip(), needle, needle, needle, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_event_details(self, seeker_id: int, kind: str, hours: int, limit: int = 50) -> list[tuple[str, str]]:
        """(detail, created_at), newest first."""
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._lock:
            return [(r["detail"], r["created_at"]) for r in self.conn.execute(
                "SELECT detail, created_at FROM events WHERE seeker_id = ? AND kind = ? AND created_at >= ? "
                "ORDER BY id DESC LIMIT ?", (seeker_id, kind, since, int(limit)))]

    def set_review_required(self, seeker_id: int, required: bool) -> None:
        with self._lock, self.conn:
            self.get_seeker(seeker_id)
            self.conn.execute("UPDATE seekers SET review_required = ? WHERE id = ?", (int(bool(required)), seeker_id))

    def max_source_run_id(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM source_runs").fetchone()[0])

    def mark_partial_runs_since(self, run_id: int) -> None:
        """Runs made for a subset of seekers say nothing about a source's normal volume: keep them out of its history."""
        with self._lock, self.conn:
            self.conn.execute("UPDATE source_runs SET notes = '[partial] ' || notes WHERE id > ? AND notes NOT LIKE '[partial]%'",
                              (run_id,))

    def event_total_since(self, kind: str, hours: int) -> int:
        """Events of one kind across all seekers (used for global daily caps)."""
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._lock:
            return int(self.conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ? AND created_at >= ?", (kind, since)).fetchone()[0])

    def last_event_times(self, kind: str) -> dict[str, str]:
        """detail -> most recent time, for rotating through work fairly."""
        with self._lock:
            return {r["detail"]: r["t"] for r in self.conn.execute(
                "SELECT detail, MAX(created_at) AS t FROM events WHERE kind = ? GROUP BY detail", (kind,))}

    def set_valid_through(self, posting_id: int, value: str | None) -> None:
        """Record a closing date (any ISO date or datetime; stored as YYYY-MM-DD, the last open day)."""
        day = (value or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return                                   # 2026-13-45 is not a deadline
        with self._lock, self.conn:
            prior = self.conn.execute("SELECT valid_through, date('now') AS today FROM job_postings WHERE id = ?",
                                      (posting_id,)).fetchone()
            self.conn.execute("UPDATE job_postings SET valid_through = ? WHERE id = ?", (day, posting_id))
            if prior and prior["valid_through"] and prior["valid_through"] < prior["today"] <= day:
                # the source extended the deadline: bring back jobs that were expired only because of the old date
                self.conn.execute("UPDATE seeker_jobs SET status = 'scored' WHERE posting_id = ? AND status = 'expired'",
                                  (posting_id,))

    def expire_past_deadlines(self) -> int:
        """Mark every seeker's job expired once its closing date has passed (never a job already applied to)."""
        with self._lock, self.conn:
            cur = self.conn.execute(
                "UPDATE seeker_jobs SET status = 'expired' WHERE status NOT IN ('expired', 'applied', 'skipped') "
                "AND posting_id IN (SELECT id FROM job_postings WHERE valid_through IS NOT NULL "
                "AND valid_through < date('now')) "
                "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.seeker_id = seeker_jobs.seeker_id "
                "AND a.posting_id = seeker_jobs.posting_id AND a.status IN ('applied','interview','offer'))")
            return cur.rowcount

    def source_urls(self, posting_id: int) -> list[str]:
        """Every URL a (merged) posting was seen under, oldest first: the same job can sit on a login board and on the
        company's own form, and the caller picks the best one."""
        with self._lock:
            rows = self.conn.execute("SELECT url FROM posting_sources WHERE posting_id = ? AND url IS NOT NULL "
                                     "ORDER BY seen_at", (int(posting_id),)).fetchall()
        return [r["url"] for r in rows]

    def posting_view(self, seeker_id: int, posting_id: int) -> dict | None:
        """Return a posting only if it is linked to this seeker."""
        with self._lock:
            row = self.conn.execute(
                "SELECT jp.id, jp.title, jp.company_name, jp.location, "
                "       jp.description, jp.canonical_url AS url, jp.posted_at, "
                "       jp.first_seen_at, jp.last_seen_at, "
                "       GROUP_CONCAT(DISTINCT ps.source) AS sources "
                "FROM seeker_jobs sj JOIN job_postings jp ON jp.id = sj.posting_id "
                "LEFT JOIN posting_sources ps ON ps.posting_id = jp.id "
                "WHERE sj.seeker_id = ? AND jp.id = ? GROUP BY jp.id",
                (seeker_id, posting_id),
            ).fetchone()
        return dict(row) if row else None

    # --- resume versions ----------------------------------------------
    def record_resume_version(
        self,
        seeker_id: int,
        posting_id: int,
        path: str,
        format: str,
        validation_report: dict,
        extraction_ok: bool,
    ) -> int:
        """Record a generated resume only for a posting linked to this seeker."""
        if not self.is_linked(seeker_id, posting_id):
            raise ValueError(
                f"posting {posting_id} is not linked to seeker {seeker_id}; "
                "refusing to record a resume for an unrelated job"
            )
        if format not in ("docx", "pdf"):
            raise ValueError(f"unsupported resume format: {format}")
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO resume_versions "
                "(seeker_id, posting_id, path, format, generated_at, "
                "validation_report, extraction_ok) VALUES (?,?,?,?,?,?,?)",
                (seeker_id, posting_id, path, format, _now(),
                 json.dumps(validation_report, ensure_ascii=False, sort_keys=True),
                 int(extraction_ok)),
            )
            return int(cur.lastrowid)

    def resume_versions(self, seeker_id: int, posting_id: int | None = None) -> list[dict]:
        """Return only resume records belonging to this seeker."""
        sql = (
            "SELECT id, seeker_id, posting_id, path, format, generated_at, "
            "validation_report, extraction_ok FROM resume_versions "
            "WHERE seeker_id = ?"
        )
        params: list[object] = [seeker_id]
        if posting_id is not None:
            sql += " AND posting_id = ?"
            params.append(posting_id)
        sql += " ORDER BY id DESC"
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["validation_report"] = json.loads(item["validation_report"] or "{}")
            item["extraction_ok"] = bool(item["extraction_ok"])
            result.append(item)
        return result

    def seeker_sheet_rows(self, seeker_id: int) -> list[dict]:
        """Return a one-way, seeker-scoped view suitable for Sheets export."""
        with self._lock:
            self.get_seeker(seeker_id)
            rows = self.conn.execute(
                "SELECT sj.posting_id, jp.title, jp.company_name, jp.location, "
                "       jp.canonical_url AS url, sj.status, sj.first_seen_at, "
                "       s.fit_score, s.reasoning, a.status AS application_status "
                "FROM seeker_jobs sj JOIN job_postings jp ON jp.id = sj.posting_id "
                "LEFT JOIN scores s ON s.seeker_id = sj.seeker_id "
                "   AND s.posting_id = sj.posting_id "
                "LEFT JOIN applications a ON a.seeker_id = sj.seeker_id "
                "   AND a.posting_id = sj.posting_id "
                "WHERE sj.seeker_id = ? ORDER BY sj.first_seen_at DESC, sj.posting_id DESC",
                (seeker_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- machine run ledger -------------------------------------------
    def start_machine_run(self, machine: str) -> int:
        """Start one scheduler run and return its durable ledger ID."""
        machine = " ".join(str(machine or "").split())
        if not machine:
            raise ValueError("machine must not be empty")
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO machine_runs (machine, started_at, status) VALUES (?,?,?)",
                (machine, _now(), "running"),
            )
            return int(cur.lastrowid)

    def finish_machine_run(self, run_id: int, status: str) -> None:
        if status not in ("completed", "error"):
            raise ValueError(f"unknown machine run status: {status}")
        with self._lock, self.conn:
            updated = self.conn.execute(
                "UPDATE machine_runs SET finished_at = ?, status = ? WHERE id = ?",
                (_now(), status, int(run_id)),
            ).rowcount
            if updated != 1:
                raise ValueError(f"no machine run with id {run_id}")

    def latest_machine_run(self, machine: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT id, machine, started_at, finished_at, status "
                "FROM machine_runs WHERE machine = ? ORDER BY id DESC LIMIT 1",
                (machine,),
            ).fetchone()
        return dict(row) if row else None

    # --- seeker actions and applications ------------------------------
    def set_job_status(self, seeker_id: int, posting_id: int, status: str) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(f"unknown job status: {status}")
        with self._lock, self.conn:
            updated = self.conn.execute(
                "UPDATE seeker_jobs SET status = ? WHERE seeker_id = ? AND posting_id = ?",
                (status, seeker_id, posting_id),
            ).rowcount
            if updated != 1:
                raise ValueError(
                    f"posting {posting_id} is not linked to seeker {seeker_id}"
                )
            self.conn.execute(
                "INSERT INTO events (seeker_id, posting_id, kind, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (seeker_id, posting_id, "job_status", status, _now()),
            )

    def record_application(
        self,
        seeker_id: int,
        posting_id: int,
        status: str,
        notes: str = "",
        resume_version_id: int | None = None,
    ) -> None:
        if status not in APPLICATION_STATUSES:
            raise ValueError(f"unknown application status: {status}")
        clean_notes = " ".join(str(notes or "").split())[:1000]
        with self._lock, self.conn:
            if not self.is_linked(seeker_id, posting_id):
                raise ValueError(
                    f"posting {posting_id} is not linked to seeker {seeker_id}"
                )
            if resume_version_id is not None:
                valid = self.conn.execute(
                    "SELECT 1 FROM resume_versions WHERE id = ? AND seeker_id = ? "
                    "AND posting_id = ? AND extraction_ok = 1",
                    (resume_version_id, seeker_id, posting_id),
                ).fetchone()
                if valid is None:
                    raise ValueError("resume_version_id is not a validated version for this posting")
            existing = self.conn.execute(
                "SELECT status FROM applications WHERE seeker_id = ? AND posting_id = ?",
                (seeker_id, posting_id)).fetchone()
            if existing and existing["status"] in ("interview", "offer", "rejected") and status in ("started", "applied"):
                return                          # a later outcome is never rolled back to an earlier stage
            applied_at = _now() if status == "applied" else None
            response_at = _now() if status in ("interview", "offer", "rejected") else None
            self.conn.execute(
                "INSERT INTO applications "
                "(seeker_id, posting_id, status, resume_version_id, applied_at, notes, response_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(seeker_id, posting_id) DO UPDATE SET "
                "response_at=COALESCE(excluded.response_at, applications.response_at), "
                "status=excluded.status, "
                "resume_version_id=COALESCE(excluded.resume_version_id, applications.resume_version_id), "
                "applied_at=COALESCE(excluded.applied_at, applications.applied_at), "
                "notes=CASE WHEN excluded.notes = '' THEN applications.notes ELSE excluded.notes END",
                (seeker_id, posting_id, status, resume_version_id, applied_at, clean_notes, response_at),
            )
            if status == "applied":
                self.conn.execute(
                    "UPDATE seeker_jobs SET status = 'applied' "
                    "WHERE seeker_id = ? AND posting_id = ?",
                    (seeker_id, posting_id),
                )
            self.conn.execute(
                "INSERT INTO events (seeker_id, posting_id, kind, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (seeker_id, posting_id, "application", status, _now()),
            )

    def applications(self, seeker_id: int, limit: int = 100) -> list[dict]:
        limit = max(0, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute(
                "SELECT a.seeker_id, a.posting_id, a.status, a.resume_version_id, "
                "       a.applied_at, a.response_at, a.notes, jp.title, "
                "       jp.company_name, jp.canonical_url AS url "
                "FROM applications a JOIN job_postings jp ON jp.id = a.posting_id "
                "WHERE a.seeker_id = ? ORDER BY COALESCE(a.applied_at, '') DESC, "
                "a.posting_id DESC LIMIT ?",
                (seeker_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def tracked_jobs(self, seeker_id: int, min_score: float, today: str, open_limit: int = 100,
                     closed_limit: int = 15, closed_days: int = 14) -> list[dict]:
        """Jobs for the tracker sheet: good open matches, and good jobs whose deadline passed in the last `closed_days`.

        Open means the same as best_matches(open_only=True) (not skipped, applied or nudged out; seen in the last 45
        days). Closed needs a real deadline before `today`. A job with any application row is the application list's
        business, never listed here. `today` is YYYY-MM-DD and is passed in so the result never depends on the clock."""
        base = ("SELECT jp.id AS posting_id, jp.title, jp.company_name, s.fit_score, sj.status AS seeker_status, "
                "       sj.first_seen_at, jp.valid_through, jp.canonical_url AS url, {closed} AS closed "
                "FROM scores s JOIN seeker_jobs sj ON sj.seeker_id = s.seeker_id AND sj.posting_id = s.posting_id "
                "JOIN job_postings jp ON jp.id = s.posting_id "
                "WHERE s.seeker_id = ? AND s.fit_score >= ? "
                "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.seeker_id = s.seeker_id AND a.posting_id = s.posting_id) ")
        with self._lock:
            opened = self.conn.execute(
                base.format(closed=0) + "AND (jp.valid_through IS NULL OR jp.valid_through >= ?) "
                "AND sj.status NOT IN ('skipped', 'applied', 'expired') AND jp.last_seen_at >= date(?, '-45 days') "
                "ORDER BY s.fit_score DESC, sj.first_seen_at DESC, jp.id DESC LIMIT ?",
                (seeker_id, float(min_score), today, today, max(0, int(open_limit)))).fetchall()
            closed = self.conn.execute(
                base.format(closed=1) + "AND jp.valid_through IS NOT NULL AND jp.valid_through < ? "
                "AND jp.valid_through >= date(?, ?) AND sj.status NOT IN ('skipped', 'applied') "
                "ORDER BY jp.valid_through DESC, s.fit_score DESC, jp.id DESC LIMIT ?",
                (seeker_id, float(min_score), today, today, f"-{int(closed_days)} days", max(0, int(closed_limit)))).fetchall()
        return [dict(r) for r in opened] + [dict(r) for r in closed]

    def _open_tracker_claim(self, seeker_id: int, minutes: int) -> int | None:
        """The id of the latest refresh claim that started within `minutes` and has no matching done event, else None."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        row = self.conn.execute("SELECT id, created_at FROM events WHERE seeker_id = ? AND kind = 'tracker_pending' "
                                "ORDER BY id DESC LIMIT 1", (seeker_id,)).fetchone()
        if row is None or row["created_at"] < cutoff:
            return None
        done = self.conn.execute("SELECT 1 FROM events WHERE seeker_id = ? AND kind = 'tracker_done' AND detail = ?",
                                 (seeker_id, str(row["id"]))).fetchone()
        return None if done else int(row["id"])

    def tracker_busy(self, seeker_id: int, minutes: int = 30) -> bool:
        """Read-only: is a refresh of the sheet still running?"""
        with self._lock:
            return self._open_tracker_claim(seeker_id, minutes) is not None

    def claim_tracker(self, seeker_id: int, minutes: int = 30) -> int | None:
        """Take the one lease for refreshing the tracker sheet; returns the claim id, or None when a refresh started in
        the last `minutes` has not finished (two Fo turns writing one sheet would duplicate rows). Atomic across processes."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if self._open_tracker_claim(seeker_id, minutes) is not None:
                    self.conn.execute("ROLLBACK")
                    return None
                cur = self.conn.execute(
                    "INSERT INTO events (seeker_id, posting_id, kind, detail, created_at) VALUES (?,?,?,?,?)",
                    (seeker_id, None, "tracker_pending", "refresh", _now()))
                self.conn.execute("COMMIT")
                return int(cur.lastrowid)
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    def finish_tracker(self, seeker_id: int, claim_id: int) -> None:
        """Release exactly this claim (a late finisher cannot release somebody else's)."""
        self.record_event("tracker_done", str(claim_id), seeker_id)

    def tracker_link(self, seeker_id: int) -> str | None:
        """The saved link to this seeker's tracker sheet (taken from Fo's own reply), unless it was reset."""
        with self._lock:
            row = self.conn.execute(
                "SELECT kind, detail FROM events WHERE seeker_id = ? AND kind IN ('tracker_link', 'tracker_link_reset') "
                "ORDER BY id DESC LIMIT 1", (seeker_id,)).fetchone()
        return row["detail"] if row and row["kind"] == "tracker_link" else None

    def pin_tracker_link(self, seeker_id: int, url: str) -> bool:
        """Save the link the first time only (True when it was saved); a later, different one is never swapped in."""
        if self.tracker_link(seeker_id):
            return False
        self.record_event("tracker_link", url, seeker_id)
        return True

    def reset_tracker_link(self, seeker_id: int) -> None:
        self.record_event("tracker_link_reset", "", seeker_id)

    def pending_followups(self, seeker_id: int, limit: int = 5) -> list[dict]:
        """Return deliberate 3-day nudges and 7-day expiry candidates."""
        limit = max(0, min(int(limit), 100))
        now = datetime.now(timezone.utc)
        nudge_cutoff = (now - timedelta(days=3)).isoformat()
        expiry_cutoff = (now - timedelta(days=7)).isoformat()
        with self._lock:
            self.get_seeker(seeker_id)
            rows = self.conn.execute(
                "SELECT n.posting_id, MIN(n.delivered_at) AS first_delivered_at, "
                "       jp.title, jp.company_name, jp.canonical_url AS url, "
                "       s.fit_score, s.reasoning, sj.status "
                "FROM notifications n JOIN job_postings jp ON jp.id = n.posting_id "
                "JOIN seeker_jobs sj ON sj.seeker_id = n.seeker_id "
                "   AND sj.posting_id = n.posting_id "
                "LEFT JOIN scores s ON s.seeker_id = n.seeker_id "
                "   AND s.posting_id = n.posting_id "
                "WHERE n.seeker_id = ? AND n.kind = 'first' "
                "  AND n.delivered_at <= ? "
                "  AND sj.status NOT IN ('skipped', 'applied', 'expired') "
                "  AND (jp.valid_through IS NULL OR jp.valid_through >= date('now')) "
                "  AND NOT EXISTS (SELECT 1 FROM notifications later "
                "                  WHERE later.seeker_id = n.seeker_id "
                "                    AND later.posting_id = n.posting_id "
                "                    AND later.kind = 'expiry') "
                "GROUP BY n.posting_id ORDER BY first_delivered_at ASC LIMIT ?",
                (seeker_id, nudge_cutoff, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["kind"] = "expiry" if item["first_delivered_at"] <= expiry_cutoff else "nudge"
            if item["kind"] == "nudge" and self.conn.execute(
                    "SELECT 1 FROM notifications WHERE seeker_id = ? AND posting_id = ? AND kind = 'nudge'",
                    (seeker_id, item["posting_id"])).fetchone():
                continue                       # already nudged; wait for the 7-day expiry
            result.append(item)
        return result
