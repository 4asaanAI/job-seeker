"""The user's own form data: fields read once from their saved resume, and an answers vault they fill in themselves.

Invariant: only the user's own facts go into form answers. Fo rewrites the user's words in a formal, polite tone (the
user asked for that, 2026-10-19), so a rewording is accepted when it keeps every fact (faithful_rewording). The resume fields are the user's resume text, split (never
reworded); the vault holds only what the user typed (the tool and the dashboard say so, and nothing else writes it).
`unsourced` checks a filled-in form against both, so a value from anywhere else is caught before submit.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from .resume.engine import BaseResume

VAULT_FILE = "answers.yaml"
MAX_ANSWERS, MAX_QUESTION, MAX_ANSWER = 20, 120, 3000   # the same caps as apply.vet_answers
# Suggested keys for the answers most forms ask; any other question text is allowed too.
STANDARD_QUESTIONS = {
    "work_authorization": "Are you legally authorised to work in the country of the job?",
    "sponsorship": "Will you now or in the future need visa sponsorship?",
    "notice_period": "What is your notice period / when can you start?",
    "salary_expectation": "What are your salary expectations?",
    "relocation": "Are you willing to relocate?",
    "current_ctc": "What is your current compensation?",
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+?\(?\+?\d[\d ()\-]{7,}\d")
_LINK = re.compile(r"(?:https?://)?(?:www\.)?([\w-]+\.)+[a-z]{2,}(?:/\S*)?", re.I)
_CURRENT = re.compile(r"\b(present|current|now|till date|to date)\b", re.I)


def form_fields(base: BaseResume) -> dict:
    """Common form fields, each copied or split from the resume text (never reworded or computed)."""
    parts = base.name.split()
    out: dict = {"full_name": base.name, "first_name": parts[0] if parts else "",
                 "last_name": " ".join(parts[1:]) if len(parts) > 1 else "", "headline": base.headline,
                 "email": None, "phone": None, "address": None, "links": []}
    for line in base.contact:
        email = _EMAIL.search(line)
        if email and out["email"] is None:
            out["email"] = email.group(0)
            continue
        phone = _PHONE.search(line)
        if phone and not _LINK.fullmatch(line.strip()) and out["phone"] is None and len(line) < 40:
            out["phone"] = phone.group(0).strip()
            continue
        if _LINK.fullmatch(line.strip()):
            out["links"].append(line.strip())
        elif out["address"] is None:
            out["address"] = line.strip()
    current = next((e for e in base.experience if _CURRENT.search(e.dates)), None)
    out["current_employer"] = current.employer if current else None      # a job with an end date is never "current"
    out["current_title"] = current.title if current else None
    out["experience"] = [{"employer": e.employer, "title": e.title, "dates": e.dates} for e in base.experience]
    out["education"] = [{"institution": e.institution, "degree": e.degree, "dates": e.dates} for e in base.education]
    out["skills"] = list(base.skills)
    return out


# ---- the answers vault ----------------------------------------------------------------------------------------------------

def _clean_question(question: object) -> str:
    key = " ".join(str(question or "").split())
    return STANDARD_QUESTIONS.get(key, key)


def load_answers(folder: Path) -> dict[str, str]:
    path = Path(folder) / VAULT_FILE
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return {str(q): str(a) for q, a in data.items() if str(q).strip() and str(a).strip()}


def _write(folder: Path, answers: dict[str, str]) -> None:
    path = Path(folder) / VAULT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=VAULT_FILE + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(answers, fh, sort_keys=False, allow_unicode=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_answer(folder: Path, question: object, answer: object) -> tuple[str, dict[str, str]]:
    """Store one answer exactly as the user typed it (an empty answer removes the question). Raises ValueError."""
    q, a = _clean_question(question), str(answer or "").strip()
    if not q:
        raise ValueError("the question must not be empty")
    if len(q) > MAX_QUESTION or len(a) > MAX_ANSWER:
        raise ValueError(f"a question is limited to {MAX_QUESTION} characters and an answer to {MAX_ANSWER}")
    answers = load_answers(folder)
    if a:
        if q not in answers and len(answers) >= MAX_ANSWERS:
            raise ValueError(f"at most {MAX_ANSWERS} saved answers; remove one first")
        answers[q] = a
    else:
        answers.pop(q, None)
    _write(folder, answers)
    return q, answers


def merged_answers(vault: dict[str, str], given: dict[str, str] | None) -> dict[str, str] | None:
    """The vault prefills every application; an answer given for this one job wins for the same question."""
    out = dict(vault)
    for q, a in (given or {}).items():
        out[_clean_question(q)] = a
    return out or None


# ---- the pre-submit check ---------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9@+]+(?:[.'_-][a-z0-9@+]+)*")
YES_NO = {"yes", "no", "true", "false", "y", "n"}


def _norm(text: object) -> str:
    """Lower-case words separated by single spaces: matching is by whole words, never inside a word."""
    return " ".join(_TOKEN.findall(str(text or "").lower()))


def unsourced(value: object, sources: list[str]) -> bool:
    """True when a filled-in value is not a whole-word run of the user's own texts (resume, saved answers). A bare
    yes/no is never "found" in prose: it must be the user's own answer to that question (see check_form)."""
    v = _norm(value)
    if not v:
        return False
    if v in YES_NO:
        return True
    return not any(f" {v} " in f" {_norm(s)} " for s in sources)


def _choice_matches(value: str, saved: str) -> bool:
    """A choice must equal the saved answer, or the yes/no word it starts with."""
    v, s = _norm(value), _norm(saved)
    return bool(v) and (v == s or (v in YES_NO and s.split(" ", 1)[0] == v))


