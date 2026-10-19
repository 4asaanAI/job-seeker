"""Fo build list, section 01: facts, filters, freshness, duplicate guard and learned score adjustments."""
from datetime import datetime, timedelta, timezone

import pytest

from jobpilot.pipeline import _excluded
from jobpilot.profiles import Profile
from jobpilot.ranking import (Facts, facts_for, filter_reason, is_staffing_agency, posted_day, rejection_category,
                              reweight, salary, work_mode)
from jobpilot.store.db import Store
from jobpilot.store.models import Posting


def _profile(**kw):
    base = dict(name="A", headline="PM", location="Gurgaon", sources=["s"], search_terms=["pm"])
    return Profile(**{**base, **kw})


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    yield s
    s.close()


def _post(store, sid, ext, title="Product Manager", company="Acme", posted=None, **kw):
    pid = store.upsert_posting(Posting(company_name=company, source=kw.pop("source", "src"), external_id=ext,
                                       title=title, location=kw.pop("location", "Gurgaon"), description=kw.pop("desc", "d"),
                                       url=f"https://x.test/{ext}", posted_at=posted))
    if posted:
        store.set_posting_facts(pid, facts_for(title, "Gurgaon", "d", posted))
    store.link_posting(sid, pid)
    return pid


# ---- facts ----

@pytest.mark.parametrize("text,expected", [
    ("CTC 20-30 LPA", (2_000_000, 3_000_000, "INR")),
    ("Salary: 25 lakhs per annum", (2_500_000, None, "INR")),
    ("₹18,00,000 - ₹24,00,000 a year", (1_800_000, 2_400_000, "INR")),
    ("$120,000 - $150,000", (120_000, 150_000, "USD")),
    ("$140k-$170k base", (140_000, 170_000, "USD")),
    ("$45/hour", (None, None, None)),
    ("competitive pay", (None, None, None)),
])
def test_salary_is_read_only_when_stated_yearly(text, expected):
    assert salary(text) == expected


def test_work_mode_prefers_title_and_location():
    assert work_mode("PM (Remote)", "India", "an onsite role") == "remote"
    assert work_mode("PM", "Bengaluru - Hybrid", "") == "hybrid"
    assert work_mode("PM", "Pune", "This is a fully remote role") == "remote"
    assert work_mode("PM", "Pune", "Work from office five days") == "onsite"
    assert work_mode("PM", "Pune", "great team") is None


def test_posted_day_reads_iso_and_epoch():
    assert posted_day("2026-10-01T10:00:00Z") == "2026-10-01"
    assert posted_day("1760000000000") == posted_day("1760000000")
    assert posted_day("last week") is None


def test_staffing_agencies_are_recognised():
    assert is_staffing_agency("TeamLease Services")
    assert is_staffing_agency("ABC Corp", "We are hiring for our client, a leading bank")
    assert not is_staffing_agency("Stripe", "Join our payments team")
    # found on real data: firms that serve clients are not agencies
    assert not is_staffing_agency("Google", "Help our clients grow; you will work for our clients across Asia")
    assert not is_staffing_agency("DWS", "As investors on behalf of our clients, it is our role to find solutions")
    assert not is_staffing_agency("MakeAR", "We are currently hiring for a Client Servicing Associate in Noida")
    assert is_staffing_agency("Aviate", "About the Company Our client is redefining consumer buying")


# ---- filters ----

def test_filters_drop_only_on_known_facts():
    p = _profile(salary_floor=2_000_000, salary_currency="INR", work_modes=["remote", "hybrid"],
                 blacklist_companies=["Evil Co"], locations=["Gurgaon", "Remote"])
    ok = Facts()
    assert filter_reason(p, "Acme", "PM", "Gurgaon", "", ok) is None
    assert filter_reason(p, "Evil Co Pvt Ltd", "PM", "Gurgaon", "", ok) == "company is on your blacklist"
    assert filter_reason(p, "Acme", "PM", "Gurgaon", "", Facts(1_000_000, 1_500_000, "INR")) == "salary below your floor"
    assert filter_reason(p, "Acme", "PM", "Gurgaon", "", Facts(100_000, None, "USD")) is None   # other currency: unknown
    assert filter_reason(p, "Acme", "PM", "Gurgaon", "", Facts(work_mode="onsite")).startswith("onsite job")
    assert filter_reason(p, "Acme", "PM", "Mumbai", "", ok) == "location not in your list"
    assert filter_reason(p, "Acme", "PM", "Anywhere", "", Facts(work_mode="remote")) is None


def test_whitelist_beats_the_staffing_filter():
    p = _profile(whitelist_companies=["Randstad"])
    assert filter_reason(p, "Randstad", "PM", "Gurgaon", "", Facts()) is None
    assert filter_reason(_profile(), "Randstad", "PM", "Gurgaon", "", Facts()) == "staffing agency post"
    assert filter_reason(_profile(exclude_staffing_agencies=False), "Randstad", "PM", "Gurgaon", "", Facts()) is None


