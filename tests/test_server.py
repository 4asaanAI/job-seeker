import pytest
from pathlib import Path

from jobpilot.server import build_tools
from jobpilot.store.db import Store
from jobpilot.store.models import Posting


def _tools(tmp_path, **kw):
    store = Store(tmp_path / "t.db")
    store.migrate()
    return build_tools(store, out_dir=tmp_path, **kw), store


@pytest.fixture
def operator(tmp_path):
    tools, store = _tools(tmp_path)
    yield tools, store
    store.close()


def _seed(store, name="Alex", tid=1, ext="n1"):
    sid = store.add_seeker(name, tid, f"{name}.yaml", 20)
    pid = store.upsert_posting(
        Posting(company_name="M&S", source="naukri", external_id=ext,
                title="Senior Merchandiser", location="Gurgaon", description="d",
                url="https://x.test/1", posted_at="2026-09-18"))
    store.link_posting(sid, pid)
    return sid, pid


def test_resolve_known_telegram_sender(operator):
    tools, store = operator
    sid = store.add_seeker("Alex", 100000001, "a.yaml", 20)
    assert tools["resolve_telegram_sender"](100000001)["seeker_id"] == sid


def test_resolve_unknown_telegram_sender_is_refused(operator):
    tools, _ = operator
    out = tools["resolve_telegram_sender"](999999)
    assert out["seeker_id"] is None
    assert "not recognised" in out["error"].lower()


def test_record_score_then_notifiable_then_suppressed(operator):
    tools, store = operator
    sid, pid = _seed(store)
    assert tools["record_score"](sid, pid, 91.0,
                                  "Direct M&S account experience")["ok"]
    assert [n["posting_id"] for n in tools["get_notifiable"](sid, 70.0, 5)["items"]] == [pid]
    assert tools["record_notification"](sid, pid, "digest", "first")["ok"]
    assert tools["get_notifiable"](sid, 70.0, 5)["items"] == []


def test_pinned_process_refuses_another_seeker(tmp_path):
    _, store = _tools(tmp_path)
    a, _ = _seed(store, "Alex", 1, "n1")
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    store.close()

    pinned, store2 = _tools(tmp_path, pinned_seeker_id=a, operator_mode=False)
    refused = pinned["get_notifiable"](b, 70.0, 5)
    assert refused["ok"] is False
    assert "pinned" in refused["error"]
    assert pinned["get_notifiable"](a, 70.0, 5)["items"] == []
    store2.close()


def test_pinned_process_refuses_resolving_another_telegram_id(tmp_path):
    _, store = _tools(tmp_path)
    a = store.add_seeker("Alex", 111, "a.yaml", 20)
    store.add_seeker("Priya", 222, "b.yaml", 20)
    store.close()

    pinned, store2 = _tools(tmp_path, pinned_seeker_id=a, operator_mode=False)
    assert pinned["resolve_telegram_sender"](111)["seeker_id"] == a
    assert pinned["resolve_telegram_sender"](222)["seeker_id"] is None
    store2.close()


def test_seeker_mode_has_no_roster_or_discovery_tools(tmp_path):
    pinned, store = _tools(tmp_path, pinned_seeker_id=1, operator_mode=False)
    for name in ("list_seekers", "add_seeker", "discover", "source_health"):
        assert name not in pinned
    store.close()


def test_seeker_mode_requires_a_process_pin(tmp_path):
    with pytest.raises(ValueError, match="requires JOBPILOT_SEEKER_ID"):
        _tools(tmp_path, operator_mode=False)


def test_scoring_batch_resolves_relative_profile_from_jobpilot_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    profile_dir = tmp_path / "data" / "seekers" / "a"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.yaml").write_text(
        "name: A\nheadline: Analyst\nlocation: Austin\n"
        "sources: [indeed_us]\nsearch_terms: [analyst]\n"
        "rubric: sample\n",
        encoding="utf-8",
    )
    rubric_dir = tmp_path / "data" / "rubrics"
    rubric_dir.mkdir(parents=True)
    (rubric_dir / "sample.md").write_text("sample rubric", encoding="utf-8")

    tools, store = _tools(tmp_path)
    sid = store.add_seeker("A", 1, "data/seekers/a/profile.yaml", 20)
    pid = store.upsert_posting(
        Posting(company_name="Acme", source="indeed", external_id="a1",
                title="Analyst", location="Austin", description="d",
                url="https://x.test/a1", posted_at="2026-09-18"))
    store.link_posting(sid, pid)
    out = tools["get_scoring_batch"](sid, 5)
    try:
        assert out["ok"] is True
        assert out["rubric"] == "sample rubric"
    finally:
        store.close()


