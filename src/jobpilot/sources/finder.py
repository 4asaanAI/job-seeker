"""Find an employer's public job feed from its name alone.

Two passes, cheapest first:

1. Guess: probe the keyless ATS APIs (Greenhouse, Lever, Ashby,
   SmartRecruiters) at identifiers derived from the name.
2. Search: web-search each ATS's own domain for the employer (Workday,
   SmartRecruiters, Oracle, Greenhouse, Lever, Ashby) and turn every result
   URL into a candidate feed. This is what finds a Workday tenant such as
   ``nike.wd1.myworkdayjobs.com/nke``, which no name-derived guess reaches.

A candidate is accepted only when it is identifiably that employer (the ATS
reports a matching company name, or its tenant/slug starts with the name) and
it returns at least one job through the same parser discovery uses. Anything
else is "not found": a wrong employer's jobs are worse than none.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

from ..config import ATSConfig
from .ats_json import ATSJsonSource, _default_fetcher, _default_poster

SUFFIXES = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
            "pvt", "plc", "gmbh", "hq", "the", "technologies"}
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
}
SEARCH_DOMAINS = ("myworkdayjobs.com", "smartrecruiters.com", "oraclecloud.com",
                  "greenhouse.io", "lever.co", "ashbyhq.com")
MAX_SEARCHED_CANDIDATES = 10
LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")

Search = Callable[[str, str], list[dict]]

# Wigolo uses any LLM provider key it finds in its environment (there is no
# "off" setting). JobPilot never wants Wigolo's own synthesis -- Claude does
# the reasoning -- so it runs with every provider key removed.
LLM_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "GROQ_API_KEY", "WIGOLO_LLM_API_KEY", "WIGOLO_LLM_PROVIDER")


def wigolo_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in LLM_KEYS}


def _words(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", name.casefold().replace("&", " and "))
    return [w for w in words if w not in SUFFIXES]


def normalise(name: str) -> str:
    return "".join(_words(name))


def slugs(name: str) -> list[str]:
    words = _words(name)
    if not words:
        return []
    return list(dict.fromkeys(["".join(words), "-".join(words)]))


def _related(key: str, wanted: str) -> bool:
    """A tenant/slug belongs to the employer: one starts with the other."""
    key = normalise(key)
    return len(key) >= 3 and (key.startswith(wanted) or wanted.startswith(key))


def wigolo_search(query: str, domain: str) -> list[dict]:
    """Web search restricted to one domain; [] when Wigolo is unavailable."""
    exe = shutil.which("wigolo")
    if not exe:
        return []
    try:
        done = subprocess.run(
            [exe, "search", query, f"--include-domains={domain}", "--no-content",
             "--max-results=8", "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=90, check=False, env=wigolo_env())
        return [r for r in json.loads(done.stdout).get("results", []) if r.get("url")]
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return []


def searxng_search(query: str, domain: str) -> list[dict] | None:
    """Query the local SearXNG directly; [] when it is not running.

    Wigolo's one-shot CLI search never consults SearXNG (only its MCP server
    and shell do), so JobPilot asks SearXNG itself. It aggregates several
    engines, which is what survives one engine throttling. Returns None when
    SearXNG is unreachable, so only that (not an honest empty) falls back.
    """
    base = os.environ.get("JOBPILOT_SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
    url = f"{base}/search?" + urlencode({"q": f"{query} site:{domain}", "format": "json"})
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310 - local operator service
            rows = json.load(response).get("results", [])
    except (OSError, ValueError, AttributeError):
        return None
    return [r for r in rows if domain in str(r.get("url") or "")]


def web_search(query: str, domain: str) -> list[dict]:
    rows = searxng_search(query, domain)
    return wigolo_search(query, domain) if rows is None else rows


def candidates_from_url(url: str) -> list[dict]:
    """Turn a public ATS page URL into the feed that lists its jobs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    parts = [p for p in parsed.path.split("/") if p]
    if parts and LOCALE.match(parts[0]):
        parts = parts[1:]
    first = parts[0] if parts else ""
    if host.endswith(".myworkdayjobs.com") and first and first != "wday":
        tenant = host.split(".")[0]
        if "internal" in first.casefold():
            return []
        return [{"vendor": "workday_cxs", "key": tenant, "slug": f"{tenant}_{first}",
                 "endpoint": f"https://{host}/wday/cxs/{tenant}/{first}/jobs"}]
    if host in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"} and first:
        return [{"vendor": "smartrecruiters", "key": first, "slug": first,
                 "endpoint": ENDPOINTS["smartrecruiters"].format(slug=first)}]
    site = re.search(r"/sites/([^/?#]+)", parsed.path)
    if host.endswith(".oraclecloud.com") and site:
        return [{"vendor": "oracle_hcm", "key": "", "slug": f"{host.split('.')[0]}_{site[1]}",
                 "endpoint": f"https://{host}/hcmRestApi/resources/latest/"
                             f"recruitingCEJobRequisitions?onlyData=true&siteNumber={site[1]}"}]
    vendor = {"boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
              "jobs.lever.co": "lever", "jobs.ashbyhq.com": "ashby"}.get(host)
    if vendor and first:
        return [{"vendor": vendor, "key": first, "slug": first,
                 "endpoint": ENDPOINTS[vendor].format(slug=first)}]
    return []


