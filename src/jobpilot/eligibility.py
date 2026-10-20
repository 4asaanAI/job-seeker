"""Per-job answers that depend on the job's country: work authorisation, visa sponsorship and relocation.

One fixed answer is wrong for most jobs ("authorised to work in the country of the job?" is Yes in India and No in the US), so
the user records WHERE they can work and where they would relocate, once, and JobPilot works the answer out for each job from
the job's location. This is plain lookup and set arithmetic, not a model's judgement: the user's facts applied to a place.
When a job's country cannot be read (a bare "Remote"), nothing is guessed: the request tells Fo to stop and ask the user.
"""
from __future__ import annotations

import re

from . import vault

# canonical country -> every way a job board or the user writes it (lower case, periods removed)
_COUNTRIES: dict[str, tuple[str, ...]] = {
    "india": ("india", "bharat"),
    "united states": ("united states", "united states of america", "usa", "u s a", "us", "u s", "america"),
    "united kingdom": ("united kingdom", "uk", "u k", "great britain", "gb", "england", "scotland", "wales", "northern ireland"),
    "canada": ("canada",),
    "australia": ("australia",),
    "new zealand": ("new zealand",),
    "ireland": ("ireland",),
    "germany": ("germany", "deutschland"),
    "france": ("france",),
    "netherlands": ("netherlands", "the netherlands", "holland"),
    "spain": ("spain",),
    "italy": ("italy",),
    "portugal": ("portugal",),
    "belgium": ("belgium",),
    "austria": ("austria",),
    "switzerland": ("switzerland",),
    "sweden": ("sweden",),
    "norway": ("norway",),
    "denmark": ("denmark",),
    "finland": ("finland",),
    "poland": ("poland",),
    "singapore": ("singapore",),
    "united arab emirates": ("united arab emirates", "uae", "u a e"),
    "saudi arabia": ("saudi arabia", "ksa"),
    "qatar": ("qatar",),
    "japan": ("japan",),
    "china": ("china",),
    "hong kong": ("hong kong",),
    "south korea": ("south korea", "korea"),
    "malaysia": ("malaysia",),
    "philippines": ("philippines",),
    "vietnam": ("vietnam",),
    "thailand": ("thailand",),
    "indonesia": ("indonesia",),
    "pakistan": ("pakistan",),
    "bangladesh": ("bangladesh",),
    "sri lanka": ("sri lanka",),
    "nepal": ("nepal",),
    "brazil": ("brazil",),
    "mexico": ("mexico",),
    "argentina": ("argentina",),
    "south africa": ("south africa",),
    "nigeria": ("nigeria",),
    "kenya": ("kenya",),
    "egypt": ("egypt",),
    "turkey": ("turkey", "turkiye"),
    "israel": ("israel",),
}
# two- or three-letter codes accepted only when written in capitals; ones that are also US state codes (CA, DE, ID, IL, IN
# after a city in the US...) are left out, except IN which boards use for India (see countries_of).
_CODES = {"IN": "india", "US": "united states", "USA": "united states", "UK": "united kingdom", "GB": "united kingdom",
          "AU": "australia", "SG": "singapore", "AE": "united arab emirates", "NL": "netherlands", "IE": "ireland",
          "FR": "france", "ES": "spain", "IT": "italy", "JP": "japan", "CN": "china", "BR": "brazil", "MX": "mexico",
          "NZ": "new zealand", "ZA": "south africa", "CH": "switzerland", "SE": "sweden", "PL": "poland", "PT": "portugal",
          "BE": "belgium", "AT": "austria", "DK": "denmark", "FI": "finland", "PK": "pakistan", "BD": "bangladesh",
          "LK": "sri lanka", "NP": "nepal", "PH": "philippines", "MY": "malaysia", "VN": "vietnam", "TH": "thailand",
          "KR": "south korea", "HK": "hong kong", "SA": "saudi arabia", "QA": "qatar", "EG": "egypt", "NG": "nigeria",
          "KE": "kenya", "TR": "turkey"}
