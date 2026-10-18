"""A cover letter assembled from a seeker's own resume facts. No model writes any of it.

Every line is a fixed template line, a value copied verbatim from the saved resume, or the company name the caller already
vetted. The job title is deliberately not in the letter: text from a job post never enters a block that Fo treats as the
user's own words.
"""
from __future__ import annotations

from .engine import BaseResume


def build_letter(base: BaseResume, company: str, bullets: list[tuple[str, str]], matched: bool,
                 skills: list[str]) -> str:
    lines = ["Dear Hiring Team,", "", f"I am writing to apply for the role at {company}."]
    if base.summary:
        lines += ["", base.summary]
    if bullets:
        lines += ["", "Work most relevant to this role:" if matched else "From my experience:"]
        lines += [f"- {text} ({employer})" for employer, text in bullets]
    if len(skills) >= 2:
        lines += ["", "Skills that match the role: " + ", ".join(skills)]
    lines += ["", "Thank you for your time and consideration.", "", "Sincerely,", base.name, base.headline, *base.contact]
    return "\n".join(lines)
