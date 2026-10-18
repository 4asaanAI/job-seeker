from pathlib import Path

import pytest

from jobpilot.resume import load_base_resume, render_text
from jobpilot.resume.engine import extract_docx_text, render_docx
from jobpilot.resume.tailor import job_keywords, suggest

RESUME = """\
name: Pat Example
headline: Business Analyst, Fintech Research
required_facts: [Pat Example, Acme]
summary: Analyst focused on payments research.
experience:
  - id: acme
    employer: Acme
    title: Business Analyst
    dates: 2022-2025
    bullets:
      - {id: a1, text: Organised the team offsite and the quarterly lunches}
      - {id: a2, text: Led requirement analysis for payments and lending products}
      - {id: a3, text: Built SQL dashboards for product stakeholders}
  - id: beta
    employer: Beta
    title: Intern
    dates: 2021
    bullets:
      - {id: b1, text: Prepared weekly reports}
education:
  - {id: edu, institution: Uni, degree: BSc, dates: 2018-2022}
skills: [Excel, SQL, Tableau, Football]
"""
JOB_TITLE = "Technical Product Manager"
JOB_DESC = ("Own the payments product roadmap. Requirements analysis, SQL, API integration, stakeholder "
            "management and working with engineering. Kubernetes experience is a plus.")


@pytest.fixture
def base(tmp_path):
    f = tmp_path / "base.yaml"
    f.write_text(RESUME, encoding="utf-8")
    return load_base_resume(f)


def test_keywords_weight_the_title_and_drop_filler():
    kws = job_keywords(JOB_TITLE, JOB_DESC)
    assert {"product", "payments", "sql"} <= set(kws[:10])
    assert "the" not in kws and "experience" not in kws


def test_suggestion_ranks_relevant_bullets_first_and_flags_gaps(base):
    s = suggest(base, JOB_TITLE, JOB_DESC, max_bullets=2)
    assert s["bullet_order"]["acme"][:2] == ["a2", "a3"]       # offsite bullet dropped
    assert s["bullet_order"]["beta"] == ["b1"]                  # at least one kept per job
    assert s["skills"][0] == "SQL" and s["skills"][-1] == "Football"
    assert "kubernetes" in s["keywords_missing_from_resume"]    # a gap, never invented
    assert s["experience_order"] == ["acme", "beta"]            # history order is not rearranged


def test_suggestion_only_uses_ids_and_skills_from_the_resume(base):
    s = suggest(base, JOB_TITLE, JOB_DESC)
    real = {e.id: {b for b, _ in e.bullets} for e in base.experience}
    for exp, ids in s["bullet_order"].items():
        assert set(ids) <= real[exp]
    assert set(s["skills"]) == set(base.skills)


def test_rendered_text_contains_only_resume_text(base):
    s = suggest(base, JOB_TITLE, JOB_DESC, max_bullets=2)
    text = render_text(base, bullet_order=s["bullet_order"], skills=s["skills"])
    assert "Led requirement analysis" in text and "Organised the team offsite" not in text
    allowed = " ".join([base.name, base.headline, base.summary or "",
                        *[t for e in base.experience for _, t in e.bullets], *base.skills,
                        *[f"{e.employer} {e.title} {e.dates}" for e in base.experience]]).lower()
    for line in text.splitlines():
        if line.startswith("- "):
            assert line[2:].lower() in allowed


def test_docx_renders_selected_bullets_and_passes_the_fact_check(base, tmp_path):
    out = tmp_path / "r.docx"
    report = render_docx(base, out, bullet_order={"acme": ["a3", "a2"]})
    assert report.ok
    text = extract_docx_text(out)
    assert text.index("Built SQL dashboards") < text.index("Led requirement analysis")
    assert "Organised the team offsite" not in text


@pytest.mark.parametrize("bad", [{"acme": ["a9"]}, {"acme": []}, {"acme": ["a1", "a1"]}, {"nope": ["a1"]}])
def test_bad_bullet_selections_are_refused(base, bad):
    with pytest.raises(ValueError):
        render_text(base, bullet_order=bad)
