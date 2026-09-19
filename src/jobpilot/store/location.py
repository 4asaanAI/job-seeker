# src/jobpilot/store/location.py
"""Parse a free-text job-board location string into (city, region, country).

Boards hand back wildly different shapes for the same real place: Naukri
spells "Gurgaon, Haryana, India" in full; Indeed India abbreviates the same
job down to "HR, IN" with no city at all; Indeed US gives "Austin, TX, US".
Treating a state code as a city (the bug this module fixes) makes the same
real job look like a different one on every board.

Everything here is a pure function: no database access, no knowledge of
`Posting` or `Store`.
"""
from __future__ import annotations

import re

# A location with no recognisable geography at all — not a place, not
# missing information. "Remote, US" must not become a city named "remote".
_DROP_WORDS = {"remote"}

_CITY_ALIASES = {
    "gurugram": "gurgaon",
    "bengaluru": "bangalore",
    "delhi ncr": "delhi",
    "ncr": "delhi",
    "bombay": "mumbai",
}

# ISO-ish aliases seen in JobSpy output, cleaned of periods before lookup
# ("U.S." -> "us"). Keyed and valued in lower case.
_COUNTRY_ALIASES = {
    "india": "india",
    "in": "india",
    "united states": "united states",
    "usa": "united states",
    "us": "united states",
    "america": "united states",
}

# Standard USPS two-letter codes plus full names, all lower case.
_US_STATES = {
    "alabama": "alabama", "al": "alabama",
    "alaska": "alaska", "ak": "alaska",
    "arizona": "arizona", "az": "arizona",
    "arkansas": "arkansas", "ar": "arkansas",
    "california": "california", "ca": "california",
    "colorado": "colorado", "co": "colorado",
    "connecticut": "connecticut", "ct": "connecticut",
    "delaware": "delaware", "de": "delaware",
    "florida": "florida", "fl": "florida",
    "georgia": "georgia", "ga": "georgia",
    "hawaii": "hawaii", "hi": "hawaii",
    "idaho": "idaho", "id": "idaho",
    "illinois": "illinois", "il": "illinois",
    "indiana": "indiana", "in": "indiana",
    "iowa": "iowa", "ia": "iowa",
    "kansas": "kansas", "ks": "kansas",
    "kentucky": "kentucky", "ky": "kentucky",
    "louisiana": "louisiana", "la": "louisiana",
    "maine": "maine", "me": "maine",
    "maryland": "maryland", "md": "maryland",
    "massachusetts": "massachusetts", "ma": "massachusetts",
    "michigan": "michigan", "mi": "michigan",
    "minnesota": "minnesota", "mn": "minnesota",
    "mississippi": "mississippi", "ms": "mississippi",
    "missouri": "missouri", "mo": "missouri",
    "montana": "montana", "mt": "montana",
    "nebraska": "nebraska", "ne": "nebraska",
    "nevada": "nevada", "nv": "nevada",
    "new hampshire": "new hampshire", "nh": "new hampshire",
    "new jersey": "new jersey", "nj": "new jersey",
    "new mexico": "new mexico", "nm": "new mexico",
    "new york": "new york", "ny": "new york",
    "north carolina": "north carolina", "nc": "north carolina",
    "north dakota": "north dakota", "nd": "north dakota",
    "ohio": "ohio", "oh": "ohio",
    "oklahoma": "oklahoma", "ok": "oklahoma",
    "oregon": "oregon", "or": "oregon",
    "pennsylvania": "pennsylvania", "pa": "pennsylvania",
    "rhode island": "rhode island", "ri": "rhode island",
    "south carolina": "south carolina", "sc": "south carolina",
    "south dakota": "south dakota", "sd": "south dakota",
    "tennessee": "tennessee", "tn": "tennessee",
    "texas": "texas", "tx": "texas",
    "utah": "utah", "ut": "utah",
    "vermont": "vermont", "vt": "vermont",
    "virginia": "virginia", "va": "virginia",
    "washington": "washington", "wa": "washington",
    "west virginia": "west virginia", "wv": "west virginia",
    "wisconsin": "wisconsin", "wi": "wisconsin",
    "wyoming": "wyoming", "wy": "wyoming",
    "district of columbia": "district of columbia", "dc": "district of columbia",
}