def test_operator_mode_exposes_operator_tools(operator):
    tools, _ = operator
    for name in ("list_seekers", "add_seeker", "discover", "source_health"):
        assert name in tools


def test_add_seeker_tool_onboards_without_editing_code(operator):
    tools, store = operator
    out = tools["add_seeker"]("Priya", 555,
                              "data/seekers/priya/profile.yaml", 15)
    assert out["seeker_id"] in [s.id for s in store.list_seekers()]


def test_add_seeker_rejects_absolute_or_parent_paths(operator):
    tools, _ = operator
    assert tools["add_seeker"]("Priya", 555, "/tmp/profile.yaml")["ok"] is False
    assert tools["add_seeker"]("Priya", 555, "../profile.yaml")["ok"] is False


def test_every_tool_has_a_docstring(operator):
    tools, _ = operator
    missing = [name for name, fn in tools.items() if not (fn.__doc__ or "").strip()]
    assert missing == []


def test_record_score_on_unlinked_posting_reports_an_error(operator):
    tools, store = operator
    _, pid = _seed(store, "Alex", 1, "n1")
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    out = tools["record_score"](b, pid, 90.0, "wrong seeker")
    assert out["ok"] is False
    assert "not linked" in out["error"]


def test_unknown_seeker_gives_a_clear_error_not_stopiteration(operator):
    tools, _ = operator
    out = tools["build_digest"](9999)
    assert out["ok"] is False
    assert "no seeker" in out["error"]


def test_m2_status_and_history_are_per_seeker(operator):
    tools, store = operator
    sid, pid = _seed(store)
    assert tools["record_score"](sid, pid, 91.0, "good fit")["ok"]
    assert tools["record_notification"](sid, pid, "test", "first")["ok"]

    status = tools["my_status"](sid)
    assert status["ok"] is True
    assert status["found"] == 1
    assert status["scored"] == 1
    assert status["shown"] == 1
    assert status["applied"] == 0

    history = tools["my_history"](sid, "Senior")
    assert history["ok"] is True
    assert history["items"][0]["posting_id"] == pid
    assert history["items"][0]["title"] == "Senior Merchandiser"


def test_m2_investigation_handoff_never_exposes_an_unlinked_posting(operator):
    tools, store = operator
    sid, pid = _seed(store)
    handoff = tools["investigate_posting"](sid, pid)
    assert handoff["ok"] is True
    assert "web_fetch" in handoff["next_step"]
    assert handoff["posting"]["id"] == pid
    assert tools["verify_still_open"](sid, pid)["status"] == "check_with_web_fetch"

    other = store.upsert_posting(
        Posting(company_name="Other", source="indeed", external_id="other",
                title="Other Role", location="Austin", description="d",
                url="https://x.test/other", posted_at="2026-09-18"))
    refused = tools["investigate_posting"](sid, other)
    assert refused["ok"] is False
    assert "linked" in refused["error"]


def test_watchlist_and_company_career_handoff_are_scoped(operator):
    tools, store = operator
    sid, _ = _seed(store)
    added = tools["add_target_company"](sid, "  Target Sourcing  ")
    assert added["ok"] is True
    assert store.target_company_names(sid) == ["Target Sourcing"]
    career = tools["check_company_careers"](sid, added["company_id"])
    assert career["ok"] is True
    assert "web_fetch" in career["next_step"]

    other = store.add_seeker("Other", 2, "other.yaml", 20)
    refused = tools["check_company_careers"](other, added["company_id"])
    assert refused["ok"] is False
    assert "watchlist" in refused["error"]


def test_pinned_process_scopes_new_m2_tools(tmp_path):
    _, store = _tools(tmp_path)
    a, pid = _seed(store, "A", 1, "a1")
    b = store.add_seeker("B", 2, "b.yaml", 20)
    store.close()
    pinned, store2 = _tools(tmp_path, pinned_seeker_id=a, operator_mode=False)
    assert pinned["my_status"](a)["ok"] is True
    refused = pinned["my_status"](b)
    assert refused["ok"] is False
    assert "pinned" in refused["error"]
    refused_history = pinned["my_history"](b)
    assert refused_history["ok"] is False
    assert "pinned" in refused_history["error"]
    store2.close()


