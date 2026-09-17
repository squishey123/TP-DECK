"""Persist SKU → location lookups across runs until the TTL expires."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("tpdeck")

CACHE_PATH = Path(__file__).resolve().parent / "sku_location_cache.json"


def _read_raw() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("SKU cache unreadable (%s); starting empty", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(data: dict[str, Any]) -> None:
    try:
        with CACHE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.error("Failed to write SKU cache: %s", exc)


class SkuLocationCache:
    """File-backed SKU location cache. Entries older than ttl_hours are dropped."""

    def __init__(self, ttl_hours: float = 12.0) -> None:
        self.ttl_seconds = max(0.0, float(ttl_hours)) * 3600.0
        self._data = self._prune(_read_raw())
        self._dirty = False

    def _prune(self, data: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        kept: dict[str, Any] = {}
        dropped = 0
        for sku, entry in data.items():
            if not isinstance(entry, dict):
                dropped += 1
                continue
            location = str(entry.get("location") or "").strip()
            try:
                ts = float(entry.get("ts") or 0)
            except (TypeError, ValueError):
                dropped += 1
                continue
            if not location or (now - ts) > self.ttl_seconds:
                dropped += 1
                continue
            kept[str(sku)] = {"location": location, "ts": ts}
        if dropped:
            logger.info("SKU cache dropped %s stale/invalid entries", dropped)
            self._dirty = True
        return kept

    def get(self, sku: str) -> Optional[str]:
        entry = self._data.get(sku)
        if not entry:
            return None
        location = str(entry.get("location") or "") or None
        if location and location.casefold() == "unknown":
            return None
        return location

    def put(self, sku: str, location: str) -> None:
        sku = (sku or "").strip()
        location = (location or "").strip()
        if not sku or not location or location.casefold() == "unknown":
            return
        self._data[sku] = {"location": location, "ts": time.time()}
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        _write_raw(self._data)
        self._dirty = False
        logger.info("SKU cache saved (%s entries)", len(self._data))
