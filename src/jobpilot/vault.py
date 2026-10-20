"""The user's own form data: fields read once from their saved resume, and an answers vault they fill in themselves.

Invariant: only the user's own facts go into form answers. Fo rewrites the user's words in a formal, polite tone (the
user asked for that, 2026-10-19), the user approves a formal version of each answer once (see "the formal version" below). The resume fields are the user's resume text, split (never
reworded); the vault holds only what the user typed (the tool and the dashboard say so, and nothing else writes it).
`unsourced` checks a filled-in form against both, so a value from anywhere else is caught before submit.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
import tempfile
from pathlib import Path

import yaml

from .resume.engine import BaseResume

VAULT_FILE = "answers.yaml"
MAX_FIELDS = 100
MAX_ANSWERS, MAX_QUESTION, MAX_ANSWER = 60, 120, 3000   # per-request answers are capped by apply.vet_answers
MAX_TOTAL_ANSWER_CHARS = 40_000
YES_NO_CHOICES = ["Yes", "No"]
# The ready-made questions the dashboard offers (group, kind, options, hint). kind: choice | text | long. Any other
# question text is allowed too; these are only the ones most forms ask.
QUESTION_CATALOG = [
    {"key": "work_authorization", "group": "Work eligibility", "kind": "choice", "options": YES_NO_CHOICES,
     "question": "Are you legally authorised to work in the country of the job?",
     "hint": "Answer for the country you are applying in."},
    {"key": "sponsorship", "group": "Work eligibility", "kind": "choice", "options": YES_NO_CHOICES,
     "question": "Will you now or in the future need visa sponsorship?"},
    {"key": "over_18", "group": "Work eligibility", "kind": "choice", "options": YES_NO_CHOICES,
     "question": "Are you at least 18 years old?"},
    {"key": "background_check", "group": "Work eligibility", "kind": "choice", "options": YES_NO_CHOICES,
     "question": "Do you consent to a background check?"},
    {"key": "notice_period", "group": "Availability", "kind": "text",
     "question": "What is your notice period / when can you start?",
     "hint": "For example: 30 days, or the date you can join."},
    {"key": "interview_availability", "group": "Availability", "kind": "text",
     "question": "When are you available for interviews?", "hint": "Days, times and your time zone."},
    {"key": "non_compete", "group": "Availability", "kind": "text",
     "question": "Are you bound by a non-compete or any agreement that limits who you can work for?"},
    {"key": "salary_expectation", "group": "Compensation", "kind": "text",
     "question": "What are your salary expectations?", "hint": "A figure or range, with currency and per year or month."},
    {"key": "current_ctc", "group": "Compensation", "kind": "text",
     "question": "What is your current compensation?", "hint": "Fixed and variable, with currency."},
    {"key": "relocation", "group": "Location and work style", "kind": "choice", "options": YES_NO_CHOICES,
     "question": "Are you willing to relocate?"},
    {"key": "work_mode_pref", "group": "Location and work style", "kind": "choice",
     "options": ["Remote", "Hybrid", "On-site", "Flexible"],
     "question": "Do you prefer remote, hybrid or on-site work?"},
    {"key": "current_location", "group": "Location and work style", "kind": "text",
     "question": "Where are you currently located?"},
    {"key": "travel", "group": "Location and work style", "kind": "choice", "options": ["Yes", "No", "Occasionally"],
     "question": "Are you willing to travel for work?"},
    {"key": "years_experience", "group": "About you", "kind": "text",
     "question": "How many years of professional experience do you have?"},
    {"key": "highest_education", "group": "About you", "kind": "text",
     "question": "What is your highest level of education?"},
    {"key": "languages", "group": "About you", "kind": "text",
     "question": "Which languages do you speak, and how well?"},
    {"key": "linkedin", "group": "About you", "kind": "text", "question": "What is your LinkedIn profile URL?"},
    {"key": "portfolio", "group": "About you", "kind": "text", "question": "Do you have a portfolio or website? Please share the link."},
    {"key": "references", "group": "About you", "kind": "choice", "options": ["Yes", "No", "On request"],
     "question": "Can you provide professional references?"},
    {"key": "why_role", "group": "Short written answers", "kind": "long",
     "question": "Why are you interested in this role and company?"},
    {"key": "strengths", "group": "Short written answers", "kind": "long", "question": "What are your key strengths?"},
    {"key": "achievement", "group": "Short written answers", "kind": "long",
     "question": "Describe your proudest professional achievement."},
    {"key": "why_leaving", "group": "Short written answers", "kind": "long",
     "question": "Why are you looking for a new role?"},
    {"key": "how_heard", "group": "Short written answers", "kind": "text", "question": "How did you hear about this job?"},
    {"key": "gender", "group": "Voluntary (optional)", "kind": "choice",
     "options": ["Male", "Female", "Non-binary", "Prefer not to say"], "question": "What is your gender? (voluntary)",
     "hint": "Optional. Many forms ask; \"Prefer not to say\" is always allowed."},
    {"key": "disability", "group": "Voluntary (optional)", "kind": "choice",
     "options": ["Yes", "No", "Prefer not to say"], "question": "Do you have a disability? (voluntary)"},
    {"key": "veteran", "group": "Voluntary (optional)", "kind": "choice",
     "options": ["Yes", "No", "Prefer not to say"], "question": "Are you a protected veteran? (voluntary)"},
]
STANDARD_QUESTIONS = {q["key"]: q["question"] for q in QUESTION_CATALOG}

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


@contextlib.contextmanager
def vault_lock(folder: Path):
    """One writer at a time across processes (Claude, the bridge and the dashboard share these files)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ".vault.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


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


