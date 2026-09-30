"""CSS selectors and Razor page addresses. Edited in locators.json, not Settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tpdeck")

LOCATORS_PATH = Path(__file__).resolve().parent / "locators.json"
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

DEFAULTS: dict[str, str] = {
    "ebay_order_id": "[id$=\"__order-info\"] .order-details",
    "ebay_buyer": "div.user-details.details span > button > span:nth-child(1)",
    "ebay_sku": "span.item-custom-sku span.sh-bold",
    "ebay_qty": "div.quantity strong",
    "erp_sku_input": (
        "#ag-grid-inventory-detail .ag-header-row-floating-filter "
        "> div:nth-child(2) input"
    ),
    "erp_location": (
        "#ag-grid-inventory-detail .ag-center-cols-container "
        ".ag-row > div:nth-child(3)"
    ),
    "erp_serial_input": (
        "#div_TabSalesOrderItems > div:nth-child(5) > "
        "div.clearfix.allocated-controls > div:nth-child(1)"
    ),
    "sales_orders_url": "https://techyparts.razorerp.com/Admin/SalesOrders.aspx",
    "sales_orders_url_pattern": "SalesOrders.aspx",
    "sales_order_detail_pattern": "SalesOrder.aspx?orderId=",
    "sales_order_filter": (
        "#ag-grid .ag-header-row-floating-filter > div:nth-child(1) input"
    ),
    "sales_order_rows": "#ag-grid .ag-center-cols-container .ag-row",
    "service_order_label": (
        "#div_PageSalesOrder .wrap-sales-list > ul:nth-child(2) > li:nth-child(2)"
    ),
}

_MOVED_SETTING_KEYS = ("selectors", "sales_order_url_pattern")


def load_locators() -> dict[str, str]:
    """Return locators. Copies legacy settings selectors across once."""
    merged = dict(DEFAULTS)
    existed = LOCATORS_PATH.exists()
    if existed:
        merged.update(_read_file(LOCATORS_PATH))
    legacy = _read_legacy_selectors()
    wrote = True
    if legacy:
        for key, value in legacy.items():
            text = str(value or "").strip()
            if text and key in DEFAULTS:
                merged[key] = text
        wrote = _write(merged)
    elif not existed:
        wrote = _write(merged)
    if wrote:
        _strip_legacy_settings()
    return {key: str(merged.get(key) or DEFAULTS[key]) for key in DEFAULTS}


def _read_file(path: Path) -> dict[str, str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s: %s", path.name, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value is not None}


def _read_legacy_selectors() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read settings.json for locators: %s", exc)
        return {}
    selectors = data.get("selectors") if isinstance(data, dict) else None
    return selectors if isinstance(selectors, dict) else {}


def _write(locators: dict[str, str]) -> bool:
    payload = {key: str(locators.get(key) or "") for key in DEFAULTS}
    try:
        with LOCATORS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.error("Failed to write locators.json: %s", exc)
        return False
    return True


def _strip_legacy_settings() -> None:
    if not SETTINGS_PATH.exists():
        return
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read settings.json: %s", exc)
        return
    if not isinstance(data, dict):
        return
    if not any(key in data for key in _MOVED_SETTING_KEYS):
        return
    for key in _MOVED_SETTING_KEYS:
        data.pop(key, None)
    try:
        with SETTINGS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.error("Failed to remove legacy selectors from settings.json: %s", exc)
    else:
        logger.info("Moved CSS selectors into locators.json")
