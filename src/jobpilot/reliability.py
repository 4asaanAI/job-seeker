"""Stable error codes, token health, source cooldowns and the next scheduled run.

Every tool failure carries `code`, one of ERROR_CODES, beside its plain-language `error`, so a caller (Claude, Fo through
the bridge, the dashboard) can branch on the reason without parsing English. The wording may change; the codes may not.
"""
from __future__ import annotations

import functools
import plistlib
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# code -> what it means. Append new codes; never rename or reuse one.
ERROR_CODES = {
    "seeker_forbidden": "this process serves another seeker",
    "seeker_unknown": "no such seeker",
    "posting_not_linked": "the job is not one of this seeker's jobs",
    "posting_closed": "the job's closing date has passed",
    "duplicate_application": "an application for this job is already recorded",
    "application_in_flight": "another application is still being worked on",
    "approval_required": "the user's explicit OK (a plan, a code or a confirmation) is missing or invalid",
    "review_mode_on": "review mode needs a confirmation for each application",
    "rate_limited": "a daily or per-board limit was reached",
    "unsafe_content": "text or a link failed the safety check",
    "resume_missing": "no saved resume",
    "not_configured": "a setting, key or account this needs is not set up",
    "busy": "the same work is already running",
    "not_found": "nothing matches the request",
    "invalid_argument": "an argument is missing or not allowed",
    "upstream_error": "a job board, Fo or another service failed",
    "error": "an unexpected failure",
}

# First match wins; patterns are matched against the lower-cased error text.
_RULES = (
    (r"pinned to seeker|linked to a different seeker", "seeker_forbidden"),
    (r"no seeker with id|not an active seeker", "seeker_unknown"),
    (r"not linked to (this )?seeker|is not linked", "posting_not_linked"),
    (r"closing date .*passed|deadline .*passed", "posting_closed"),
    (r"already recorded|already applied", "duplicate_application"),
    (r"still (waiting|being worked on|in flight)|in flight", "application_in_flight"),
    (r"review mode", "review_mode_on"),
    (r"approval|approved apply list|plan_id|confirm|code (is|was)|wrong code|too many wrong", "approval_required"),
    (r"limit (reached|of)|daily limit|too soon|cooling down|try again tomorrow", "rate_limited"),
    (r"unsafe|safety check|cannot be used|looks unsafe|withheld", "unsafe_content"),
    (r"no resume|resume saved", "resume_missing"),
    (r"not configured|not set up|is not enabled|needs \[|no key|no fo transport|no secret", "not_configured"),
    (r"still running|already running|busy", "busy"),
    (r"no such|not found|nothing (to|matches)|no .* to (write|record|retry)", "not_found"),
    (r"must be|must not|unknown .*status|unsupported|invalid|choose from|at most|needs a|required", "invalid_argument"),
    (r"http|unreachable|timed out|timeout|fo error|failed to", "upstream_error"),
)


def error_code(message: object) -> str:
    text = str(message or "").lower()
    for pattern, code in _RULES:
        if re.search(pattern, text):
            return code
    return "error"


def with_code(out: object) -> object:
    """Add `code` to a failed tool result that has none (results that already carry one keep it)."""
    if isinstance(out, dict) and out.get("ok") is False and "code" not in out:
        out["code"] = error_code(out.get("error"))
    return out


def coded(fn):
    """Wrap a tool so every failure carries a stable code. Keeps the signature (FastMCP builds schemas from it)."""
    @functools.wraps(fn)
    def call(*args, **kwargs):
        return with_code(fn(*args, **kwargs))
    return call


# ---- token health -------------------------------------------------------------------------------------------------------

KEY_MAX_DAYS = 90          # the bridge key stops working after this many days; rotate it with `jobpilot.bridge --rotate`
KEY_WARN_DAYS = 14


def key_age_days(path: Path, now: float | None = None) -> float | None:
    try:
        return ((time.time() if now is None else now) - path.stat().st_mtime) / 86400
    except OSError:
        return None


def token_health(paths: dict[str, Path], max_days: int = KEY_MAX_DAYS, warn_days: int = KEY_WARN_DAYS,
                 now: float | None = None) -> list[dict]:
    """One line per secret: its age, days left, and a warning when it expires within `warn_days` (or already has)."""
    out = []
    for name, path in paths.items():
        age = key_age_days(path, now)
        if age is None:
            out.append({"token": name, "state": "missing"})
            continue
        left = max_days - age
        state = "expired" if left <= 0 else "expiring" if left <= warn_days else "ok"
        row = {"token": name, "state": state, "age_days": round(age, 1), "days_left": round(max(0.0, left), 1)}
        if state != "ok":
            row["warning"] = (f"The {name} {'has expired' if state == 'expired' else f'expires in {left:.0f} days'}; "
                              "rotate it with: python -m jobpilot.bridge --rotate")
        out.append(row)
    return out


def key_expired(path: Path, max_days: int = KEY_MAX_DAYS, now: float | None = None) -> bool:
    age = key_age_days(path, now)
    return age is not None and age >= max_days


# ---- source cooldowns -----------------------------------------------------------------------------------------------------

COOLDOWN_HOURS = 6         # a source that answered blocked (rate limit, captcha) is left alone this long


def cooling_down(last_run: dict | None, hours: float = COOLDOWN_HOURS, now: datetime | None = None) -> bool:
    """A source whose latest run was blocked is not asked again for `hours`; asking harder is what gets an IP banned."""
    if not last_run or last_run.get("status") != "blocked":
        return False
    try:
        when = datetime.fromisoformat(str(last_run["run_at"]))
    except (KeyError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) - when < timedelta(hours=hours)


# ---- the next scheduled run ---------------------------------------------------------------------------------------------

DAILY_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.jobpilot.daily.plist"


def daily_time(plist: Path = DAILY_PLIST) -> tuple[int, int] | None:
    """(hour, minute) of the daily run from its launchd job, or None when no daily job is installed."""
    try:
        data = plistlib.loads(plist.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    when = data.get("StartCalendarInterval")
    when = when[0] if isinstance(when, list) and when else when
    if not isinstance(when, dict):
        return None
    return int(when.get("Hour", 0)), int(when.get("Minute", 0))


def next_run(at: tuple[int, int] | None, now: datetime | None = None) -> str | None:
    """The next local time the daily run starts, as ISO text; None when there is no schedule."""
    if at is None:
        return None
    now = now or datetime.now().astimezone()
    candidate = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat(timespec="minutes")
