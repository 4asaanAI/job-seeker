import sqlite3
import threading

import pytest

from jobpilot.store.db import Store
from jobpilot.store.models import Posting


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.migrate()
    yield s
    s.close()


def _posting(source="naukri", external_id="X1", title="Senior Merchandiser",
             company="Marks & Spencer", location="Gurgaon, India"):
    return Posting(
        company_name=company, source=source, external_id=external_id,
        title=title, location=location,
        description="Own the critical path for womenswear.",
        url=f"https://example.test/{source}/{external_id}", posted_at="2026-09-18",
    )


def test_migrate_is_idempotent(store):
    store.migrate()
    assert store.list_seekers() == []


def test_add_and_get_seeker(store):
    sid = store.add_seeker("Alex", 100000001, "data/seekers/alex/profile.yaml", 20)
    s = store.get_seeker(sid)
    assert s.telegram_user_id == 100000001
    assert s.output_token  # random, non-empty


def test_get_unknown_seeker_raises_not_stopiteration(store):
    with pytest.raises(ValueError, match="no seeker"):
        store.get_seeker(9999)


def test_list_seekers_excludes_inactive_by_default(store):
    a = store.add_seeker("Active", 1, "a.yaml", 20)
    b = store.add_seeker("Gone", 2, "b.yaml", 20)
    store.conn.execute("UPDATE seekers SET active = 0 WHERE id = ?", (b,))
    store.conn.commit()
    assert [s.id for s in store.list_seekers()] == [a]
    assert len({s.id for s in store.list_seekers(active_only=False)}) == 2


def test_upsert_same_source_and_external_id_is_one_row(store):
    assert store.upsert_posting(_posting()) == store.upsert_posting(_posting())


def test_same_role_from_two_different_sources_is_one_posting(store):
    """The headline dedupe requirement: one role, one row, two source refs."""
    a = store.upsert_posting(_posting(source="naukri", external_id="nk-1"))
    b = store.upsert_posting(_posting(source="indeed", external_id="in-9"))
    assert a == b

    rows = store.conn.execute(
        "SELECT source FROM posting_sources WHERE posting_id = ? ORDER BY source",
        (a,),
    ).fetchall()
    assert [r["source"] for r in rows] == ["indeed", "naukri"]


def test_same_role_from_three_sources_is_one_posting(store):
    ids = {
        store.upsert_posting(_posting(source=s, external_id=f"{s}-1"))
        for s in ("naukri", "indeed", "linkedin")
    }
    assert len(ids) == 1
    count = store.conn.execute("SELECT COUNT(*) c FROM job_postings").fetchone()["c"]
    assert count == 1


def test_different_roles_are_not_merged(store):
    a = store.upsert_posting(_posting(external_id="n1", title="Senior Merchandiser"))
    b = store.upsert_posting(_posting(source="indeed", external_id="i1",
                                      title="Production Manager"))
    assert a != b


def test_updating_a_posting_refreshes_derived_columns(store):
    pid = store.upsert_posting(_posting(title="Senior Merchandiser"))
    store.upsert_posting(_posting(title="Senior Merchandiser  "))
    row = store.conn.execute(
        "SELECT normalized_title, content_hash FROM job_postings WHERE id = ?", (pid,)
    ).fetchone()
    assert row["normalized_title"] == "senior merchandiser"
    assert row["content_hash"]


def test_seeker_jobs_are_isolated_when_both_seekers_have_data(store):
    """Both seekers hold rows, so an empty result cannot pass by accident."""
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    pa = store.upsert_posting(_posting(external_id="a1", title="A Role"))
    pb = store.upsert_posting(_posting(external_id="b1", title="B Role"))
    store.link_posting(a, pa)
    store.link_posting(b, pb)

    assert [j.posting_id for j in store.seeker_jobs(a)] == [pa]
    assert [j.posting_id for j in store.seeker_jobs(b)] == [pb]


def test_linking_same_posting_twice_is_idempotent(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(a, pid)
    store.link_posting(a, pid)
    assert len(store.seeker_jobs(a)) == 1


def test_store_is_usable_from_another_thread(store):
    """FastMCP runs sync tools on worker threads; a same-thread-only
    connection would raise ProgrammingError on every live call."""
    errors: list[BaseException] = []

    def work():
        try:
            store.add_seeker("Threaded", 77, "t.yaml", 5)
            store.upsert_posting(_posting(external_id="thr-1"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert errors == []
    assert any(s.name == "Threaded" for s in store.list_seekers())


def test_concurrent_writes_do_not_corrupt(store):
    def work(n):
        for i in range(20):
            store.upsert_posting(_posting(source="naukri", external_id=f"t{n}-{i}",
                                          title=f"Role {n}-{i}"))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c = store.conn.execute("SELECT COUNT(*) c FROM job_postings").fetchone()["c"]
    assert c == 80


def test_record_event_writes_a_row(store):
    store.record_event("source_error", "RuntimeError: 429")
    row = store.conn.execute("SELECT kind, detail FROM events").fetchone()
    assert row["kind"] == "source_error"
