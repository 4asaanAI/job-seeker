
from jobpilot.sources.base import SourceStatus
from jobpilot.sources.health import classify
from jobpilot.store.db import Store


def test_transport_error_is_error_not_blocked():
    status, note = classify(0, canary_ok=False, canary_count=0, raw_count=0,
                            history=[], error="RuntimeError: 429")
    assert status is SourceStatus.ERROR
    assert "429" in note


def test_failed_canary_is_blocked():
    status, note = classify(0, canary_ok=False, canary_count=0, raw_count=0,
                            history=[40, 38, 42])
    assert status is SourceStatus.BLOCKED
    assert "blocked" in note.lower()


def test_failed_canary_is_blocked_even_with_some_results():
    status, _ = classify(5, canary_ok=False, canary_count=0, raw_count=5, history=[40])
    assert status is SourceStatus.BLOCKED


def test_rows_arriving_but_none_normalising_is_error():
    """The site answered; our mapping is broken. Never call this quiet."""
    status, note = classify(0, canary_ok=True, canary_count=20, raw_count=37,
                            history=[])
    assert status is SourceStatus.ERROR
    assert "normalis" in note.lower() or "mapping" in note.lower()


def test_zero_results_with_passing_canary_and_no_raw_rows_is_quiet():
    status, _ = classify(0, canary_ok=True, canary_count=20, raw_count=0, history=[])
    assert status is SourceStatus.HEALTHY


def test_zero_results_against_a_busy_history_is_degraded():
    """Spec 5.3 step 4: a drop below 25% of the trailing median marks the
    source DEGRADED. Zero is below 25% of any positive median, and a block
    that starts after the canary passed looks exactly like this."""
    status, note = classify(0, canary_ok=True, canary_count=20, raw_count=0,
                            history=[40, 38, 42])
    assert status is SourceStatus.DEGRADED
    assert "median" in note


def test_zero_results_with_empty_history_stays_healthy():
    status, _ = classify(0, canary_ok=True, canary_count=20, raw_count=0, history=[])
    assert status is SourceStatus.HEALTHY


def test_zero_results_with_only_zero_history_stays_healthy():
    status, _ = classify(0, canary_ok=True, canary_count=20, raw_count=0,
                         history=[0, 0])
    assert status is SourceStatus.HEALTHY


def test_empty_string_error_still_counts_as_an_error():
    status, _ = classify(5, canary_ok=True, canary_count=20, raw_count=5,
                         history=[], error="")
    assert status is SourceStatus.ERROR


def test_sharp_drop_against_history_is_degraded():
    status, _ = classify(3, canary_ok=True, canary_count=20, raw_count=3,
                         history=[40, 38, 42, 41])
    assert status is SourceStatus.DEGRADED


def test_degradation_is_detected_with_only_one_historical_run():
    """A collapsing source must not be invisible for its first three runs."""
    status, _ = classify(1, canary_ok=True, canary_count=20, raw_count=1,
                         history=[40])
    assert status is SourceStatus.DEGRADED


def test_normal_volume_is_healthy():
    status, _ = classify(39, canary_ok=True, canary_count=20, raw_count=39,
                         history=[40, 38, 42])
    assert status is SourceStatus.HEALTHY


def test_no_history_with_passing_canary_is_healthy():
    status, _ = classify(12, canary_ok=True, canary_count=20, raw_count=12,
                         history=[])
    assert status is SourceStatus.HEALTHY


def test_source_runs_round_trip(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    s.record_source_run("naukri", True, 20, 40, 40, "healthy", "", latency_ms=1200)
    s.record_source_run("naukri", True, 20, 38, 38, "healthy", "")
    s.record_source_run("indeed", False, 0, 0, 0, "blocked", "canary failed")

    assert s.recent_counts("naukri", 5) == [38, 40]

    summary = {h["source"]: h for h in s.source_health_summary()}
    assert summary["naukri"]["status"] == "healthy"
    assert summary["indeed"]["status"] == "blocked"
    s.close()


def test_recent_counts_excludes_zero_result_healthy_runs(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    s.record_source_run("naukri", True, 20, 40, 40, "healthy", "")
    s.record_source_run("naukri", True, 20, 0, 0, "healthy", "")
    s.record_source_run("naukri", True, 20, 0, 0, "healthy", "")
    s.record_source_run("naukri", True, 20, 38, 38, "healthy", "")
    assert s.recent_counts("naukri", 5) == [38, 40]
    s.close()


def test_source_health_summary_is_per_site_not_per_library(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    s.record_source_run("naukri", True, 20, 40, 40, "healthy", "")
    s.record_source_run("linkedin", False, 0, 0, 0, "blocked", "")
    sources = {h["source"] for h in s.source_health_summary()}
    assert sources == {"naukri", "linkedin"}
    s.close()
