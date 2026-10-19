
import pytest
import yaml
from docx import Document

from jobpilot import doctor, onboard
from jobpilot.store.db import Store

LONG = ("Pat Example. Business analyst with experience in payments and lending research. " * 6)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.migrate()
    yield s
    s.close()


def resume_docx(tmp_path, text=LONG):
    d = Document()
    d.add_paragraph(text)
    p = tmp_path / "resume.docx"
    d.save(p)
    return p


def test_onboard_creates_folder_profile_source_text_and_seeker(tmp_path, store):
    out = onboard.onboard(store, tmp_path, name="Pat Example", resume=resume_docx(tmp_path), role="Product Manager",
                          location="India", terms=["product manager"], sources=["indeed_in"], notes=["20 LPA+"])
    folder = tmp_path / out["folder"]
    assert (folder / "uploaded_resume.docx").is_file() and "payments" in (folder / "resume_source.txt").read_text()
    prof = yaml.safe_load((folder / "profile.yaml").read_text())
    assert prof["rubric"] == "general" and prof["search_terms"] == ["product manager"] and prof["notes"] == ["20 LPA+"]
    assert store.list_seekers()[0].id == out["seeker_id"]


def test_onboard_refuses_unreadable_missing_duplicate_and_incomplete(tmp_path, store):
    with pytest.raises(ValueError, match="not found"):
        onboard.onboard(store, tmp_path, name="A", resume=tmp_path / "nope.pdf", role="r", location="l", terms=["t"], sources=["s"])
    with pytest.raises(ValueError, match="could not be read"):
        onboard.onboard(store, tmp_path, name="A", resume=resume_docx(tmp_path, "tiny"), role="r", location="l", terms=["t"], sources=["s"])
    good = resume_docx(tmp_path)
    onboard.onboard(store, tmp_path, name="A", resume=good, role="r", location="l", terms=["t"], sources=["s"])
    with pytest.raises(ValueError, match="already exists"):
        onboard.onboard(store, tmp_path, name="A", resume=good, role="r", location="l", terms=["t"], sources=["s"])
    with pytest.raises(ValueError, match="at least one"):
        onboard.onboard(store, tmp_path, name="B", resume=good, role="r", location="l", terms=[], sources=["s"])
    assert not (tmp_path / "data" / "seekers" / "b").exists()          # nothing half-created


def test_doctor_flags_missing_pieces_then_goes_green(tmp_path, store, monkeypatch):
    monkeypatch.setenv("JOBPILOT_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[paths]\ndata_dir="data"\nout_dir="out"\n[limits]\nscore_batch=40\nmin_request_gap_seconds=4.0\n', encoding="utf-8")
    first = doctor.checks(store, tmp_path, None)
    assert any(not ok and "at least one seeker" in what for ok, what, fix in first)
    out = onboard.onboard(store, tmp_path, name="Pat", resume=resume_docx(tmp_path), role="PM", location="X",
                          terms=["pm"], sources=["indeed_in"])
    res = doctor.checks(store, tmp_path, out["seeker_id"])
    bad = [what for ok, what, fix in res if not ok and "claude CLI" not in what and "Python" not in what]
    assert bad == ["structured resume saved (base_resume.yaml)"]          # the one step left for Claude to do
