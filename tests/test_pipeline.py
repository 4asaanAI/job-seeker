import pytest

from jobpilot.config import CanaryConfig, SiteConfig
from jobpilot.pipeline import SeekerTarget, run_discovery
from jobpilot.profiles import Profile
from jobpilot.sources.base import SourceStatus
from jobpilot.sources.jobspy_source import JobSpySource
from jobpilot.store.db import Store

ALL_SOURCES = ("naukri", "linkedin", "indeed")


def no_sleep(seconds: float) -> None:
    return None


def make_cfg(name):
    return SiteConfig(
        name=name, enabled=True, country_indeed="india", hours_old=168,
        results_wanted=50, fetch_descriptions=True,
        canary=CanaryConfig(search_term="merchandiser", location=None,
                            results_wanted=20, min_results=1),
    )


def make_profile(terms=("merchandiser",), location="Gurgaon",
                 sources=ALL_SOURCES, also_search=(), exclude=()):
    return Profile(
        name="Seeker", headline="Senior Merchandiser", location=location,
        sources=list(sources), accounts=["Ralph Lauren"],
        search_terms=list(terms), target_companies=[],
        exclude_keywords=list(exclude), min_score=70.0,
        also_search=list(also_search),
    )


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    yield s
    s.close()


def rows(site, n=2):
    return [
        {"site": site, "id": f"{site}-{i}", "title": f"Senior Merchandiser {i}",
         "company": "M&S", "location": "Gurgaon", "description": "d",
         "job_url": f"https://x.test/{site}/{i}", "date_posted": "2026-09-18"}
        for i in range(n)
    ]


def source(site, scraper):
    return JobSpySource(make_cfg(site), scraper=scraper, sleeper=no_sleep)


def recorder(site, n=2):
    """Scraper stub that records every call's kwargs and returns rows."""
    calls = []

    def scraper(**kwargs):
        calls.append(kwargs)
        return rows(site, n)

    scraper.calls = calls
    return scraper


def searches(scraper):
    """Real searches only: the canary carries no hours_old."""
    return [c for c in scraper.calls if "hours_old" in c]


def test_discovery_stores_and_links(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    src = source("naukri", lambda **k: rows("naukri"))

    results = run_discovery(store, [src], [SeekerTarget(sid, make_profile())])

    assert results[0].status is SourceStatus.HEALTHY
    assert len(store.seeker_jobs(sid)) == 2


def test_one_failing_site_does_not_discard_another_sites_results(store):
    """The defect that would have made the smoke gate unpassable."""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)

    def boom(**kwargs):
        raise RuntimeError("429 Too Many Requests")

    good = source("naukri", lambda **k: rows("naukri"))
    bad = source("linkedin", boom)

    results = run_discovery(store, [good, bad], [SeekerTarget(sid, make_profile())])
    by_site = {r.source: r for r in results}

    assert by_site["naukri"].status is SourceStatus.HEALTHY
    assert by_site["linkedin"].status is SourceStatus.ERROR
    assert len(store.seeker_jobs(sid)) == 2  # naukri's results survived


