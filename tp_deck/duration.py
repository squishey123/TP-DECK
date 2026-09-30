"""Parse cache lifetimes such as 30m, 12h, 1d, 2w, and 1M."""

from __future__ import annotations

import re

MAX_CACHE_SECONDS = 366 * 24 * 60 * 60

_DURATION = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mo|MO|Mo|m|h|H|d|D|w|W|M)"
)
_BARE_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_UNIT_SECONDS = {
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
}
_MONTH_SECONDS = 30 * 24 * 60 * 60
_MINUTE_SECONDS = 60


def parse_duration(text: str) -> float:
    """Return seconds. A bare number is hours. Zero and spans over 366 days fail."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Enter a cache lifetime such as 12h, 30m, or 1d.")

    if _BARE_NUMBER.fullmatch(raw):
        return _bounded(float(raw) * _UNIT_SECONDS["h"])

    match = _DURATION.fullmatch(raw)
    if match is None:
        raise ValueError(
            "Use a number plus m, h, d, w, or M. "
            "Examples: 30m, 12h, 1d, 2w, 1M."
        )

    amount = float(match.group(1))
    unit = match.group(2)
    if unit.lower() == "mo" or unit == "M":
        seconds = amount * _MONTH_SECONDS
    elif unit == "m":
        seconds = amount * _MINUTE_SECONDS
    else:
        seconds = amount * _UNIT_SECONDS[unit.lower()]
    return _bounded(seconds)


def cache_ttl_seconds(settings: dict) -> float:
    """Prefer cache_ttl text, then the older cache_ttl_hours number."""
    raw = settings.get("cache_ttl")
    if isinstance(raw, str) and raw.strip():
        return parse_duration(raw.strip())
    try:
        hours = float(settings.get("cache_ttl_hours", 12))
    except (TypeError, ValueError):
        hours = 12.0
    return _bounded(max(0.0, hours) * _UNIT_SECONDS["h"])


def cache_ttl_label(settings: dict) -> str:
    """Text to show in the settings field."""
    raw = settings.get("cache_ttl")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    try:
        hours = float(settings.get("cache_ttl_hours", 12))
    except (TypeError, ValueError):
        hours = 12.0
    if hours == int(hours):
        return f"{int(hours)}h"
    return f"{hours}h"


def _bounded(seconds: float) -> float:
    if seconds <= 0:
        raise ValueError("Cache lifetime must be greater than zero.")
    if seconds > MAX_CACHE_SECONDS:
        raise ValueError("Cache lifetime cannot be longer than 366 days.")
    return float(seconds)
