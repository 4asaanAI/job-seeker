"""Match-time rules: facts read from a posting, the seeker's filters, and the score adjustments learned from outcomes.

Everything here is plain Python over text the boards sent, so it is deterministic and testable. A fact that cannot be
read is None, and a filter never drops a job for an unknown fact (a missing salary is not a low salary).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

# ---- facts ------------------------------------------------------------------------------------------------------------

LAKH = 100_000


@dataclass(frozen=True)
class Facts:
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None      # INR | USD | EUR | GBP
    work_mode: str | None = None             # remote | hybrid | onsite
    posted_on: str | None = None             # YYYY-MM-DD


_NUM = r"(\d+(?:[.,]\d+)*)"
_DASH = r"\s*(?:-|–|—|to)\s*"
_LPA = re.compile(_NUM + r"(?:" + _DASH + _NUM + r")?\s*(?:lpa|lakhs?(?:\s*per\s*annum)?|l\.?p\.?a\.?)\b", re.I)
_RUPEE = re.compile(r"(?:₹|inr|rs\.?)\s*" + _NUM + r"(?:" + _DASH + r"(?:₹|inr|rs\.?)?\s*" + _NUM + r")?", re.I)
_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP"}
_MONEY = re.compile(r"([$€£])\s*" + _NUM + r"\s*([kK])?(?:" + _DASH + r"[$€£]?\s*" + _NUM + r"\s*([kK])?)?")
_CODE = re.compile(r"\b(USD|EUR|GBP)\s*" + _NUM + r"\s*([kK])?(?:" + _DASH + _NUM + r"\s*([kK])?)?", re.I)


def _amount(text: str, k: str | None = None) -> float | None:
    try:
        value = float(text.replace(",", ""))
    except ValueError:
        return None
    return value * 1000 if k else value


def salary(text: str) -> tuple[float | None, float | None, str | None]:
    """A yearly salary range stated in the text, or (None, None, None). Hourly or monthly figures are ignored."""
    text = text or ""
    m = _LPA.search(text)
    if m:
        low, high = _amount(m.group(1)), _amount(m.group(2)) if m.group(2) else None
        if low is not None and 0 < low < 1000:
            return low * LAKH, (high * LAKH if high else None), "INR"
    m = _RUPEE.search(text)
    if m:
        low, high = _amount(m.group(1)), _amount(m.group(2)) if m.group(2) else None
        if low and low >= 100_000:                          # a monthly rupee figure is too easy to misread: skip it
            return low, (high if high and high >= low else None), "INR"
    for pattern, currency_of in ((_MONEY, lambda g: _SYMBOL[g]), (_CODE, lambda g: g.upper())):
        m = pattern.search(text)
        if m:
            low, high = _amount(m.group(2), m.group(3)), _amount(m.group(4), m.group(5)) if m.group(4) else None
            if low and low >= 10_000 and not re.search(r"/\s*(?:hr|hour|month|mo)\b|per\s+(?:hour|month)",
                                                       text[m.end():m.end() + 20], re.I):
                return low, (high if high and high >= low else None), currency_of(m.group(1))
    return None, None, None


_REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)
_ONSITE = re.compile(r"\b(on-?site|in[- ]office|work from office|wfo)\b", re.I)
_REMOTE_DESC = re.compile(r"\b(fully remote|100% remote|remote[- ]first|this is a remote|remote position|remote role)\b", re.I)


def work_mode(title: str, location: str, description: str) -> str | None:
    """remote / hybrid / onsite when the posting says so; the title and location speak louder than the description."""
    head = f"{title or ''} {location or ''}"
    for pattern, mode in ((_HYBRID, "hybrid"), (_REMOTE, "remote"), (_ONSITE, "onsite")):
        if pattern.search(head):
            return mode
    desc = description or ""
    if _HYBRID.search(desc):
        return "hybrid"
    if _REMOTE_DESC.search(desc):
        return "remote"
    if _ONSITE.search(desc):
        return "onsite"
    return None


def posted_day(value: str | None) -> str | None:
    """YYYY-MM-DD from an ISO date or datetime, or epoch seconds/milliseconds; None when unreadable."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{9,13}", text):
        seconds = int(text) / (1000 if len(text) > 11 else 1)
        return time.strftime("%Y-%m-%d", time.gmtime(seconds))
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return m.group(0)
    return None


def facts_for(title: str, location: str | None, description: str | None, posted_at: str | None) -> Facts:
    low, high, currency = salary(f"{title or ''} {location or ''} {description or ''}")
    return Facts(low, high, currency, work_mode(title, location or "", description or ""), posted_day(posted_at))


# ---- companies --------------------------------------------------------------------------------------------------------

_STAFFING_NAMES = re.compile(
    r"\b(staffing|recruit(?:ment|ers?|ing)|manpower|placements?|headhunt\w*|talent (?:solutions|acquisition|partners)|"
    r"hr (?:solutions|services|consult\w*)|consultancy services|outsourcing|executive search|"
    r"randstad|adecco|teamlease|quess|kelly services|robert half|michael page|abc consultants|insight global|"
    r"teksystems|hays|allegis|aerotek|cielo|kforce|collabera|ciel hr|careernet|xpheno)\b", re.I)
# Only phrases an agency uses about the employer it recruits for: consultancies and banks also "serve our clients".
_STAFFING_TEXT = re.compile(
    r"\b(on behalf of (?:our|a) client\b|on behalf of one of our clients|(?:hiring|recruiting|looking) for (?:our|a|one of our) clients?"
    r"(?!\s+(?:servic\w*|success|relations\w*|partner|facing|management|onboarding|support|experience|delivery))|"
    r"our client,? (?:is|a|an|one of)|for one of our clients|client of ours|our esteemed client)\b", re.I)


def norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _listed(company: str, names: list[str]) -> bool:
    hay = f" {norm_company(company)} "
    return any(f" {n} " in hay for n in (norm_company(x) for x in names) if n)


def is_staffing_agency(company: str, description: str | None = None) -> bool:
    return bool(_STAFFING_NAMES.search(company or "") or _STAFFING_TEXT.search((description or "")[:3000]))


# ---- the seeker's filters ---------------------------------------------------------------------------------------------

def filter_reason(profile, company: str, title: str, location: str | None, description: str | None,
                  facts: Facts) -> str | None:
    """Why this job is filtered out for this seeker, or None when it passes. Unknown facts always pass."""
    if _listed(company, getattr(profile, "blacklist_companies", []) or []):
        return "company is on your blacklist"
    if _listed(company, getattr(profile, "whitelist_companies", []) or []):
        return None                                             # a company the user asked for always reaches matches
    if getattr(profile, "exclude_staffing_agencies", True) and is_staffing_agency(company, description):
        return "staffing agency post"
    floor, currency = getattr(profile, "salary_floor", None), getattr(profile, "salary_currency", None)
    if floor and currency and facts.salary_currency == currency.upper():
        best = facts.salary_max or facts.salary_min
        if best is not None and best < float(floor):
            return "salary below your floor"
    modes = [m.lower() for m in getattr(profile, "work_modes", []) or []]
    if modes and facts.work_mode and facts.work_mode not in modes:
        return f"{facts.work_mode} job; you asked for {', '.join(modes)}"
    places = [p for p in getattr(profile, "locations", []) or [] if str(p).strip()]
    if places and location and not (facts.work_mode == "remote" and "remote" in [p.lower() for p in places]):
        where = location.lower()
        if not any(str(p).lower() in where for p in places):
            return "location not in your list"
    return None


# ---- adjustments learned from outcomes --------------------------------------------------------------------------------

OUTCOME_WEIGHT = {"rejected": -5.0, "withdrawn": -3.0, "skipped": -4.0, "interview": 5.0, "offer": 5.0}
COMPANY_CAP = 15.0
WHITELIST_BOOST = 10.0
FILTERED = -100.0


def company_adjust(outcomes: list[str]) -> float:
    """The sum of earlier outcomes at the same company, capped at +/-15."""
    total = sum(OUTCOME_WEIGHT.get(o, 0.0) for o in outcomes)
    return max(-COMPANY_CAP, min(COMPANY_CAP, total))


def clamp(score: float) -> float:
    return max(0.0, min(100.0, score))


# ---- rejection reasons ------------------------------------------------------------------------------------------------

REJECTION_CATEGORIES = (
    ("seniority", r"\b(senior|junior|seniority|level|too experienced|overqualified|under-?qualified)\b"),
    ("experience", r"\b(experience|years|background)\b"),
    ("skills", r"\b(skills?|technical|stack|qualifications?)\b"),
    ("location", r"\b(location|relocat\w*|on-?site|based in|time ?zone)\b"),
    ("visa", r"\b(visa|sponsor\w*|work authori[sz]ation|right to work)\b"),
    ("salary", r"\b(salary|compensation|budget|pay)\b"),
    ("filled", r"\b(filled|closed|no longer|on hold|internal candidate)\b"),
)


def rejection_category(reason: str) -> str | None:
    text = reason or ""
    for name, pattern in REJECTION_CATEGORIES:
        if re.search(pattern, text, re.I):
            return name
    return "other" if text.strip() else None


# ---- reweighting --------------------------------------------------------------------------------------------------------

def reweight(store, seeker_id: int, profile) -> dict:
    """Recompute every scored job's adjustment for this seeker: filters, the company's earlier outcomes, the user's lists
    and per-board weights. The model's own score is never changed; only fit_score = model score + adjustment moves."""
    outcomes: dict[str, list[tuple[int, str]]] = {}
    for posting_id, company, outcome in store.company_outcomes(seeker_id):
        outcomes.setdefault(norm_company(company), []).append((posting_id, outcome))
    weights = getattr(profile, "source_weights", {}) or {}
    whitelist = getattr(profile, "whitelist_companies", []) or []
    changes, filtered, moved = [], 0, 0
    for row in store.reweight_rows(seeker_id):
        facts = Facts(row["salary_min"], row["salary_max"], row["salary_currency"], row["work_mode"], row["posted_on"])
        reason = filter_reason(profile, row["company_name"], row["title"], row["location"], row["description"], facts)
        notes: list[str] = []
        if reason:
            adjust, notes = FILTERED, [f"filtered: {reason}"]
            filtered += 1
        else:
            adjust = 0.0
            earlier = [o for pid, o in outcomes.get(norm_company(row["company_name"]), []) if pid != row["posting_id"]]
            company = company_adjust(earlier)
            if company:
                adjust += company
                notes.append(f"company history {company:+.0f}")
            boards = [weights[s] for s in (row["sources"] or "").split(",") if s in weights]
            if boards:
                board = max(boards)
                adjust += board
                notes.append(f"board weight {board:+.0f}")
            if whitelist and _listed(row["company_name"], whitelist):
                adjust += WHITELIST_BOOST
                notes.append(f"whitelisted {WHITELIST_BOOST:+.0f}")
        if adjust != (row["adjust"] or 0.0):
            moved += 1
        changes.append((row["posting_id"], adjust, "; ".join(notes)))
    store.set_adjustments(seeker_id, changes)
    return {"seeker_id": seeker_id, "scored_jobs": len(changes), "filtered": filtered, "changed": moved}
