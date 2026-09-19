# tests/test_notifications.py
import pytest

from jobpilot.store.db import Store
from jobpilot.store.models import Posting


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    yield s
    s.close()


def _posting(ext="X1", title="Senior Merchandiser"):
    return Posting(company_name="M&S", source="naukri", external_id=ext,
                   title=title, location="Gurgaon", description="d",
                   url=f"https://x.test/{ext}", posted_at="2026-09-18")


def _seed(store, name="Alex", tid=1, ext="X1", title="Senior Merchandiser",
          score=90.0):
    sid = store.add_seeker(name, tid, f"{name}.yaml", 20)
    pid = store.upsert_posting(_posting(ext, title))
    store.link_posting(sid, pid)
    store.record_score(sid, pid, score, "Strong M&S account match", "claude")
    return sid, pid


def test_unnotified_high_scoring_job_is_notifiable(store):
    sid, pid = _seed(store)
    out = store.notifiable_postings(sid, 70.0, 5)
    assert [j.posting_id for j in out] == [pid]
    assert out[0].fit_score == 90.0
    assert out[0].reasoning


def test_already_notified_job_is_not_notifiable_again(store):
    sid, pid = _seed(store)
    store.record_notification(sid, pid, "digest", "first")
    assert store.notifiable_postings(sid, 70.0, 5) == []


def test_only_a_first_notification_suppresses(store):
    """A nudge must not permanently suppress; M3 needs re-surfacing."""
    sid, pid = _seed(store)
    store.record_notification(sid, pid, "digest", "nudge")
    assert [j.posting_id for j in store.notifiable_postings(sid, 70.0, 5)] == [pid]


def test_low_scoring_job_is_not_notifiable(store):
    sid, _ = _seed(store, score=30.0)
    assert store.notifiable_postings(sid, 70.0, 5) == []


def test_notification_to_one_seeker_does_not_suppress_another(store):
    sid_a, pid = _seed(store, name="Alex", tid=1)
    sid_b = store.add_seeker("Priya", 2, "b.yaml", 20)
    store.link_posting(sid_b, pid)
    store.record_score(sid_b, pid, 85.0, "Also a fit", "claude")

    store.record_notification(sid_a, pid, "digest", "first")

    assert store.notifiable_postings(sid_a, 70.0, 5) == []
    assert [j.posting_id for j in store.notifiable_postings(sid_b, 70.0, 5)] == [pid]


def test_scoring_an_unlinked_posting_raises(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(a, pid)

    with pytest.raises(ValueError, match="not linked"):
        store.record_score(b, pid, 90.0, "wrong seeker")


def test_score_out_of_range_raises(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(sid, pid)
    with pytest.raises(ValueError, match="0 and 100"):
        store.record_score(sid, pid, 999.0, "injected")


def test_reasoning_is_capped_and_stripped_of_urls(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 80.0, "Apply at https://evil.test/phish " + "x" * 500)

    row = store.conn.execute("SELECT reasoning FROM scores").fetchone()
    assert "https://evil.test" not in row["reasoning"]
    assert len(row["reasoning"]) <= 300


def test_unscored_jobs_excludes_scored_ones(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    p1 = store.upsert_posting(_posting("n1", "Role One"))
    p2 = store.upsert_posting(_posting("n2", "Role Two"))
    store.link_posting(sid, p1)
    store.link_posting(sid, p2)
    store.record_score(sid, p1, 80.0, "scored")

    assert [j.posting_id for j in store.unscored_jobs(sid, 10)] == [p2]


def test_unscored_jobs_is_per_seeker(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    pa = store.upsert_posting(_posting("n1", "A Role"))
    pb = store.upsert_posting(_posting("n2", "B Role"))
    store.link_posting(a, pa)
    store.link_posting(b, pb)

    assert [j.posting_id for j in store.unscored_jobs(a, 10)] == [pa]
    assert [j.posting_id for j in store.unscored_jobs(b, 10)] == [pb]


def test_notifying_an_unlinked_posting_raises_and_writes_nothing(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(a, pid)

    with pytest.raises(ValueError, match="not linked"):
        store.record_notification(b, pid, "digest", "first")

    assert store.conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
    assert store.conn.in_transaction is False

    store.link_posting(b, pid)
    store.record_score(b, pid, 95.0, "Strong fit", "claude")
    assert [j.posting_id for j in store.notifiable_postings(b, 70.0, 5)] == [pid]


@pytest.mark.parametrize("limit", [-1, 0])
def test_notifiable_postings_non_positive_limit_returns_nothing(store, limit):
    """SQLite treats a negative LIMIT as unlimited; that must not leak."""
    sid, _ = _seed(store)
    assert store.notifiable_postings(sid, 70.0, limit) == []


@pytest.mark.parametrize("limit", [-1, 0])
def test_unscored_jobs_non_positive_limit_returns_nothing(store, limit):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting("n1", "Role One"))
    store.link_posting(sid, pid)
    assert store.unscored_jobs(sid, limit) == []
