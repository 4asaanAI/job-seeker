"""Build a structured resume from a fill-in text file (for someone with no resume file) and check a structured one.

Everything built here is the person's own typed text. It becomes base_resume.yaml and, as plain text, resume_source.txt,
so the facts-only checks every later resume, Doc, letter and application relies on hold from the start. Nothing here
polishes or invents wording.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from ..apply import _instruction_like
from .engine import load_base_resume, render_text, unverified_facts

TEMPLATE = """\
# Fill this in with your own words from your CV, save the file, then go back to the terminal.
# Lines starting with # are ignored. Nothing you type is reworded.
# Example:  CONTACT: pat@example.com | +91 98765 43210 | Pune
# Example:  JOB: Acme Corp | Product Manager | 2020 - present
# Example:  EDUCATION: Example University | BTech, Computer Science | 2012 - 2016
NAME:
HEADLINE:
CONTACT:
SUMMARY:

# One JOB line per job, then its achievements, one per line starting with "- ". Copy the block for more jobs.
JOB:
-
-

# Optional. Repeat the line for more.
EDUCATION:

# Separate skills with commas.
SKILLS:
"""
_KEYS = ("NAME", "HEADLINE", "CONTACT", "SUMMARY", "JOB", "EDUCATION", "SKILLS")
_BULLETS = ("-", "•", "*")
_KNOWN = ("name", "headline", "contact", "summary", "experience", "education", "skills")


def _s(value: object) -> str:
    return " ".join(str(value or "").split())


def _parts(n: int, key: str, value: str, what: str) -> list[str] | None:
    parts = [p.strip() for p in value.split("|")]
    if not any(parts):
        return None                                   # the blank line from the template
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"line {n}: a {key} line needs three parts separated by |  ({what})")
    return parts


def parse_template(text: str) -> dict:
    """The fill-in file to the answers build_resume takes. Errors name the line."""
    a: dict = {"name": "", "headline": "", "contact": [], "summary": "", "jobs": [], "education": [], "skills": []}
    job = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line[0] in _BULLETS and line[1:2] in ("", " "):
            bullet = line[1:].strip()
            if bullet:
                if job is None:
                    raise ValueError(f"line {n}: an achievement must come after a JOB line")
                job["bullets"].append(bullet)
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip().upper(), value.strip()
        if not sep or key not in _KEYS:
            raise ValueError(f'line {n}: I do not understand "{line[:40]}". Start lines with one of: ' + ", ".join(_KEYS))
        if key == "JOB":
            parts = _parts(n, key, value, "employer | job title | dates")
            job = {"employer": parts[0], "title": parts[1], "dates": parts[2], "bullets": []} if parts else None
            if job:
                a["jobs"].append(job)
        elif key == "EDUCATION":
            parts = _parts(n, key, value, "school | degree | dates")
            if parts:
                a["education"].append({"institution": parts[0], "degree": parts[1], "dates": parts[2]})
        elif key == "SKILLS":
            a["skills"] += [s.strip() for s in value.split(",") if s.strip()]
        elif key == "CONTACT":
            a["contact"] = [p.strip() for p in value.split("|") if p.strip()]
        else:
            a[key.lower()] = value
    return a


def build_resume(answers: dict) -> dict:
    name, headline = _s(answers.get("name")), _s(answers.get("headline"))
    if not (name and headline):
        raise ValueError("a name and a headline are required")
    jobs = []
    for i, job in enumerate((j for j in answers.get("jobs") or [] if _s(j.get("employer"))), 1):
        bullets = [b for b in (_s(t) for t in job.get("bullets") or []) if b]
        jobs.append({"id": f"job{i}", "employer": _s(job.get("employer")), "title": _s(job.get("title")),
                     "dates": _s(job.get("dates")),
                     "bullets": [{"id": f"job{i}-b{k}", "text": t} for k, t in enumerate(bullets, 1)]})
    education = [{"id": f"edu{i}", "institution": _s(e.get("institution")), "degree": _s(e.get("degree")),
                  "dates": _s(e.get("dates"))}
                 for i, e in enumerate((e for e in answers.get("education") or [] if _s(e.get("institution"))), 1)]
    if not (any(j["bullets"] for j in jobs) or education):
        raise ValueError("add at least one job with an achievement, or one education line")
    skills = [s for s in (_s(x) for x in answers.get("skills") or []) if s]
    if not skills:
        raise ValueError("add at least one skill")
    resume: dict = {"name": name, "headline": headline,
                    "contact": [c for c in (_s(x) for x in answers.get("contact") or []) if c]}
    if _s(answers.get("summary")):
        resume["summary"] = _s(answers.get("summary"))
    resume["experience"] = jobs
    resume["education"] = education
    resume["skills"] = skills
    return resume


def _load(resume: dict):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "base_resume.yaml"
        path.write_text(yaml.safe_dump(resume, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return load_base_resume(path)


def resume_text(resume: dict) -> str:
    """The plain text of the resume; raises ValueError naming the empty field (for example experience.dates)."""
    return render_text(_load(resume))


def known_keys_only(data: object) -> dict:
    """Drop everything a model might add (above all required_facts, which switches the fail-closed checks off or on)."""
    if not isinstance(data, dict):
        raise ValueError("the structured resume is not a mapping")
    return {k: data[k] for k in _KNOWN if k in data}


def verify_structured(resume: object, source_text: str) -> list[str]:
    """Empty when the structured resume loads and every value in it appears in the source text."""
    try:
        base = _load(known_keys_only(resume))
    except Exception as exc:  # noqa: BLE001 - any malformed shape is a problem to report, never a crash
        return [f"the structured resume is not valid: {type(exc).__name__}: {exc}"]
    return [f"not found in the resume: {v}" for v in unverified_facts(base, source_text)]


def flagged_lines(resume: dict) -> list[str]:
    """Values that read like an instruction to an assistant (for a resume a model laid out: shown to the user, not blocked)."""
    base = _load(resume)
    values = [base.headline, base.summary or "", *base.skills]
    for e in base.experience:
        values += [e.employer, e.title, *(t for _, t in e.bullets)]
    return [v for v in values if v and _instruction_like(v)]