def _smartrecruiters_ids(company: str) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", company) if w.casefold() not in SUFFIXES]
    title = "".join(w[:1].upper() + w[1:] for w in words)
    return list(dict.fromkeys([title, title + "Group"])) if title else []


NARROW_SITE = re.compile(r"early|intern|campus|universit|graduate|student|alumni|"
                         r"internal|contingent|agency|veteran", re.I)


def _rank(cand: dict, wanted: str) -> tuple[int, int, int]:
    """Main public careers site of the employer's own tenant first: a
    country tenant (deloitteie) or an early-careers site is a small slice."""
    site = cand["slug"][len(cand["key"]) + 1:] if cand["vendor"] == "workday_cxs" else ""
    return (0 if normalise(cand["key"]) == wanted else 1,
            1 if NARROW_SITE.search(site) else 0,
            0 if re.search(r"extern|career|job", site, re.I) or normalise(site) == wanted else 1)


def find_feed(
    company: str,
    fetch: Callable[[str, float], object] = _default_fetcher,
    post: Callable[[str, dict, float], object] = _default_poster,
    search: Search = web_search,
    timeout: float = 15.0,
) -> dict | None:
    """Return {vendor, slug, endpoint, company_name, jobs} or None."""
    wanted = normalise(company)
    if len(wanted) < 2:
        return None
    name = " ".join(company.split())

    def verify(cand: dict) -> dict | None:
        vendor, key = cand["vendor"], cand["key"]
        try:
            if vendor == "greenhouse":
                board = fetch(f"https://boards-api.greenhouse.io/v1/boards/{key}", timeout)
                if normalise(str(board.get("name") or "")) != wanted:
                    return None
            elif vendor == "smartrecruiters":
                page = fetch(f"{cand['endpoint']}?limit=1", timeout)
                rows = page.get("content") or []
                owner = str(((rows[0] if rows else {}).get("company") or {}).get("name") or "")
                if not _related(owner, wanted):
                    return None
            elif vendor == "oracle_hcm":
                if wanted not in normalise(cand.get("context", "")):
                    return None
            elif cand.get("guessed"):
                if key.replace("-", "") != wanted:
                    return None
            elif not _related(key, wanted):
                return None
            source = ATSJsonSource(
                ATSConfig(name="probe", vendor=vendor, company_name=name,
                          endpoint=cand["endpoint"], enabled=True,
                          fetch_timeout_seconds=timeout),
                fetcher=fetch, poster=post)
            ok, _rows, jobs = source.canary()
        except Exception:  # 404, timeout, bad JSON: this candidate is simply wrong
            return None
        if not ok or jobs < 1:
            return None
        return {"vendor": vendor, "slug": cand["slug"], "endpoint": cand["endpoint"],
                "company_name": name, "jobs": jobs}

    def first_verified(cands: list[dict]) -> dict | None:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for hit in pool.map(verify, cands):
                if hit:
                    return hit
        return None

    guessed = [{"vendor": v, "key": s, "slug": s, "guessed": True,
                "endpoint": ENDPOINTS[v].format(slug=s)}
               for v in ("greenhouse", "lever", "ashby") for s in slugs(company)]
    guessed += [{"vendor": "smartrecruiters", "key": i, "slug": i,
                 "endpoint": ENDPOINTS["smartrecruiters"].format(slug=i)}
                for i in _smartrecruiters_ids(company)]
    hit = first_verified(guessed)
    if hit:
        return hit

    with ThreadPoolExecutor(max_workers=3) as pool:
        # Engines are phrasing-sensitive ("Nike careers" finds nothing on
        # myworkdayjobs.com, "Nike careers jobs" finds the tenant).
        results = pool.map(lambda d: search(f"{name} careers jobs", d)
                           or search(f"{name} jobs", d), SEARCH_DOMAINS)
        found: dict[str, dict] = {}
        for rows in results:
            for row in rows:
                context = f"{row.get('title', '')} {row.get('snippet', '')}"
                for cand in candidates_from_url(str(row["url"])):
                    found.setdefault(cand["endpoint"], {**cand, "context": context})
    ordered = sorted(found.values(), key=lambda c: _rank(c, wanted))
    return first_verified(ordered[:MAX_SEARCHED_CANDIDATES])
