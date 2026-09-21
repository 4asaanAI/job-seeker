# src/jobpilot/pipeline.py
from __future__ import annotations

import time
from dataclasses import dataclass

from .profiles import Profile
from .sources.base import SourceResult, SourceStatus
from .sources.health import classify
from .store.db import Store
from .store.models import Posting


class DiscoverySource:
    """Structural source contract shared by JobSpy and official ATS adapters."""

    name: str

    def canary(self) -> tuple[bool, int, int]:
        raise NotImplementedError

    def search(self, term: str, location: str | None) -> tuple[list[Posting], int]:
        raise NotImplementedError


@dataclass(frozen=True)
class SeekerTarget:
    seeker_id: int
    profile: Profile


def _excluded(posting: Posting, profile: Profile) -> bool:
    haystack = f"{posting.title} {posting.company_name}".lower()
    return any(kw.lower() in haystack for kw in profile.exclude_keywords)


def _describe(exc: BaseException) -> str:
    # str(exc) is empty for RuntimeError(); classify() reads an empty error
    # as "no error", so the type name must always be present.
    return f"{type(exc).__name__}: {exc}"


def _plan_searches(
    targets: list[SeekerTarget],
) -> dict[tuple[str, str], tuple[str, str, list[SeekerTarget]]]:
    """Group subscribed targets by the (term, location) they need.

    The shared unit is (term, location): two seekers asking for the same term
    in the same place cost one scrape. Each seeker searches its own location
    plus also_search. Values keep the first spelling seen for the scrape.
    """
    plan: dict[tuple[str, str], tuple[str, str, list[SeekerTarget]]] = {}
    for t in targets:
        locations = [t.profile.location, *t.profile.also_search]
        for term in t.profile.search_terms:
            for location in locations:
                key = (term.strip().casefold(), location.strip().casefold())
                plan.setdefault(key, (term, location, []))[2].append(t)
    return plan


def _run_one_site(
    store: Store, source: DiscoverySource, targets: list[SeekerTarget]
) -> SourceResult:
    """Everything for one site, isolated. A failure here reaches no other site.

    `targets` are only those subscribed to this source. The run record is
    written in `finally`, so a mid-loop failure still leaves a row.
    """
    started = time.monotonic()
    canary_ok = False
    canary_count = 0
    raw_count = 0
    unique: dict[tuple[str, str], Posting] = {}
    status = SourceStatus.ERROR
    notes = ""

    try:
        found_by_search: list[tuple[list[Posting], list[SeekerTarget]]] = []
        try:
            canary_ok, _canary_raw, canary_count = source.canary()
            if canary_ok:
                for term, location, subscribers in _plan_searches(targets).values():
                    found, raw = source.search(term, location)
                    raw_count += raw
                    found_by_search.append((found, subscribers))
                    for p in found:
                        unique[(p.source, p.external_id)] = p

            status, notes = classify(
                results_count=len(unique), canary_ok=canary_ok,
                canary_count=canary_count, raw_count=raw_count,
                history=store.recent_counts(source.name, 7),
            )
            if status in (SourceStatus.HEALTHY, SourceStatus.DEGRADED):
                _persist(store, found_by_search)
                _describe_missing(store, source, unique.values())
        except Exception as exc:  # noqa: BLE001 - contained to this site
            error = _describe(exc)
            status, notes = classify(
                results_count=len(unique), canary_ok=canary_ok,
                canary_count=canary_count, raw_count=raw_count,
                history=[], error=error,
            )
            store.record_event("source_error", f"{source.name}: {error}")
    finally:
        problem = getattr(source, "last_problem", "")
        if problem and status is not SourceStatus.HEALTHY:
            notes = f"{notes} The site said: {problem}"
        latency_ms = int((time.monotonic() - started) * 1000)
        store.record_source_run(
            source=source.name, canary_ok=canary_ok, canary_count=canary_count,
            raw_count=raw_count, results_count=len(unique), status=status.value,
            notes=notes, latency_ms=latency_ms,
        )

    return SourceResult(
        source=source.name, status=status, canary_ok=canary_ok,
        canary_count=canary_count, raw_count=raw_count,
        postings=list(unique.values()), latency_ms=latency_ms, notes=notes,
    )


def _persist(
    store: Store, found_by_search: list[tuple[list[Posting], list[SeekerTarget]]]
) -> None:
    posting_ids: dict[tuple[str, str], int] = {}
    for found, subscribers in found_by_search:
        for p in found:
            key = (p.source, p.external_id)
            if key not in posting_ids:
                posting_id = store.upsert_posting(p)
                company_id = store.match_company(p.company_name)
                if company_id is not None:
                    store.attach_company(posting_id, company_id)
                posting_ids[key] = posting_id
            for t in subscribers:
                if not _excluded(p, t.profile):
                    store.link_posting(t.seeker_id, posting_ids[key])


MAX_DESCRIBED_PER_RUN = 60


def _describe_missing(store: Store, source: DiscoverySource, postings) -> None:
    """Fetch full text for listings whose feed carries none (Workday, Oracle).

    Only postings still stored without a description are fetched, capped per
    run, so each job costs one detail request once, and older rows fill in
    over later runs. A failure leaves the posting as it was.
    """
    describe = getattr(source, "describe", None)
    if describe is None:
        return
    done = 0
    for p in postings:
        if done >= MAX_DESCRIBED_PER_RUN:
            return
        if p.description or not store.needs_description(p.source, p.external_id):
            continue
        done += 1
        try:
            text = describe(p)
        except Exception as exc:  # noqa: BLE001 - enrichment is optional
            store.record_event("source_error", f"{source.name}: describe {_describe(exc)}")
            continue
        if text:
            store.set_description(p.source, p.external_id, text)


def run_discovery(
    store: Store, sources: list[DiscoverySource], targets: list[SeekerTarget]
) -> list[SourceResult]:
    """Run each subscribed site independently; one result and row per site.

    A source no target subscribes to is skipped entirely: no scrape, no row.
    """
    results: list[SourceResult] = []
    for source in sources:
        subscribed = [t for t in targets if source.name in t.profile.sources]
        if not subscribed:
            continue
        try:
            results.append(_run_one_site(store, source, subscribed))
        except Exception as exc:  # noqa: BLE001 - our own bookkeeping failed
            results.append(
                SourceResult(source=source.name, status=SourceStatus.ERROR,
                             canary_ok=False, notes=_describe(exc))
            )
    return results
