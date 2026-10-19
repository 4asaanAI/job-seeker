
import pytest

from jobpilot.profiles import Profile
from jobpilot.scoring import build_scoring_batch, load_rubric
from jobpilot.store.db import Store
from jobpilot.store.models import Posting


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    yield s
    s.close()


def _profile(rubric="apparel-brand-side", notes=None, target_companies=("Marks & Spencer",)):
    return Profile(
        name="Test Seeker", headline="Senior Merchandiser - PD", location="Gurgaon",
        sources=["naukri_in"], accounts=["Ralph Lauren", "Marks & Spencer"],
        search_terms=["merchandiser"], target_companies=list(target_companies),
        exclude_keywords=[], min_score=70.0, notes=list(notes or []), rubric=rubric,
    )


def _posting(company="Marks & Spencer Reliance India", external_id="n1",
             title="Senior Merchandiser", description="Own the critical path."):
    return Posting(
        company_name=company, source="naukri", external_id=external_id, title=title,
        location="Gurgaon", description=description,
        url=f"https://x.test/{external_id}", posted_at="2026-09-18",
    )


def test_rubric_encodes_the_spec_exclusions(tmp_path):
    # No per-seeker rubric.md anywhere near this path -> falls back to the
    # real, shared apparel-brand-side.md.
    text = load_rubric(tmp_path / "nonexistent" / "profile.yaml", "apparel-brand-side").lower()
    assert "export house" in text
    assert "buying office" in text
    assert "ralph lauren" in text or "account adjacency" in text
    assert "untrusted" in text  # injection warning about scraped text


def test_load_rubric_prefers_the_per_seeker_file(tmp_path):
    seeker_dir = tmp_path / "seekers" / "someone"
    seeker_dir.mkdir(parents=True)
    (seeker_dir / "rubric.md").write_text("PERSONAL RUBRIC TEXT", encoding="utf-8")

    text = load_rubric(seeker_dir / "profile.yaml", "apparel-brand-side")

    assert text == "PERSONAL RUBRIC TEXT"


def test_load_rubric_raises_a_clear_error_when_nothing_resolves(tmp_path):
    with pytest.raises(FileNotFoundError, match="rubric"):
        load_rubric(tmp_path / "nowhere" / "profile.yaml", None)


def test_two_seekers_in_different_fields_get_different_rubrics(tmp_path):
    """The tenancy bug this replaces: a single-entry cache would serve one
    seeker's rubric to another. Load seeker A's first, then seeker B's, and
    confirm B's own text comes back rather than A's cached one."""
    a_dir = tmp_path / "seekers" / "a"
    b_dir = tmp_path / "seekers" / "b"
    a_dir.mkdir(parents=True)
    b_dir.mkdir(parents=True)
    (a_dir / "rubric.md").write_text("SEEKER A RUBRIC", encoding="utf-8")
    (b_dir / "rubric.md").write_text("SEEKER B RUBRIC", encoding="utf-8")

    assert load_rubric(a_dir / "profile.yaml", None) == "SEEKER A RUBRIC"
    assert load_rubric(b_dir / "profile.yaml", None) == "SEEKER B RUBRIC"
    # And re-fetching A afterwards must not have been clobbered either.
    assert load_rubric(a_dir / "profile.yaml", None) == "SEEKER A RUBRIC"


def test_batch_includes_descriptions_and_target_flag(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(sid, pid)

    batch = build_scoring_batch(store, sid, _profile(), 10)

    assert batch["seeker_id"] == sid
    assert batch["rubric"]
    job = batch["jobs"][0]
    assert job["posting_id"] == pid
    assert job["description"] == "Own the critical path."
    assert job["is_target_company"] is True


def test_target_company_match_is_whole_word_not_substring(store):
    """'Next' must not match 'Nextgen Exports' -- the same false-positive
    class Store.match_company was already fixed to avoid (Task 6)."""
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    miss = store.upsert_posting(_posting(company="Nextgen Exports", external_id="n1"))
    hit = store.upsert_posting(_posting(company="Next Retail Plc", external_id="n2"))
    store.link_posting(sid, miss)
    store.link_posting(sid, hit)

    batch = build_scoring_batch(store, sid, _profile(target_companies=["Next"]), 10)

    flags = {j["posting_id"]: j["is_target_company"] for j in batch["jobs"]}
    assert flags[miss] is False
    assert flags[hit] is True


def test_batch_is_per_seeker(store):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    pa = store.upsert_posting(_posting(company="M&S", external_id="n1", title="A Role"))
    pb = store.upsert_posting(_posting(company="Gap", external_id="n2", title="B Role"))
    store.link_posting(a, pa)
    store.link_posting(b, pb)

    assert [j["posting_id"] for j in build_scoring_batch(store, a, _profile(), 10)["jobs"]] == [pa]
    assert [j["posting_id"] for j in build_scoring_batch(store, b, _profile(), 10)["jobs"]] == [pb]


def test_batch_respects_limit(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    for i in range(5):
        pid = store.upsert_posting(_posting(company=f"Co {i}", external_id=f"n{i}",
                                             title=f"Role {i}"))
        store.link_posting(sid, pid)

    assert len(build_scoring_batch(store, sid, _profile(), 2)["jobs"]) == 2


def test_already_scored_jobs_are_not_rebatched(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 80.0, "done")

    assert build_scoring_batch(store, sid, _profile(), 10)["jobs"] == []


def test_profile_summary_includes_notes(store):
    """AMENDED requirement: the scorer must see sponsorship, salary and
    notice-period facts, not just headline/accounts/targets."""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(sid, pid)
    profile = _profile(notes=["Notice period: 2 months", "Compensation: Rs 65,000/month pre-tax"])

    batch = build_scoring_batch(store, sid, profile, 10)

    assert "Notice period: 2 months" in batch["profile_summary"]
    assert "Compensation: Rs 65,000/month pre-tax" in batch["profile_summary"]


def test_batch_warns_the_job_text_is_untrusted(store):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(_posting())
    store.link_posting(sid, pid)

    batch = build_scoring_batch(store, sid, _profile(), 10)

    assert "untrusted" in batch["note"].lower()
