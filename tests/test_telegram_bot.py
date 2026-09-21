import json
import os
import re
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
    assert raw == 1 and [(j.company_name, j.title, j.location) for j in jobs] == [
        ("Acme", "Product Analyst", "Remote")]
    rv = ATSJsonSource(ATSConfig(name="remotive", vendor="remotive", company_name="Remotive",
                                 endpoint="https://x.test", enabled=True),
                       fetcher=lambda u, t: {"jobs": [{"id": 9, "title": "Data Analyst", "company_name": "Beta",
                                                        "url": "https://remotive.com/9",
                                                        "candidate_required_location": "USA"}]})
    assert [j.company_name for j in rv.search("data", None)[0]] == ["Beta"]


def test_workday_cxs_searches_server_side_and_builds_links():
    from jobpilot.config import ATSConfig
    from jobpilot.sources.ats_json import ATSJsonSource
    calls = []

    def poster(url, body, timeout):
        calls.append(body)
        rows = [{"title": "Merchandiser", "externalPath": f"/job/Gurgaon/M_{body['offset']}",
                 "locationsText": "Gurgaon"} for _ in range(20 if body["offset"] < 20 else 3)]
        return {"total": 23, "jobPostings": rows}

    src = ATSJsonSource(ATSConfig(
        name="pvh", vendor="workday_cxs", company_name="PVH", enabled=True,
        endpoint="https://pvh.wd1.myworkdayjobs.com/wday/cxs/pvh/PVH_Careers/jobs"), poster=poster)
    # Rows here are all "Gurgaon"; searching a different city still returns
    # them, since an employer-specific source is not filtered by home city.
    jobs, raw = src.search("senior merchandiser", "Mumbai")
    assert raw == 23 and len(jobs) == 23 and len(calls) == 2
    assert calls[0]["searchText"] == "senior merchandiser"
    assert jobs[0].url == "https://pvh.wd1.myworkdayjobs.com/PVH_Careers/job/Gurgaon/M_0"
    src.search("senior merchandiser", "Gurgaon")
    assert len(calls) == 2
    assert src.canary()[0] and len(calls) == 3


def test_oracle_hcm_builds_keyword_query_and_links():
    from jobpilot.config import ATSConfig
    from jobpilot.sources.ats_json import ATSJsonSource
    urls = []

    def fetcher(url, timeout):
        urls.append(url)
        return {"items": [{"requisitionList": [
            {"Id": "77", "Title": "Sourcing Merchandiser", "PrimaryLocation": "Bangalore, India",
             "PostedDate": "2026-09-19"}]}]}

    src = ATSJsonSource(ATSConfig(
        name="ms", vendor="oracle_hcm", company_name="M&S", enabled=True,
        endpoint="https://x.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?siteNumber=CX_1"),
        fetcher=fetcher)
    jobs, _ = src.search("merchandiser", "Bangalore")
    assert "keyword=merchandiser" in urls[0] and "siteNumber=CX_1" in urls[0]
    assert jobs[0].url == "https://x.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/77"
    # An employer-specific source is not filtered by the seeker's home city:
    # the seeker opted into this one employer, and location fit is the
    # scoring rubric's job, not a blunt substring match on office names.
    assert len(src.search("merchandiser", "Mumbai")[0]) == 1


def test_bamboohr_builds_careers_url_and_joins_location():
    from jobpilot.config import ATSConfig
    from jobpilot.sources.ats_json import ATSJsonSource
    src = ATSJsonSource(ATSConfig(
        name="proscia", vendor="bamboohr", company_name="Proscia", enabled=True,
        endpoint="https://proscia.bamboohr.com/careers/list"),
        fetcher=lambda u, t: {"meta": {"totalCount": 1}, "result": [
            {"id": "259", "jobOpeningName": "Implementation Engineer, EU",
             "atsLocation": {"country": "Ireland", "state": None, "city": "Dublin"}}]})
    jobs, raw = src.search("engineer", None)
    assert raw == 1 and len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://proscia.bamboohr.com/careers/259"
    assert job.location == "Dublin, Ireland" and job.company_name == "Proscia"


def test_breezy_maps_list_fields_and_keeps_own_url():
    from jobpilot.config import ATSConfig
    from jobpilot.sources.ats_json import ATSJsonSource
    src = ATSJsonSource(ATSConfig(
        name="embraer", vendor="breezy", company_name="Embraer", enabled=True,
        endpoint="https://embraer.breezy.hr/json"),
        fetcher=lambda u, t: [{"id": "4658211f5c93", "name": "A&P Technician",
                               "location": {"name": "Macon, GA"},
                               "url": "https://embraer.breezy.hr/p/4658211f5c93-a-p-technician",
                               "published_date": "2026-08-01T00:00:00Z"}])
    jobs, raw = src.search("technician", None)
    assert raw == 1 and jobs[0].location == "Macon, GA"
    assert jobs[0].url == "https://embraer.breezy.hr/p/4658211f5c93-a-p-technician"


