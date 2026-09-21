import pytest

from jobpilot.profiles import load_companies, load_profile
from jobpilot.store.db import Store

PROFILE = """
name: Test Seeker
headline: Merchandiser - Sample Track
location: Gurgaon
sources: [naukri_in, indeed_in]
accounts: [Acme Apparel, Brand Two]
search_terms:
  - merchandiser
  - product development merchandiser
target_companies:
  - Marks & Spencer
exclude_keywords:
  - retail store
min_score: 70
"""

COMPANIES = (
    "name,tier,ats_vendor,careers_url,region,enabled\n"
    "Marks & Spencer,1,workday,https://example.test/ms,NCR,1\n"
    "Ralph Lauren,1,workday,https://example.test/rl,NCR,1\n"
    "Disabled Co,4,,,NCR,0\n"
)


def _write(tmp_path, text, name="profile.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_load_profile(tmp_path):
    prof = load_profile(_write(tmp_path, PROFILE))
    assert prof.name == "Test Seeker"
    assert "Acme Apparel" in prof.accounts
    assert prof.min_score == 70.0
    assert prof.exclude_keywords == ["retail store"]
    assert prof.sources == ["naukri_in", "indeed_in"]


def test_profile_missing_required_field_raises(tmp_path):
    with pytest.raises(ValueError, match="search_terms"):
        load_profile(_write(tmp_path, "name: Someone\n"))


@pytest.mark.parametrize("line", ["", "sources: []\n", "sources:\n"])
def test_profile_missing_or_empty_sources_raises(tmp_path, line):
    text = PROFILE.replace("sources: [naukri_in, indeed_in]\n", line)
    with pytest.raises(ValueError, match="sources"):
        load_profile(_write(tmp_path, text))


def test_notes_and_also_search_default_empty(tmp_path):
    prof = load_profile(_write(tmp_path, PROFILE))
    assert prof.notes == []
    assert prof.also_search == []


def test_notes_and_also_search_round_trip(tmp_path):
    text = PROFILE + "notes:\n  - Needs visa sponsorship\nalso_search: [United States]\n"
    prof = load_profile(_write(tmp_path, text))
    assert prof.notes == ["Needs visa sponsorship"]
    assert prof.also_search == ["United States"]


def test_rubric_defaults_to_none(tmp_path):
    prof = load_profile(_write(tmp_path, PROFILE))
    assert prof.rubric is None


def test_rubric_round_trips(tmp_path):
    text = PROFILE + "rubric: apparel-brand-side\n"
    prof = load_profile(_write(tmp_path, text))
    assert prof.rubric == "apparel-brand-side"


def test_load_companies_skips_disabled(tmp_path):
    rows = load_companies(_write(tmp_path, COMPANIES, "companies.csv"))
    assert [r.name for r in rows] == ["Marks & Spencer", "Ralph Lauren"]


def _store_with_companies(tmp_path):
    c = _write(tmp_path, COMPANIES, "companies.csv")
    s = Store(tmp_path / "t.db")
    s.migrate()
    s.sync_companies(load_companies(c))
    return s, c


def test_sync_companies_is_idempotent(tmp_path):
    s, c = _store_with_companies(tmp_path)
    assert s.sync_companies(load_companies(c)) == 2
    count = s.conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    assert count == 2
    s.close()


def test_match_company_handles_real_world_naming(tmp_path):
    """Boards list 'Marks & Spencer Reliance India Pvt Ltd', not 'Marks & Spencer'."""
    s, _ = _store_with_companies(tmp_path)
    assert s.match_company("Marks & Spencer Reliance India Pvt Ltd") is not None
    assert s.match_company("Ralph Lauren Sourcing India") is not None
    assert s.match_company("Acme Exports Pvt Ltd") is None
    s.close()


def test_attach_company_sets_company_id(tmp_path):
    from jobpilot.store.models import Posting

    s, _ = _store_with_companies(tmp_path)
    cid = s.match_company("Ralph Lauren")
    pid = s.upsert_posting(Posting(
        company_name="Ralph Lauren Sourcing India", source="naukri_in", external_id="1",
        title="Merchandiser", location="Gurgaon", description="d",
        url="https://x.test/1", posted_at=None,
    ))
    s.attach_company(pid, cid)
    row = s.conn.execute("SELECT company_id FROM job_postings WHERE id = ?", (pid,)).fetchone()
    assert row["company_id"] == cid
    s.close()


def test_shipped_company_list_exists_because_discover_loads_it():
    # discover() reads this file on every run; deleting it breaks every search.
    repo = Path(__file__).parents[1]
    assert load_companies(repo / "data" / "companies.csv")
