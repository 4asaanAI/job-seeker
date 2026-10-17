"""A conservative, facts-only resume renderer.

The model may choose and order facts from a seeker's structured base resume,
but this module never accepts model-written factual prose. Every rendered file
is extracted again and must contain the configured required facts before it is
considered deliverable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt


class ResumeValidationError(ValueError):
    """Raised when a generated resume fails its blocking extraction gate."""

    def __init__(self, report: "ValidationReport"):
        self.report = report
        super().__init__(
            f"resume validation failed for {report.path}: "
            + ", ".join(report.missing)
        )


@dataclass(frozen=True)
class Experience:
    id: str
    employer: str
    title: str
    dates: str
    bullets: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Education:
    id: str
    institution: str
    degree: str
    dates: str


@dataclass(frozen=True)
class BaseResume:
    name: str
    headline: str
    contact: tuple[str, ...]
    summary: str | None
    experience: tuple[Experience, ...]
    education: tuple[Education, ...]
    skills: tuple[str, ...]
    required_facts: tuple[str, ...]


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    format: str
    path: str
    missing: tuple[str, ...]
    extracted_chars: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "format": self.format,
            "path": self.path,
            "missing": list(self.missing),
            "extracted_chars": self.extracted_chars,
        }


def _required(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"resume field {label} must not be empty")
    return text


def _unique_ids(items: list[dict[str, Any]], label: str) -> None:
    ids = [_required(item.get("id"), f"{label}.id") for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"resume {label} ids must be unique")


def load_base_resume(path: Path) -> BaseResume:
    """Load and validate a structured resume without rewriting any facts."""
    source = Path(path)
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"resume {source} must contain a YAML mapping")

    experience_raw = raw.get("experience") or []
    education_raw = raw.get("education") or []
    if not isinstance(experience_raw, list) or not isinstance(education_raw, list):
        raise ValueError("resume experience and education must be lists")
    _unique_ids(experience_raw, "experience")
    _unique_ids(education_raw, "education")

    experience: list[Experience] = []
    for item in experience_raw:
        if not isinstance(item, dict):
            raise ValueError("each resume experience item must be a mapping")
        bullets_raw = item.get("bullets") or []
        if not isinstance(bullets_raw, list):
            raise ValueError(f"resume experience {item.get('id')} bullets must be a list")
        bullets: list[tuple[str, str]] = []
        for index, bullet in enumerate(bullets_raw, start=1):
            if isinstance(bullet, dict):
                bullet_id = _required(
                    bullet.get("id"), f"experience.{item.get('id')}.bullet.id"
                )
                text = _required(
                    bullet.get("text"), f"experience.{item.get('id')}.bullet.text"
                )
            else:
                bullet_id = f"{item['id']}-bullet-{index}"
                text = _required(
                    bullet, f"experience.{item.get('id')}.bullet.text"
                )
            bullets.append((bullet_id, text))
        if len({bullet_id for bullet_id, _ in bullets}) != len(bullets):
            raise ValueError(f"resume experience {item.get('id')} bullet ids must be unique")
        experience.append(
            Experience(
                id=_required(item.get("id"), "experience.id"),
                employer=_required(item.get("employer"), "experience.employer"),
                title=_required(item.get("title"), "experience.title"),
                dates=_required(item.get("dates"), "experience.dates"),
                bullets=tuple(bullets),
            )
        )

    education = tuple(
        Education(
            id=_required(item.get("id"), "education.id"),
            institution=_required(item.get("institution"), "education.institution"),
            degree=_required(item.get("degree"), "education.degree"),
            dates=_required(item.get("dates"), "education.dates"),
        )
        for item in education_raw
    )
    skills_raw = raw.get("skills") or []
    if not isinstance(skills_raw, list):
        raise ValueError("resume skills must be a list")
    skills = tuple(_required(skill, "skills[]") for skill in skills_raw)

    required = raw.get("required_facts")
    if required is None:
        required = [
            _required(raw.get("name"), "name"),
            _required(raw.get("headline"), "headline"),
            *[item.employer for item in experience],
        ]
        if experience:
            required.append(experience[0].title)
    if not isinstance(required, list):
        raise ValueError("resume required_facts must be a list")

    return BaseResume(
        name=_required(raw.get("name"), "name"),
        headline=_required(raw.get("headline"), "headline"),
        contact=tuple(_required(item, "contact[]") for item in (raw.get("contact") or [])),
        summary=str(raw.get("summary") or "").strip() or None,
        experience=tuple(experience),
        education=education,
        skills=skills,
        required_facts=tuple(_required(item, "required_facts[]") for item in required),
    )


def _select_experience(
    base: BaseResume, experience_order: list[str] | None
) -> list[Experience]:
    by_id = {item.id: item for item in base.experience}
    if experience_order is None:
        return list(base.experience)
    if len(experience_order) != len(set(experience_order)):
        raise ValueError("experience_order contains duplicates")
    unknown = [item for item in experience_order if item not in by_id]
    if unknown:
        raise ValueError(f"experience_order contains unknown facts: {unknown}")
    return [by_id[item] for item in experience_order]


def _select_skills(base: BaseResume, skills: list[str] | None) -> list[str]:
    if skills is None:
        return list(base.skills)
    if len(skills) != len(set(skills)):
        raise ValueError("skills contains duplicates")
    unknown = [item for item in skills if item not in base.skills]
    if unknown:
        raise ValueError(f"skills contains facts not present in base resume: {unknown}")
    return skills


def _configure_document(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Inches(0.55)
    section.bottom_margin = Inches(0.55)
    section.left_margin = Inches(0.65)
    section.right_margin = Inches(0.65)
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(9.5)
    for paragraph in document.paragraphs:
        paragraph.paragraph_format.space_after = Pt(0)


def _add_heading(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(7)
    paragraph.paragraph_format.space_after = Pt(2)
    run = paragraph.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(10.5)


def _add_bullet(document: Document, text: str) -> None:
    paragraph = document.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(1)
    paragraph.add_run(text)


def _render_document(
    base: BaseResume,
    path: Path,
    experience_order: list[str] | None,
    skills: list[str] | None,
) -> None:
    document = Document()
    _configure_document(document)

    name = document.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name.add_run(base.name)
    run.bold = True
    run.font.size = Pt(18)
    headline = document.add_paragraph()
    headline.alignment = WD_ALIGN_PARAGRAPH.CENTER
    headline.add_run(base.headline)
    if base.contact:
        contact = document.add_paragraph()
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact.add_run(" | ".join(base.contact))

    if base.summary:
        _add_heading(document, "Summary")
        document.add_paragraph(base.summary)

    _add_heading(document, "Experience")
    for item in _select_experience(base, experience_order):
        heading = document.add_paragraph()
        heading.paragraph_format.space_before = Pt(4)
        employer = heading.add_run(item.employer)
        employer.bold = True
        heading.add_run(f" | {item.title} | {item.dates}")
        for _bullet_id, text in item.bullets:
            _add_bullet(document, text)

    if base.education:
        _add_heading(document, "Education")
        for item in base.education:
            paragraph = document.add_paragraph()
            paragraph.add_run(item.institution).bold = True
            paragraph.add_run(f" | {item.degree} | {item.dates}")

    selected_skills = _select_skills(base, skills)
    if selected_skills:
        _add_heading(document, "Skills")
        document.add_paragraph(" | ".join(selected_skills))

    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


def _report(format_name: str, path: Path, text: str, required: tuple[str, ...]) -> ValidationReport:
    lowered = text.casefold()
    missing = tuple(fact for fact in required if fact.casefold() not in lowered)
    return ValidationReport(
        ok=not missing,
        format=format_name,
        path=str(path),
        missing=missing,
        extracted_chars=len(text),
    )


def extract_docx_text(path: Path) -> str:
    document = Document(path)
    if document.tables:
        raise ValueError("resume DOCX contains a table; ATS-safe layout forbids tables")
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def validate_docx(path: Path, required_facts: tuple[str, ...]) -> ValidationReport:
    return _report("docx", Path(path), extract_docx_text(Path(path)), required_facts)


def validate_pdf(path: Path, required_facts: tuple[str, ...]) -> ValidationReport:
    """Extract and validate a PDF; pypdf is an explicit runtime dependency."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised in packaging checks
        raise RuntimeError("PDF validation requires the pypdf dependency") from exc
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return _report("pdf", Path(path), text, required_facts)


