from __future__ import annotations

import pytest

from jobpilot.config import ATSConfig
from jobpilot.pipeline import SeekerTarget, run_discovery
from jobpilot.profiles import Profile
from jobpilot.sources.ats_json import ATSJsonSource
from jobpilot.sources.base import SourceStatus
from jobpilot.store.db import Store


def config(vendor: str, *, minimum: int = 1) -> ATSConfig:
    return ATSConfig(
        name=f"{vendor}_acme", vendor=vendor, company_name="Acme & Co",
        endpoint=f"https://jobs.example.test/{vendor}.json", enabled=True,
        canary_min_results=minimum,
    )


PAYLOADS = {
    "greenhouse": {"jobs": [{
        "id": 101, "title": "Senior Merchandiser",
        "location": {"name": "Gurgaon"},
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
        "content": "<p>Own <strong>product development</strong>.</p>",
        "created_at": "2026-09-18T10:00:00Z",
    }]},
    "lever": [{
        "id": "lev-202", "text": "Product Manager",
        "categories": {"location": "Austin, TX"},
        "hostedUrl": "https://jobs.lever.co/acme/202",
        "descriptionPlain": "Own the roadmap.", "createdAt": 1726650000000,
    }],
    "workday": {"jobPostings": [{
        "jobPostingId": "wd-303", "title": "Business Analyst",
        "locationsText": "West Lafayette, IN",
        "externalUrl": "https://acme.wd5.myworkdayjobs.com/en-US/jobs/job/wd-303",
        "jobDescription": "Analyse demand and reporting.", "postedOn": "2026-09-17",
    }]},
    "successfactors": {"d": {"results": [{
        "jobReqId": "sf-404", "jobTitle": "Sourcing Manager",
        "jobLocation": [{"name": "Bangalore"}, {"name": "Delhi"}],
        "jobUrl": "https://acme.jobs.ondemand.com/job/404",
        "jobDescription": "Lead supplier capability management.",
        "postingDate": "2026-09-16",
    }]}},
}


@pytest.mark.parametrize("vendor", ["greenhouse", "lever", "workday", "successfactors"])
def test_each_supported_vendor_normalizes_to_posting(vendor):
    calls = []

    def fetcher(url, timeout):
        calls.append((url, timeout))
        return PAYLOADS[vendor]

    source = ATSJsonSource(config(vendor), fetcher=fetcher)
    assert source.canary() == (True, 1, 1)
    term = {
        "greenhouse": "merchandiser",
        "lever": "manager",
        "workday": "analyst",
        "successfactors": "manager",
    }[vendor]
    found, raw = source.search(term, None)

    assert raw == 1
    assert len(found) == 1
    posting = found[0]
    assert posting.company_name == "Acme & Co"
    assert posting.source == f"{vendor}_acme"
    assert posting.external_id and posting.title
    assert posting.url and posting.url.startswith("https://")
    assert posting.description and "<" not in posting.description
    assert len(calls) == 1  # canary and searches share one response cache


def test_workday_nested_data_shape_and_local_filtering():
    source = ATSJsonSource(
        config("workday"),
        fetcher=lambda _url, _timeout: {"data": {"jobPostings": [{
            "externalPath": "/job/505", "jobTitle": "Product Analyst",
            "location": "Austin, TX", "url": "https://example.test/jobs/505",
            "description": "Product insights",
        }]}},
    )
    assert source.canary() == (True, 1, 1)
    assert source.search("analyst", "Austin")[0][0].external_id == "/job/505"
    # Location is judged by scoring, not by a city substring on employer feeds.
    assert len(source.search("analyst", "Gurgaon")[0]) == 1
    assert source.search("designer", "Austin") == ([], 0)


