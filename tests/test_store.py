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


def test_resighting_a_city_listing_with_country_only_location_destroys_nothing(store):
    g, m, c, sid = _three_known_shapes(store)

    store.upsert_posting(_posting(source="naukri", external_id="g1", location="India"))

    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 3
    assert _count(store, "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?", (sid,)) == 2
    assert _count(store, "SELECT COUNT(*) FROM scores WHERE seeker_id = ?", (sid,)) == 2


def test_known_single_city_listing_resighted_country_only_keeps_its_city(store):
    """Only one city known: a country-only re-sighting must not overwrite it.

    The board dropped the city from its own listing; that is not new location
    information, so neither the stored string nor the identity may change.
    """
    pid = store.upsert_posting(_posting(source="naukri", external_id="g1",
                                        location="Gurgaon, India"))
    again = store.upsert_posting(_posting(source="naukri", external_id="g1",
                                          location="India"))
    assert again == pid
    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 1
    loc = store.conn.execute(
        "SELECT location FROM job_postings WHERE id = ?", (pid,)).fetchone()["location"]
    assert "Gurgaon" in loc


def _world(store, world):
    """Build one starting world and link a seeker with scores to its city rows.

    P = Gurgaon (naukri/g1), M = Mumbai (naukri/m1), C = country-only (indeed/c1).
    A world holding a city-less row but only ONE known city is not reachable
    through upsert_posting: with a single city known, a city-less sighting is
    adopted by it (Ruling 12), so "cityless" carries both cities as well.
    The seeker is linked to P (and M), never to C, so a merge in either
    direction moves rows rather than colliding: the counts below can only drop
    if something was destroyed.
    """
    ids = {"P": store.upsert_posting(
        _posting(source="naukri", external_id="g1", location="Gurgaon, India"))}
    if world in ("cities", "cityless"):
        ids["M"] = store.upsert_posting(
            _posting(source="naukri", external_id="m1", location="Mumbai, India"))
    if world == "cityless":
        ids["C"] = store.upsert_posting(
            _posting(source="indeed", external_id="c1", location="India"))
    assert len(set(ids.values())) == len(ids)  # the world really has distinct rows

    sid = store.add_seeker("A", 1, "a.yaml", 20)
    store.link_posting(sid, ids["P"])
    _score(store, sid, ids["P"], 90.0, "2026-09-19T01:00:00")
    if "M" in ids:
        store.link_posting(sid, ids["M"])
        _score(store, sid, ids["M"], 40.0, "2026-09-19T01:00:00")
    return ids, sid