_STOP = {"the", "a", "an", "you", "your", "are", "do", "will", "to", "of", "in", "for", "or", "and", "is", "this",
         "what", "now", "future", "any", "be", "with", "have", "if", "please", "require", "need", "at"}


def _words(text: object) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", str(text or "").lower()) if w not in _STOP and len(w) > 2}


def matching_answer(label: str, answers: dict[str, str]) -> str | None:
    """The user's saved answer whose question shares the most words with this form label (at least two), else None."""
    best, best_n = None, 1
    for question, answer in answers.items():
        n = len(_words(label) & _words(question))
        if n > best_n:
            best, best_n = answer, n
    return best


# ---- a formal rewording of the user's own answer -------------------------------------------------------------------------
# Fo rewrites the user's answers in a formal, polite tone before filling them in. A rewording is accepted only when it
# keeps the facts: no number, name or yes/no that the user's own text does not have, the same negation, and still
# about the same thing.
_NEGATION = re.compile(r"\b(no|not|never|none|cannot|can't|don't|doesn't|won't|without|n't)\b|n't\b", re.I)
_POLITE = {"i", "dear", "thank", "thanks", "regards", "sincerely", "kindly", "please", "yes", "no", "hiring", "team",
           "mr", "ms", "mrs", "dr"}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}


def _word(w: str) -> str:
    """'December' and 'Dec' are the same fact."""
    return w[:3] if w[:3] in _MONTHS and w.isalpha() else w


def _names(text: str) -> set[str]:
    """Capitalised words that do not start a sentence: the names and places a rewording must not invent."""
    out = set()
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]+)", text or ""):
        word = _word(m.group(1).lower())
        if word not in _POLITE:
            out.add(word)
    return out


def faithful_rewording(value: str, saved: str, other_sources: str = "") -> bool:
    """True when `value` reads as the user's `saved` answer reworded, with every fact kept and none added."""
    if not value or not saved or len(value) > 3 * len(saved) + 300:
        return False
    known = f"{saved} {other_sources}"
    if not _numbers(value) <= _numbers(known):
        return False
    if not _names(value) <= {_word(w) for w in _TOKEN.findall(known.lower())} | _POLITE:
        return False
    if bool(_NEGATION.search(saved)) != bool(_NEGATION.search(value)):
        return False
    first = _norm(saved).split(" ", 1)[0]
    words = set(_norm(value).split())
    if first in YES_NO and ({"yes", "no"} - {first}) & words and first not in words:
        return False
    content = [w for w in _norm(saved).split() if len(w) > 3 and w not in _STOP]
    if content:
        stems = {w[:5] for w in _norm(value).split()}
        if sum(w[:5] in stems for w in content) < max(1, round(0.4 * len(content))):
            return False
    return True


_LETTER_FIELD = re.compile(r"cover letter|motivation letter|letter of (?:motivation|interest)", re.I)


def check_form(fields: list[dict], resume_text: str, answers: dict[str, str] | None = None,
               letter_text: str | None = None) -> dict:
    """fields: [{label, required, value, kind}] as filled in on the form (kind "file" for an upload, "choice" for a
    dropdown, radio button or checkbox). Returns the checklist: every required field filled, every file present, and
    every value traceable to the user's own text (resume or saved answers)."""
    answers = answers or {}
    sources = [resume_text, *answers.values()]
    checklist, problems = [], 0
    for f in fields[:100]:
        label = " ".join(str(f.get("label") or "field").split())[:80]
        value = " ".join(str(f.get("value") or "").split())[:500]
        required = bool(f.get("required"))
        kind = str(f.get("kind") or "text").lower()
        if kind == "file":
            item = {"field": label, "ok": bool(value) or not required,
                    "check": "file attached" if value else ("required file missing" if required else "optional, left empty")}
        elif not value:
            item = {"field": label, "ok": not required,
                    "check": "required field is empty" if required else "optional, left empty"}
        elif kind in ("choice", "checkbox", "select", "radio", "consent"):
            saved = matching_answer(label, answers)
            if saved is None:
                item = {"field": label, "ok": False, "check": "no saved answer of the user's for this question"}
            elif _choice_matches(value, saved):
                item = {"field": label, "ok": True, "check": "matches the user's saved answer"}
            else:
                item = {"field": label, "ok": False, "check": "differs from the user's saved answer"}
        elif letter_text and _LETTER_FIELD.search(label) and not unsourced(value, [letter_text]):
            item = {"field": label, "ok": True, "check": "the cover letter the user approved"}
        elif value and _norm(value) in YES_NO and (saved := matching_answer(label, answers)) is not None:
            ok = _choice_matches(value, saved)
            item = {"field": label, "ok": ok, "check": "matches the user's saved answer" if ok
                    else "differs from the user's saved answer"}
        elif not unsourced(value, sources):
            item = {"field": label, "ok": True, "check": "from the user's own text"}
        elif (saved := matching_answer(label, answers)) is not None and faithful_rewording(value, saved, resume_text):
            item = {"field": label, "ok": True, "check": "a formal rewording of the user's saved answer, facts unchanged"}
        elif saved is not None:
            item = {"field": label, "ok": False, "check": "a rewording that changes or adds facts compared with the "
                                                          "user's saved answer"}
        else:
            item = {"field": label, "ok": False, "check": "value is not in the resume or the user's saved answers (if the "
                                                          "user just gave it, save it with save_answer first)"}
        problems += not item["ok"]
        checklist.append(item)
    return {"ready": problems == 0 and bool(checklist), "problems": problems, "checklist": checklist}
