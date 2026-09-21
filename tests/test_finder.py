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
    fetch = _fetcher({board: {"name": "Acme"}, jobs: {"jobs": [{"id": 1}]}})
    hit = find_feed("Acme Inc", fetch)
    assert hit["vendor"] == "greenhouse" and hit["endpoint"] == jobs and hit["jobs"] == 1
    impostor = _fetcher({board: {"name": "Acme Plumbing Supplies"}, jobs: {"jobs": [{"id": 1}]}})
    assert find_feed("Acme", impostor) is None


def test_lever_and_ashby_need_an_exact_slug_and_live_jobs():
    ashby = "https://api.ashbyhq.com/posting-api/job-board/north-wind"
    assert find_feed("North Wind", _fetcher({ashby: {"jobs": [{"id": "a"}]}}))["vendor"] == "ashby"
    assert find_feed("North Wind", _fetcher({ashby: {"jobs": []}})) is None
    assert find_feed("Nobody", _fetcher({})) is None


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
    assert "no public job feed" in out["target_companies"]["Tiny Local Shop"]
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