# The whole identity matrix, as a specification:
#   world  -- what already exists: "alone" = P only, "cities" = P + M,
#             "cityless" = P + M + C.
#   key    -- "known" re-sights naukri/g1 (P's own key); "unknown" is a new key.
#   city   -- the sighting's location: same city as P, a different city, or
#             country-only (no city token).
#   rows   -- job_postings rows afterwards; id -- the posting id returned.
#   holds  -- the postings the seeker still has a seeker_jobs AND a scores row
#             for afterwards: exactly what a wrong merge would destroy.
_MATRIX = [
    # --- world "alone": P (Gurgaon) is the only row ------------------------
    ("alone", "known", "Gurgaon, India", 1, "P", ("P",),
     "Rule A: the key's own posting, and the city is unchanged"),
    ("alone", "known", "Pune, India", 1, "P", ("P",),
     "Rule A: the board moved its own listing to Pune; identity follows it, no new row"),
    ("alone", "known", "India", 1, "P", ("P",),
     "Rule B: no city token, so P's Gurgaon stands; Rule C: no exact collision, no merge"),
    ("alone", "unknown", "Gurgaon, India", 1, "P", ("P",),
     "Rule D: the exact hash is P's, so another board's sighting of the same role folds in"),
    ("alone", "unknown", "Pune, India", 2, "NEW", ("P",),
     "Rule D: a different city is a different role and no city-less row exists"),
    ("alone", "unknown", "India", 1, "P", ("P",),
     "Rule D / Ruling 12: exactly one city known, so the city-less sighting adopts it"),
    # --- world "cities": P (Gurgaon) and M (Mumbai) ------------------------
    ("cities", "known", "Gurgaon, India", 2, "P", ("P", "M"),
     "Rule A: the key's own posting; M is a different city and is untouched"),
    ("cities", "known", "Mumbai, India", 1, "M", ("M",),
     "Rule C, the one merging cell: P's board moved it onto M's exact city, so this is "
     "the same role as M and P folds into it; the seeker keeps ONE seeker_jobs row and "
     "ONE score on M, by the round-2 merge policy (later scored_at wins, a tie keeps "
     "the survivor's row)"),
    ("cities", "known", "Pune, India", 2, "P", ("P", "M"),
     "Rule A/C: P moves to Pune in place; no posting owns the Pune hash, so no merge"),
    ("cities", "known", "India", 2, "P", ("P", "M"),
     "Rule B: the sighting adds no location information, so P keeps Gurgaon"),
    ("cities", "unknown", "Gurgaon, India", 2, "P", ("P", "M"),
     "Rule D: exact hash match on P"),
    ("cities", "unknown", "Pune, India", 3, "NEW", ("P", "M"),
     "Rule D: no exact hash, no city-less row to fall back to, so a third city row"),
    ("cities", "unknown", "India", 3, "NEW", ("P", "M"),
     "Rule D: two cities known makes adoption ambiguous, so the city-less row stays apart"),
    # --- world "cityless": P (Gurgaon), M (Mumbai), C (country-only) -------
    ("cityless", "known", "Gurgaon, India", 3, "P", ("P", "M"),
     "Rule A: the key's own posting; the city-less row C never captures it"),
    ("cityless", "known", "Pune, India", 3, "P", ("P", "M"),
     "Rule A/C: P moves to Pune in place; C does not own the Pune hash, so no merge"),
    ("cityless", "known", "India", 3, "P", ("P", "M"),
     "THE DEFECT: Rule A+B keep P's Gurgaon identity instead of folding it into C"),
    ("cityless", "unknown", "Gurgaon, India", 3, "P", ("P", "M"),
     "Rule D: exact hash match on P wins over the city-less fallback"),
    ("cityless", "unknown", "Pune, India", 3, "C", ("P", "M"),
     "Rule D: no exact hash, so the city-less fallback folds this sighting into C"),
    ("cityless", "unknown", "India", 3, "C", ("P", "M"),
     "Rule D: adoption is ambiguous with two cities, so the city-less hash finds C"),
]


@pytest.mark.parametrize(
    "world,key,location,rows,expect,holds,reason", _MATRIX,
    ids=[f"{w}-{k}-{loc.split(',')[0].lower()}" for w, k, loc, _, _, _, _ in _MATRIX],
)
def test_sighting_identity_matrix(store, world, key, location, rows, expect, holds, reason):
    ids, sid = _world(store, world)
    fits_before = {
        r[0] for r in store.conn.execute(
            "SELECT fit_score FROM scores WHERE seeker_id = ?", (sid,))}

    source, external_id = ("naukri", "g1") if key == "known" else ("bright", "b1")
    got = store.upsert_posting(
        _posting(source=source, external_id=external_id, location=location))

    assert _count(store, "SELECT COUNT(*) FROM job_postings") == rows, reason
    if expect == "NEW":
        assert got not in set(ids.values()), reason
    else:
        assert got == ids[expect], reason
    # What the seeker owns: one live row per posting in `holds`, nothing lost to
    # a wrong merge, nothing duplicated or left dangling by a right one.
    expected = {ids[k] for k in holds}
    links = [r[0] for r in store.conn.execute(
        "SELECT posting_id FROM seeker_jobs WHERE seeker_id = ?", (sid,))]
    scored = [r[0] for r in store.conn.execute(
        "SELECT posting_id FROM scores WHERE seeker_id = ?", (sid,))]
    assert sorted(links) == sorted(expected), reason
    assert sorted(scored) == sorted(expected), reason
    fits_after = {r[0] for r in store.conn.execute(
        "SELECT fit_score FROM scores WHERE seeker_id = ?", (sid,))}
    assert fits_after <= fits_before, reason  # no score was invented
    if len(expected) == len(fits_before):
        assert fits_after == fits_before, reason  # and, with no merge, none lost


