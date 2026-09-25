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
    "location_blacklist": ["tech", "internal"],
    "location_deprioritize": ["Andrei & Alex Office"],
    "location_deprioritize_prefixes": ["PR"],
    "location_lowest_priority": ["HQ"],
    "cache_ttl_hours": 12,
    "cache_enabled": True,
    "ebay_refresh_hold_enabled": True,
    "ebay_refresh_hold_minutes": 5,
    "show_results_popup": False,
    "ebay_autocycle_minutes": 10,
    "sales_order_url_pattern": "SalesOrder.aspx",
    "serial_timeout_ms": 10000,
    "serial_clear_confirm_ms": 500,
    "pick_list_exclude_locations": ["HQ", "unavailable"],
    "pick_list_exclude_prefixes": ["PR"],
    "pick_list_exclude_misc": True,
    "selectors": {
        "ebay_order_id": "",
        "ebay_buyer": "",
        "ebay_sku": "",
        "ebay_qty": "div.quantity strong",
        "erp_sku_input": "",
        "erp_location": "",
        "erp_serial_input": (
            "#div_TabSalesOrderItems > div:nth-child(5) > "
            "div.clearfix.allocated-controls > div:nth-child(1)"
        ),
    },
}


def load_settings() -> dict[str, Any]:
    """Return settings merged over defaults. Creates the file if missing."""
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
    if "selectors" in data and isinstance(data["selectors"], dict):
        selectors = DEFAULTS["selectors"].copy()
        selectors.update(data["selectors"])
        merged["selectors"] = selectors
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
