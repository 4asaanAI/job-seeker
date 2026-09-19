from datetime import datetime, timedelta, timezone

import pytest

from jobpilot.digest.render import render_digest
from jobpilot.store.db import Store
from jobpilot.store.models import Posting


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    yield s
    s.close()


def _add(store, sid, ext, title, score, company="M&S", url=None):
    pid = store.upsert_posting(
        Posting(company_name=company, source="naukri", external_id=ext,
                title=title, location="Gurgaon", description="d",
                url=url if url is not None else f"https://x.test/{ext}",
                posted_at="2026-09-18"))
    store.link_posting(sid, pid)
    store.record_score(sid, pid, score, "Account adjacency to M&S", "claude")
    return pid


def test_digest_lists_high_scoring_jobs(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-1", "Senior Merchandiser", 88.0)
    store.record_source_run("naukri", True, 20, 40, 40, "healthy", "Returned 40 results.")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "Senior Merchandiser" in html
    assert "Account adjacency to M&amp;S" in html or "Account adjacency" in html


def test_second_digest_does_not_repeat_the_same_job(store, tmp_path):
    """The headline goal, asserted against the thing that reaches the seeker."""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-1", "Senior Merchandiser", 88.0)
    store.record_source_run("naukri", True, 20, 40, 40, "healthy", "")

    first = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "Senior Merchandiser" in first

    second = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "Senior Merchandiser" not in second


def test_record_false_does_not_consume_the_job(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-1", "Senior Merchandiser", 88.0)
    store.record_source_run("naukri", True, 20, 40, 40, "healthy", "")

    render_digest(store, sid, 70.0, 5, tmp_path, record=False)
    again = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "Senior Merchandiser" in again


def test_digest_states_a_blocked_source_and_does_not_claim_quiet(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    store.record_source_run("naukri", False, 0, 0, 0, "blocked", "canary below floor")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "blocked" in html.lower()
    assert "genuinely quiet period" not in html


def test_quiet_but_healthy_run_says_so(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    store.record_source_run("naukri", True, 20, 0, 0, "healthy", "Returned 0 results.")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "genuinely quiet period" in html


def test_digest_with_no_source_runs_refuses_to_claim_health(store, tmp_path):
    """any([]) is False. Announcing 'All sources healthy' knowing nothing is
    the exact failure the spec exists to prevent."""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "no source health" in html.lower()
    assert "all sources healthy" not in html.lower()
    assert "genuinely quiet period" not in html


def test_a_stale_healthy_run_is_flagged_not_silently_trusted(store, tmp_path):
    """Deferred final-review finding: source_health_summary's staleness must
    actually be wired into the digest, not left theoretical. A source whose
    last run predates the staleness window must not be reported as current
    just because its OWN status was healthy at the time."""
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-1", "Senior Merchandiser", 88.0)
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    store.conn.execute(
        "INSERT INTO source_runs (source, run_at, canary_ok, canary_count, raw_count, "
        "results_count, status, notes) VALUES (?,?,?,?,?,?,?,?)",
        ("naukri", old, 1, 20, 40, 40, "healthy", ""),
    )
    store.conn.commit()

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")

    assert "could not be read" in html.lower() or "stale" in html.lower()
    assert "all sources healthy" not in html.lower()


def test_a_fresh_healthy_run_is_not_flagged_stale(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-1", "Senior Merchandiser", 88.0)
    store.record_source_run("naukri", True, 20, 40, 40, "healthy", "")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")

    assert "could not be read" not in html.lower()


def test_digest_escapes_html_in_job_titles(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-x", "<script>alert(1)</script>", 90.0)
    store.record_source_run("naukri", True, 20, 5, 5, "healthy", "")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_digest_never_emits_a_javascript_url(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    pid = store.upsert_posting(
        Posting(company_name="X", source="naukri", external_id="n9",
                title="Role", location="G", description="d",
                url=None, posted_at="2026-09-18"))
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 90.0, "fit")
    store.conn.execute(
        "UPDATE job_postings SET canonical_url = 'javascript:alert(1)' WHERE id = ?",
        (pid,))
    store.conn.commit()
    store.record_source_run("naukri", True, 20, 5, 5, "healthy", "")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "javascript:" not in html


def test_digest_is_per_seeker_when_both_have_data(store, tmp_path):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    _add(store, a, "n-1", "Alex Only Role", 90.0)
    _add(store, b, "n-2", "Priya Only Role", 90.0, company="Gap")
    store.record_source_run("naukri", True, 20, 5, 5, "healthy", "")

    html_b = render_digest(store, b, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "Priya Only Role" in html_b
    assert "Alex Only Role" not in html_b


def test_digests_live_in_per_seeker_directories(store, tmp_path):
    a = store.add_seeker("Alex", 1, "a.yaml", 20)
    b = store.add_seeker("Priya", 2, "b.yaml", 20)
    store.record_source_run("naukri", True, 20, 5, 5, "healthy", "")

    pa = render_digest(store, a, 70.0, 5, tmp_path)
    pb = render_digest(store, b, 70.0, 5, tmp_path)

    assert pa.parent != pb.parent
    assert store.get_seeker(a).output_token in str(pa)


def test_digest_reports_found_versus_cleared(store, tmp_path):
    sid = store.add_seeker("Alex", 1, "a.yaml", 20)
    _add(store, sid, "n-1", "Low Role", 30.0)
    store.record_source_run("naukri", True, 20, 40, 40, "healthy", "")

    html = render_digest(store, sid, 70.0, 5, tmp_path).read_text(encoding="utf-8")
    assert "40" in html  # found
    assert "none cleared" in html.lower() or "0 cleared" in html.lower()