_CITIES: dict[str, str] = {}
for _country, _cities in {
    "india": "mumbai delhi new delhi gurgaon gurugram noida ghaziabad faridabad bangalore bengaluru hyderabad chennai kolkata pune "
             "ahmedabad jaipur lucknow chandigarh kochi cochin indore bhopal surat nagpur coimbatore thiruvananthapuram "
             "visakhapatnam vadodara mysore mysuru mohali ncr haryana karnataka maharashtra tamil nadu telangana kerala gujarat "
             "rajasthan uttar pradesh punjab west bengal odisha goa",
    "united kingdom": "london manchester birmingham edinburgh glasgow bristol leeds cambridge oxford liverpool belfast cardiff",
    "united states": "new york san francisco los angeles seattle austin boston chicago denver atlanta dallas houston miami "
                     "washington san jose palo alto mountain view san diego portland philadelphia phoenix",
    "canada": "toronto vancouver montreal ottawa calgary waterloo",
    "germany": "berlin munich hamburg frankfurt cologne stuttgart",
    "france": "paris lyon marseille", "netherlands": "amsterdam rotterdam eindhoven", "spain": "madrid barcelona",
    "ireland": "dublin cork", "australia": "sydney melbourne brisbane perth", "singapore": "singapore",
    "united arab emirates": "dubai abu dhabi sharjah", "switzerland": "zurich geneva", "japan": "tokyo osaka",
    "sweden": "stockholm", "italy": "milan rome", "portugal": "lisbon", "poland": "warsaw krakow",
}.items():
    # multi-word entries are separated by two spaces below where needed; single spaces split on known two-word cities
    for _c in re.findall(r"new delhi|san francisco|los angeles|new york|san jose|palo alto|mountain view|san diego|abu dhabi|"
                         r"tamil nadu|uttar pradesh|west bengal|[a-z]+", _cities):
        _CITIES[_c] = _country

_ALIASES = {alias: canon for canon, names in _COUNTRIES.items() for alias in names}
_SPLIT = re.compile(r"[,;/|()\[\]\-–—•]| or | and |&")


def normalize_country(text: object) -> str | None:
    """The canonical country for what the user typed ("USA", "United States", "U.K.", "IN"), or None when unknown."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw in _CODES:
        return _CODES[raw]
    key = re.sub(r"\s+", " ", raw.lower().replace(".", " ")).strip()
    return _ALIASES.get(key)


def normalize_list(items: object) -> list[str]:
    """User-entered countries as canonical names (unknown ones are kept as typed, lower case, so nothing is dropped)."""
    out = []
    for item in items or []:
        text = " ".join(str(item).split())[:60]
        if text:
            canon = normalize_country(text) or text.lower()
            if canon not in out:
                out.append(canon)
    return out


def countries_of(location: object) -> set[str]:
    """Every country a job's location text names: 'Austin, TX, US' -> {united states}; 'Gurugram, HR, IN' -> {india};
    'Remote (India, Bangladesh)' -> both; a bare 'Remote' or an unreadable place -> an empty set."""
    found: set[str] = set()
    for token in _SPLIT.split(str(location or "")):
        token = " ".join(token.split())
        if not token:
            continue
        canon = _CODES.get(token) or _ALIASES.get(re.sub(r"\s+", " ", token.lower().replace(".", " ")).strip())
        if canon is None:
            low = token.lower()
            canon = _CITIES.get(low) or next((c for w, c in _CITIES.items() if len(w) > 4 and f" {w} " in f" {low} "), None)
        if canon:
            found.add(canon)
    return found


def _q(key: str) -> str:
    return vault.STANDARD_QUESTIONS[key]


def derive(authorised: list[str], relocation: list[str], location: object) -> dict:
    """What can be said for a job at `location`. Returns {answers: {question: answer}, unsure: [question, ...], basis: str}.
    Empty answers and no unsure when the user has not recorded where they can work."""
    authorised = normalize_list(authorised)
    relocation = normalize_list(relocation)
    if not authorised:
        return {"answers": {}, "unsure": [], "basis": "no work-eligibility countries recorded"}
    countries = countries_of(location)
    if not countries:
        return {"answers": {}, "unsure": [_q("work_authorization"), _q("sponsorship")],
                "basis": "the job's country is not clear from its location"}
    where = ", ".join(sorted(countries))
    ok = bool(countries & set(authorised))
    answers = {_q("work_authorization"): "Yes" if ok else "No", _q("sponsorship"): "No" if ok else "Yes"}
    basis = f"job in {where}; you are {'authorised there' if ok else 'not recorded as authorised there'}"
    if countries & set(relocation):
        answers[_q("relocation")] = "Yes"
        basis += "; you would relocate there"
    return {"answers": answers, "unsure": [], "basis": basis}


def for_posting(profile, posting: dict | None) -> dict:
    """derive() for a stored posting (its location text)."""
    return derive(getattr(profile, "work_authorized_countries", []) or [], getattr(profile, "relocation_countries", []) or [],
                  (posting or {}).get("location"))