def test_backoff_doubles_and_caps():
    from jobpilot.telegram_bot import _backoff_seconds, MAX_BACKOFF_SECONDS
    assert [_backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert _backoff_seconds(10) == MAX_BACKOFF_SECONDS


def test_run_exits_after_max_consecutive_poll_failures(home, store, monkeypatch):
    import jobpilot.telegram_bot as tb
    monkeypatch.setattr(tb, "MAX_CONSECUTIVE_FAILURES", 3)
    monkeypatch.setattr(tb.time, "sleep", lambda s: None)
    calls = []

    def failing_http(method, params, timeout=40):
        calls.append(method)
        if method != "getUpdates":
            return {"ok": True}
        raise TimeoutError("boom")

    bot = tb.Bot("TOKEN", home, store, http=failing_http)
    with pytest.raises(SystemExit):
        bot.run()
    assert calls.count("getUpdates") == 3


def test_run_resets_failure_count_after_success(home, store, monkeypatch):
    import jobpilot.telegram_bot as tb
    monkeypatch.setattr(tb.time, "sleep", lambda s: None)
    logged = []
    monkeypatch.setattr(tb, "_log", lambda msg: logged.append(msg))
    polls = {"n": 0}

    def flaky_http(method, params, timeout=40):
        if method != "getUpdates":
            return {"ok": True}
        polls["n"] += 1
        if polls["n"] == 1:
            raise TimeoutError("boom")
        if polls["n"] >= 3:
            raise SystemExit(0)
        return {"result": []}

    bot = tb.Bot("TOKEN", home, store, http=flaky_http)
    with pytest.raises(SystemExit):
        bot.run()
    assert any("poll recovered after 1 failure" in m for m in logged)


def test_single_instance_lock_blocks_a_live_duplicate(home, monkeypatch):
    from jobpilot import telegram_bot as tb
    state = home / "telegram-state"
    state.mkdir(exist_ok=True)
    # _pid_alive now verifies the PID is actually running telegram_bot, not
    # merely alive (a recycled PID could belong to any unrelated process,
    # including this test runner) -- so the "duplicate is live" case is
    # exercised by stubbing that check, not by relying on our own PID.
    monkeypatch.setattr(tb, "_pid_alive", lambda pid: True)
    assert tb._acquire_single_instance_lock(state)
    assert (state / "bot.pid").read_text() == str(os.getpid())
    assert not tb._acquire_single_instance_lock(state)


def test_single_instance_lock_ignores_a_dead_pid(home, monkeypatch):
    from jobpilot import telegram_bot as tb
    state = home / "telegram-state"
    state.mkdir(exist_ok=True)
    (state / "bot.pid").write_text("999999999")
    monkeypatch.setattr(tb, "_pid_alive", lambda pid: False)
    assert tb._acquire_single_instance_lock(state)
    assert (state / "bot.pid").read_text() == str(os.getpid())


def test_single_instance_lock_ignores_garbage_pid_file(home):
    from jobpilot.telegram_bot import _acquire_single_instance_lock
    state = home / "telegram-state"
    state.mkdir(exist_ok=True)
    (state / "bot.pid").write_text("not-a-pid")
    assert _acquire_single_instance_lock(state)


def test_pid_alive_on_windows_verifies_it_is_our_process(monkeypatch):
    from jobpilot import telegram_bot as tb
    monkeypatch.setattr(tb.os, "name", "nt")

    def fake_run(cmd, **kw):
        pid = re.search(r"ProcessId=(\d+)", cmd[-1]).group(1)
        cmdlines = {"111": "...python.exe -m jobpilot.telegram_bot", "222": "notepad.exe"}
        return SimpleNamespace(returncode=0, stdout=cmdlines.get(pid, ""), stderr="")

    monkeypatch.setattr(tb.subprocess, "run", fake_run)
    assert tb._pid_alive(111) is True
    assert tb._pid_alive(222) is False, "a recycled PID belonging to an unrelated process must not count as alive"


def test_pid_alive_on_windows_fails_open_on_check_error(monkeypatch):
    from jobpilot import telegram_bot as tb
    monkeypatch.setattr(tb.os, "name", "nt")

    def raising_run(cmd, **kw):
        raise OSError("powershell not found")

    monkeypatch.setattr(tb.subprocess, "run", raising_run)
    assert tb._pid_alive(999) is False, "an unverifiable check must not block startup"


def test_set_menu_survives_individual_call_failures(home, store):
    calls = []

    def flaky_http(method, params, timeout=40):
        calls.append(method)
        if method == "setMyName":
            raise RuntimeError("429 Too Many Requests")
        return {"ok": True}

    bot = tb.Bot("TOKEN", home, store, http=flaky_http)
    bot.set_menu()
    assert calls == ["setMyCommands", "setMyName", "setMyDescription", "setMyShortDescription"]


def test_single_instance_lock_is_race_safe_under_concurrent_start(home, monkeypatch):
    """The actual bug this guards against: two processes starting within
    milliseconds of each other must not both win. A plain read-then-write
    has a gap both can slip through; only one thread may hold the win here.

    All 20 threads here share one real PID (os.getpid()), so the platform's
    real _pid_alive (which on Windows verifies the PID is actually running
    telegram_bot, not just alive -- this test process is pytest, not that)
    would call every thread's own claim "not really us" and let them all
    reclaim the lock. Stubbed to plain liveness so this test isolates what
    it's actually checking: the acquire path's atomicity, not _pid_alive's
    own verification, which has its own dedicated tests above.
    """
    import threading
    from jobpilot import telegram_bot as tb
    from jobpilot.telegram_bot import _acquire_single_instance_lock
    monkeypatch.setattr(tb, "_pid_alive", lambda pid: pid == os.getpid())
    state = home / "telegram-state"
    state.mkdir(exist_ok=True)
    results = []
    barrier = threading.Barrier(20)

    def attempt():
        barrier.wait()
        results.append(_acquire_single_instance_lock(state))

    threads = [threading.Thread(target=attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


RESUME_TEXT = (
    "Sam Rivera\nData Analyst\nsam@example.test\n"
    "Acme Corp, Analyst, Jan 2023 - Present\n"
    "Built weekly KPI dashboards in Tableau for the sales team.\n"
    "State University, B.S. Statistics, 2018 - 2022\nSkills: SQL, Python, Tableau\n"
)
STRUCTURED = {
    "name": "Sam Rivera", "headline": "Data Analyst", "contact": ["sam@example.test"],
    "experience": [{"id": "acme", "employer": "Acme Corp", "title": "Analyst",
                    "dates": "Jan 2023 - Present", "bullets": [
                        {"id": "kpi", "text": "Built weekly KPI dashboards in Tableau for the sales team."}]}],
    "education": [{"id": "su", "institution": "State University",
                   "degree": "B.S. Statistics", "dates": "2018 - 2022"}],
    "skills": ["SQL", "Python", "Tableau"],
}


def _registered(home, store, uid=700):
    build_tools(store, home, operator_mode=False, onboard_telegram_id=uid)["register_me"](
        "Sam", "Analyst", "Austin", ["data analyst"], ["indeed_us"])
    return store.seeker_by_telegram_id(uid)


def _docx_bytes(tmp_path, text):
    from docx import Document
    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    path = tmp_path / "in.docx"
    doc.save(path)
    return path.read_bytes()


def _upload_bot(home, store, data):
    sent, runs, files = [], [], []

    def http(method, params, timeout=40):
        sent.append((method, params))
        return {"ok": True, "result": {"file_path": "documents/file_1.docx"}}

    def runner(cmd, **kw):
        runs.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    bot = tb.Bot("TOKEN", home, store, http=http, runner=runner,
                 download=lambda url: data, upload=lambda chat, path: files.append(path))
    return bot, sent, runs, files


def _doc_msg(uid, name="cv.docx", size=1000):
    return {"update_id": 1, "message": {
        "from": {"id": uid, "is_bot": False}, "chat": {"id": uid, "type": "private"},
        "document": {"file_id": "F1", "file_name": name, "file_size": size}}}


def test_uploaded_resume_is_stored_and_handed_to_claude_as_data(home, store, tmp_path):
    seeker = _registered(home, store)
    bot, sent, runs, _ = _upload_bot(home, store, _docx_bytes(tmp_path, RESUME_TEXT))
    bot.handle(_doc_msg(700))
    folder = (home / seeker.profile_path).parent
    assert (folder / "uploaded_resume.docx").is_file()
    assert "Built weekly KPI dashboards" in (folder / "resume_source.txt").read_text()
    prompt = runs[0][1]["input"]
    assert "save_base_resume" in prompt and "RESUME START" in prompt
    assert "Built weekly KPI dashboards" in prompt
    assert "Resume received" in _texts(sent)[0]


def test_upload_refuses_wrong_type_oversize_and_unreadable(home, store, tmp_path):
    bot, sent, runs, _ = _upload_bot(home, store, b"x")
    bot.handle(_doc_msg(702, name="cv.png"))
    assert "PDF or Word" in _texts(sent)[-1]
    bot.handle(_doc_msg(702, size=tb.MAX_UPLOAD + 1))
    assert "too large" in _texts(sent)[-1]
    bot.handle(_doc_msg(702))  # unreadable bytes
    assert "could not be read" in _texts(sent)[-1]
    assert runs == []


def test_new_user_is_onboarded_from_resume_then_it_is_processed(home, store, tmp_path):
    bot, sent, runs, _ = _upload_bot(home, store, _docx_bytes(tmp_path, RESUME_TEXT))
    onboarding = build_tools(store, home, operator_mode=False, onboard_telegram_id=703)
    prompts = []

    def runner(cmd, **kw):
        prompts.append(kw["input"])
        if len(prompts) == 1:  # the onboarding session registers the user
            onboarding["register_me"]("Sam Rivera", "Data Analyst", "Austin",
                                      ["data analyst"], ["indeed_us"])
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    bot.runner = runner
    bot.handle(_doc_msg(703))
    assert "set up my profile" in prompts[0] and "Built weekly KPI" in prompts[0]
    assert len(prompts) == 2 and "save_base_resume" in prompts[1]
    folder = (home / store.seeker_by_telegram_id(703).profile_path).parent
    assert (folder / "uploaded_resume.docx").is_file()
    assert (folder / "resume_source.txt").is_file()
    assert not (home / "telegram-state" / "pending" / "703").exists()


def test_available_sources_offers_boards_and_employer_feeds(home, store):
    tools = build_tools(store, home, operator_mode=False, onboard_telegram_id=704)
    catalog = {row["source"]: row["covers"] for row in tools["available_sources"]()}
    assert "job board" in catalog["indeed_us"] and "linkedin_us" not in catalog
    assert catalog["databricks"] == "Databricks careers page, all locations"
    assert catalog["remoteok"] == "remote jobs, worldwide"
    assert tools["register_me"]("Sam", "Analyst", "Austin", ["analyst"],
                                ["indeed_us", "databricks"])["ok"]


def test_save_base_resume_accepts_only_verbatim_facts(home, store):
    seeker = _registered(home, store)
    tools = build_tools(store, home, pinned_seeker_id=seeker.id, operator_mode=False)
    assert not tools["save_base_resume"](seeker.id, STRUCTURED)["ok"]  # no upload yet
    folder = (home / seeker.profile_path).parent
    (folder / "resume_source.txt").write_text(RESUME_TEXT, encoding="utf-8")
    invented = json.loads(json.dumps(STRUCTURED))
    invented["experience"][0]["bullets"][0]["text"] = "Grew revenue 40% with dashboards."
    refused = tools["save_base_resume"](seeker.id, invented)
    assert not refused["ok"] and refused["unverified"] == ["Grew revenue 40% with dashboards."]
    assert not (folder / "base_resume.yaml").exists()
    assert tools["save_base_resume"](seeker.id, STRUCTURED)["ok"]
    facts = tools["resume_facts"](seeker.id)
    assert facts["ok"] and facts["experience"][0]["employer"] == "Acme Corp"
    assert not (folder / "base_resume.draft.yaml").exists()


def test_rendered_resume_is_sent_back_as_a_document(home, store, monkeypatch):
    from jobpilot.store.db import Posting
    seeker = _registered(home, store)
    folder = (home / seeker.profile_path).parent
    (folder / "resume_source.txt").write_text(RESUME_TEXT, encoding="utf-8")
    tools = build_tools(store, home / "out", pinned_seeker_id=seeker.id, operator_mode=False)
    tools["save_base_resume"](seeker.id, STRUCTURED)
    pid = store.upsert_posting(Posting(
        company_name="Globex", source="indeed", external_id="g1", title="Data Analyst",
        location="Austin", description="d", url="https://x.test/g1", posted_at="2026-09-18"))
    store.link_posting(seeker.id, pid)
    bot, sent, runs, files = _upload_bot(home, store, b"")
    old = tools["render_resume"](seeker.id, pid)
    assert old["ok"] and old["validation"]["ok"]

    def render_during_run(cmd, **kw):
        runs.append(cmd)
        tools["render_resume"](seeker.id, pid)
        return SimpleNamespace(returncode=0, stdout="Here is your resume.", stderr="")

    bot.runner = render_during_run
    bot.handle(_msg(700, "/resume"))
    assert len(files) == 1 and files[0].suffix == ".docx" and files[0] != Path(old["path"])


def test_caption_sent_with_the_resume_is_passed_on_as_the_request(home, store, tmp_path):
    _registered(home, store)
    bot, sent, runs, _ = _upload_bot(home, store, _docx_bytes(tmp_path, RESUME_TEXT))
    msg = _doc_msg(700)
    msg["message"]["caption"] = "Tailor resumes for my top 5 jobs and check Ralph Lauren openings"
    bot.handle(msg)
    prompt = runs[0][1]["input"]
    assert prompt.index("RESUME END") < prompt.index("check Ralph Lauren openings")
    assert "actual request" in prompt