PROFILE = ("name: A\nheadline: Analyst\nlocation: Austin\nsources: [indeed_us]\n"
           "search_terms: [analyst]\n")


def _seeker_with_resume(tmp_path, store, name="a", tid=1, ext="n1"):
    folder = tmp_path / "data" / "seekers" / name
    folder.mkdir(parents=True)
    (folder / "profile.yaml").write_text(PROFILE, encoding="utf-8")
    (folder / "base_resume.yaml").write_text(
        "name: A\nheadline: Analyst\n"
        "required_facts: [A, Analyst, Acme]\n"
        "experience:\n"
        "  - id: current\n    employer: Acme\n    title: Analyst\n    dates: 2024 - Present\n"
        "    bullets: [{id: b1, text: Analysed product data}]\n"
        "skills: [SQL]\n",
        encoding="utf-8",
    )
    sid = store.add_seeker(name, tid, f"data/seekers/{name}/profile.yaml", 20)
    pid = store.upsert_posting(
        Posting(company_name="M&S", source="naukri", external_id=ext,
                title="Senior Merchandiser", location="Gurgaon", description="d",
                url=f"https://x.test/{ext}", posted_at="2026-09-18"))
    store.link_posting(sid, pid)
    return sid, pid


def test_resume_tools_render_and_record_only_structured_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    tools, store = _tools(tmp_path)
    sid, pid = _seeker_with_resume(tmp_path, store)

    facts = tools["resume_facts"](sid)
    assert facts["ok"] is True
    assert facts["experience"][0]["id"] == "current"
    rendered = tools["render_resume"](sid, pid, ["current"], ["SQL"])

    assert rendered["ok"] is True
    assert rendered["validation"]["ok"] is True
    assert Path(rendered["path"]).exists()
    history = tools["my_resumes"](sid, pid)
    assert history["ok"] is True
    assert history["items"][0]["id"] == rendered["resume_version_id"]
    store.close()


def test_resume_tools_only_ever_read_the_callers_own_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    store = Store(tmp_path / "t.db")
    store.migrate()
    a, pid = _seeker_with_resume(tmp_path, store, "a", 1, "n1")
    b, _ = _seeker_with_resume(tmp_path, store, "b", 2, "n2")
    pinned = build_tools(store, tmp_path, pinned_seeker_id=a, operator_mode=False)
    with pytest.raises(TypeError):  # no path parameter to point at someone else
        pinned["resume_facts"](a, "data/seekers/b/base_resume.yaml")
    assert not pinned["resume_facts"](b)["ok"]
    other = store.upsert_posting(
        Posting(company_name="Other", source="indeed", external_id="other-resume",
                title="Other", location="Austin", description="d",
                url="https://x.test/other-resume", posted_at="2026-09-18"))
    refused = pinned["render_resume"](a, other)
    assert refused["ok"] is False and "linked" in refused["error"]
    (tmp_path / "data" / "seekers" / "a" / "base_resume.yaml").unlink()
    missing = pinned["resume_facts"](a)
    assert not missing["ok"] and "PDF or Word" in missing["error"]
    store.close()


def test_seeker_actions_and_application_records_are_scoped(operator):
    tools, store = operator
    sid, pid = _seed(store)

    assert tools["set_job_status"](sid, pid, "shortlisted")["ok"] is True
    assert tools["record_application"](
        sid, pid, "applied", notes="submitted on employer portal"
    )["ok"] is True
    applications = tools["my_applications"](sid)
    assert applications["ok"] is True
    assert applications["items"][0]["status"] == "applied"
    assert store.seeker_jobs(sid)[0].status == "applied"
    assert tools["get_notifiable"](sid, 70, 5)["items"] == []

    other = store.add_seeker("Other", 2, "other.yaml", 20)
    refused = tools["record_application"](other, pid, "applied")
    assert refused["ok"] is False
    assert "not linked" in refused["error"]


def test_followups_are_one_time_nudge_then_expiry(operator):
    tools, store = operator
    sid, pid = _seed(store)
    assert tools["record_score"](sid, pid, 90, "fit")["ok"]
    assert tools["record_notification"](sid, pid, "test", "first")["ok"]
    store.conn.execute(
        "UPDATE notifications SET delivered_at = '2026-09-10T00:00:00+00:00' "
        "WHERE seeker_id = ? AND posting_id = ? AND kind = 'first'", (sid, pid)
    )
    followup = tools["get_followups"](sid)
    assert followup["ok"] is True
    assert followup["items"][0]["kind"] == "expiry"
    assert tools["record_notification"](sid, pid, "test", "expiry")["ok"]
    assert tools["get_followups"](sid)["items"] == []


