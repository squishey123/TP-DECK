"""Permanent Uline item cache: item #, name, price, units per set, aliases."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("tpdeck")

CACHE_PATH = Path(__file__).resolve().parent / "uline_cache.json"
_WSPACE = re.compile(r"\s+")
_ITEM_SHAPE = re.compile(r"^[A-Z]{1,3}-\d{1,6}(-[A-Z0-9]+)?$")


def normalize_item(text: str) -> str:
    """Uline item number in canonical form, uppercase, no spaces around dashes."""
    raw = unicodedata.normalize("NFKC", str(text or ""))
    raw = _WSPACE.sub("", raw).strip().upper()
    raw = re.sub(r"/", "-", raw)
    if _ITEM_SHAPE.match(raw):
        return raw
    match = re.match(r"^([A-Z]{1,3})-?(\d{1,6})", raw)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return raw


def normalize_alias(text: str) -> str:
    """Alias key: casefolded, single spaces, leading/trailing # trimmed."""
    raw = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("\u2019", "'")
    raw = _WSPACE.sub(" ", raw).strip()
    raw = re.sub(r"^[-/#]+\s*", "", raw)
    raw = re.sub(r"\s*[-/#]+$", "", raw)
    return raw


@dataclass(frozen=True)
class UlineItem:
    item: str
    name: str
    price: float
    units_per_set: int
    unit: str
    min_sets: int = 1
    base_unit: str = ""
    tiers: tuple[tuple[int, float], ...] = ()
    aliases: tuple[str, ...] = ()
    url: str = ""
    in_stock: bool = True
    note: str = ""
    added: str = ""
    checked: str = ""


def today() -> str:
    return date.today().isoformat()


def tier_price(tiers: Iterable[tuple[int, float]], base: float, qty: int) -> float:
    """Price per unit at the highest tier the pack count reaches."""
    best = float(base)
    for minimum, price in sorted(tiers, key=lambda row: int(row[0])):
        if int(qty) >= int(minimum):
            best = float(price)
    return best


def parse_price(text: str) -> Optional[float]:
    """$37, $37.00, $.80 → 37.0, 37.0, 0.8. None when no money in the text."""
    cleaned = unicodedata.normalize("NFKC", str(text or "")).replace(",", "")
    match = re.search(r"\$\s*(\d*\.\d{1,2}|\d+(?:\.\d{1,2})?)", cleaned)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def parse_units(text: str) -> Optional[int]:
    """1,750 → 1750. None when no whole number in the text."""
    cleaned = unicodedata.normalize("NFKC", str(text or "")).replace(",", "")
    match = re.search(r"\d+", cleaned)
    return int(match.group(0)) if match else None


def _as_str(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_aliases(value: Any) -> tuple[str, ...]:
    parts = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple)) else []
    out: list[str] = []
    for raw in parts:
        alias = normalize_alias(_as_str(raw))
        if alias and alias not in out:
            out.append(alias)
    return tuple(out)


