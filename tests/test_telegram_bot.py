import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jobpilot import telegram_bot as tb
from jobpilot.server import build_tools
from jobpilot.store.db import Store

REPO = Path(__file__).parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    shutil.copy(REPO / "config.toml", tmp_path / "config.toml")
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(home):
    s = Store(home / "t.db")
    s.migrate()
    yield s
    s.close()


def _bot(home, store, reply="hi there"):
    sent, runs = [], []

    def http(method, params, timeout=40):
        sent.append((method, params))
        return {"ok": True}

    def runner(cmd, **kw):
        runs.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout=reply, stderr="")

    return tb.Bot("TOKEN", home, store, http=http, runner=runner), sent, runs


def _msg(uid, text, chat="private", bot=False):
    return {"update_id": 1, "message": {
        "from": {"id": uid, "is_bot": bot}, "chat": {"id": uid, "type": chat}, "text": text}}


def _texts(sent):
    return [p["text"] for m, p in sent if m == "sendMessage"]


def test_group_and_bot_messages_are_ignored(home, store):
    bot, sent, runs = _bot(home, store)
    bot.handle(_msg(5, "hi", chat="group"))
    bot.handle(_msg(5, "hi", bot=True))
    assert not sent and not runs


def test_unknown_sender_is_onboarded_not_served(home, store):
    bot, sent, runs = _bot(home, store)
    bot.handle(_msg(42, "hello"))
    cmd, kw = runs[0]
    cfg = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text())
    env = cfg["mcpServers"]["jobpilot"]["env"]
    assert env["JOBPILOT_ONBOARD_TELEGRAM_ID"] == "42" and "JOBPILOT_SEEKER_ID" not in env
    assert kw["input"] == "hello" and "--session-id" in cmd
    assert _texts(sent) == ["hi there"]


def test_known_sender_is_pinned_to_own_seeker_and_resumes(home, store):
    sid = store.add_seeker("A", 7, "x.yaml", 20)
    store.add_seeker("B", 8, "y.yaml", 20)
    bot, sent, runs = _bot(home, store)
    bot.handle(_msg(7, "one"))
    bot.handle(_msg(7, "two"))
    env = json.loads(Path(runs[0][0][runs[0][0].index("--mcp-config") + 1]).read_text())[
        "mcpServers"]["jobpilot"]["env"]
    assert env["JOBPILOT_SEEKER_ID"] == str(sid)
    assert "--session-id" in runs[0][0] and "--resume" in runs[1][0]
    assert "--tools" in runs[0][0] and runs[0][0][runs[0][0].index("--tools") + 1] == ""


def test_slash_command_maps_to_fixed_prompt_and_reset(home, store):
    store.add_seeker("A", 7, "x.yaml", 20)
    bot, sent, runs = _bot(home, store)
    bot.handle(_msg(7, "/jobs"))
    assert "best new job matches" in runs[0][1]["input"].lower()
    bot.handle(_msg(7, "/reset"))
    assert len(runs) == 1 and "new conversation" in _texts(sent)[-1]


def test_rate_limit_and_length_cap(home, store, monkeypatch):
    monkeypatch.setattr(tb, "PER_HOUR", 2)
    bot, sent, runs = _bot(home, store)
    for _ in range(3):
        bot.handle(_msg(9, "hi"))
    assert len(runs) == 2 and "too quickly" in _texts(sent)[-1]
    bot.handle(_msg(10, "x" * 3000))
    assert "too long" in _texts(sent)[-1]


def test_long_reply_is_chunked(home, store):
    bot, sent, _ = _bot(home, store, reply="y" * 9000)
    bot.handle(_msg(3, "hi"))
    assert [len(t) for t in _texts(sent)] == [4000, 4000, 1000]


def test_register_me_creates_isolated_seeker(home, store):
    tools = build_tools(store, home, operator_mode=False, onboard_telegram_id=555)
    assert set(tools) == {"available_sources", "register_me"}
    ok = tools["register_me"]("Sam", "Data analyst", "Austin", ["data analyst"],
                              ["indeed_us", "linkedin_us", "bogus"])
    assert ok["ok"]
    seeker = store.seeker_by_telegram_id(555)
    prof = yaml.safe_load((home / seeker.profile_path).read_text())
    assert prof["sources"] == ["indeed_us"] and prof["rubric"] == "general"
    assert not tools["register_me"]("Sam", "x", "y", ["z"], ["indeed_us"])["ok"]


def test_register_me_rejects_no_valid_source(home, store):
    tools = build_tools(store, home, operator_mode=False, onboard_telegram_id=556)
    assert not tools["register_me"]("Sam", "h", "l", ["t"], ["linkedin_us"])["ok"]
    assert store.seeker_by_telegram_id(556) is None


