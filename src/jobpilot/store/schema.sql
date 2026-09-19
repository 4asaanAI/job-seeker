-- src/jobpilot/store/schema.sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    tier        INTEGER,
    ats_vendor  TEXT,
    careers_url TEXT,
    region      TEXT,
    enabled     INTEGER NOT NULL DEFAULT 1
);

-- One row per real-world role, regardless of how many boards carry it.
CREATE TABLE IF NOT EXISTS job_postings (
    id               INTEGER PRIMARY KEY,
    company_id       INTEGER REFERENCES companies(id),
    company_name     TEXT NOT NULL,
    title            TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    location         TEXT,
    description      TEXT,
    canonical_url    TEXT,
    posted_at        TEXT,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    content_hash     TEXT NOT NULL UNIQUE
);

-- Each board occurrence of that role.
CREATE TABLE IF NOT EXISTS posting_sources (
    posting_id  INTEGER NOT NULL REFERENCES job_postings(id),
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url         TEXT,
    seen_at     TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_posting_sources_posting
    ON posting_sources(posting_id);

CREATE TABLE IF NOT EXISTS seekers (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    telegram_user_id INTEGER UNIQUE,
    profile_path     TEXT NOT NULL,
    output_token     TEXT NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    daily_cap        INTEGER NOT NULL DEFAULT 20
);

CREATE TABLE IF NOT EXISTS seeker_jobs (
    seeker_id     INTEGER NOT NULL REFERENCES seekers(id),
    posting_id    INTEGER NOT NULL REFERENCES job_postings(id),
    status        TEXT NOT NULL DEFAULT 'discovered',
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (seeker_id, posting_id)
);

CREATE TABLE IF NOT EXISTS scores (
    seeker_id  INTEGER NOT NULL REFERENCES seekers(id),
    posting_id INTEGER NOT NULL REFERENCES job_postings(id),
    fit_score  REAL NOT NULL CHECK (fit_score >= 0 AND fit_score <= 100),
    reasoning  TEXT NOT NULL,
    model      TEXT,
    scored_at  TEXT NOT NULL,
    PRIMARY KEY (seeker_id, posting_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY,
    seeker_id    INTEGER NOT NULL REFERENCES seekers(id),
    posting_id   INTEGER NOT NULL REFERENCES job_postings(id),
    channel      TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('first','nudge','expiry')),
    delivered_at TEXT NOT NULL,
    message_ref  TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_lookup
    ON notifications(seeker_id, posting_id, kind);

CREATE TABLE IF NOT EXISTS source_runs (
    id                  INTEGER PRIMARY KEY,
    source              TEXT NOT NULL,
    run_at              TEXT NOT NULL,
    canary_ok           INTEGER NOT NULL,
    canary_count        INTEGER NOT NULL DEFAULT 0,
    raw_count           INTEGER NOT NULL DEFAULT 0,
    results_count       INTEGER NOT NULL,
    latency_ms          INTEGER,
    http_status_summary TEXT,
    status              TEXT NOT NULL,
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_runs_source ON source_runs(source, id DESC);

-- M3 (Mac fallback). Created now so the schema is stable.
CREATE TABLE IF NOT EXISTS machine_runs (
    id          INTEGER PRIMARY KEY,
    machine     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL
);

-- Per-seeker watchlist. M1 writes nothing here; M2's add_target_company
-- (spec 17.2) does, and discovery reads it beside profile.target_companies.
CREATE TABLE IF NOT EXISTS seeker_targets (
    seeker_id   INTEGER NOT NULL REFERENCES seekers(id),
    company_id  INTEGER REFERENCES companies(id),
    search_term TEXT,
    weight      REAL NOT NULL DEFAULT 1.0,
    added_at    TEXT NOT NULL,
    CHECK (company_id IS NOT NULL OR search_term IS NOT NULL)
);

-- M2. Created now so the schema is stable.
CREATE TABLE IF NOT EXISTS applications (
    seeker_id         INTEGER NOT NULL REFERENCES seekers(id),
    posting_id        INTEGER NOT NULL REFERENCES job_postings(id),
    status            TEXT NOT NULL,
    resume_version_id INTEGER,
    applied_at        TEXT,
    response_at       TEXT,
    notes             TEXT,
    PRIMARY KEY (seeker_id, posting_id)
);

CREATE TABLE IF NOT EXISTS resume_versions (
    id                INTEGER PRIMARY KEY,
    seeker_id         INTEGER NOT NULL REFERENCES seekers(id),
    posting_id        INTEGER REFERENCES job_postings(id),
    path              TEXT NOT NULL,
    format            TEXT NOT NULL,
    generated_at      TEXT NOT NULL,
    validation_report TEXT,
    extraction_ok     INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    seeker_id  INTEGER REFERENCES seekers(id),
    posting_id INTEGER REFERENCES job_postings(id),
    kind       TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
