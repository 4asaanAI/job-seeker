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


def test_title_and_location_are_last_writer_wins_across_sources(store):
    """Same role, different renderings: the latest non-null sighting wins."""
    pid = store.upsert_posting(_posting(source="naukri", external_id="n1",
                                        title="Senior Merchandiser",
                                        location="Gurgaon, India"))
    store.upsert_posting(_posting(source="indeed", external_id="i1",
                                  title="SENIOR MERCHANDISER",
                                  location="Gurugram, Haryana"))
    row = store.conn.execute(
        "SELECT title, location FROM job_postings WHERE id = ?", (pid,)
    ).fetchone()
    assert (row["title"], row["location"]) == ("SENIOR MERCHANDISER", "Gurugram, Haryana")

    # A sighting with no location must not erase the one we have.
    store.upsert_posting(_posting(source="linkedin", external_id="l1",
                                  title="Senior  Merchandiser", location=None))
    row = store.conn.execute(
        "SELECT title, location FROM job_postings WHERE id = ?", (pid,)
    ).fetchone()
    assert (row["title"], row["location"]) == ("Senior  Merchandiser", "Gurugram, Haryana")


def _count(store, sql, params=()):
    return store.conn.execute(sql, params).fetchone()[0]


def _score(store, sid, pid, fit, scored_at):
    store.conn.execute(
        "INSERT INTO scores (seeker_id, posting_id, fit_score, reasoning, scored_at) "
        "VALUES (?,?,?,?,?)", (sid, pid, fit, "r", scored_at))
    store.conn.commit()


def _notify(store, sid, pid, kind="first"):
    store.conn.execute(
        "INSERT INTO notifications (seeker_id, posting_id, channel, kind, delivered_at) "
        "VALUES (?,?,?,?,?)", (sid, pid, "telegram", kind, "2026-09-19T00:00:00"))
    store.conn.commit()


def test_title_edit_keeps_the_posting_id_and_leaves_no_sourceless_row(store):
    original = store.upsert_posting(_posting(title="Senior Merchandiser"))
    edited = store.upsert_posting(_posting(title="Senior Merchandiser II"))

    assert edited == original
    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 1
    rows = store.conn.execute(
        "SELECT posting_id FROM posting_sources WHERE source = 'naukri' AND external_id = 'X1'"
    ).fetchall()
    assert [r["posting_id"] for r in rows] == [original]
    assert _count(
        store,
        "SELECT COUNT(*) FROM job_postings jp WHERE NOT EXISTS "
        "(SELECT 1 FROM posting_sources ps WHERE ps.posting_id = jp.id)",
    ) == 0
    row = store.conn.execute(
        "SELECT title, normalized_title FROM job_postings WHERE id = ?", (original,)
    ).fetchone()
    assert (row["title"], row["normalized_title"]) == (
        "Senior Merchandiser II", "senior merchandiser ii")


def test_notification_suppression_survives_a_title_edit(store):
    """Headline promise: a job the seeker was told about is not re-announced
    because a board edited its title. (Store.notifiable_postings arrives in a
    later task; this asserts the state it reads: the notified posting id is
    the one the edited listing resolves to, and no new unnotified link exists.)"""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting(title="Senior Merchandiser"))
    store.link_posting(sid, pid)
    _score(store, sid, pid, 90.0, "2026-09-19T01:00:00")
    _notify(store, sid, pid, "first")

    edited = store.upsert_posting(_posting(title="Senior Merchandiser II"))
    store.link_posting(sid, edited)  # discovery links every posting it upserts

    assert edited == pid
    assert _count(store, "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?", (sid,)) == 1
    unnotified = store.conn.execute(
        "SELECT sj.posting_id FROM seeker_jobs sj WHERE sj.seeker_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM notifications n WHERE n.seeker_id = sj.seeker_id "
        "  AND n.posting_id = sj.posting_id AND n.kind = 'first')", (sid,)
    ).fetchall()
    assert unnotified == []


def _two_known_roles(store):
    """P = naukri listing of 'Sr Merch'; Q = indeed listing of 'Production Manager'."""
    p = store.upsert_posting(_posting(source="naukri", external_id="n1", title="Sr Merch"))
    q = store.upsert_posting(_posting(source="indeed", external_id="i1",
                                      title="Production Manager"))
    assert p != q
    return p, q


