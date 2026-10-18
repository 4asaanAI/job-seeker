"""Things a real user does that a happy-path demo never does."""
import importlib.util
from pathlib import Path

import pytest

from jobpilot import server

SPEC = importlib.util.spec_from_file_location("fo_demo", Path(__file__).resolve().parents[1] / "scripts" / "fo_demo.py")
fo_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fo_demo)


@pytest.fixture
def env(tmp_path):
    store, sid, ids = fo_demo.seed(tmp_path / "d")
    tools = server.build_tools(store, tmp_path / "d" / "out", operator_mode=True)
    yield tools, store, sid, ids
    store.close()


@pytest.mark.parametrize("name,args", [
    ("my_matches", {}), ("my_dashboard", {}), ("run_my_search", {}), ("plan_apply_batch", {}),
    ("apply_candidates", {}), ("my_applications", {}), ("my_resumes", {}), ("get_followups", {}),
    ("web_fetch", {"url": "http://example.com"}), ("record_notification", {"posting_id": 1, "channel": "fo"}),
])
def test_an_unknown_seeker_gets_a_clear_refusal_not_a_database_error(env, name, args):
    tools, *_ = env
    out = tools[name](999, **args)
    assert out["ok"] is False and "no seeker with id 999" in out["error"]


def test_duplicate_search_terms_and_companies_are_collapsed(env):
    tools, _, sid, _ = env
    out = tools["update_my_profile"](sid, search_terms=["Product Manager", "product manager", " PRODUCT MANAGER ", "analyst"])
    assert out["profile"]["search_terms"] == ["Product Manager", "analyst"]


def test_history_search_treats_percent_and_underscore_literally(env):
    tools, _, sid, _ = env
    assert tools["my_history"](sid, query="_%")["items"] == []
    assert tools["my_history"](sid, query="")["items"]


def test_history_search_still_finds_a_normal_word(env):
    tools, _, sid, _ = env
    everything = tools["my_history"](sid, query="")["items"]
    word = everything[0]["title"].split()[0]
    assert tools["my_history"](sid, query=word)["items"]


def test_discover_names_unknown_seeker_ids(env, monkeypatch):
    tools, *_ = env
    monkeypatch.setattr(server, "run_discovery", lambda *a, **k: [])
    out = tools["discover"]([4242])
    assert out["skipped_seekers"] == {4242: "no such seeker"}


def test_empty_company_name_is_refused(env):
    tools, _, sid, _ = env
    assert tools["company_openings"](sid, "   ")["ok"] is False


def test_notification_channel_must_be_a_plain_word(env):
    tools, _, sid, ids = env
    assert tools["record_notification"](sid, ids[0], "x" * 50)["ok"] is False
    assert tools["record_notification"](sid, ids[0], "digest")["ok"] is True


def test_refresh_cli_with_an_unknown_seeker_explains_instead_of_a_traceback(tmp_path):
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "JOBPILOT_HOME": str(root), "JOBPILOT_DB": str(tmp_path / "x.db")}
    run = subprocess.run([sys.executable, "-m", "jobpilot.refresh", "--seeker", "99"], env=env,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 2 and "no seeker with id 99" in run.stdout and "Traceback" not in run.stderr
