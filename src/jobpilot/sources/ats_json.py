"""Official ATS JSON sources used by the R4 discovery layer.

The endpoint is operator configuration, never model input. Each source fetches
its endpoint once per source instance, normalizes vendor-specific records into
the existing Posting contract, and filters locally for each seeker query.
"""
from __future__ import annotations

import html
import json
import re
import time
from urllib.parse import parse_qs, quote, urlparse
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import ATSConfig
from ..store.models import Posting
from .base import MAX_DESCRIPTION_CHARS, safe_url

# Per-host pacing: many employer boards share one API host (boards-api.greenhouse.io, api.lever.co), so requests to
# the same host are spaced at least this far apart across every source in the process.
HOST_GAP_SECONDS = 1.0
_last_request: dict[str, float] = {}


def _pace(url: str, sleep=time.sleep, clock=time.monotonic) -> None:
    host = (urlparse(url).hostname or "").lower()
    wait = HOST_GAP_SECONDS - (clock() - _last_request.get(host, float("-inf")))
    if wait > 0:
        sleep(wait)
    _last_request[host] = clock()


def _retry_once(call):
    """Employer APIs (Workday especially) stall now and then; one retry clears it."""
    def wrapped(*args):
        try:
            return call(*args)
        except HTTPError:
            raise
        except (TimeoutError, URLError):
            return call(*args)
    return wrapped