def test_pipeline_exclusion_uses_the_filters():
    p = _profile(blacklist_companies=["Acme"])
    posting = Posting(company_name="Acme", source="s", external_id="1", title="PM", location="Gurgaon",
                      description="", url=None, posted_at=None)
    assert _excluded(posting, p)
    assert not _excluded(posting, _profile())


# ---- freshness, deadlines and duplicates ----

def test_old_postings_leave_matches_and_digests(store):
    sid = store.add_seeker("A", None, "a/p.yaml")
    old_day = (datetime.now(timezone.utc) - timedelta(days=20)).date().isoformat()
    new_day = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    old = _post(store, sid, "1", title="Old PM", posted=old_day)
    new = _post(store, sid, "2", title="New PM", posted=new_day)
    for pid in (old, new):
        store.record_score(sid, pid, 90, "fit")
    assert [m["posting_id"] for m in store.best_matches(sid, 10, open_only=True)] == [new]
    assert [j.posting_id for j in store.notifiable_postings(sid, 70, 10)] == [new]
    store.fresh_days = 30
    assert len(store.best_matches(sid, 10, open_only=True)) == 2


def test_deadline_within_48_hours_is_flagged(store):
    sid = store.add_seeker("A", None, "a/p.yaml")
    pid = _post(store, sid, "1")
    store.record_score(sid, pid, 90, "fit")
    store.set_valid_through(pid, (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat())
    assert store.best_matches(sid, 5)[0]["deadline_soon"] is True
    assert store.notifiable_postings(sid, 70, 5)[0].deadline_soon is True


def test_a_role_already_applied_to_never_reaches_matches_again(store):
    sid = store.add_seeker("A", None, "a/p.yaml")
    first = _post(store, sid, "1", location="Gurgaon")
    twin = _post(store, sid, "2", location="Mumbai")               # same role, another city: a separate posting
    assert first != twin
    store.record_score(sid, twin, 90, "fit")
    store.record_application(sid, first, "applied")
    assert store.best_matches(sid, 5, open_only=True) == []
    assert store.notifiable_postings(sid, 70, 5) == []
    assert store.unscored_jobs(sid, 10) == []


def test_two_listings_of_one_role_appear_once(store):
    sid = store.add_seeker("A", None, "a/p.yaml")
    a = _post(store, sid, "1", location="Gurgaon")
    b = _post(store, sid, "2", location="Mumbai")
    store.record_score(sid, a, 80, "fit")
    store.record_score(sid, b, 90, "fit")
    assert [j.posting_id for j in store.notifiable_postings(sid, 70, 5)] == [b]
    store.record_notification(sid, b, "fo", "first")
    assert store.notifiable_postings(sid, 70, 5) == []                # the twin was already sent


# ---- learned adjustments ----

def test_reweight_learns_from_outcomes_and_keeps_the_model_score(store):
    sid = store.add_seeker("A", None, "a/p.yaml")
    rejected = _post(store, sid, "1", title="PM One")
    other = _post(store, sid, "2", title="PM Two")
    store.record_score(sid, other, 80, "fit")
    store.record_application(sid, rejected, "applied")
    store.record_application(sid, rejected, "rejected", reason="We went with a more senior candidate")
    out = reweight(store, sid, _profile())
    assert out["changed"] == 1
    row = store.conn.execute("SELECT fit_score, model_score, adjust_notes FROM scores WHERE posting_id = ?",
                             (other,)).fetchone()
    assert (row["fit_score"], row["model_score"]) == (75, 80) and "company history -5" in row["adjust_notes"]
    store.record_score(sid, other, 85, "rescored")                  # a new model score keeps the adjustment
    assert store.conn.execute("SELECT fit_score FROM scores WHERE posting_id = ?", (other,)).fetchone()[0] == 80
    assert store.rejection_summary(sid) == {"seniority": 1}


def test_reweight_filters_board_weights_and_whitelist(store):
    sid = store.add_seeker("A", None, "a/p.yaml")
    pid = _post(store, sid, "1", source="indeed_in")
    store.record_score(sid, pid, 70, "fit")
    reweight(store, sid, _profile(source_weights={"indeed_in": -10}))
    assert store.conn.execute("SELECT fit_score FROM scores").fetchone()[0] == 60
    reweight(store, sid, _profile(whitelist_companies=["Acme"]))
    assert store.conn.execute("SELECT fit_score FROM scores").fetchone()[0] == 80
    reweight(store, sid, _profile(blacklist_companies=["Acme"]))
    assert store.conn.execute("SELECT fit_score FROM scores").fetchone()[0] == 0
    reweight(store, sid, _profile())                                 # removing the filter restores the job
    assert store.conn.execute("SELECT fit_score FROM scores").fetchone()[0] == 70


def test_rejection_categories():
    assert rejection_category("we need someone with visa sponsorship already") == "visa"
    assert rejection_category("the role has been filled") == "filled"
    assert rejection_category("no reason") == "other"
    assert rejection_category("") is None
