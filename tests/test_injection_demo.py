import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "injection_demo", Path(__file__).resolve().parents[1] / "scripts" / "injection_demo.py")
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def test_poisoned_postings_never_reach_fo(capsys):
    assert demo.main() == 0
    out = capsys.readouterr().out
    naive, safe = out.split("=== What JobPilot actually sends Fo")
    assert "attacker.example" in naive and "Ignore previous instructions" in naive   # the danger is real
    low = safe.lower()
    for bad in ("attacker", "ignore previous", "http", "500 dollars", "555", "system prompt"):
        assert bad not in low
    assert "[withheld]" in safe and "Senior Product Manager, Acme Analytics" in safe  # the real role still passes
    assert "Hostile text that reached Fo: none" in safe