def test_each_site_gets_its_own_source_run_row(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    srcs = [source("naukri", lambda **k: rows("naukri")),
            source("indeed", lambda **k: rows("indeed"))]

    run_discovery(store, srcs, [SeekerTarget(sid, make_profile())])
    sources = {h["source"] for h in store.source_health_summary()}
    assert sources == {"naukri", "indeed"}


def test_search_failure_does_not_falsify_the_canary_flag(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    calls = {"n": 0}

    def canary_ok_then_search_fails(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return rows("naukri", 5)  # canary
        raise RuntimeError("connection reset")

    results = run_discovery(store, [source("naukri", canary_ok_then_search_fails)],
                            [SeekerTarget(sid, make_profile())])

    assert results[0].status is SourceStatus.ERROR
    assert results[0].canary_ok is True
    health = store.source_health_summary()[0]
    assert health["canary_ok"] == 1
    assert "canary returned" not in (health["notes"] or "").lower()


def test_blocked_source_stores_nothing(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    src = source("naukri", lambda **k: [])  # canary below floor

    results = run_discovery(store, [src], [SeekerTarget(sid, make_profile())])

    assert results[0].status is SourceStatus.BLOCKED
    assert store.seeker_jobs(sid) == []
    assert store.source_health_summary()[0]["status"] == "blocked"


def test_failures_are_written_to_events(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)

    def boom(**kwargs):
        raise RuntimeError("429 Too Many Requests")

    run_discovery(store, [source("naukri", boom)], [SeekerTarget(sid, make_profile())])
    row = store.conn.execute(
        "SELECT kind, detail FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["kind"] == "source_error"
    assert "429" in row["detail"]


def test_exception_with_empty_message_is_recorded_as_an_error(store):
    """str(RuntimeError()) is '', which classify() would read as 'no error'."""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)

    def silent_boom(**kwargs):
        raise RuntimeError()

    results = run_discovery(store, [source("naukri", silent_boom)],
                            [SeekerTarget(sid, make_profile())])

    assert results[0].status is SourceStatus.ERROR
    assert "RuntimeError" in results[0].notes
    health = store.source_health_summary()[0]
    assert health["status"] == "error"
    assert "RuntimeError" in health["notes"]


def test_two_seekers_sharing_a_term_scrape_once(store):
    """The whole reason global postings and per-seeker links are separate."""
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    calls = {"n": 0}

    def counting(**kwargs):
        calls["n"] += 1
        return rows("naukri")

    run_discovery(store, [source("naukri", counting)],
                  [SeekerTarget(a, make_profile()), SeekerTarget(b, make_profile())])

    assert calls["n"] == 2  # one canary + one shared search, not two searches
    assert len(store.seeker_jobs(a)) == 2
    assert len(store.seeker_jobs(b)) == 2
    total = store.conn.execute("SELECT COUNT(*) c FROM job_postings").fetchone()["c"]
    assert total == 2


def test_a_term_only_one_seeker_asked_for_links_only_to_them(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)

    def by_term(**kwargs):
        return rows("naukri", 1) if kwargs["search_term"] == "merchandiser" \
            else [{"site": "naukri", "id": "nk-tech", "title": "Tech Merch",
                   "company": "X", "location": "G", "description": "d",
                   "job_url": "https://x.test/t", "date_posted": "2026-09-18"}]

    run_discovery(
        store, [source("naukri", by_term)],
        [SeekerTarget(a, make_profile(terms=("merchandiser",))),
         SeekerTarget(b, make_profile(terms=("technical merchandiser",)))],
    )

    assert len(store.seeker_jobs(a)) == 1
    assert len(store.seeker_jobs(b)) == 1
    assert store.seeker_jobs(a)[0].posting_id != store.seeker_jobs(b)[0].posting_id


def test_excluded_keywords_are_not_linked(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)

    def mixed(**kwargs):
        return [
            {"site": "naukri", "id": "n1", "title": "Senior Merchandiser",
             "company": "M&S", "location": "G", "description": "d",
             "job_url": "https://x.test/1", "date_posted": "2026-09-18"},
            {"site": "naukri", "id": "n2", "title": "Visual Merchandiser",
             "company": "Store Co", "location": "G", "description": "d",
             "job_url": "https://x.test/2", "date_posted": "2026-09-18"},
        ]

    profile = make_profile(exclude=("visual merchandiser",))
    run_discovery(store, [source("naukri", mixed)], [SeekerTarget(sid, profile)])

    titles = [j.title for j in store.seeker_jobs(sid)]
    assert titles == ["Senior Merchandiser"]


def test_rerun_does_not_duplicate(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    src = source("naukri", lambda **k: rows("naukri"))
    t = [SeekerTarget(sid, make_profile())]

    run_discovery(store, [src], t)
    run_discovery(store, [src], t)

    assert len(store.seeker_jobs(sid)) == 2


# --- second amendment: also_search widens the locations ---------------------

def test_also_search_adds_a_scrape_per_term_and_shared_location_does_not(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    scraper = recorder("naukri")
    src = source("naukri", scraper)
    terms = ("merchandiser", "technical merchandiser")

    run_discovery(store, [src], [
        SeekerTarget(a, make_profile(terms=terms, also_search=("United States",))),
    ])
    with_also = len(searches(scraper))
    assert with_also == 4  # 2 terms x (Gurgaon + United States)
    assert {c["location"] for c in searches(scraper)} == {"Gurgaon", "United States"}

    scraper.calls.clear()
    run_discovery(store, [src], [
        SeekerTarget(a, make_profile(terms=terms, also_search=("United States",))),
        SeekerTarget(b, make_profile(terms=terms)),  # same terms, same base location
    ])
    assert len(searches(scraper)) == 4  # b adds no third scrape per term


def test_also_search_results_link_to_the_seeker_who_asked(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)

    def by_location(**kwargs):
        if kwargs.get("location") == "United States":
            return [{"site": "naukri", "id": "us-1", "title": "US Merchandiser",
                     "company": "Gap", "location": "NY", "description": "d",
                     "job_url": "https://x.test/us", "date_posted": "2026-09-18"}]
        return rows("naukri", 1)

    run_discovery(store, [source("naukri", by_location)], [
        SeekerTarget(a, make_profile(also_search=("United States",))),
        SeekerTarget(b, make_profile()),
    ])

    assert {j.title for j in store.seeker_jobs(a)} == {"Senior Merchandiser 0", "US Merchandiser"}
    assert {j.title for j in store.seeker_jobs(b)} == {"Senior Merchandiser 0"}


# --- spec 17.8: sources are scoped to the targets that subscribe ------------

def test_disjoint_sources_never_see_the_other_seekers_location(store):
    india = store.add_seeker("Alex", 1, "a.yaml", 20)
    us = store.add_seeker("Sam", 2, "s.yaml", 20)
    naukri, indeed_us = recorder("naukri"), recorder("indeed_us")

    run_discovery(
        store, [source("naukri", naukri), source("indeed_us", indeed_us)],
        [SeekerTarget(india, make_profile(location="Gurgaon", sources=("naukri",))),
         SeekerTarget(us, make_profile(location="Austin, TX", sources=("indeed_us",)))],
    )

    assert {c["location"] for c in searches(naukri)} == {"Gurgaon"}
    assert {c["location"] for c in searches(indeed_us)} == {"Austin, TX"}
    assert len(store.seeker_jobs(india)) == 2
    assert len(store.seeker_jobs(us)) == 2


def test_unsubscribed_source_is_never_called_and_writes_no_row(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    used, unused = recorder("naukri"), recorder("linkedin")

    results = run_discovery(
        store, [source("naukri", used), source("linkedin", unused)],
        [SeekerTarget(sid, make_profile(sources=("naukri",)))],
    )

    assert unused.calls == []  # not even a canary
    assert [r.source for r in results] == ["naukri"]
    assert {h["source"] for h in store.source_health_summary()} == {"naukri"}


def test_two_seekers_on_one_source_scrape_each_shared_term_once(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    scraper = recorder("naukri")

    run_discovery(store, [source("naukri", scraper)], [
        SeekerTarget(a, make_profile(terms=("merchandiser", "sourcing"), sources=("naukri",))),
        SeekerTarget(b, make_profile(terms=("merchandiser",), sources=("naukri",))),
    ])

    terms = [c["search_term"] for c in searches(scraper)]
    assert sorted(terms) == ["merchandiser", "sourcing"]


# --- FIXED-PARTIAL-STORE: a run record survives a mid-loop failure ----------

def test_store_failure_mid_loop_still_writes_run_row_and_spares_other_sites(
    store, monkeypatch
):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    real_upsert = store.upsert_posting

    def flaky_upsert(p):
        if p.source == "naukri":
            raise RuntimeError()
        return real_upsert(p)

    monkeypatch.setattr(store, "upsert_posting", flaky_upsert)

    results = run_discovery(
        store,
        [source("naukri", lambda **k: rows("naukri")),
         source("indeed", lambda **k: rows("indeed"))],
        [SeekerTarget(sid, make_profile())],
    )
    by_site = {r.source: r for r in results}
    health = {h["source"]: h for h in store.source_health_summary()}

    assert by_site["naukri"].status is SourceStatus.ERROR
    assert health["naukri"]["status"] == "error"
    assert "RuntimeError" in health["naukri"]["notes"]
    assert by_site["indeed"].status is SourceStatus.HEALTHY
    assert len(store.seeker_jobs(sid)) == 2  # indeed's rows only
