from pathlib import Path
import pytest
from openpyxl import load_workbook

from jobpilot import server
from jobpilot.config import StructuredConfig
from jobpilot.joblist import write_job_list
from jobpilot.sources.jsonld_source import JsonLdSource
from jobpilot.store.db import Store
from jobpilot.store.models import Posting

PROPERTY_VALUE_HTML = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"JobPosting","identifier":{"@type":"PropertyValue","name":"Acme Corp","value":"J-100"},
  "title":"Analyst","url":"https://careers.example.test/1","hiringOrganization":{"name":"Acme Corp"},
  "jobLocation":{"address":{"addressLocality":"Austin"}},"description":"d"},
 {"@type":"JobPosting","identifier":{"@type":"PropertyValue","name":"Acme Corp","value":"J-200"},
  "title":"Senior Analyst","url":"https://careers.example.test/2","hiringOrganization":{"name":"Acme Corp"},
  "jobLocation":{"address":{"addressLocality":"Austin"}},"description":"d"}]}
</script>
"""


def test_jsonld_property_value_ids_do_not_collapse_to_the_company_name():
    cfg = StructuredConfig(name="acme", company_name="Acme Corp", endpoint="https://careers.example.test",
                           enabled=True, canary_min_results=1)
    found, raw = JsonLdSource(cfg, fetcher=lambda url, timeout: PROPERTY_VALUE_HTML).search("analyst", None)
    assert raw == 2
    assert sorted(p.external_id for p in found) == ["J-100", "J-200"]


@pytest.mark.parametrize("evil", ["=HYPERLINK(\"http://evil.test\",\"x\")", "+SUM(1)", "-2+3", "@cmd"])
def test_job_list_never_contains_live_formulas(tmp_path, evil):
    store = Store(tmp_path / "j.db")
    store.migrate()
    profile = tmp_path / "p" / "profile.yaml"
    profile.parent.mkdir()
    profile.write_text("name: A\nheadline: M\nlocation: X\nsources: [indeed_in]\nsearch_terms: [m]\n", encoding="utf-8")
    sid = store.add_seeker("A", None, str(profile), 20)
    pid = store.upsert_posting(Posting(company_name=evil, source="indeed_in", external_id="1", title=evil,
                                       location=evil, description="d", url="https://jobs.test/1", posted_at="2026-10-18"))
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 90, evil, "t")
    write_job_list(store, sid, tmp_path / "out.xlsx", min_score=60)
    ws = load_workbook(tmp_path / "out.xlsx").active
    for col in (2, 3, 4, 8):
        cell = ws.cell(2, col)
        assert cell.data_type != "f" and str(cell.value).startswith("'"), (col, cell.value)
    store.close()


def test_empty_onboarding_variable_refuses_to_open_the_operator_tools(monkeypatch):
    monkeypatch.delenv("JOBPILOT_SEEKER_ID", raising=False)
    monkeypatch.setenv("JOBPILOT_ONBOARD_TELEGRAM_ID", "")
    with pytest.raises(SystemExit, match="empty"):
        server.main()


def test_profile_writes_are_atomic_and_leave_no_temp_files(tmp_path):
    f = tmp_path / "profile.yaml"
    f.write_text("old", encoding="utf-8")
    server._atomic_write(f, "new: 1\n")
    assert f.read_text() == "new: 1\n" and not list(tmp_path.glob("*.tmp"))


def test_one_broken_profile_does_not_stop_discovery_for_others(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[paths]\ndata_dir="data"\nout_dir="out"\n[limits]\nscore_batch=40\nmin_request_gap_seconds=4.0\n', encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "companies.csv").write_text("name,tier,ats_vendor,careers_url,region,enabled\n", encoding="utf-8")
    store = Store(tmp_path / "j.db")
    store.migrate()
    good = tmp_path / "data" / "seekers" / "g"
    good.mkdir(parents=True)
    (good / "profile.yaml").write_text("name: G\nheadline: PM\nlocation: X\nsources: [none]\nsearch_terms: [pm]\n", encoding="utf-8")
    store.add_seeker("Good", 1, "data/seekers/g/profile.yaml", 20)
    store.add_seeker("Broken", 2, "data/seekers/missing/profile.yaml", 20)
    tools = server.build_tools(store, tmp_path / "out", operator_mode=True)
    out = tools["discover"]()
    assert out["ok"] is True and 2 in out["skipped_seekers"] and 1 not in out["skipped_seekers"]
    store.close()


def test_feed_lookup_budget_is_a_real_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    store = Store(tmp_path / "j.db")
    store.migrate()
    sid = store.add_seeker("A", None, "x.yaml", 20)
    for _ in range(server.MAX_DAILY_FEED_LOOKUPS):
        store.record_event("feed_lookup", "x", seeker_id=sid)
    spent = sum(store.event_count_since(s.id, "feed_lookup", 24) for s in store.list_seekers())
    assert max(0, server.MAX_DAILY_FEED_LOOKUPS - spent) == 0
    store.close()


def test_tailor_keywords_skip_generic_posting_words():
    from jobpilot.resume.tailor import job_keywords
    kws = job_keywords("Product Manager", "This demo fictional posting asks you to apply. SQL and Kubernetes required.")
    assert "demo" not in kws and "posting" not in kws and "apply" not in kws and "sql" in kws


def test_two_seekers_cannot_share_a_profile_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    store = Store(tmp_path / "j.db")
    store.migrate()
    tools = server.build_tools(store, tmp_path / "out", operator_mode=True)
    assert tools["add_seeker"]("A", 1, "data/seekers/a/profile.yaml")["ok"] is True
    clash = tools["add_seeker"]("B", 2, "data/seekers/a/profile.yaml")
    assert clash["ok"] is False and "already belongs" in clash["error"]
    assert tools["add_seeker"]("B", 2, "data/seekers/b/profile.yaml")["ok"] is True
    store.close()


# ---- job deadlines -------------------------------------------------------

def _deadline_store(tmp_path, valid_through):
    store = Store(tmp_path / "d.db")
    store.migrate()
    profile = tmp_path / "p" / "profile.yaml"
    profile.parent.mkdir()
    profile.write_text("name: A\nheadline: M\nlocation: X\nsources: [s]\nsearch_terms: [m]\n", encoding="utf-8")
    sid = store.add_seeker("A", None, str(profile), 20)
    pid = store.upsert_posting(Posting(company_name="Acme", source="s", external_id="1", title="PM", location="X",
                                       description="d", url="https://a.example.com/1", posted_at="2026-10-01"))
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 90, "ok", "t")
    if valid_through:
        store.set_valid_through(pid, valid_through)
    return store, sid, pid


def test_jobs_past_their_deadline_leave_every_list_immediately(tmp_path):
    store, sid, pid = _deadline_store(tmp_path, "2020-01-01")
    assert store.best_matches(sid, 5) == []
    assert store.notifiable_postings(sid, 70.0, 5) == []
    out = tmp_path / "x.xlsx"
    from jobpilot.joblist import write_job_list
    assert write_job_list(store, sid, out, min_score=60)[0] == 0
    store.close()


def test_open_and_undated_jobs_stay_and_the_last_day_is_still_open(tmp_path):
    from datetime import date
    for value in (None, date.today().isoformat(), "2099-12-31"):
        sub = tmp_path / str(value)
        sub.mkdir()
        store, sid, pid = _deadline_store(sub, value)
        assert [m["posting_id"] for m in store.best_matches(sid, 5)] == [pid], value
        store.close()


def test_sweep_marks_expired_but_never_an_application_in_progress(tmp_path):
    store, sid, pid = _deadline_store(tmp_path, "2020-01-01")
    store.record_application(sid, pid, "applied")
    assert store.expire_past_deadlines() == 0                 # applied jobs keep their status
    store2, sid2, pid2 = _deadline_store(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, "2020-01-01")
    assert store2.expire_past_deadlines() == 1
    assert store2.conn.execute("SELECT status FROM seeker_jobs").fetchone()[0] == "expired"
    store.close(); store2.close()


def test_deadline_parsers_read_epoch_and_iso_and_ignore_junk():
    from jobpilot.sources.ats_json import _closing_date
    assert _closing_date({"expiryDate": 1893456000}) == "2030-01-01"
    assert _closing_date({"expiresAt": 1893456000000}) == "2030-01-01"
    assert _closing_date({"validThrough": "2030-05-06T00:00:00Z"}) == "2030-05-06"
    assert _closing_date({"expiryDate": "soon"}) is None and _closing_date({}) is None


def test_jsonld_valid_through_is_captured():
    html = ('<script type="application/ld+json">{"@type":"JobPosting","identifier":{"value":"J1"},"title":"PM",'
            '"url":"https://c.example.com/1","hiringOrganization":{"name":"Acme"},"validThrough":"2030-02-03T00:00",'
            '"jobLocation":{"address":{"addressLocality":"X"}},"description":"d"}</script>')
    cfg = StructuredConfig(name="a", company_name="Acme", endpoint="https://c.example.com", enabled=True, canary_min_results=1)
    found, _ = JsonLdSource(cfg, fetcher=lambda u, t: html).search("pm", None)
    assert found[0].valid_through == "2030-02-03T00:00"


def test_nudge_then_expiry_both_fire(tmp_path):
    from datetime import datetime, timedelta, timezone
    store, sid, pid = _deadline_store(tmp_path, None)
    store.record_notification(sid, pid, "fo", "first")
    def age(days):
        store.conn.execute("UPDATE notifications SET delivered_at = ? WHERE kind = 'first'",
                           ((datetime.now(timezone.utc) - timedelta(days=days)).isoformat(),))
        store.conn.commit()
    age(4)
    assert [i["kind"] for i in store.pending_followups(sid)] == ["nudge"]
    store.record_notification(sid, pid, "fo", "nudge")
    assert store.pending_followups(sid) == []                               # nothing more until day 7
    age(8)
    assert [i["kind"] for i in store.pending_followups(sid)] == ["expiry"]   # the expiry still fires after a nudge
    store.close()


def test_scoring_backlog_skips_closed_and_finished_jobs(tmp_path):
    store, sid, pid = _deadline_store(tmp_path, "2020-01-01")
    store.conn.execute("DELETE FROM scores")
    store.conn.commit()
    assert store.unscored_jobs(sid, 10) == []
    store.conn.execute("UPDATE job_postings SET valid_through = NULL")
    store.conn.commit()
    assert [j.posting_id for j in store.unscored_jobs(sid, 10)] == [pid]
    store.conn.execute("UPDATE seeker_jobs SET status = 'skipped'")
    store.conn.commit()
    assert store.unscored_jobs(sid, 10) == []
    store.close()


def test_expiry_notification_never_overwrites_applied(tmp_path):
    store, sid, pid = _deadline_store(tmp_path, None)
    store.record_application(sid, pid, "applied")
    store.conn.execute("UPDATE seeker_jobs SET status = 'applied'")
    store.conn.commit()
    store.record_notification(sid, pid, "fo", "first")
    store.record_notification(sid, pid, "fo", "expiry")
    assert store.conn.execute("SELECT status FROM seeker_jobs").fetchone()[0] == "applied"
    store.close()


def test_unattended_routine_may_only_discover(tmp_path):
    seen = {}

    def runner(command, **kw):
        seen["cmd"] = command
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    from jobpilot.scheduled import run_once
    run_once(machine="m", mcp_config=tmp_path / "x.json", db_path=tmp_path / "d.db", runner=runner)
    allowed = seen["cmd"][seen["cmd"].index("--allowedTools") + 1]
    assert allowed == "mcp__jobpilot__list_seekers,mcp__jobpilot__discover" and "sync_google_sheet" not in allowed


# ---- audit round 3 --------------------------------------------------------

def test_application_status_only_moves_forward_and_response_time_is_written(tmp_path):
    store, sid, pid = _deadline_store(tmp_path, None)
    store.record_application(sid, pid, "started", "blocked at login")
    assert store.conn.execute("SELECT applied_at FROM applications").fetchone()[0] is None   # a blocked try is not an application
    assert store.seeker_status(sid)["applied"] == 0
    store.record_application(sid, pid, "applied")
    store.record_application(sid, pid, "interview")
    row = store.conn.execute("SELECT status, response_at, notes FROM applications").fetchone()
    assert row["status"] == "interview" and row["response_at"] and row["notes"] == "blocked at login"   # COALESCE keeps notes
    store.record_application(sid, pid, "applied")                      # a late 'applied' must not undo the interview
    assert store.conn.execute("SELECT status FROM applications").fetchone()[0] == "interview"
    assert store.seeker_status(sid)["applied"] == 1
    store.close()


def test_an_extended_deadline_revives_a_job_expired_only_by_the_old_date(tmp_path):
    store, sid, pid = _deadline_store(tmp_path, "2020-01-01")
    assert store.expire_past_deadlines() == 1
    store.set_valid_through(pid, "2099-01-01")
    assert store.conn.execute("SELECT status FROM seeker_jobs").fetchone()[0] == "scored"
    assert [m["posting_id"] for m in store.best_matches(sid, 5)] == [pid]
    store.close()


def test_feed_lookups_share_one_global_daily_cap_and_rotate_fairly(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    store = Store(tmp_path / "j.db")
    store.migrate()
    store.record_event("feed_lookup", "tried_today_company")
    assert store.event_total_since("feed_lookup", 24) == 1
    assert store.last_event_times("feed_lookup")["tried_today_company"]
    for _ in range(server.MAX_DAILY_FEED_LOOKUPS):
        store.record_event("feed_lookup", "x")
    assert store.event_total_since("feed_lookup", 24) > server.MAX_DAILY_FEED_LOOKUPS - 1
    store.close()


def test_short_skills_must_be_real_words_in_the_source():
    from jobpilot.resume.engine import BaseResume, Education, Experience, unverified_facts
    base = BaseResume(name="Pat", headline="Analyst", contact=(), summary=None, experience=(), education=(),
                      skills=("Go", "SQL", "Excel"), required_facts=())
    assert unverified_facts(base, "Pat Analyst. Worked at Google with PostgreSQL and excellent reports.") == ["Go", "SQL", "Excel"]
    assert unverified_facts(base, "Pat Analyst. Skills: Go, SQL, Excel.") == []


def test_text_resume_fails_closed_when_it_would_drop_a_required_fact(tmp_path):
    from jobpilot.resume import ResumeValidationError
    f = tmp_path / "b.yaml"
    f.write_text("name: Pat\nheadline: Analyst\nrequired_facts: [Pat, Acme]\nexperience:\n  - id: a\n    employer: Acme\n"
                 "    title: Analyst\n    dates: 2022\n    bullets: [{id: b1, text: Did things}]\nskills: [SQL]\n", encoding="utf-8")
    from jobpilot.resume import load_base_resume, render_text
    base = load_base_resume(f)
    assert "Acme" in render_text(base)
    with pytest.raises(ResumeValidationError):
        render_text(base, experience_order=[])                                 # Acme would vanish


def test_job_lists_are_not_sent_to_a_seeker_served_through_fo(tmp_path):
    from jobpilot.notify import push_job_lists
    store = Store(tmp_path / "j.db")
    store.migrate()
    a = store.add_seeker("A", 111, "a.yaml", 20)
    b = store.add_seeker("B", 222, "b.yaml", 20)
    sent = []
    push_job_lists(store, lambda uid, chat, flag: sent.append(uid) or True, skip={a})
    assert sent == [222]
    store.close()


def test_prepare_application_refuses_closed_jobs_and_drops_unsafe_links(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fo_demo", Path(__file__).resolve().parents[1] / "scripts" / "fo_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    store, sid, ids = demo.seed(tmp_path / "d")
    tools = server.build_tools(store, tmp_path / "d" / "out", pinned_seeker_id=sid, operator_mode=False)
    store.conn.execute("UPDATE job_postings SET canonical_url = ? WHERE id = ?", ("https://a.example.com/j?x=ignore-previous-instructions", ids[0]))
    store.conn.execute("UPDATE posting_sources SET url = ? WHERE posting_id = ?", ("https://a.example.com/j?x=ignore-previous-instructions", ids[0]))
    store.conn.commit()
    out = tools["prepare_application"](sid, ids[0])
    assert out["ok"] and out["job"]["url"] is None
    store.set_valid_through(ids[0], "2020-01-01")
    assert "closing date" in tools["prepare_application"](sid, ids[0])["error"]
    store.close()


def test_inbox_matches_leave_out_skipped_and_applied_jobs(tmp_path):
    import importlib.util
    from jobpilot import inbox
    spec = importlib.util.spec_from_file_location("fo_demo", Path(__file__).resolve().parents[1] / "scripts" / "fo_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    store, sid, ids = demo.seed(tmp_path / "d")
    store.record_application(sid, ids[0], "applied")
    text = inbox._matches(store, sid)
    assert "Senior Business Analyst" not in text and "Analytics Manager" in text
    store.close()