def test_employer_with_no_matching_job_is_empty_not_a_mapping_error():
    from jobpilot.sources.health import SourceStatus, classify
    source = ATSJsonSource(config("greenhouse"), fetcher=lambda _u, _t: {"jobs": [
        {"id": str(i), "title": "Software Engineer", "location": {"name": "Pittsburgh, PA"}}
        for i in range(80)]})
    found, raw = source.search("product manager", "San Francisco")
    assert (found, raw) == ([], 0)
    status, _ = classify(results_count=0, canary_ok=True, canary_count=80,
                         raw_count=raw, history=[])
    assert status is not SourceStatus.ERROR


def test_bad_rows_are_not_counted_and_unsafe_urls_are_removed():
    source = ATSJsonSource(
        config("greenhouse"),
        fetcher=lambda _url, _timeout: {"jobs": [
            {"id": "missing-title", "absolute_url": "javascript:alert(1)"},
            {"id": "good", "title": "Good role", "absolute_url": "javascript:alert(1)"},
        ]},
    )
    assert source.canary() == (True, 2, 1)
    found, _ = source.search("good", None)
    assert len(found) == 1 and found[0].url is None


def test_canary_floor_can_block_a_sparse_response():
    source = ATSJsonSource(
        config("lever", minimum=2),
        fetcher=lambda _url, _timeout: PAYLOADS["lever"],
    )
    assert source.canary() == (False, 1, 1)


