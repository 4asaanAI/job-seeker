import shutil
from pathlib import Path

import pytest
import yaml

from jobpilot.config import ATSConfig
from jobpilot.server import build_tools
from jobpilot.sources.ats_json import ATSJsonSource
from jobpilot.sources.finder import find_feed, slugs
from jobpilot.store.db import Store

REPO = Path(__file__).parents[1]
NO_SEARCH = lambda query, domain: []  # noqa: E731 - never touch the real web


def _fetcher(pages):
    def fetch(url, timeout):
        if url not in pages:
            raise RuntimeError("HTTP 404")
        return pages[url]
    return fetch


def test_slugs_drop_legal_suffixes():
    assert slugs("Acme Labs, Inc.") == ["acmelabs", "acme-labs"]
    assert slugs("Inc.") == []


def test_greenhouse_match_requires_the_board_to_name_the_company():
    board = "https://boards-api.greenhouse.io/v1/boards/acme"
    jobs = board + "/jobs?content=true"
    fetch = _fetcher({board: {"name": "Acme"}, jobs: {"jobs": [{"id": 1, "title": "PM"}]}})
    hit = find_feed("Acme Inc", fetch, search=NO_SEARCH)
    assert hit["vendor"] == "greenhouse" and hit["endpoint"] == jobs and hit["jobs"] == 1
    impostor = _fetcher({board: {"name": "Acme Plumbing Supplies"}, jobs: {"jobs": [{"id": 1}]}})
    assert find_feed("Acme", impostor, search=NO_SEARCH) is None


def test_lever_and_ashby_need_an_exact_slug_and_live_jobs():
    ashby = "https://api.ashbyhq.com/posting-api/job-board/north-wind"
    assert find_feed("North Wind", _fetcher({ashby: {"jobs": [{"id": "a", "title": "PM"}]}}), search=NO_SEARCH)["vendor"] == "ashby"
    assert find_feed("North Wind", _fetcher({ashby: {"jobs": []}}), search=NO_SEARCH) is None
    assert find_feed("Nobody", _fetcher({}), search=NO_SEARCH) is None