# Indian states and union territories. Abbreviations follow the common
# vehicle-registration-style codes; only "hr" and "up" are confirmed against
# a real Indeed India capture (2026-09-19). The rest are best effort and may
# not exactly match a board's own convention — an unmatched code falls
# through as a city, the same failure this table exists to reduce.
_INDIAN_STATES = {
    "andhra pradesh": "andhra pradesh", "ap": "andhra pradesh",
    "arunachal pradesh": "arunachal pradesh", "ar": "arunachal pradesh",
    "assam": "assam", "as": "assam",
    "bihar": "bihar", "br": "bihar",
    "chhattisgarh": "chhattisgarh", "cg": "chhattisgarh", "ct": "chhattisgarh",
    "goa": "goa", "ga": "goa",
    "gujarat": "gujarat", "gj": "gujarat",
    "haryana": "haryana", "hr": "haryana",
    "himachal pradesh": "himachal pradesh", "hp": "himachal pradesh",
    "jharkhand": "jharkhand", "jh": "jharkhand",
    "karnataka": "karnataka", "ka": "karnataka",
    "kerala": "kerala", "kl": "kerala",
    "madhya pradesh": "madhya pradesh", "mp": "madhya pradesh",
    "maharashtra": "maharashtra", "mh": "maharashtra",
    "manipur": "manipur", "mn": "manipur",
    "meghalaya": "meghalaya", "ml": "meghalaya",
    "mizoram": "mizoram", "mz": "mizoram",
    "nagaland": "nagaland", "nl": "nagaland",
    "odisha": "odisha", "od": "odisha", "or": "odisha",
    "punjab": "punjab", "pb": "punjab",
    "rajasthan": "rajasthan", "rj": "rajasthan",
    "sikkim": "sikkim", "sk": "sikkim",
    "tamil nadu": "tamil nadu", "tn": "tamil nadu",
    "telangana": "telangana", "tg": "telangana", "ts": "telangana",
    "tripura": "tripura", "tr": "tripura",
    "uttar pradesh": "uttar pradesh", "up": "uttar pradesh",
    "uttarakhand": "uttarakhand", "uk": "uttarakhand",
    "west bengal": "west bengal", "wb": "west bengal",
    # union territories (Delhi is deliberately excluded — it is treated as a
    # city alias above, since for job search "Delhi" and "NCT of Delhi" are
    # the same place and splitting them into city vs. region adds nothing).
    "andaman and nicobar islands": "andaman and nicobar islands", "an": "andaman and nicobar islands",
    "chandigarh": "chandigarh", "ch": "chandigarh",
    "jammu and kashmir": "jammu and kashmir", "jk": "jammu and kashmir",
    "ladakh": "ladakh", "la": "ladakh",
    "puducherry": "puducherry", "py": "puducherry",
}

_REGIONS_BY_COUNTRY = {
    "united states": _US_STATES,
    "india": _INDIAN_STATES,
}


def _clean(part: str) -> str:
    return part.strip().lower().replace(".", "")


def parse_location(location: str | None) -> tuple[str, str, str]:
    """Split a raw location string into (city, region, country).

    Each element is a normalised token, or "" when the string does not say.
    Only `city` is used in a job's identity hash; `region` and `country` are
    disambiguation aids only. See src/jobpilot/store/location.py's module
    docstring and the R1 handoff for why this exists.
    """
    if not location:
        return "", "", ""
    parts = [_clean(p) for p in location.split(",") if p.strip()]
    if not parts:
        return "", "", ""

    country = ""
    if parts[-1] in _COUNTRY_ALIASES:
        country = _COUNTRY_ALIASES[parts[-1]]
        parts = parts[:-1]

    region = ""
    if parts:
        candidates = [_REGIONS_BY_COUNTRY[country]] if country in _REGIONS_BY_COUNTRY \
            else list(_REGIONS_BY_COUNTRY.values())
        for table in candidates:
            if parts[-1] in table:
                region = table[parts[-1]]
                parts = parts[:-1]
                break

    city = ""
    kept = [p for p in parts if p not in _DROP_WORDS]
    if kept:
        cleaned = re.sub(r"[^a-z0-9]+", " ", " ".join(kept)).strip()
        city = _CITY_ALIASES.get(cleaned, cleaned)

    return city, region, country
