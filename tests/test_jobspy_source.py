# tests/test_jobspy_source.py
import json
from pathlib import Path

import pytest

from jobpilot.config import CanaryConfig, SiteConfig
from jobpilot.sources.base import MAX_DESCRIPTION_CHARS, safe_url
from jobpilot.sources.jobspy_source import JobSpySource

FIXTURE = Path(__file__).parent / "fixtures" / "jobspy_naukri.json"


def make_cfg(name="naukri", fetch_descriptions=True):
    return SiteConfig(
        name=name, enabled=True, country_indeed="india", hours_old=168,
        results_wanted=50, fetch_descriptions=fetch_descriptions,
        canary=CanaryConfig(search_term="merchandiser", location=None,
                            results_wanted=20, min_results=2),
    )


def rows_from_fixture(**kwargs):
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def no_sleep(seconds: float) -> None:
    return None


def test_search_normalises_rows_into_postings():
    src = JobSpySource(make_cfg(), scraper=rows_from_fixture, sleeper=no_sleep)
    postings, raw = src.search("merchandiser", "Gurgaon")

    assert raw == 2
    assert len(postings) == 2
    p = postings[0]
    assert p.source == "naukri"
    assert p.external_id == "nk-88123"
    assert p.company_name == "Marks & Spencer Reliance India"
    assert p.description


def test_descriptions_are_requested():
    """Naukri and LinkedIn return None unless this flag is passed."""
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return []

    JobSpySource(make_cfg(), scraper=spy, sleeper=no_sleep).search("m", "G")
    assert captured["linkedin_fetch_description"] is True
    assert captured["site_name"] == ["naukri"]


def test_only_this_sites_rows_are_requested():
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return []

    JobSpySource(make_cfg("indeed"), scraper=spy, sleeper=no_sleep).search("m", "G")
    assert captured["site_name"] == ["indeed"]


def test_source_key_and_jobspy_site_are_distinct_for_a_market_source():
    """Spec 17.8: indeed_us is the source key; JobSpy is told 'indeed'."""
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return []

    cfg = SiteConfig(
        name="indeed_us", site="indeed", enabled=True, country_indeed="usa",
        hours_old=168, results_wanted=50, fetch_descriptions=True,
        canary=CanaryConfig(search_term="merchandiser", location=None,
                            results_wanted=20, min_results=2),
    )
    src = JobSpySource(cfg, scraper=spy, sleeper=no_sleep)
    src.search("m", "G")
    assert src.name == "indeed_us"
    assert captured["site_name"] == ["indeed"]
    assert captured["country_indeed"] == "usa"


def test_posting_source_falls_back_to_jobspy_site_not_source_key():
    def no_site_col(**kwargs):
        return [{"id": "i1", "title": "Role", "company": "X"}]

    cfg = SiteConfig(
        name="indeed_us", site="indeed", enabled=True, country_indeed="usa",
        hours_old=168, results_wanted=50, fetch_descriptions=True,
        canary=CanaryConfig(search_term="m", location=None,
                            results_wanted=20, min_results=1),
    )
    postings, _ = JobSpySource(cfg, scraper=no_site_col, sleeper=no_sleep).search("m", "G")
    assert postings[0].source == "indeed"


def test_rows_missing_required_fields_are_skipped_not_crashed():
    def partial(**kwargs):
        return [{"site": "naukri", "title": "No ID role"}]

    postings, raw = JobSpySource(make_cfg(), scraper=partial,
                                 sleeper=no_sleep).search("m", "G")
    assert postings == []
    assert raw == 1  # raw != normalized is itself a signal


def test_javascript_urls_are_rejected():
    def evil(**kwargs):
        return [{"site": "naukri", "id": "n1", "title": "Role", "company": "X",
                 "job_url": "javascript:alert(document.cookie)"}]

    postings, _ = JobSpySource(make_cfg(), scraper=evil, sleeper=no_sleep).search("m", "G")
    assert postings[0].url is None


def test_safe_url_allows_only_http_schemes():
    assert safe_url("https://ok.test/x") == "https://ok.test/x"
    assert safe_url("http://ok.test/x") == "http://ok.test/x"
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("data:text/html,<script>") is None
    assert safe_url("") is None
    assert safe_url(None) is None


def test_long_descriptions_are_truncated():
    def huge(**kwargs):
        return [{"site": "naukri", "id": "n1", "title": "Role", "company": "X",
                 "job_url": "https://ok.test/1", "description": "x" * 100000}]

    postings, _ = JobSpySource(make_cfg(), scraper=huge, sleeper=no_sleep).search("m", "G")
    assert len(postings[0].description) <= MAX_DESCRIPTION_CHARS


def test_canary_reports_raw_and_normalized_counts():
    src = JobSpySource(make_cfg(), scraper=rows_from_fixture, sleeper=no_sleep)
    ok, raw, normalized = src.canary()
    assert ok is True
    assert (raw, normalized) == (2, 2)


def test_canary_fails_when_below_min_results():
    def one_row(**kwargs):
        return [{"site": "naukri", "id": "n1", "title": "Role", "company": "X",
                 "job_url": "https://ok.test/1"}]

    cfg = make_cfg()
    ok, raw, normalized = JobSpySource(cfg, scraper=one_row, sleeper=no_sleep).canary()
    assert ok is False  # min_results is 2


def test_canary_detects_broken_normalisation_while_transport_works():
    """Rows arrive but none survive mapping: the ironic blindness case."""
    def renamed_columns(**kwargs):
        return [{"site": "naukri", "job_id": "n1", "job_title": "Role",
                 "employer": "X"} for _ in range(20)]

    ok, raw, normalized = JobSpySource(make_cfg(), scraper=renamed_columns,
                                       sleeper=no_sleep).canary()
    assert raw == 20
    assert normalized == 0
    assert ok is False


def test_canary_carries_no_recency_filter():
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return []

    JobSpySource(make_cfg(), scraper=spy, sleeper=no_sleep).canary()
    assert captured.get("hours_old") is None


def test_scraper_exception_propagates_so_the_caller_can_label_it():
    def boom(**kwargs):
        raise RuntimeError("429 Too Many Requests")

    src = JobSpySource(make_cfg(), scraper=boom, sleeper=no_sleep)
    with pytest.raises(RuntimeError, match="429"):
        src.canary()