def save_answer(folder: Path, question: object, answer: object, overwrite: bool = True) -> tuple[str, dict[str, str]]:
    """Store one answer exactly as the user typed it (an empty answer removes the question). overwrite=False refuses to
    change an answer that already exists. Raises ValueError."""
    q, a = _clean_question(question), str(answer or "").strip()
    if not q:
        raise ValueError("the question must not be empty")
    if len(q) > MAX_QUESTION or len(a) > MAX_ANSWER:
        raise ValueError(f"a question is limited to {MAX_QUESTION} characters and an answer to {MAX_ANSWER}")
    with vault_lock(folder):
        answers = load_answers(folder)
        if not overwrite and (q in answers or not a):
            raise ValueError("that question already has a saved answer; the user changes it in the dashboard (Search profile)")
        if a:
            if q not in answers and len(answers) >= MAX_ANSWERS:
                raise ValueError(f"at most {MAX_ANSWERS} saved answers; remove one first")
            if sum(len(v) for k, v in answers.items() if k != q) + len(a) > MAX_TOTAL_ANSWER_CHARS:
                raise ValueError("the saved answers are too long in total (40,000 characters); shorten or remove one")
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


_KEY_STEMS = ("sponsor", "authori", "notice", "salary", "compens", "ctc", "relocat", "clearance", "disab", "veteran")


def matching_answer(label: str, answers: dict[str, str]) -> str | None:
    """The user's saved answer for a form label. A key topic in the label (sponsorship, work authorisation, notice,
    salary, relocation...) selects only the questions about that topic; otherwise the most words in common (at least
    two) wins. A tie is no match: never guess between two answers."""
    low = (label or "").lower()
    pool = list(answers.items())
    for stem in _KEY_STEMS:
        if stem in low:
            topical = [(q, a) for q, a in pool if stem in q.lower()]
            if topical:
                pool = topical
                break
    scored = sorted(((len(_words(label) & _words(q)), q, a) for q, a in pool), key=lambda t: -t[0])
    if not scored or scored[0][0] < (1 if len(pool) < len(answers) else 2):
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][2]


# ---- the formal version of an answer, approved by the user ----------------------------------------------------------------
# The user asked for their own words to be put in a formal, polite tone before they go into a form. A model cannot be
# trusted to keep every fact (and a code check cannot prove it did), so JobPilot writes the formal version ONCE, the user
# approves that exact text, and from then on Fo copies the approved text word for word. Editing the original answer
# cancels the approval. Warnings (changed numbers, dropped qualifiers, flipped negation...) are shown with the draft.

FORMAL_FILE = "answers_formal.yaml"
FORMAL_PROMPT = """Rewrite the job seeker's answer below in a formal, polite tone for a job application form. Rules:
- Keep every fact, number, date, amount, currency, unit, name and every yes or no EXACTLY as given. Add nothing.
- Never soften, flip or drop a refusal, a limit (only, at least, at most, within, immediately) or a condition.
- At most one and a half times the original length. Plain text. No greeting or sign-off unless the answer has one.
Reply with only the rewritten answer.

Question: {question}
Answer: {answer}
"""
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
                 "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "lakh", "lakhs", "crore",
                 "million", "billion", "half", "dozen", "couple"}
