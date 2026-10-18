import importlib.util
from pathlib import Path

from jobpilot import refresh as rf

SPEC = importlib.util.spec_from_file_location(
    "fo_demo", Path(__file__).resolve().parents[1] / "scripts" / "fo_demo.py")
fo_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fo_demo)


def test_refresh_discovers_then_scores_then_plans_in_order(tmp_path):
    store, sid, ids = fo_demo.seed(tmp_path / "d")
    calls = []
    out = rf.refresh(
        store, tmp_path / "d", sid,
        discover=lambda s: calls.append("discover") or {"sites": [{"source": "x", "status": "healthy", "found": 3}],
                                                           "skipped_seekers": {}},
        ask=lambda prompt: calls.append("score") or "[]",
        plan=lambda s: calls.append("plan") or {"items": []})
    assert calls[0] == "discover" and calls[-1] == "plan"
    assert out["sources"][0]["found"] == 3 and "scoring" in out and out["plan"] == {"items": []}
    store.close()


def test_no_search_skips_discovery(tmp_path):
    store, sid, ids = fo_demo.seed(tmp_path / "d")
    out = rf.refresh(store, tmp_path / "d", sid, discover=lambda s: (_ for _ in ()).throw(AssertionError("no")),
                     ask=lambda p: "[]", plan=lambda s: {"items": []}, search=False)
    assert "sources" not in out
    store.close()
