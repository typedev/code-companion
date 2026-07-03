"""Human-readable relative time formatting.

Shared helper used by the Project Manager, issues panel and git history panel
so the "N days ago" phrasing lives in exactly one place.
"""

from datetime import datetime, timezone

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY  # approximate
_YEAR = 365 * _DAY


def parse_iso(value: str) -> datetime | None:
    """Parse an ISO8601 timestamp (GitHub style, trailing 'Z' allowed)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def humanize_relative(dt: datetime | None) -> str:
    """Format a datetime as a smart relative string, e.g. "yesterday".

    Accepts both aware and naive datetimes: aware values are compared in UTC,
    naive values are assumed to be local time. Future timestamps clamp to
    "just now". Falls back to an absolute date once older than a year.
    """
    if dt is None:
        return ""

    if dt.tzinfo is not None:
        now = datetime.now(timezone.utc)
        dt = dt.astimezone(timezone.utc)
    else:
        now = datetime.now()

    seconds = (now - dt).total_seconds()
    if seconds < 0:
        seconds = 0

    if seconds < 45:
        return "just now"
    if seconds < 90:
        return "a minute ago"
    if seconds < _HOUR:
        n = int(seconds / _MINUTE)
        return f"{n} minutes ago"
    if seconds < 90 * _MINUTE:
        return "an hour ago"
    if seconds < _DAY:
        n = int(seconds / _HOUR)
        return f"{n} hours ago"
    if seconds < 2 * _DAY:
        return "yesterday"
    if seconds < _WEEK:
        n = int(seconds / _DAY)
        return f"{n} days ago"
    if seconds < 2 * _WEEK:
        return "a week ago"
    if seconds < _MONTH:
        n = int(seconds / _WEEK)
        return f"{n} weeks ago"
    if seconds < 2 * _MONTH:
        return "a month ago"
    if seconds < _YEAR:
        n = int(seconds / _MONTH)
        return f"{n} months ago"
    if seconds < 2 * _YEAR:
        return "a year ago"
    return dt.strftime("%b %d, %Y")


def humanize_relative_iso(value: str) -> str:
    """Convenience wrapper: parse an ISO8601 string then humanize it."""
    return humanize_relative(parse_iso(value))
