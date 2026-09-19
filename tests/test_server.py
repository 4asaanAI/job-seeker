import pytest

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
    assert [n["posting_id"] for n in tools["get_notifiable"](sid, 70.0, 5)] == [pid]
    assert tools["record_notification"](sid, pid, "digest", "first")["ok"]
    assert tools["get_notifiable"](sid, 70.0, 5) == []


def test_pinned_process_refuses_another_seeker(tmp_path):
    _, store = _tools(tmp_path)
    a, _ = _seed(store, "Alex", 1, "n1")
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    store.close()

    pinned, store2 = _tools(tmp_path, pinned_seeker_id=a, operator_mode=False)
    refused = pinned["get_notifiable"](b, 70.0, 5)
    assert refused["ok"] is False
    assert "pinned" in refused["error"]
    assert pinned["get_notifiable"](a, 70.0, 5) == []
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
    assert handoff["browser_required"] is True
    assert handoff["posting"]["id"] == pid
    assert tools["verify_still_open"](sid, pid)["status"] == "browser_check_required"

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
    assert career["browser_required"] is True

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