def test_edit_that_collides_with_a_known_role_merges_into_it(store):
    p, q = _two_known_roles(store)
    a = store.add_seeker("A", 1, "a.yaml", 20)
    b = store.add_seeker("B", 2, "b.yaml", 20)
    for sid in (a, b):
        store.link_posting(sid, p)
        store.link_posting(sid, q)
    store.conn.execute("UPDATE seeker_jobs SET status = 'presented' "
                       "WHERE seeker_id = ? AND posting_id = ?", (a, p))
    store.conn.execute("UPDATE seeker_jobs SET status = 'scored' "
                       "WHERE seeker_id = ? AND posting_id = ?", (a, q))
    store.conn.commit()
    _score(store, a, p, 80.0, "2026-09-19T09:00:00")   # later on P: P's survives for A
    _score(store, a, q, 10.0, "2026-09-19T01:00:00")
    _score(store, b, p, 20.0, "2026-09-19T01:00:00")
    _score(store, b, q, 70.0, "2026-09-19T09:00:00")   # later on Q: Q's survives for B

    merged = store.upsert_posting(_posting(source="naukri", external_id="n1",
                                           title="Production Manager"))

    assert merged == q
    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 1
    srcs = store.conn.execute(
        "SELECT source, posting_id FROM posting_sources ORDER BY source").fetchall()
    assert [(r["source"], r["posting_id"]) for r in srcs] == [("indeed", q), ("naukri", q)]
    for sid in (a, b):
        assert _count(store, "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?", (sid,)) == 1
    status = store.conn.execute(
        "SELECT status FROM seeker_jobs WHERE seeker_id = ?", (a,)).fetchone()["status"]
    assert status == "presented"
    fit = {r["seeker_id"]: r["fit_score"] for r in store.conn.execute(
        "SELECT seeker_id, fit_score FROM scores")}
    assert fit == {a: 80.0, b: 70.0}


def test_merge_moves_notification_history(store):
    p, q = _two_known_roles(store)
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    store.link_posting(sid, p)
    _notify(store, sid, p, "first")
    store.record_event("seen", "x", seeker_id=sid, posting_id=p)

    merged = store.upsert_posting(_posting(source="naukri", external_id="n1",
                                           title="Production Manager"))
    assert merged == q
    store.link_posting(sid, merged)
    notified = store.conn.execute(
        "SELECT posting_id FROM notifications WHERE seeker_id = ? AND kind = 'first'",
        (sid,)).fetchall()
    assert [r["posting_id"] for r in notified] == [q]
    assert _count(store, "SELECT COUNT(*) FROM events WHERE posting_id = ?", (q,)) == 1
    assert _count(store, "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?", (sid,)) == 1


def test_merge_colliding_applications_keeps_one_row(store):
    p, q = _two_known_roles(store)
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    store.conn.execute("INSERT INTO applications (seeker_id, posting_id, status, applied_at) "
                       "VALUES (?,?,?,?)", (sid, p, "applied", "2026-09-18"))
    store.conn.execute("INSERT INTO applications (seeker_id, posting_id, status) "
                       "VALUES (?,?,?)", (sid, q, "interested"))
    store.conn.commit()
    store.upsert_posting(_posting(source="naukri", external_id="n1", title="Production Manager"))
    rows = store.conn.execute("SELECT posting_id, status FROM applications").fetchall()
    assert [(r["posting_id"], r["status"]) for r in rows] == [(q, "applied")]


def test_failure_inside_a_merge_rolls_everything_back(store):
    p, q = _two_known_roles(store)
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    store.link_posting(sid, p)
    store.link_posting(sid, q)
    _score(store, sid, p, 80.0, "2026-09-19T09:00:00")
    _notify(store, sid, p, "first")
    # Deleting the dropped posting is the merge's last step; make it blow up.
    store.conn.execute("CREATE TRIGGER boom BEFORE DELETE ON job_postings "
                       "BEGIN SELECT RAISE(ABORT, 'boom'); END")
    store.conn.commit()

    with pytest.raises(sqlite3.DatabaseError, match="boom"):
        store.upsert_posting(_posting(source="naukri", external_id="n1",
                                      title="Production Manager"))

    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 2
    srcs = store.conn.execute(
        "SELECT source, posting_id FROM posting_sources ORDER BY source").fetchall()
    assert [(r["source"], r["posting_id"]) for r in srcs] == [("indeed", q), ("naukri", p)]
    assert _count(store, "SELECT COUNT(*) FROM seeker_jobs") == 2
    assert _count(store, "SELECT COUNT(*) FROM scores WHERE posting_id = ?", (p,)) == 1
    assert _count(store, "SELECT COUNT(*) FROM notifications WHERE posting_id = ?", (p,)) == 1
    title = store.conn.execute("SELECT title FROM job_postings WHERE id = ?", (p,)).fetchone()
    assert title["title"] == "Sr Merch"


def test_company_renderings_merge(store):
    a = store.upsert_posting(_posting(source="naukri", external_id="a", company="Marks & Spencer"))
    b = store.upsert_posting(_posting(source="indeed", external_id="b", company="Marks and Spencer"))
    c = store.upsert_posting(_posting(source="linkedin", external_id="c",
                                      company="Marks and Spencer India Pvt. Ltd."))
    assert a == b == c


