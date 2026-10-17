import importlib.util
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobpilot.fo import FoResult
from jobpilot.server import build_tools
from jobpilot.store.db import Store

SPEC = importlib.util.spec_from_file_location(
    "fo_demo", Path(__file__).resolve().parents[1] / "scripts" / "fo_demo.py")
fo_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fo_demo)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("demo tests must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_dry_run_prints_messages_and_sends_nothing(tmp_path, capsys):
    assert fo_demo.main(["--dir", str(tmp_path / "d")]) == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing was sent" in out
    assert "Senior Business Analyst" in out and "Data Entry Clerk" not in out  # below 70
    assert "Business Intelligence Lead" in out  # the follow-up
    assert "JOBPILOT_DB=" in out and "JOBPILOT_SEEKER_ID=" in out
    store = Store(tmp_path / "d" / "jobpilot.db")
    count = store.conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    store.close()
    assert count == 1  # only the seeded 4-day-old one


def test_seeded_data_supports_resume_and_matches_tools(tmp_path):
    store, sid, ids = fo_demo.seed(tmp_path / "d")
    tools = build_tools(store, tmp_path / "d" / "out", pinned_seeker_id=sid, operator_mode=False)
    assert tools["my_matches"](sid)
    result = tools["render_resume"](sid, ids[0])
    assert result["ok"], result
    assert Path(result["path"]).exists()
    assert tools["get_followups"](sid)["items"]
    store.close()


def test_send_needs_key_and_a_typed_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FO_API_KEY", raising=False)
    assert fo_demo.main(["--send", "--dir", str(tmp_path / "d")]) == 1
    monkeypatch.setenv("FO_API_KEY", "k")
    calls = []
    factory = lambda key, url: SimpleNamespace(send=lambda m: calls.append(m) or FoResult("complete", "ok"))
    assert fo_demo.main(["--send", "--dir", str(tmp_path / "d")], input_fn=lambda p: "no",
                        client_factory=factory) == 1
    assert calls == []


def test_send_with_yes_records_fo_notifications(tmp_path, monkeypatch):
    monkeypatch.setenv("FO_API_KEY", "k")
    calls = []
    factory = lambda key, url: SimpleNamespace(send=lambda m: calls.append(m) or FoResult("complete", "ok"))
    assert fo_demo.main(["--send", "--dir", str(tmp_path / "d")], input_fn=lambda p: "yes",
                        client_factory=factory) == 0
    assert len(calls) == 2
    store = Store(tmp_path / "d" / "jobpilot.db")
    channels = {r[0] for r in store.conn.execute("SELECT channel FROM notifications")}
    store.close()
    assert channels == {"fo"}


def test_refuses_to_delete_a_folder_that_is_not_a_demo_folder(tmp_path):
    victim = tmp_path / "precious"
    victim.mkdir()
    (victim / "keep.txt").write_text("data")
    with pytest.raises(SystemExit, match="not a Fo demo folder"):
        fo_demo.main(["--dir", str(victim)])
    assert (victim / "keep.txt").exists()


def test_keep_reuses_the_database_instead_of_reseeding(tmp_path):
    d = tmp_path / "d"
    fo_demo.main(["--dir", str(d)])
    marker = d / "jobpilot.db"
    inode = marker.stat().st_ino
    assert fo_demo.main(["--dir", str(d), "--keep"]) == 0
    assert marker.stat().st_ino == inode
