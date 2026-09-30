"""Permanent sister-SKU map. One original SKU points at one alternative and a qty."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("tpdeck")

OVERRIDES_PATH = Path(__file__).resolve().parent / "sku_overrides.json"


@dataclass(frozen=True)
class SkuOverride:
    sister: str
    qty: int


def validate_override(original: str, sister: str, qty: int) -> Optional[str]:
    """Return an error message, or None when the row can be saved."""
    original = (original or "").strip()
    sister = (sister or "").strip()
    if not original:
        return "Original SKU is empty"
    if not sister:
        return "Alternative SKU is empty"
    if sister == original:
        return f"{original} cannot map to itself"
    try:
        amount = int(qty)
    except (TypeError, ValueError):
        return "Quantity must be a whole number"
    if amount < 1:
        return "Quantity must be at least 1"
    return None


def resolve_sku(
    sku: str,
    overrides: dict[str, SkuOverride],
) -> tuple[str, int, str]:
    """Return (lookup SKU, qty factor, display SKU)."""
    key = (sku or "").strip()
    found = overrides.get(key)
    if found is None:
        return key, 1, key
    return found.sister, found.qty, f"{key} / {found.sister}"


def load_overrides() -> dict[str, SkuOverride]:
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        with OVERRIDES_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("SKU overrides unreadable (%s); starting empty", exc)
        return {}
    if not isinstance(data, dict):
        return {}

    loaded: dict[str, SkuOverride] = {}
    for raw_sku, entry in data.items():
        original = str(raw_sku or "").strip()
        if not original or not isinstance(entry, dict):
            continue
        sister = str(entry.get("sister") or "").strip()
        try:
            qty = int(entry.get("qty") or 1)
        except (TypeError, ValueError):
            continue
        if validate_override(original, sister, qty):
            continue
        loaded[original] = SkuOverride(sister=sister, qty=qty)
    return loaded


def save_overrides(overrides: dict[str, SkuOverride]) -> None:
    payload = {
        sku: {"sister": item.sister, "qty": int(item.qty)}
        for sku, item in sorted(overrides.items())
    }
    try:
        with OVERRIDES_PATH.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.error("Failed to write SKU overrides: %s", exc)
        raise
    logger.info("SKU overrides saved (%s entries)", len(payload))


def merge_overrides(entries: list[tuple[str, str, int]]) -> None:
    """Add or replace mappings. Existing rows not in entries are kept."""
    current = load_overrides()
    for original, sister, qty in entries:
        original = (original or "").strip()
        sister = (sister or "").strip()
        error = validate_override(original, sister, qty)
        if error:
            raise ValueError(error)
        current[original] = SkuOverride(sister=sister, qty=int(qty))
    save_overrides(current)


def replace_overrides(entries: list[tuple[str, str, int]]) -> None:
    """Replace the whole list with the editor contents."""
    cleaned: dict[str, SkuOverride] = {}
    seen: set[str] = set()
    for original, sister, qty in entries:
        original = (original or "").strip()
        sister = (sister or "").strip()
        if not original and not sister:
            continue
        error = validate_override(original, sister, qty)
        if error:
            raise ValueError(error)
        if original in seen:
            raise ValueError(f"{original} is listed more than once")
        seen.add(original)
        cleaned[original] = SkuOverride(sister=sister, qty=int(qty))
    save_overrides(cleaned)