def test_my_matches_lists_best_scored_jobs_including_already_sent(operator):
    tools, store = operator
    sid, pid = _seed(store)
    store.record_score(sid, pid, 85.0, "strong fit", "m")
    out = tools["my_matches"](sid)
    assert out["ok"] and out["items"][0]["posting_id"] == pid and out["items"][0]["fit_score"] == 85.0
    assert not tools["my_matches"](sid + 99)["ok"] or tools["my_matches"](sid + 99)["items"] == []


def test_prepare_application_returns_facts_only_text_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    tools, store = _tools(tmp_path)
    sid, pid = _seeker_with_resume(tmp_path, store)
    out = tools["prepare_application"](sid, pid, ["current"], ["SQL"])
    assert out["ok"] is True
    assert out["job"] == {"title": "Senior Merchandiser", "company": "M&S",
                          "url": out["job"]["url"]}
    assert "Analysed product data" in out["resume_text"] and "SQL" in out["resume_text"]
    assert "Nothing was sent" in out["note"]
    bad = tools["prepare_application"](sid, pid, ["invented"], None)
    assert bad["ok"] is False
    store.close()


def test_prepare_application_refuses_unlinked_posting_and_other_seekers(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    tools, store = _tools(tmp_path)
    sid, pid = _seeker_with_resume(tmp_path, store)
    assert tools["prepare_application"](sid, pid + 99)["ok"] is False
    from jobpilot.server import build_tools
    pinned = build_tools(store, tmp_path / "out", pinned_seeker_id=sid, operator_mode=False)
    assert pinned["prepare_application"](sid + 1, pid)["ok"] is False
    store.close()


def test_build_fo_message_is_vetted_wrapped_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    tools, store = _tools(tmp_path)
    sid, pid = _seeker_with_resume(tmp_path, store)
    store.record_score(sid, pid, 91.0, "SECRET http://evil.example reasoning", "claude")
    out = tools["build_fo_message"](sid)
    assert out["ok"] and out["record_after"] == [{"posting_id": pid, "kind": "first"}]
    msg = out["message"]
    assert "SECRET" not in msg and "evil.example" not in msg and "http" not in msg
    assert "take no other action" in msg and f"id {pid}" in msg and "91/100" in msg
    assert store.notifiable_postings(sid, 70.0, 5)  # nothing was recorded as sent
    assert tools["build_fo_message"](sid, "followups")["message"] is None
    assert tools["build_fo_message"](sid, "bogus")["ok"] is False
    store.close()


def test_tailor_resume_suggests_renders_and_remembers_the_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    tools, store = _tools(tmp_path)
    sid, pid = _seeker_with_resume(tmp_path, store)
    out = tools["tailor_resume"](sid, pid)
    assert out["ok"] and out["job"]["title"] == "Senior Merchandiser"
    assert out["suggestion"]["bullet_order"]["current"] == ["b1"] and out["previous_versions"] == []
    sug = out["suggestion"]
    rendered = tools["render_resume"](sid, pid, sug["experience_order"], sug["skills"], sug["bullet_order"])
    assert rendered["ok"] and Path(rendered["path"]).exists()
    again = tools["tailor_resume"](sid, pid)
    assert again["previous_versions"][0]["selection"]["bullet_order"] == {"current": ["b1"]}
    assert tools["render_resume"](sid, pid, None, None, {"current": ["nope"]})["ok"] is False
    assert tools["tailor_resume"](sid, pid + 99)["ok"] is False
    store.close()


def test_build_fo_message_refuses_other_seekers_in_operator_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[paths]\ndata_dir="data"\nout_dir="out"\n[limits]\nscore_batch=40\nmin_request_gap_seconds=4.0\n'
        '[fo]\nenabled=true\nseeker_id=1\n', encoding="utf-8")
    tools, store = _tools(tmp_path)
    a, pa = _seeker_with_resume(tmp_path, store, name="A", ext="A1")
    b, pb = _seeker_with_resume(tmp_path, store, name="B", tid=2, ext="B1")
    store.record_score(a, pa, 90.0, "ok", "t")
    store.record_score(b, pb, 90.0, "ok", "t")
    assert tools["build_fo_message"](a)["ok"] is True
    other = tools["build_fo_message"](b)
    assert other["ok"] is False and "different seeker" in other["error"]
    store.close()