def render_text(
    base: BaseResume,
    *,
    experience_order: list[str] | None = None,
    skills: list[str] | None = None,
) -> str:
    """Plain-text resume from base facts only, for channels that cannot carry a file."""
    lines = [base.name, base.headline, *base.contact]
    if base.summary:
        lines += ["", "SUMMARY", base.summary]
    chosen = _select_experience(base, experience_order)
    if chosen:
        lines += ["", "EXPERIENCE"]
        for item in chosen:
            lines.append(f"{item.title}, {item.employer} ({item.dates})")
            lines += [f"- {text}" for _, text in item.bullets]
    if base.education:
        lines += ["", "EDUCATION"]
        lines += [f"{e.degree}, {e.institution} ({e.dates})" for e in base.education]
    picked = _select_skills(base, skills)
    if picked:
        lines += ["", "SKILLS", ", ".join(picked)]
    return "\n".join(lines)


def render_docx(
    base: BaseResume,
    path: Path,
    *,
    experience_order: list[str] | None = None,
    skills: list[str] | None = None,
) -> ValidationReport:
    """Render only base facts and fail closed if extraction loses a fact."""
    output = Path(path)
    _render_document(base, output, experience_order, skills)
    report = validate_docx(output, base.required_facts)
    if not report.ok:
        output.unlink(missing_ok=True)
        raise ResumeValidationError(report)
    return report


def extract_source_text(path: Path) -> str:
    """Plain text of a seeker's own uploaded resume (PDF, DOCX or TXT)."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(source)).pages)
    if suffix == ".docx":
        document = Document(source)
        lines = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.extend(cell.text for cell in row.cells)
        return "\n".join(lines)
    if suffix == ".txt":
        return source.read_text(encoding="utf-8")
    raise ValueError(f"unsupported resume format: {suffix or 'none'}")


_DASHES = str.maketrans({"–": "-", "—": "-", "‑": "-"})


def _squash(text: str) -> str:
    # PDF extraction splits words ("client -facing", "GP A"), so compare with
    # all whitespace removed; casing and dash style are not facts.
    return "".join(text.translate(_DASHES).casefold().split())


def unverified_facts(base: BaseResume, source_text: str) -> list[str]:
    """Every value in ``base`` that does not appear verbatim in the source.

    A structured resume built from an uploaded file must be a pure re-layout
    of that file: anything not found in it is a model-written claim.
    """
    haystack = _squash(source_text)
    values = [base.name, base.headline, *base.contact, base.summary or "", *base.skills]
    for item in base.experience:
        values += [item.employer, item.title, item.dates, *(text for _, text in item.bullets)]
    for item in base.education:
        values += [item.institution, item.degree, item.dates]
    return [value for value in values if value and _squash(value) not in haystack]