@_retry_once
def _default_fetcher(url: str, timeout: float) -> object:
    _pace(url)
    request = Request(url, headers={"User-Agent": "JobPilot/0.3"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - config URL
        if getattr(response, "status", 200) >= 400:
            raise RuntimeError(f"ATS endpoint returned HTTP {response.status}")
        return json.load(response)


@_retry_once
def _default_poster(url: str, body: dict, timeout: float) -> object:
    _pace(url)
    request = Request(url, data=json.dumps(body).encode(), headers={
        "User-Agent": "JobPilot/0.4", "Content-Type": "application/json",
        "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - config URL
        return json.load(response)


SEARCHED = frozenset({"workday_cxs", "oracle_hcm", "smartrecruiters", "foundit",
                      "himalayas", "jobicy"})
BOARD_CANARY_TERM = {"foundit": "manager", "himalayas": "manager", "jobicy": "manager"}
# Foundit is a whole job board: searched by term and city, paced, few pages.
FOUNDIT_PAGES = 3
FOUNDIT_GAP_SECONDS = 2.0
MAX_PAGES = 5


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(_text(item) for item in value)
    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    # Greenhouse escapes its HTML twice: entities survive the first pass.
    return " ".join(html.unescape(value).split())


def _nested(item: dict, *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return ""


def _items(payload: object, vendor: str) -> list[dict]:
    if vendor == "greenhouse":
        value = payload.get("jobs", []) if isinstance(payload, dict) else []
    elif vendor == "bamboohr":
        value = payload.get("result", []) if isinstance(payload, dict) else []
    elif vendor == "breezy":
        value = payload if isinstance(payload, list) else []
    elif vendor == "remoteok":
        value = payload if isinstance(payload, list) else []
    elif vendor == "remotive":
        value = payload.get("jobs", []) if isinstance(payload, dict) else []
    elif vendor == "lever":
        value = payload if isinstance(payload, list) else []
    elif vendor == "ashby":
        value = payload.get("jobs", []) if isinstance(payload, dict) else []
        value = [job for job in value if isinstance(job, dict) and job.get("isListed", True)]
    elif vendor == "workday":
        if isinstance(payload, dict):
            data = payload.get("data")
            nested = data.get("jobPostings", []) if isinstance(data, dict) else []
            value = payload.get("jobPostings", nested)
        else:
            value = []
    else:  # SuccessFactors commonly wraps OData records under d.results.
        if isinstance(payload, dict):
            data = payload.get("d")
            nested = data.get("results", []) if isinstance(data, dict) else []
            value = payload.get("results", nested)
        else:
            value = []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _to_posting(item: dict, cfg: ATSConfig) -> Posting | None:
    vendor = cfg.vendor
    row_company = ""
    if vendor == "workday_cxs":
        parts = urlparse(cfg.endpoint).path.strip("/").split("/")
        site = parts[3] if len(parts) > 3 else ""
        origin = "{0.scheme}://{0.netloc}".format(urlparse(cfg.endpoint))
        external_id = _nested(item, "externalPath")
        title = _nested(item, "title")
        location = _nested(item, "locationsText")
        url = f"{origin}/{site}{external_id}" if external_id else ""
        description = ""
        posted_at = ""
    elif vendor == "oracle_hcm":
        query = parse_qs(urlparse(cfg.endpoint).query)
        site = (query.get("siteNumber") or ["CX_1"])[0]
        origin = "{0.scheme}://{0.netloc}".format(urlparse(cfg.endpoint))
        external_id = _nested(item, "Id")
        title = _nested(item, "Title")
        location = _nested(item, "PrimaryLocation")
        url = f"{origin}/hcmUI/CandidateExperience/en/sites/{site}/job/{external_id}"
        description = _nested(item, "ShortDescriptionStr")
        posted_at = _nested(item, "PostedDate")
    elif vendor == "foundit":
        external_id = _nested(item, "jobId", "id")
        title = _nested(item, "title")
        location = ", ".join(dict.fromkeys(
            loc.get("city") for loc in item.get("locations") or [] if isinstance(loc, dict) and loc.get("city")))
        path = _text(_nested(item, "jdUrl"))
        url = f"https://www.foundit.in{path}" if path.startswith("/") else ""
        description = _nested(item, "description")
        posted = item.get("postedAt")
        posted_at = (time.strftime("%Y-%m-%d", time.gmtime(posted / 1000))
                     if isinstance(posted, (int, float)) else "")
        row_company = "" if item.get("hideCompanyName") else _nested(item, "companyName")
    elif vendor == "himalayas":
        external_id = _nested(item, "guid")
        title = _nested(item, "title")
        places = item.get("locationRestrictions") or []
        location = "Remote" + (f" ({', '.join(map(str, places))})" if places else "")
        url = _nested(item, "applicationLink", "guid")
        description = _nested(item, "description", "excerpt")
        posted = item.get("pubDate")
        posted_at = (time.strftime("%Y-%m-%d", time.gmtime(posted))
                     if isinstance(posted, (int, float)) else "")
        row_company = _nested(item, "companyName")
    elif vendor == "jobicy":
        external_id = _nested(item, "id")
        title = _nested(item, "jobTitle")
        geo = _text(_nested(item, "jobGeo"))
        location = f"Remote ({geo})" if geo else "Remote"
        url = _nested(item, "url")
        description = _nested(item, "jobDescription", "jobExcerpt")
        posted_at = _nested(item, "pubDate")
        row_company = _nested(item, "companyName")
    elif vendor == "bamboohr":
        # BambooHR's list endpoint carries no job URL; the public page is a
        # fixed <tenant>/careers/<id> path (verified against a live tenant).
        origin = "{0.scheme}://{0.netloc}".format(urlparse(cfg.endpoint))
        external_id = _nested(item, "id")
        title = _nested(item, "jobOpeningName")
        ats_loc = item.get("atsLocation") if isinstance(item.get("atsLocation"), dict) else {}
        location = ", ".join(v for v in (ats_loc.get("city"), ats_loc.get("state") or ats_loc.get("province"),
                                          ats_loc.get("country")) if v)
        url = f"{origin}/careers/{external_id}" if external_id else ""
        description = ""
        posted_at = ""
    elif vendor == "breezy":
        external_id = _nested(item, "id")
        title = _nested(item, "name")
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        location = _nested(loc, "name")
        url = _nested(item, "url")
        description = _nested(item, "description")
        posted_at = _nested(item, "published_date")
    elif vendor == "remoteok":
        external_id = _nested(item, "id", "slug")
        title = _nested(item, "position")
        location = _nested(item, "location") or "Remote"
        url = _nested(item, "url", "apply_url")
        description = _nested(item, "description")
        posted_at = _nested(item, "date")
        row_company = _nested(item, "company")
    elif vendor == "remotive":
        external_id = _nested(item, "id")
        title = _nested(item, "title")
        location = _nested(item, "candidate_required_location") or "Remote"
        url = _nested(item, "url")
        description = _nested(item, "description")
        posted_at = _nested(item, "publication_date")
        row_company = _nested(item, "company_name")
    elif vendor == "greenhouse":
        external_id = _nested(item, "id", "requisition_id")
        title = _nested(item, "title", "name")
        location = _nested(item.get("location", {}), "name") if isinstance(item.get("location"), dict) else _nested(item, "location")
        url = _nested(item, "absolute_url", "url")
        description = _nested(item, "content", "description")
        posted_at = _nested(item, "updated_at", "created_at")
    elif vendor == "lever":
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        external_id = _nested(item, "id", "lever_id")
        title = _nested(item, "text", "title")
        location = _nested(categories, "location")
        url = _nested(item, "hostedUrl", "applyUrl", "url")
        description = _nested(item, "descriptionPlain", "description")
        posted_at = _nested(item, "createdAt", "updatedAt")
    elif vendor == "smartrecruiters":
        place = item.get("location") if isinstance(item.get("location"), dict) else {}
        external_id = _nested(item, "id")
        title = _nested(item, "name")
        location = place.get("fullLocation") or place.get("city")
        identifier = urlparse(cfg.endpoint).path.strip("/").split("/")[2:3]
        url = (f"https://jobs.smartrecruiters.com/{identifier[0]}/{_text(external_id)}"
               if identifier and external_id else "")
        description = ""
        posted_at = _nested(item, "releasedDate")
    elif vendor == "ashby":
        external_id = _nested(item, "id")
        title = _nested(item, "title")
        location = _nested(item, "location")
        url = _nested(item, "jobUrl", "applyUrl")
        description = _nested(item, "descriptionPlain")
        posted_at = _nested(item, "publishedAt")
    elif vendor == "workday":
        external_id = _nested(item, "jobPostingId", "externalPath", "id")
        title = _nested(item, "title", "jobTitle")
        location = _nested(item, "locationsText", "location")
        url = _nested(item, "externalUrl", "url")
        description = _nested(item, "jobDescription", "description", "bulletFields")
        posted_at = _nested(item, "postedOn", "postedDate", "updatedDate")
    else:
        external_id = _nested(item, "jobPostingId", "jobReqId", "externalCode", "id")
        title = _nested(item, "jobTitle", "title", "name")
        location = _nested(item, "jobLocation", "location", "locations")
        url = _nested(item, "jobUrl", "externalUrl", "url")
        description = _nested(item, "jobDescription", "description")
        posted_at = _nested(item, "postingDate", "createdAt", "lastModifiedDate")

    external_id = _text(external_id)
    title = _text(title)
    company = _text(row_company) or _text(cfg.company_name)
    if not external_id or not title or not company:
        return None
    description = _text(description)[:MAX_DESCRIPTION_CHARS] or None
    return Posting(
        company_name=company,
        source=cfg.name,
        external_id=external_id,
        title=title,
        location=_text(location) or None,
        description=description,
        url=safe_url(_text(url)),
        posted_at=_text(posted_at) or None,
        valid_through=_closing_date(item),
    )


def _closing_date(item: dict) -> str | None:
    """A closing date when the feed states one: epoch seconds/ms or an ISO date. Anything unparseable is ignored."""
    for key in ("expiryDate", "expiresAt", "expiration_date", "validThrough", "closingDate", "applicationDeadline"):
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, (int, float)) and value > 0:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return time.strftime("%Y-%m-%d", time.gmtime(seconds))
        if isinstance(value, str) and len(value) >= 10 and value[:4].isdigit() and value[4] == "-":
            return value[:10]
    return None


class ATSJsonSource:
    """One configured employer ATS endpoint implementing the source contract."""

    def __init__(
        self,
        cfg: ATSConfig,
        fetcher: Callable[[str, float], object] | None = None,
        poster: Callable[[str, dict, float], object] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.name = cfg.name
        self._fetcher = fetcher or _default_fetcher
        self._poster = poster or _default_poster
        self._sleep = sleeper
        self._terms: dict[str, tuple[list[dict], list[Posting]]] = {}
        self._rows: list[dict] | None = None
        self._postings: list[Posting] | None = None

    def _load(self) -> tuple[list[dict], list[Posting]]:
        if self._rows is None or self._postings is None:
            payload = self._fetcher(self.cfg.endpoint, self.cfg.fetch_timeout_seconds)
            rows = _items(payload, self.cfg.vendor)
            postings = [
                posting for row in rows
                if (posting := _to_posting(row, self.cfg)) is not None
            ]
            self._rows, self._postings = rows, postings
        return self._rows, self._postings

    def _fetch_term(self, term: str, pages: int = MAX_PAGES, location: str | None = None):
        """Server-side keyword search for boards too large to download whole."""
        key = (term or "").casefold().strip()
        where = (location or "").strip() if self.cfg.vendor == "foundit" else ""
        cache = f"{key}|{where.casefold()}"
        if cache in self._terms and pages == MAX_PAGES:
            return self._terms[cache]
        if self.cfg.vendor in ("foundit", "himalayas"):
            pages = min(pages, FOUNDIT_PAGES)
        elif self.cfg.vendor == "jobicy":
            pages = 1
        rows: list[dict] = []
        timeout = self.cfg.fetch_timeout_seconds
        for page in range(pages):
            if self.cfg.vendor == "workday_cxs":
                data = self._poster(self.cfg.endpoint, {
                    "appliedFacets": {}, "limit": 20, "offset": page * 20,
                    "searchText": key}, timeout)
                batch = data.get("jobPostings", []) if isinstance(data, dict) else []
                size = 20
            elif self.cfg.vendor == "foundit":
                if page:
                    self._sleep(FOUNDIT_GAP_SECONDS)
                url = (f"{self.cfg.endpoint}?start={page * 15}&limit=15&queryDerived=true"
                       f"&query={quote(key)}" + (f"&locations={quote(where)}" if where else ""))
                data = self._fetcher(url, timeout)
                batch = data.get("data", []) if isinstance(data, dict) else []
                size = 15
            elif self.cfg.vendor in ("himalayas", "jobicy"):
                # Remote boards; the endpoint carries the fixed country filter.
                if page:
                    self._sleep(FOUNDIT_GAP_SECONDS)
                if self.cfg.vendor == "himalayas":
                    url = f"{self.cfg.endpoint}&q={quote(key)}&page={page + 1}"
                    listed, size = "jobs", 20
                else:
                    url = f"{self.cfg.endpoint}&tag={quote(key)}"
                    listed, size = "jobs", 50
                data = self._fetcher(url, timeout)
                batch = data.get(listed, []) if isinstance(data, dict) else []
            elif self.cfg.vendor == "smartrecruiters":
                clean = re.sub(r"[^\w ]", " ", key).strip()
                url = (f"{self.cfg.endpoint}?limit=100&offset={page * 100}"
                       + (f"&q={quote(clean)}" if clean else ""))
                data = self._fetcher(url, timeout)
                batch = data.get("content", []) if isinstance(data, dict) else []
                size = 100
            else:
                clean = re.sub(r"[^\w ]", " ", key).strip()
                base = self.cfg.endpoint
                site = (parse_qs(urlparse(base).query).get("siteNumber") or ["CX_1"])[0]
                url = (base.split("?")[0] + "?onlyData=true&expand=requisitionList"
                       f"&finder=findReqs;siteNumber={site},limit=50,offset={page * 50}"
                       + (f",keyword={quote(clean)}" if clean else "")
                       + ",sortBy=POSTING_DATES_DESC")
                data = self._fetcher(url, timeout)
                items = data.get("items", []) if isinstance(data, dict) else []
                batch = (items[0].get("requisitionList", []) if items else [])
                size = 50
            rows.extend(b for b in batch if isinstance(b, dict))
            if len(batch) < size:
                break
        postings = [p for row in rows if (p := _to_posting(row, self.cfg)) is not None]
        if pages == MAX_PAGES:
            self._terms[cache] = (rows, postings)
        return rows, postings

    def describe(self, posting: Posting) -> str | None:
        """Full job text for vendors whose list endpoint omits it."""
        vendor, timeout = self.cfg.vendor, self.cfg.fetch_timeout_seconds
        if vendor == "workday_cxs":
            # List: .../wday/cxs/<tenant>/<site>/jobs; detail: .../<site><externalPath>
            data = self._fetcher(self.cfg.endpoint.rsplit("/jobs", 1)[0] + posting.external_id, timeout)
            info = data.get("jobPostingInfo", {}) if isinstance(data, dict) else {}
            text = info.get("jobDescription") if isinstance(info, dict) else ""
        elif vendor == "oracle_hcm":
            base = self.cfg.endpoint.split("recruitingCEJobRequisitions")[0]
            site = (parse_qs(urlparse(self.cfg.endpoint).query).get("siteNumber") or ["CX_1"])[0]
            data = self._fetcher(
                f"{base}recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                f"&finder=ById;Id=%22{quote(posting.external_id)}%22,siteNumber={site}", timeout)
            items = data.get("items", []) if isinstance(data, dict) else []
            text = " ".join(_text(items[0].get(k)) for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
                ) if items else ""
        elif vendor == "smartrecruiters":
            data = self._fetcher(f"{self.cfg.endpoint.rstrip('/')}/{quote(posting.external_id)}", timeout)
            sections = ((data.get("jobAd") or {}).get("sections") or {}) if isinstance(data, dict) else {}
            text = " ".join(_text(v.get("text")) for v in sections.values() if isinstance(v, dict))
        else:
            return None
        return _text(text)[:MAX_DESCRIPTION_CHARS] or None

    def canary(self) -> tuple[bool, int, int]:
        """Fetch the configured endpoint and require normalized jobs."""
        term = BOARD_CANARY_TERM.get(self.cfg.vendor, "")
        rows, postings = (self._fetch_term(term, pages=1)
                          if self.cfg.vendor in SEARCHED else self._load())
        return len(postings) >= self.cfg.canary_min_results, len(rows), len(postings)

    def search(self, term: str, location: str | None) -> tuple[list[Posting], int]:
        """Filter the cached official ATS response for one seeker query."""
        rows, postings = (self._fetch_term(term, location=location)
                          if self.cfg.vendor in SEARCHED else self._load())
        wanted = (term or "").casefold().strip()
        # Every source here is one employer, or a remote-only board, that the
        # seeker subscribed to on purpose. Their jobs post in whatever office
        # format the site uses, which rarely contains the seeker's home city
        # as a substring (and never does for "Worldwide"), so location is not
        # filtered here: the scoring rubric judges location fit.
        found = []
        for posting in postings:
            haystack = " ".join((posting.title, posting.company_name,
                                  posting.description or "")).casefold()
            if (wanted and self.cfg.vendor not in SEARCHED
                    and wanted not in haystack):
                continue
            found.append(posting)
        # A whole-board download returns every job whatever the query, so its
        # row count says nothing about this search; canary() already proved
        # the field mapping. Reporting it here would turn "this employer has
        # no such job" into a false "mapping broken" error.
        raw = len(rows) if self.cfg.vendor in SEARCHED else len(found)
        return found, raw
