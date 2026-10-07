"""Persist SKU → location lookups across runs until the TTL expires."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("tpdeck")

CACHE_PATH = Path(__file__).resolve().parent / "sku_location_cache.json"
PROMPT_TTL_SECONDS = 24 * 60 * 60
_PROMPTS_KEY = "_prompts"
UNKNOWN_LOCATION = "Unknown"


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


def _is_unknown(location: str) -> bool:
    return str(location or "").strip().casefold() == UNKNOWN_LOCATION.casefold()


class SkuLocationCache:
    """File-backed SKU location cache. Entries older than ttl_seconds are dropped.

    Unknown locations are stored like any other location. Prompt times live
    beside them and are kept for 24 hours even after the location entry expires.
    """

    def __init__(self, ttl_seconds: float = 12 * 3600) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._dirty = False
        raw = _read_raw()
        self._prompts = self._load_prompts(raw)
        self._data = self._prune(raw)

    def _load_prompts(self, data: dict[str, Any]) -> dict[str, float]:
        raw = data.get(_PROMPTS_KEY)
        if not isinstance(raw, dict):
            return {}
        prompts: dict[str, float] = {}
        now = time.time()
        for sku, stamp in raw.items():
            key = str(sku or "").strip()
            try:
                ts = float(stamp)
            except (TypeError, ValueError):
                continue
            if key and (now - ts) <= PROMPT_TTL_SECONDS:
                prompts[key] = ts
        return prompts

    def _prune(self, data: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        kept: dict[str, Any] = {}
        dropped = 0
        for sku, entry in data.items():
            if sku == _PROMPTS_KEY:
                continue
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
        expired_prompts = 0
        fresh: dict[str, float] = {}
        for sku, ts in self._prompts.items():
            if (now - ts) > PROMPT_TTL_SECONDS:
                expired_prompts += 1
                continue
            fresh[sku] = ts
        self._prompts = fresh
        if dropped or expired_prompts:
            logger.info(
                "SKU cache dropped %s stale locations and %s old prompts",
                dropped,
                expired_prompts,
            )
            self._dirty = True
        return kept

    def get(self, sku: str) -> Optional[str]:
        entry = self._data.get(sku)
        if not entry:
            return None
        return str(entry.get("location") or "") or None

    def put(self, sku: str, location: str, *, allow_unknown: bool = False) -> None:
        sku = (sku or "").strip()
        location = (location or "").strip()
        if not sku or not location:
            return
        if _is_unknown(location) and not allow_unknown:
            return
        self._data[sku] = {"location": location, "ts": time.time()}
        self._dirty = True

    def prompt_due(self, sku: str) -> bool:
        ts = self._prompts.get((sku or "").strip())
        if ts is None:
            return True
        return (time.time() - ts) >= PROMPT_TTL_SECONDS

    def mark_prompted(self, sku: str) -> None:
        sku = (sku or "").strip()
        if not sku:
            return
        self._prompts[sku] = time.time()
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        payload: dict[str, Any] = dict(self._data)
        if self._prompts:
            payload[_PROMPTS_KEY] = dict(sorted(self._prompts.items()))
        _write_raw(payload)
        self._dirty = False
        logger.info(
            "SKU cache saved (%s entries, %s prompts)",
            len(self._data),
            len(self._prompts),
        )