def test_a_location_column_that_disagrees_with_the_hash_cannot_cause_a_merge(store):
    """Rule D can overwrite a city-less row's location with a city it never hashed.

    Rule B must not read a city out of that polluted column: doing so gives the
    row a stranger's identity and merges it into whichever posting owns that
    city, destroying the seeker's saved job and score.
    """
    g = store.upsert_posting(_posting(source="naukri", external_id="g1",
                                      location="Gurgaon, India"))
    m = store.upsert_posting(_posting(source="naukri", external_id="m1",
                                      location="Mumbai, India"))
    c = store.upsert_posting(_posting(source="indeed", external_id="c1", location="India"))
    assert len({g, m, c}) == 3
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    store.link_posting(sid, g)
    store.link_posting(sid, c)
    _score(store, sid, g, 90.0, "2026-09-19T01:00:00")
    _score(store, sid, c, 40.0, "2026-09-19T01:00:00")

    # 1. An unknown key sighting a third city folds into C (Rule D) and
    #    overwrites C's location, which its city-less hash never saw.
    assert store.upsert_posting(
        _posting(source="bright", external_id="b1", location="Pune, India")) == c
    polluted = store.conn.execute(
        "SELECT location FROM job_postings WHERE id = ?", (c,)).fetchone()["location"]
    assert polluted == "Pune, India"  # the disagreement this test is about

    # 2. P's own board moves it to Pune, so P now owns the Pune hash.
    assert store.upsert_posting(
        _posting(source="naukri", external_id="g1", location="Pune, India")) == g

    # 3. C's own board re-sights it country-only. C has no city of its own, so
    #    it stays C: nothing here says it is the same role as P.
    back = store.upsert_posting(_posting(source="indeed", external_id="c1", location="India"))

    assert back == c
    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 3
    assert _count(store, "SELECT COUNT(*) FROM seeker_jobs WHERE seeker_id = ?", (sid,)) == 2
    fit = {r["posting_id"]: r["fit_score"] for r in store.conn.execute(
        "SELECT posting_id, fit_score FROM scores WHERE seeker_id = ?", (sid,))}
    assert fit == {g: 90.0, c: 40.0}


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


def _assert_nothing_lost(store, sid, p, c, back):
    assert back == p
    assert _count(store, "SELECT COUNT(*) FROM job_postings") == 3
    assert sorted(j.posting_id for j in store.seeker_jobs(sid)) == sorted([p, c])
    scores = {r["posting_id"]: r["fit_score"] for r in store.conn.execute(
        "SELECT posting_id, fit_score FROM scores WHERE seeker_id = ?", (sid,))}
    assert scores == {p: 90.0, c: 40.0}


def test_company_rename_does_not_let_a_country_only_sighting_merge_the_listing(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    title = "Senior Merchandiser"
    p = store.upsert_posting(_posting("naukri", "g1", title, "Marks & Spencer", "Gurgaon, India"))
    assert store.upsert_posting(
        _posting("naukri", "g1", title, "M&S", "Gurgaon, India")) == p
    assert store.conn.execute(
        "SELECT company_name FROM job_postings WHERE id = ?", (p,)
    ).fetchone()["company_name"] == "M&S"
    m = store.upsert_posting(_posting("naukri", "m1", title, "M&S", "Mumbai, India"))
    c = store.upsert_posting(_posting("indeed", "c1", title, "M&S", "India"))
    assert c not in (p, m)
    store.link_posting(sid, p)
    store.link_posting(sid, c)
    _score(store, sid, p, 90.0, "2026-09-19T01:00:00")
    _score(store, sid, c, 40.0, "2026-09-19T01:00:00")

    back = store.upsert_posting(_posting("naukri", "g1", title, "M&S", "India"))

    _assert_nothing_lost(store, sid, p, c, back)


def test_country_only_adoption_does_not_downgrade_a_city_rows_location(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    title = "Senior Merchandiser"
    co = "Marks & Spencer"
    p = store.upsert_posting(_posting("naukri", "g1", title, co, "Gurgaon, India"))
    assert store.upsert_posting(_posting("bright", "b1", title, co, "India")) == p
    assert "Gurgaon" in store.conn.execute(
        "SELECT location FROM job_postings WHERE id = ?", (p,)).fetchone()["location"]
    m = store.upsert_posting(_posting("naukri", "m1", title, co, "Mumbai, India"))
    c = store.upsert_posting(_posting("xing", "x1", title, co, "India"))
    assert c not in (p, m)
    store.link_posting(sid, p)
    store.link_posting(sid, c)
    _score(store, sid, p, 90.0, "2026-09-19T01:00:00")
    _score(store, sid, c, 40.0, "2026-09-19T01:00:00")

    back = store.upsert_posting(_posting("naukri", "g1", title, co, "India"))

    _assert_nothing_lost(store, sid, p, c, back)