def test_ats_source_plugs_into_discovery_without_network(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.migrate()
    seeker_id = store.add_seeker("A", 11, "a.yaml", 20)
    profile = Profile(
        name="A", headline="Product Manager", location="Austin",
        sources=["lever_acme"], search_terms=["product"],
    )
    source = ATSJsonSource(
        config("lever"), fetcher=lambda _url, _timeout: PAYLOADS["lever"]
    )
    results = run_discovery(store, [source], [SeekerTarget(seeker_id, profile)])

    assert results[0].status is SourceStatus.HEALTHY
    assert len(store.seeker_jobs(seeker_id)) == 1
    store.close()


def test_greenhouse_double_escaped_content_becomes_plain_text():
    item = {"id": 1, "title": "PM", "absolute_url": "https://x.test/1",
            "content": "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;"}
    source = ATSJsonSource(config("greenhouse"), fetcher=lambda u, t: {"jobs": [item]})
    assert source.search("", None)[0][0].description == "Build & ship"


def test_workday_description_is_fetched_once_and_fills_old_rows(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.migrate()
    seeker_id = store.add_seeker("A", 11, "a.yaml", 20)
    cfg = ATSConfig(name="gap", vendor="workday_cxs", company_name="Gap",
                    endpoint="https://gap.wd1.myworkdayjobs.com/wday/cxs/gap/GAP/jobs", enabled=True)
    row = {"title": "Merchandiser", "externalPath": "/job/SF/Merch_R1", "locationsText": "SF"}
    details = []

    def fetcher(url, timeout):
        details.append(url)
        return {"jobPostingInfo": {"jobDescription": "<p>Plan the assortment.</p>"}}

    profile = Profile(name="A", headline="M", location="SF", sources=["gap"], search_terms=["merch"])
    target = [SeekerTarget(seeker_id, profile)]
    for _ in range(2):
        source = ATSJsonSource(cfg, fetcher=fetcher,
                               poster=lambda u, b, t: {"jobPostings": [row]})
        assert run_discovery(store, [source], target)[0].status is SourceStatus.HEALTHY
    assert details == ["https://gap.wd1.myworkdayjobs.com/wday/cxs/gap/GAP/job/SF/Merch_R1"]
    desc = store.conn.execute("SELECT description FROM job_postings").fetchone()[0]
    assert desc == "Plan the assortment."
    store.close()


def test_oracle_description_uses_the_detail_endpoint():
    cfg = ATSConfig(name="ms", vendor="oracle_hcm", company_name="M&S", enabled=True,
                    endpoint="https://x.oraclecloud.com/hcmRestApi/resources/latest/"
                             "recruitingCEJobRequisitions?onlyData=true&siteNumber=CX_1")
    seen = []

    def fetcher(url, timeout):
        seen.append(url)
        return {"items": [{"ExternalDescriptionStr": "<p>Lead &amp; buy.</p>"}]}

    from jobpilot.store.models import Posting
    text = ATSJsonSource(cfg, fetcher=fetcher).describe(
        Posting(company_name="M&S", source="ms", external_id="123", title="Buyer",
                location=None, description=None, url=None, posted_at=None))
    assert text == "Lead & buy."
    assert "recruitingCEJobRequisitionDetails" in seen[0] and "Id=%22123%22" in seen[0]


def test_foundit_searches_by_term_and_city_with_paced_pages():
    cfg = ATSConfig(name="foundit_in", vendor="foundit", company_name="Foundit", enabled=True,
                    endpoint="https://www.foundit.in/home/api/searchResultsPage")
    urls, pauses = [], []
    job = {"jobId": 7, "title": "Merchandiser", "companyName": "Shoppers Stop",
           "locations": [{"city": "Delhi"}, {"country": "India"}],
           "jdUrl": "/job/merchandiser-7", "description": "<p>Buy &amp; plan</p>",
           "postedAt": 1789665971000}

    def fetcher(url, timeout):
        urls.append(url)
        return {"data": [dict(job, jobId=len(urls) * 100 + i) for i in range(15)]}

    found, raw = ATSJsonSource(cfg, fetcher=fetcher, sleeper=pauses.append).search(
        "senior merchandiser", "Gurgaon")
    assert len(urls) == 3 and len(found) == raw == 45, "at most three pages"
    assert pauses == [2.0, 2.0]
    assert "query=senior%20merchandiser" in urls[0] and "locations=Gurgaon" in urls[0]
    p = found[0]
    assert (p.company_name, p.location, p.url, p.description, p.posted_at) == (
        "Shoppers Stop", "Delhi", "https://www.foundit.in/job/merchandiser-7", "Buy & plan", "2026-09-17")


def test_himalayas_remote_search_keeps_country_filter_and_pages():
    cfg = ATSConfig(name="himalayas_us", vendor="himalayas", company_name="Himalayas (US)",
                    enabled=True, endpoint="https://himalayas.app/jobs/api/search?country=US")
    urls = []

    def fetcher(url, timeout):
        urls.append(url)
        return {"jobs": [{"guid": f"https://himalayas.app/j/{len(urls)}-{i}", "title": "Product Analyst",
                          "companyName": "Acme", "locationRestrictions": ["United States"],
                          "applicationLink": f"https://himalayas.app/j/{len(urls)}-{i}",
                          "description": "<p>Analyse</p>", "pubDate": 1788965060} for i in range(20)]}

    found, _ = ATSJsonSource(cfg, fetcher=fetcher, sleeper=lambda s: None).search("product analyst", None)
    assert len(urls) == 3 and len(found) == 60
    assert urls[1] == "https://himalayas.app/jobs/api/search?country=US&q=product%20analyst&page=2"
    assert (found[0].company_name, found[0].location) == ("Acme", "Remote (United States)")


def test_jobicy_remote_search_is_one_call_with_tag():
    cfg = ATSConfig(name="jobicy_us", vendor="jobicy", company_name="Jobicy (US)", enabled=True,
                    endpoint="https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa")
    urls = []

    def fetcher(url, timeout):
        urls.append(url)
        return {"jobs": [{"id": i, "jobTitle": "Business Analyst", "companyName": "ManTech",
                          "jobGeo": "USA", "url": f"https://jobicy.com/jobs/{i}",
                          "jobDescription": "d", "pubDate": "2026-09-03T12:35:03+00:00"} for i in range(50)]}

    found, _ = ATSJsonSource(cfg, fetcher=fetcher).search("business analyst", None)
    assert urls == ["https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa&tag=business%20analyst"]
    assert len(found) == 50 and found[0].location == "Remote (USA)"