_QUALIFIERS = {"only", "least", "most", "within", "immediately", "negotiable", "minimum", "maximum", "approximately",
               "around", "about", "exactly", "anywhere", "except", "unless", "provided", "subject", "depending", "upto",
               "earliest", "latest", "flexible", "currently", "previously"}
_NEG = re.compile(r"\b(no|not|never|none|nobody|nothing|neither|nor|cannot|cant|dont|doesnt|wont|isnt|arent|wasnt|"
                  r"without|unable|refuse\w*|decline\w*)\b|n't|\bun[a-z]{4,}\b|\bnon[a-z]{3,}\b", re.I)
_UNITS = re.compile(r"[$€£₹%]|\b(?:usd|eur|gbp|inr|rs|lpa|lakhs?|crores?|k|days?|weeks?|months?|years?|hours?|minutes?)\b",
                    re.I)
_POLITE = {"i", "dear", "thank", "thanks", "regards", "sincerely", "kindly", "please", "yes", "no", "hiring", "team",
           "mr", "ms", "mrs", "dr", "however", "furthermore", "additionally", "currently", "indeed", "my", "our", "the"}


def _count_negations(text: str) -> int:
    return len(_NEG.findall((text or "").replace("’", "'")))


def formal_warnings(raw: str, formal: str) -> list[str]:
    """Differences a person must look at before approving a formal version. An empty list is no proof: the user reads it."""
    raw, formal = str(raw or ""), str(formal or "")
    if not formal.strip():
        return ["the formal version is empty"]
    out = []

    def nums(t):
        return sorted(n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", t))

    def words(t):
        return set(re.findall(r"[a-z]+", t.lower()))

    def units(t):
        return sorted(u.lower().rstrip("s") for u in _UNITS.findall(t))
    if nums(raw) != nums(formal):
        out.append("the numbers differ from your answer")
    if (words(raw) & _NUMBER_WORDS) != (words(formal) & _NUMBER_WORDS):
        out.append("a number written as a word differs from your answer")
    if units(raw) != units(formal):
        out.append("a currency or unit differs from your answer")
    if (words(raw) & _QUALIFIERS) != (words(formal) & _QUALIFIERS):
        out.append("a limiting word (only, at least, immediately, ...) was added or dropped")
    if _count_negations(raw) != _count_negations(formal):
        out.append("a refusal or negation was added, dropped or changed")
    first_raw, first_formal = (re.findall(r"[a-z]+", raw.lower()) or [""])[0], (re.findall(r"[a-z]+", formal.lower()) or [""])[0]
    if first_raw in YES_NO and first_formal in YES_NO and first_raw != first_formal:
        out.append("the yes or no changed")
    def month(w):                                            # "December" and "Dec" are the same word
        return w[:3] if w[:3] in {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"} else w
    new_names = ({month(w.lower()) for w in re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]+)", formal)}
                 - {month(w) for w in words(raw)} - _POLITE)
    if new_names:
        out.append("names that are not in your answer: " + ", ".join(sorted(new_names)[:5]))
    if len(formal) > 1.5 * len(raw) + 60:
        out.append("much longer than your answer")
    return out


def load_formal(folder: Path) -> dict[str, dict]:
    path = Path(folder) / FORMAL_FILE
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(q): v for q, v in data.items() if isinstance(v, dict) and v.get("text")} if isinstance(data, dict) else {}


def _write_formal(folder: Path, data: dict) -> None:
    path = Path(folder) / FORMAL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=FORMAL_FILE + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _bad_text(text: str) -> bool:
    return bool(re.search(r"https?://|www\.|\b[\w-]+\.(?:com|in|io|ly|me|co|net|org)\b|[\w.+-]+@[\w-]+\.", text or "", re.I))


def set_formal_draft(folder: Path, question: str, formal: str, for_answer: str | None = None) -> list[str]:
    """Store a formal version for a saved answer, unapproved. Returns its warnings. for_answer: the answer the writer was
    given; if the saved answer changed meanwhile, the draft is refused."""
    q = _clean_question(question)
    with vault_lock(folder):
        raw = load_answers(folder).get(q)
        if raw is None:
            raise ValueError("no saved answer for that question")
        if for_answer is not None and raw != for_answer:
            raise ValueError("the answer changed while the formal version was being written; write it again")
        text = " ".join(str(formal or "").split())[:MAX_ANSWER]
        warnings = formal_warnings(raw, text)
        if _bad_text(text) and not _bad_text(raw):
            warnings.append("it contains a link or an address that your answer does not")
        data = load_formal(folder)
        data[q] = {"for": raw, "text": text, "approved": False, "warnings": warnings}
        _write_formal(folder, data)
    return warnings


