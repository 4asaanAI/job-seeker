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


def top_bullets(base: BaseResume, title: str, description: str, n: int = 2,
                experience_order: list[str] | None = None,
                bullet_order: dict[str, list[str]] | None = None) -> tuple[list[tuple[str, str]], bool]:
    """(employer, bullet) pairs, verbatim, that share the most terms with the job, and whether any actually matched.

    Only bullets the application really uses are considered (experience_order / bullet_order, same meaning as in
    the resume render); ties keep resume order."""
    keywords = job_keywords(title, description)
    items = []
    for e in base.experience:
        if experience_order is not None and e.id not in experience_order:
            continue
        keep = set(bullet_order[e.id]) if bullet_order and e.id in bullet_order else None
        items += [(e.employer, text) for bid, text in e.bullets if keep is None or bid in keep]
    ranked = sorted(enumerate(items), key=lambda it: (-_score(it[1][1], keywords), it[0]))[:n]
    return [item for _, item in ranked], any(_score(item[1], keywords) > 0 for _, item in ranked)


def matching_skills(base: BaseResume, title: str, description: str, n: int = 6) -> list[str]:
    """The seeker's own skills that the job mentions, in resume order."""
    keywords = job_keywords(title, description)
    return [s for s in base.skills if _score(s, keywords) > 0][:n]
