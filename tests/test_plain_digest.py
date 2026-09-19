from jobpilot.digest.plain import render_plain_digest
from jobpilot.store.db import Store
from jobpilot.store.models import Posting


def test_plain_digest_has_no_html_and_records_only_when_requested(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.migrate()
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    pid = store.upsert_posting(
        Posting(company_name="Acme", source="indeed", external_id="a1",
                title="Analyst", location="Austin", description="<b>safe</b>",
                url="https://example.test/a1", posted_at="2026-09-18")
    )
    store.link_posting(sid, pid)
    store.record_score(sid, pid, 90, "Strong fit")

    preview = render_plain_digest(store, sid, 70, 5)
    assert preview.posting_ids == (pid,)
    assert "<" not in preview.text and ">" not in preview.text
    assert preview.recorded is False
    assert store.seeker_history(sid) == []

    delivered = render_plain_digest(store, sid, 70, 5, record=True)
    assert delivered.recorded is True
    assert delivered.posting_ids == (pid,)
    assert len(store.seeker_history(sid)) == 1
    store.close()
