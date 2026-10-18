import json

from jobpilot import runlog


def test_ledger_counts_traces_and_keeps_failures(tmp_path):
    runlog.record_run("refresh_deliver", True, {"scored": 5}, [{"tool": "mcp__fo__ask_fo", "status": "complete", "seconds": 41.2,
                                                                 "chars": 300, "message_head": "JobPilot: 3 new", "reply_head": "Sent"}], tmp_path)
    runlog.record_run("refresh_deliver", False, {"error": "FoError"}, [], tmp_path)
    runs = runlog.read_runs(tmp_path)
    assert runlog.run_count(runs) == {"refresh_deliver": {"runs": 2, "succeeded": 1}}
    text = runlog.render_count(runs)
    assert "| `refresh_deliver` | 2 | 1 | 1 |" in text and "Failed runs (kept, not hidden)" in text
    trace = runlog.render_trace(runs[0])
    assert "mcp__fo__ask_fo" in trace and "41.2s" in trace and "Fo replied: 'Sent'" in trace


def test_bad_lines_and_missing_file_are_ignored(tmp_path):
    assert runlog.read_runs(tmp_path) == []
    path = runlog.ledger_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json\n" + json.dumps({"kind": "x", "ok": True, "at": "t", "steps": {}, "fo_calls": []}) + "\n[1]\n")
    assert len(runlog.read_runs(tmp_path)) == 1


def test_fo_client_traces_its_calls_without_the_key():
    import json as j
    from jobpilot.fo import FoClient, FO_URL

    class Resp:
        def __init__(self, data):
            self.data = data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n=None):
            return self.data

    body = j.dumps({"result": {"structuredContent": {"status": "complete", "response": "Sent it."}}}).encode()
    from types import SimpleNamespace
    client = FoClient("wak_secretkey", FO_URL, opener=SimpleNamespace(open=lambda req, timeout=0: Resp(body)))
    assert client.send("hello there").status == "complete"
    entry = client.trace[0]
    assert entry["tool"] == "mcp__fo__ask_fo" and entry["status"] == "complete" and entry["chars"] == 11
    assert "wak_secretkey" not in j.dumps(client.trace)