def test_location_renderings_merge_when_detail_arrives_later(store):
    """A sighting with no city is adopted by a later one that has it."""
    a = store.upsert_posting(_posting(source="naukri", external_id="a", location=None))
    b = store.upsert_posting(_posting(source="indeed", external_id="b", location="India"))
    c = store.upsert_posting(_posting(source="linkedin", external_id="c", location="Gurgaon, India"))
    d = store.upsert_posting(_posting(source="glassdoor", external_id="d", location="Gurugram"))
    assert a == b == c == d
    loc = store.conn.execute("SELECT location FROM job_postings WHERE id = ?", (a,)).fetchone()
    assert loc["location"] == "Gurugram"


def test_same_role_in_different_cities_is_not_merged(store):
    a = store.upsert_posting(_posting(source="naukri", external_id="a", location="Gurgaon, India"))
    b = store.upsert_posting(_posting(source="indeed", external_id="b", location="Mumbai, India"))
    assert a != b


def _three_known_shapes(store):
    """Gurgaon, Mumbai and a country-only listing of the same company and title."""
    g = store.upsert_posting(_posting(source="naukri", external_id="g1", location="Gurgaon, India"))
    m = store.upsert_posting(_posting(source="naukri", external_id="m1", location="Mumbai, India"))
    c = store.upsert_posting(_posting(source="indeed", external_id="c1", location="India"))
    assert len({g, m, c}) == 3
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    store.link_posting(sid, g)
    store.link_posting(sid, m)
    _score(store, sid, g, 90.0, "2026-09-19T01:00:00")
    _score(store, sid, m, 40.0, "2026-09-19T01:00:00")
    return g, m, c, sid


def test_resighting_a_city_listing_never_merges_it_into_the_cityless_row(store):
    g, m, c, sid = _three_known_shapes(store)

    again_g = store.upsert_posting(_posting(source="naukri", external_id="g1",
                                            location="Gurgaon, India"))
    again_m = store.upsert_posting(_posting(source="naukri", external_id="m1",
                                            location="Mumbai, India"))

    assert (again_g, again_m) == (g, m)
    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 3
    links = store.conn.execute(
        "SELECT posting_id FROM seeker_jobs WHERE seeker_id = ? ORDER BY posting_id", (sid,)
    ).fetchall()
    assert [r["posting_id"] for r in links] == sorted([g, m])
    fit = {r["posting_id"]: r["fit_score"] for r in store.conn.execute(
        "SELECT posting_id, fit_score FROM scores WHERE seeker_id = ?", (sid,))}
    assert fit == {g: 90.0, m: 40.0}


@pytest.mark.xfail(strict=True, reason=(
    "OPEN, awaiting coordinator ruling: a city-less re-sighting of a known city listing "
    "resolves (ambiguous, several cities known) to the city-less row's hash and merges "
    "into it. Remove this marker when fixed."))
def test_resighting_a_city_listing_with_country_only_location_destroys_nothing(store):
    g, m, c, sid = _three_known_shapes(store)

    store.upsert_posting(_posting(source="naukri", external_id="g1", location="India"))

    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 3
    assert _count(store, "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?", (sid,)) == 2
    assert _count(store, "SELECT COUNT(*) FROM scores WHERE seeker_id = ?", (sid,)) == 2


def test_cpp_and_csharp_titles_are_not_merged(store):
    a = store.upsert_posting(_posting(external_id="a", title="C++ Developer"))
    b = store.upsert_posting(_posting(external_id="b", title="C# Developer"))
    assert a != b


def test_failed_write_is_rolled_back_not_committed_by_the_next_call(store):
    # source=None passes the job_postings insert, then violates NOT NULL on
    # posting_sources: a genuine mid-write failure.
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_posting(_posting(source=None))
    store.add_seeker("Unrelated", 5, "u.yaml", 20)  # commits whatever is pending
    count = store.conn.execute("SELECT COUNT(*) c FROM job_postings").fetchone()["c"]
    assert count == 0


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


def test_concurrent_upserts_of_the_same_posting_yield_one_row(store):
    n = 8
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def work(i):
        try:
            barrier.wait()
            store.upsert_posting(_posting(source=f"src{i}", external_id=f"e{i}"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    postings = store.conn.execute("SELECT COUNT(*) c FROM job_postings").fetchone()["c"]
    sources = store.conn.execute("SELECT COUNT(*) c FROM posting_sources").fetchone()["c"]
    assert (postings, sources) == (1, n)


def test_record_event_writes_a_row(store):
    store.record_event("source_error", "RuntimeError: 429")
    row = store.conn.execute("SELECT kind, detail FROM events").fetchone()
    assert row["kind"] == "source_error"
