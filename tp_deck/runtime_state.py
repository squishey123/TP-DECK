"""Session flags. These reset every time TP DECK starts."""

from __future__ import annotations

from typing import Any

slow_razor_enabled: bool = False


def razor_multiplier(settings: dict[str, Any]) -> float:
    """1 unless Slow Razor is on for this launch. Saved multiplier is capped at 20."""
    if not slow_razor_enabled:
        return 1.0
    try:
        value = float(settings.get("slow_razor_multiplier", 10))
    except (TypeError, ValueError):
        value = 10.0
    if value < 1:
        return 1.0
    if value > 20:
        return 20.0
    return value


def scale_ms(base: int, multiplier: float) -> int:
    return max(1, int(round(float(base) * float(multiplier))))
