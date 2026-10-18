"""Deterministic, facts-only tailoring hints: rank a saved resume's own bullets and skills
against a job posting. Nothing here writes or invents text; it only suggests which of the
seeker's existing bullet ids and skills to keep and in what order."""
from __future__ import annotations

import re
from collections import Counter

from .engine import BaseResume

_STOP = frozenset("""a about above after all also an and any are as at be been being but by can could do does for
from had has have having he her his how i if in into is it its may more most must no not of on one or our out over
should so such than that the their them then there these they this those to up us was we were what when which who
will with within would you your able across including per plus etc experience experienced years year work working
role roles team teams job position candidate company strong ability responsibilities requirements preferred
required skills knowledge looking join opportunity benefits employer equal opportunity posting apply applicant demo fictional hire hiring location remote office based full time part""".split())
_WORD = re.compile(r"[a-z][a-z0-9+#.\-]{1,}")


def terms(text: str) -> list[str]:
    return [w.strip(".-") for w in _WORD.findall((text or "").lower())
            if w.strip(".-") not in _STOP and len(w.strip(".-")) >= 3]


def job_keywords(title: str, description: str, limit: int = 25) -> list[str]:
    """Most informative job terms: title words count triple."""
    counts: Counter[str] = Counter()
    for w in terms(title):
        counts[w] += 3
    for w in terms(description[:8000]):
        counts[w] += 1
    return [w for w, _ in counts.most_common(limit)]


def _score(text: str, keywords: list[str]) -> int:
    present = set(terms(text))
    return sum(1 for k in keywords if k in present)


def suggest(base: BaseResume, title: str, description: str, max_bullets: int = 4) -> dict:
    """Suggest bullet ids per experience (most relevant first, at least one kept), a skill order,
    and the job terms the resume does not mention (gaps to tell the user, never to fabricate)."""
    keywords = job_keywords(title, description)
    resume_terms = set(terms(" ".join([base.headline, base.summary or "",
                                       *[t for e in base.experience for _, t in e.bullets],
                                       *base.skills])))
    bullet_order = {}
    for exp in base.experience:
        if not exp.bullets:
            continue                       # nothing to select; render keeps the entry as is
        ranked = sorted(enumerate(exp.bullets), key=lambda ib: (-_score(ib[1][1], keywords), ib[0]))
        keep = [bid for _, (bid, _t) in ranked[:max(1, max_bullets)]]
        bullet_order[exp.id] = keep
    skills = sorted(base.skills, key=lambda s: (-_score(s, keywords), base.skills.index(s)))
    return {
        "job_keywords": keywords,
        "experience_order": [e.id for e in base.experience],
        "bullet_order": bullet_order,
        "skills": skills,
        "keywords_missing_from_resume": [k for k in keywords if k not in resume_terms][:15],
        "keywords_matched": [k for k in keywords if k in resume_terms][:15],
    }
