import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("trace_from_claude", Path(__file__).resolve().parents[1] / "scripts" / "trace_from_claude.py")
trace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trace)


def event(kind, **block):
    return json.dumps({"timestamp": "2026-10-19T01:02:03.456Z", "message": {"content": [{"type": kind, **block}]}})


def test_calls_are_listed_in_order_with_outcomes_and_masked():
    lines = [
        event("tool_use", id="1", name="mcp__jobpilot__build_fo_apply_message",
              input={"posting_id": 1, "note": "mail me at pat@example.com or +91 98765 43210"}),
        event("tool_result", tool_use_id="1", content="ok " + "x" * 400),
        event("tool_use", id="2", name="mcp__fo__ask_fo", input={"message": "hello"}),
        event("tool_result", tool_use_id="2", content=[{"type": "text", "text": "failed"}], is_error=True),
        event("tool_use", id="3", name="Bash", input={"command": "ls"}),
        "not json",
    ]
    items = trace.calls(lines)
    assert [c["name"] for c in items] == ["mcp__jobpilot__build_fo_apply_message", "mcp__fo__ask_fo", "Bash"]
    assert [c["ok"] for c in items] == [True, False, None]
    assert "pat@example.com" not in items[0]["input"] and "98765" not in items[0]["input"]
    assert "[email]" in items[0]["input"] and "[number]" in items[0]["input"] and len(items[0]["result"]) <= 204
    text = trace.render(items, "demo")
    assert "3 tool call(s)" in text and "(error)" in text and "(no result)" in text
