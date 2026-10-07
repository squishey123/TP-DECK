"""Load and persist settings.json for TP DECK."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tpdeck")

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

DEFAULTS: dict[str, Any] = {
    "mode": "single",
    "ebay_port": 9222,
    "erp_port": 9223,
    "chrome_version": "",
    "window_x": 100,
    "window_y": 100,
    "emergency_hotkey": "pause",
    "ebay_url_pattern": "ebay.com/sh/ord",
    "erp_url_pattern": "",
    "wait_timeout_ms": 10000,
    "erp_submit_key": "Enter",
    "location_blacklist": ["tech", "internal", "LOST"],
    "location_deprioritize": ["Andrei & Alex Office"],
    "location_deprioritize_prefixes": ["PR"],
    "location_lowest_priority": ["HQ"],
    "cache_ttl_hours": 12,
    "cache_enabled": True,
    "slow_razor_multiplier": 10,
    "ebay_refresh_hold_enabled": True,
    "ebay_refresh_hold_minutes": 5,
    "show_results_popup": False,
    "ebay_autocycle_minutes": 10,
    "shipstation_url_pattern": "shipstation.com/orders/awaiting-shipment",
    "shipstation_sync_every_cycles": 3,
    "serial_timeout_ms": 10000,
    "serial_clear_confirm_ms": 500,
    "pick_list_exclude_locations": ["HQ", "unavailable", "LOST"],
    "pick_list_exclude_prefixes": ["PR"],
    "pick_list_exclude_misc": True,
}


def load_settings() -> dict[str, Any]:
    """Return settings merged over defaults. Creates the file if missing."""
    from tp_deck.locators import load_locators

    load_locators()
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULTS.copy())
        return DEFAULTS.copy()

    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read settings.json: %s — using defaults", exc)
        return DEFAULTS.copy()

    merged = DEFAULTS.copy()
    merged.update(data)
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    """Write the full settings dict to disk."""
    try:
        with SETTINGS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.error("Failed to write settings.json: %s", exc)


def update_settings(**kwargs: Any) -> dict[str, Any]:
    """Patch specific keys and persist. Returns the updated settings."""
    settings = load_settings()
    settings.update(kwargs)
    save_settings(settings)
    return settings