def _as_tiers(value: Any) -> tuple[tuple[int, float], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    tiers: list[tuple[int, float]] = []
    for row in value:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            tiers.append((max(1, _as_int(row[0], 1)), max(0.0, _as_float(row[1], 0.0))))
    return tuple(sorted(tiers, key=lambda row: row[0]))


def _read_raw() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Uline cache unreadable (%s); starting empty", exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_cache() -> dict[str, UlineItem]:
    """Every cached item keyed by canonical item number."""
    items: dict[str, UlineItem] = {}
    for raw_key, entry in _read_raw().items():
        if not isinstance(entry, dict):
            continue
        key = normalize_item(_as_str(entry.get("item") or raw_key))
        if not key:
            continue
        items[key] = UlineItem(
            item=key,
            name=_as_str(entry.get("name")),
            price=_as_float(entry.get("price"), 0.0),
            units_per_set=max(1, _as_int(entry.get("units_per_set"), 1)),
            unit=_as_str(entry.get("unit")) or "Each",
            min_sets=max(1, _as_int(entry.get("min_sets"), 1)),
            base_unit=_as_str(entry.get("base_unit")),
            tiers=_as_tiers(entry.get("tiers")),
            aliases=_as_aliases(entry.get("aliases")),
            url=_as_str(entry.get("url")),
            in_stock=bool(entry.get("in_stock", True)),
            note=_as_str(entry.get("note")),
            added=_as_str(entry.get("added")),
            checked=_as_str(entry.get("checked")),
        )
    return items


def save_cache(cache: dict[str, UlineItem]) -> None:
    payload = {
        key: {
            "item": item.item,
            "name": item.name,
            "price": item.price,
            "units_per_set": item.units_per_set,
            "unit": item.unit,
            "min_sets": item.min_sets,
            "base_unit": item.base_unit,
            "tiers": [list(tier) for tier in item.tiers],
            "aliases": list(item.aliases),
            "url": item.url,
            "in_stock": item.in_stock,
            "note": item.note,
            "added": item.added,
            "checked": item.checked,
        }
        for key, item in sorted(cache.items())
    }
    try:
        with CACHE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        logger.error("Failed to write Uline cache: %s", exc)
        raise
    logger.info("Uline cache saved (%s items)", len(payload))


def alias_index(cache: dict[str, UlineItem]) -> dict[str, str]:
    """Alias key -> canonical item number."""
    return {alias: key for key, item in cache.items() for alias in item.aliases}


def resolve_key(cache: dict[str, UlineItem], key: str) -> tuple[str, Optional[UlineItem]]:
    """Resolve a typed Item # or alias to (canonical key, cached item or None)."""
    typed = _as_str(key)
    if not typed:
        return "", None
    item_key = normalize_item(typed)
    found = cache.get(item_key)
    if found is not None:
        return item_key, found
    target = alias_index(cache).get(normalize_alias(typed))
    if target:
        return target, cache.get(target)
    return item_key, None


def put_item(cache: dict[str, UlineItem], item: UlineItem) -> UlineItem:
    """Store resolved data. Aliases and the added date survive the merge."""
    key = normalize_item(item.item)
    if not key:
        raise ValueError("Item number is empty")
    existing = cache.get(key)
    new_aliases = _as_aliases(item.aliases)
    merged = UlineItem(
        item=key,
        name=item.name or (existing.name if existing else ""),
        price=item.price if item.price > 0 else (existing.price if existing else 0.0),
        units_per_set=item.units_per_set if item.units_per_set >= 1
        else (existing.units_per_set if existing else 1),
        unit=item.unit or (existing.unit if existing else "Each"),
        min_sets=item.min_sets if item.min_sets >= 1
        else (existing.min_sets if existing else 1),
        base_unit=item.base_unit or (existing.base_unit if existing else ""),
        tiers=item.tiers if item.tiers else (existing.tiers if existing else ()),
        aliases=new_aliases + tuple(
            a for a in (existing.aliases if existing else ()) if a not in new_aliases
        ),
        url=item.url or (existing.url if existing else ""),
        in_stock=item.in_stock,
        note=item.note or (existing.note if existing else ""),
        added=(existing.added if existing and existing.added else today()),
        checked=item.checked or today(),
    )
    cache[key] = merged
    return merged


def add_alias(cache: dict[str, UlineItem], key: str, alias: str) -> str:
    """Attach an alias to a cached item. Returns the alias owner.

    One alias maps to one item, so an alias already used by another item is
    not added, and the owner is returned so the UI can say who owns it.
    """
    canonical, found = resolve_key(cache, key)
    if found is None:
        raise ValueError(f"{key} is not in the cache")
    alias_key = normalize_alias(alias)
    if not alias_key:
        raise ValueError("Alias is empty")
    owner = alias_index(cache).get(alias_key)
    if owner is not None and owner != canonical:
        return owner
    if alias_key not in found.aliases:
        cache[canonical] = replace(found, aliases=found.aliases + (alias_key,))
    return canonical


def remove_alias(cache: dict[str, UlineItem], key: str, alias: str) -> None:
    canonical, found = resolve_key(cache, key)
    if found is None:
        raise ValueError(f"{key} is not in the cache")
    alias_key = normalize_alias(alias)
    kept = tuple(a for a in found.aliases if a != alias_key)
    if len(kept) == len(found.aliases):
        raise ValueError(f"{alias} is not an alias of {canonical}")
    cache[canonical] = replace(found, aliases=kept)


def edit_item(cache: dict[str, UlineItem], key: str, **fields: Any) -> UlineItem:
    """Edit cached fields. Aliases and the added date are preserved."""
    canonical, found = resolve_key(cache, key)
    if found is None:
        raise ValueError(f"{key} is not in the cache")
    updates: dict[str, Any] = {}
    for name, value in fields.items():
        if name == "item":
            updates["item"] = normalize_item(_as_str(value))
        elif name == "name":
            updates["name"] = _as_str(value)
        elif name == "price":
            updates["price"] = max(0.0, _as_float(value, found.price))
        elif name == "units_per_set":
            updates["units_per_set"] = max(1, _as_int(value, found.units_per_set))
        elif name == "min_sets":
            updates["min_sets"] = max(1, _as_int(value, found.min_sets))
        elif name == "base_unit":
            updates["base_unit"] = _as_str(value)
        elif name == "unit":
            updates["unit"] = _as_str(value) or "Each"
        elif name == "tiers":
            updates["tiers"] = _as_tiers(value)
        elif name == "aliases":
            updates["aliases"] = _as_aliases(value)
        elif name == "url":
            updates["url"] = _as_str(value)
        elif name == "in_stock":
            updates["in_stock"] = bool(value)
        elif name == "note":
            updates["note"] = _as_str(value)
    updated = replace(found, **updates)
    if canonical != updated.item:
        cache.pop(canonical, None)
    cache[updated.item] = updated
    return updated


def remove_item(cache: dict[str, UlineItem], key: str) -> UlineItem:
    """Drop one cached item and its aliases. Returns the removed row."""
    canonical, found = resolve_key(cache, key)
    if found is None:
        raise ValueError(f"{key} is not in the cache")
    cache.pop(canonical)
    return found


def merge_items(cache: dict[str, UlineItem], items: Iterable[UlineItem]) -> None:
    for item in items:
        put_item(cache, item)
