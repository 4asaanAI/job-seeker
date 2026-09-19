# tests/test_capture_redaction.py
"""Offline tests for scripts/capture_fixture.py redaction. Made-up strings only."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "capture_fixture.py"
spec = importlib.util.spec_from_file_location("capture_fixture", SCRIPT)
cap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cap)


def test_email_is_redacted_including_markdown_escaped_form():
    text, n = cap.redact_text(r"mail jane.doe@example.com or reasonable\_help@example.org now")
    assert text == "mail [redacted] or [redacted] now"
    assert n == 2


def test_phone_with_trailing_full_stop_and_comma_is_redacted():
    text, n = cap.redact_text("Call 1-800-555-0199. Or (800) 555-0199, or +1 800 555 0199.")
    assert "555" not in text
    assert text.count("[redacted]") == 3 and n == 3
    assert text.startswith("Call [redacted]. Or [redacted], or")


def test_normal_sentence_is_unchanged():
    s = "Salary $120,000 to $150,000 posted 2026-09-17. Req JR-202620289-1, 5+ years."
    assert cap.redact_text(s) == (s, 0)


def test_urls_are_left_alone_and_survivors_flags_leftovers():
    rows = [{"job_url": "https://x.example/job/100778997232/", "description": "hi"}]
    assert cap.redact_rows(rows) == (rows, 0)
    assert cap.survivors(rows) == []
    assert cap.survivors([{"description": "write me@x.com"}]) == ["@"]
    assert cap.survivors([{"description": "dial 555 123 4567"}]) != []
