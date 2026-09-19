from __future__ import annotations

import statistics

from .base import SourceStatus

DEGRADED_FRACTION = 0.25
MIN_HISTORY = 1


def classify(
    results_count: int,
    canary_ok: bool,
    canary_count: int,
    raw_count: int,
    history: list[int],
    error: str | None = None,
) -> tuple[SourceStatus, str]:
    """Decide what a result count actually means.

    Order matters. Each branch answers a different question, and the whole
    point of the system is that "we got nothing" is only allowed to mean
    "there is nothing" when every other explanation has been ruled out.
    """
    if error:
        return (
            SourceStatus.ERROR,
            f"Source call failed: {error}. Result count carries no information.",
        )

    if not canary_ok:
        return (
            SourceStatus.BLOCKED,
            f"Canary returned {canary_count} usable rows, below its floor: the "
            "source is blocked, rate-limited, or its response shape changed. "
            "Its low result count carries no information.",
        )

    if results_count == 0 and raw_count > 0:
        return (
            SourceStatus.ERROR,
            f"Source returned {raw_count} rows but none survived normalisation: "
            "the site answered and our field mapping is broken. This is not a "
            "quiet market.",
        )

    usable = [h for h in history if h > 0]
    if results_count > 0 and len(usable) >= MIN_HISTORY:
        median = statistics.median(usable)
        if results_count < median * DEGRADED_FRACTION:
            return (
                SourceStatus.DEGRADED,
                f"Returned {results_count} against a recent median of {median:.0f}.",
            )

    return (SourceStatus.HEALTHY, f"Returned {results_count} results.")