def test_ashby_rows_become_postings_and_unlisted_are_skipped():
    cfg = ATSConfig(name="ashby_x", vendor="ashby", company_name="X", endpoint="https://x.test", enabled=True)
    payload = {"jobs": [
        {"id": "1", "title": " Product Manager", "location": "Austin, TX",
         "jobUrl": "https://jobs.ashbyhq.com/x/1", "descriptionPlain": "Own the roadmap",
         "publishedAt": "2026-09-01"},
        {"id": "2", "title": "Hidden", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/x/2"},
    ]}
    postings = ATSJsonSource(cfg, fetcher=lambda u, t: payload)._load()[1]
    assert [(p.title, p.location, p.description) for p in postings] == [
        ("Product Manager", "Austin, TX", "Own the roadmap")]


@pytest.fixture
def home(tmp_path, monkeypatch):
    shutil.copy(REPO / "config.toml", tmp_path / "config.toml")
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    store = Store(tmp_path / "t.db")
    store.migrate()
    yield tmp_path, store
    store.close()


def _finder(calls):
    def finder(company):
        calls.append(company)
        if company == "Northwind":
            return {"vendor": "ashby", "slug": "northwind", "company_name": "Northwind",
                    "endpoint": "https://api.ashbyhq.com/posting-api/job-board/northwind", "jobs": 7}
        return None
    return finder


def test_target_companies_get_their_feeds_found_and_shared(home):
    path, store = home
    calls = []
    onboard = build_tools(store, path, operator_mode=False, onboard_telegram_id=9,
                          feed_finder=_finder(calls))
    out = onboard["register_me"]("Sam", "PM", "Austin", ["product manager"], ["indeed_us"],
                                 target_companies=["Northwind", "Stripe", "Tiny Local Shop"])
    assert out["ok"]
    assert "7 open jobs" in out["target_companies"]["Northwind"]
    assert "already covered" in out["target_companies"]["Stripe"]  # configured feed, no lookup
    assert "not found yet" in out["target_companies"]["Tiny Local Shop"]
    assert calls == ["Northwind", "Tiny Local Shop"]
    seeker = store.seeker_by_telegram_id(9)
    profile = yaml.safe_load((path / seeker.profile_path).read_text())
    assert profile["sources"] == ["indeed_us", "ashby_northwind", "stripe"]

    # Found once, the feed is offered to everyone and never looked up again.
    other = build_tools(store, path, operator_mode=False, onboard_telegram_id=10,
                        feed_finder=_finder(calls))
    names = {row["source"] for row in other["available_sources"]()}
    assert "ashby_northwind" in names
    assert "already covered" in other["register_me"](
        "Kim", "PM", "Austin", ["pm"], ["indeed_us"], target_companies=["Northwind Inc"]
    )["target_companies"]["Northwind Inc"]
    assert calls == ["Northwind", "Tiny Local Shop"]


def test_update_profile_looks_up_new_companies_with_a_cap(home):
    path, store = home
    calls = []
    build_tools(store, path, operator_mode=False, onboard_telegram_id=11,
                feed_finder=_finder(calls))["register_me"](
        "Sam", "PM", "Austin", ["pm"], ["indeed_us"])
    sid = store.seeker_by_telegram_id(11).id
    tools = build_tools(store, path, pinned_seeker_id=sid, operator_mode=False,
                        feed_finder=_finder(calls))
    many = [f"Company {i}" for i in range(7)]
    out = tools["update_my_profile"](sid, target_companies=["Northwind", *many])
    assert out["ok"] and "ashby_northwind" in out["profile"]["sources"]
    assert len(calls) == 5
    assert "not checked this time" in out["target_companies"]["Company 6"]


def test_ats_page_urls_become_feed_endpoints():
    from jobpilot.sources.finder import candidates_from_url as c
    assert c("https://nike.wd1.myworkdayjobs.com/en-US/nke/job/Portland/PM_R-1")[0]["endpoint"] == \
        "https://nike.wd1.myworkdayjobs.com/wday/cxs/nike/nke/jobs"
    assert c("https://walmart.wd504.myworkdayjobs.com/Non-WorkdayInternal") == []
    assert c("https://careers.smartrecruiters.com/BoschGroup/it")[0]["endpoint"] == \
        "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings"
    oracle = c("https://fa-x.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/")[0]
    assert oracle["vendor"] == "oracle_hcm" and oracle["endpoint"].endswith("siteNumber=CX_1")
    assert c("https://job-boards.greenhouse.io/acme/jobs/1")[0]["key"] == "acme"
    assert c("https://example.com/careers") == []


def _search(results):
    return lambda query, domain: results.get(domain, [])


def test_workday_tenant_is_found_by_search_and_impostors_are_rejected():
    results = {"myworkdayjobs.com": [
        {"url": "https://genpact.wd108.myworkdayjobs.com/External_Careers", "title": "Genpact"},
        {"url": "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/X", "title": "Acme"},
    ]}
    posts = []

    def post(url, body, timeout):
        posts.append(url)
        return {"total": 3, "jobPostings": [
            {"externalPath": "/job/1", "title": "Analyst", "locationsText": "Austin"}]}

    hit = find_feed("Acme Corp", _fetcher({}), post=post, search=_search(results))
    assert hit["vendor"] == "workday_cxs"
    assert hit["endpoint"] == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs"
    assert not any("genpact" in url for url in posts)  # never even queried


def test_smartrecruiters_guess_needs_the_api_to_name_the_company():
    url = "https://api.smartrecruiters.com/v1/companies/{}/postings?limit=1"
    bosch = {"content": [{"id": "1", "name": "Engineer", "company": {"name": "Bosch Group"}}]}
    pages = {url.format("BoschGroup"): bosch,
             url.format("BoschGroup").replace("limit=1", "limit=100&offset=0"): bosch}
    hit = find_feed("Bosch", _fetcher(pages), search=NO_SEARCH)
    assert hit["vendor"] == "smartrecruiters" and hit["slug"] == "BoschGroup"
    other = {url.format("Acme"): {"content": [{"id": "1", "name": "x", "company": {"name": "Beta"}}]}}
    assert find_feed("Acme", _fetcher(other), search=NO_SEARCH) is None


def test_smartrecruiters_rows_become_postings_with_public_links():
    cfg = ATSConfig(name="sr", vendor="smartrecruiters", company_name="Bosch",
                    endpoint="https://api.smartrecruiters.com/v1/companies/BoschGroup/postings",
                    enabled=True)
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return {"content": [{"id": "74", "name": "Controls Engineer",
                             "location": {"fullLocation": "Charleston, SC, United States"},
                             "releasedDate": "2026-09-21"}]}

    found, raw = ATSJsonSource(cfg, fetcher=fetch).search("controls engineer", "Austin")
    assert raw == 1 and found[0].url == "https://jobs.smartrecruiters.com/BoschGroup/74"
    assert found[0].location == "Charleston, SC, United States"
    assert "q=controls%20engineer" in seen[0]


def test_daily_discovery_retries_companies_that_were_missed(home, monkeypatch):
    path, store = home
    (path / "data").mkdir(exist_ok=True)
    shutil.copy(REPO / "data" / "companies.csv", path / "data" / "companies.csv")
    monkeypatch.setattr("jobpilot.server.run_discovery", lambda store, sources, targets: [])
    online = {"up": False}

    def finder(company):
        if online["up"] and company == "Northwind":
            return {"vendor": "ashby", "slug": "northwind", "company_name": "Northwind",
                    "endpoint": "https://api.ashbyhq.com/posting-api/job-board/northwind", "jobs": 7}
        return None

    build_tools(store, path, operator_mode=False, onboard_telegram_id=12,
                feed_finder=finder)["register_me"](
        "Sam", "PM", "Austin", ["pm"], ["indeed_us"], target_companies=["Northwind"])
    seeker = store.seeker_by_telegram_id(12)
    operator = build_tools(store, path, feed_finder=finder)
    assert operator["discover"]([seeker.id])["feeds_added"] == {}  # search still down
    online["up"] = True
    assert operator["discover"]([seeker.id])["feeds_added"] == {seeker.id: ["ashby_northwind"]}
    profile = yaml.safe_load((path / seeker.profile_path).read_text())
    assert profile["sources"] == ["indeed_us", "ashby_northwind"]
    assert operator["discover"]([seeker.id])["feeds_added"] == {}  # nothing left to retry


def test_wigolo_runs_without_any_llm_provider_key(monkeypatch):
    from jobpilot.sources import finder
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("PATH_MARKER", "kept")
    env = finder.wigolo_env()
    assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["PATH_MARKER"] == "kept"


def test_searxng_is_tried_first_and_wigolo_only_when_it_is_down(monkeypatch):
    from jobpilot.sources import finder
    calls = []
    monkeypatch.setattr(finder, "searxng_search", lambda q, d: calls.append("sx") and None)
    monkeypatch.setattr(finder, "wigolo_search", lambda q, d: calls.append("wg") or [{"url": "u"}])
    assert finder.web_search("Nike careers jobs", "myworkdayjobs.com") == [{"url": "u"}]
    assert calls == ["sx", "wg"]
    monkeypatch.setattr(finder, "searxng_search", lambda q, d: [{"url": "s"}])
    calls.clear()
    assert finder.web_search("x", "y") == [{"url": "s"}] and calls == []


def test_searxng_keeps_only_results_on_the_requested_domain(monkeypatch):
    import io
    import json as _json
    from jobpilot.sources import finder
    body = {"results": [{"url": "https://nike.wd1.myworkdayjobs.com/nke"},
                        {"url": "https://example.com/nike"}]}
    monkeypatch.setattr(finder, "urlopen", lambda url, timeout: io.BytesIO(_json.dumps(body).encode()))
    assert finder.searxng_search("Nike", "myworkdayjobs.com") == [
        {"url": "https://nike.wd1.myworkdayjobs.com/nke"}]


def test_main_careers_site_beats_early_careers_and_country_tenants():
    from jobpilot.sources.finder import _rank, candidates_from_url
    urls = ["https://deloitteie.wd3.myworkdayjobs.com/Early_Careers",
            "https://visa.wd5.myworkdayjobs.com/Visa_Early_Careers",
            "https://visa.wd5.myworkdayjobs.com/Visa"]
    cands = [candidates_from_url(u)[0] for u in urls]
    assert sorted(cands, key=lambda c: _rank(c, "visa"))[0]["slug"] == "visa_Visa"
    deloitte = [candidates_from_url(u)[0] for u in (
        "https://deloitteie.wd3.myworkdayjobs.com/Early_Careers",
        "https://deloitte.wd1.myworkdayjobs.com/External")]
    assert sorted(deloitte, key=lambda c: _rank(c, "deloitte"))[0]["key"] == "deloitte"


def test_empty_searxng_answer_does_not_trigger_the_slow_fallback(monkeypatch):
    from jobpilot.sources import finder
    monkeypatch.setattr(finder, "searxng_search", lambda q, d: [])
    monkeypatch.setattr(finder, "wigolo_search", lambda q, d: pytest.fail("fallback ran"))
    assert finder.web_search("Nobody", "myworkdayjobs.com") == []