def test_run_my_search_daily_limit(home, store, monkeypatch):
    sid = store.add_seeker("A", 1, "x.yaml", 20)
    tools = build_tools(store, home, pinned_seeker_id=sid, operator_mode=False)
    assert "run_my_search" in tools and "discover" not in tools
    calls = []
    monkeypatch.setattr(store, "event_count_since", lambda *a: len(calls))
    tools2 = build_tools(store, home, pinned_seeker_id=sid, operator_mode=False)
    calls.extend([1, 2, 3])
    assert not tools2["run_my_search"](sid)["ok"]


def test_register_me_keeps_one_searchable_city(home, store):
    tools = build_tools(store, home, operator_mode=False, onboard_telegram_id=600)
    assert tools["register_me"]("Sam", "h", "Gurgaon / Delhi-NCR, India", ["t"], ["indeed_in"])["ok"]
    prof = yaml.safe_load((home / store.seeker_by_telegram_id(600).profile_path).read_text())
    assert prof["location"] == "Gurgaon"


def test_update_my_profile_changes_only_given_fields(home, store):
    onboard = build_tools(store, home, operator_mode=False, onboard_telegram_id=601)
    onboard["register_me"]("Sam", "Analyst", "Pune", ["analyst"], ["indeed_in"])
    sid = store.seeker_by_telegram_id(601).id
    tools = build_tools(store, home, pinned_seeker_id=sid, operator_mode=False)
    out = tools["update_my_profile"](sid, location="Mumbai / Navi Mumbai", exclude_keywords=["intern"])
    assert out["ok"] and out["profile"]["location"] == "Mumbai"
    assert out["profile"]["headline"] == "Analyst" and out["profile"]["exclude_keywords"] == ["intern"]
    assert not tools["update_my_profile"](sid, sources=["linkedin_in"])["ok"]
    assert not tools["update_my_profile"](sid + 1, location="x")["ok"]


def test_push_digests_records_only_after_delivery(home, store):
    from jobpilot.notify import push_digests
    from jobpilot.store.models import Posting
    sid = store.add_seeker("A", 70, "x.yaml", 20)
    pid = store.upsert_posting(Posting(
        company_name="Acme", source="indeed_in", external_id="e1", title="Analyst",
        location="Pune", description="d", url="https://x.test/1", posted_at="2026-09-19"))
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 90, "Strong match")
    seen = []

    def failing(chat, text):
        raise RuntimeError("network")

    assert push_digests(store, failing) == 0
    assert push_digests(store, lambda c, t: seen.append((c, t))) == 1
    assert seen[0][0] == 70 and "CareerDesk update" in seen[0][1] and "Analyst, Acme" in seen[0][1]
    assert push_digests(store, lambda c, t: seen.append((c, t))) == 0


def test_link_code_attaches_existing_seeker_once(home, store):
    sid = store.add_seeker("Sam", None, "x.yaml", 20)
    bot, sent, runs = _bot(home, store)
    code = bot.create_link_code(sid)
    bot.handle(_msg(800, "/link wrong"))
    assert "not valid" in _texts(sent)[-1] and store.seeker_by_telegram_id(800) is None
    bot.handle(_msg(800, f"/link {code}"))
    assert store.seeker_by_telegram_id(800).id == sid and "linked" in _texts(sent)[-1]
    bot.handle(_msg(801, f"/link {code}"))
    assert store.seeker_by_telegram_id(801) is None and not runs


def test_link_refuses_account_that_already_has_a_profile(home, store):
    store.add_seeker("Existing", 900, "x.yaml", 20)
    sid = store.add_seeker("Other", None, "y.yaml", 20)
    bot, sent, _ = _bot(home, store)
    bot.handle(_msg(900, f"/link {bot.create_link_code(sid)}"))
    assert "could not be linked" in _texts(sent)[-1]


def test_remote_feed_vendors_keep_each_rows_company():
    from jobpilot.config import ATSConfig
    from jobpilot.sources.ats_json import ATSJsonSource
    ok = ATSJsonSource(ATSConfig(name="remoteok", vendor="remoteok", company_name="RemoteOK",
                                 endpoint="https://x.test", enabled=True),
                       fetcher=lambda u, t: [{"legal": "notice"},
                                             {"id": "1", "position": "Product Analyst", "company": "Acme",
                                              "url": "https://remoteok.com/1", "date": "2026-09-18"}])
    jobs, raw = ok.search("analyst", "Remote")
    assert raw == 2 and [(j.company_name, j.title, j.location) for j in jobs] == [
        ("Acme", "Product Analyst", "Remote")]
    rv = ATSJsonSource(ATSConfig(name="remotive", vendor="remotive", company_name="Remotive",
                                 endpoint="https://x.test", enabled=True),
                       fetcher=lambda u, t: {"jobs": [{"id": 9, "title": "Data Analyst", "company_name": "Beta",
                                                        "url": "https://remotive.com/9",
                                                        "candidate_required_location": "USA"}]})
    assert [j.company_name for j in rv.search("data", None)[0]] == ["Beta"]