def approve_formal(folder: Path, question: str, require_clean: bool = False, expected_text: str | None = None) -> str:
    """Approve the stored formal version exactly as it is. require_clean refuses a draft that has warnings. expected_text:
    the text the user was shown; approval happens only if the stored draft is still exactly that (checked in one read)."""
    q = _clean_question(question)
    with vault_lock(folder):
        formal = load_formal(folder)
        entry = formal.get(q)
        if entry is None or entry.get("for") != load_answers(folder).get(q):
            raise ValueError("no current formal draft for that answer: draft one first")
        if expected_text is not None and " ".join(str(expected_text).split()) != entry["text"]:
            raise ValueError("the formal version changed since you read it; read the new one first")
        if require_clean and entry.get("warnings"):
            raise ValueError("the draft has warnings (" + "; ".join(entry["warnings"][:3]) + "); the user approves it in the "
                             "dashboard after reading them")
        formal[q]["approved"] = True
        _write_formal(folder, formal)
    return entry["text"]


def effective_answers(folder: Path) -> dict[str, str]:
    """What goes into forms: the approved formal text where one is current, else the answer exactly as the user typed it."""
    raw = load_answers(folder)
    out = dict(raw)
    for q, entry in load_formal(folder).items():
        if entry.get("approved") and q in raw and entry.get("for") == raw[q]:
            out[q] = entry["text"]
    return out


def awaiting_formal(folder: Path) -> list[str]:
    """Saved answers that have no approved formal version yet (they go into forms exactly as typed)."""
    ok = approved_formal(folder)
    return [q for q in load_answers(folder) if q not in ok]


def approved_formal(folder: Path) -> dict[str, str]:
    """question -> the approved, current formal text (for the pre-submit check)."""
    raw = load_answers(folder)
    return {q: e["text"] for q, e in load_formal(folder).items()
            if e.get("approved") and q in raw and e.get("for") == raw[q]}


_LETTER_FIELD = re.compile(r"cover letter|motivation letter|letter of (?:motivation|interest)", re.I)


def check_form(fields: list[dict], resume_text: str, answers: dict[str, str] | None = None,
               letter_text: str | None = None, formal: dict[str, str] | None = None) -> dict:
    """fields: [{label, required, value, kind}] as filled in on the form (kind "file" for an upload, "choice" for a
    dropdown, radio button or checkbox). Returns the checklist: every required field filled, every file present, and
    every value traceable to the user's own text (resume or saved answers)."""
    answers = answers or {}
    formal = formal or {}                       # question -> the formal text the user approved
    sources = [resume_text, *answers.values(), *formal.values()]
    checklist, problems = [], 0
    if len(fields) > MAX_FIELDS:
        checklist.append({"field": "(form)", "ok": False, "check": f"more than {MAX_FIELDS} fields: split the check"})
        problems += 1
    for f in fields[:MAX_FIELDS]:
        label = " ".join(str(f.get("label") or "field").split())[:80]
        value = " ".join(str(f.get("value") or "").split())
        required = bool(f.get("required"))
        kind = str(f.get("kind") or "text").lower()
        if kind == "file":
            item = {"field": label, "ok": bool(value) or not required,
                    "check": "file attached" if value else ("required file missing" if required else "optional, left empty")}
        elif len(value) > MAX_ANSWER:
            item = {"field": label, "ok": False, "check": f"longer than {MAX_ANSWER} characters: not checked, so not accepted"}
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
            item = {"field": label, "ok": True, "check": "from the user's own text (or the formal version they approved)"}
        else:
            item = {"field": label, "ok": False, "check": "value is not in the resume, the user's saved answers or their "
                                                          "approved formal versions (if the user just gave it, save it "
                                                          "with save_answer and approve its formal version first)"}
        problems += not item["ok"]
        checklist.append(item)
    return {"ready": problems == 0 and bool(checklist), "problems": problems, "checklist": checklist}
