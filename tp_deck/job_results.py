"""Build scrape and pick-list rows for the clipboard and the results table."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from tp_deck.pick_list import (
    build_pick_list,
    is_excluded_pick_location,
    ordered_pick_rows,
)
from tp_deck.sku_overrides import SkuOverride, resolve_sku

logger = logging.getLogger("tpdeck")

UNKNOWN_LOCATION = "Unknown"

ORDER_HEADERS = ("Order", "Buyer", "SKU", "Location")
PICK_HEADERS = ("Group", "Qty", "SKU", "Location")


@dataclass(frozen=True)
class AutomationResult:
    """Status line for the dashboard, plus clipboard text when a job copied it."""

    status: str
    clipboard_text: Optional[str] = None
    failed_serials: tuple[tuple[int, str], ...] = ()
    job: str = ""
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    unknown_flags: tuple[bool, ...] = ()
    note: str = "Copied to clipboard"
    pending_unknowns: tuple[str, ...] = ()
    defer_copy: bool = False
    lines: tuple[tuple[str, str, str, int], ...] = ()
    queried: tuple[tuple[str, str], ...] = ()
    close_detail_url: Optional[str] = None


def is_unknown_location(location: str) -> bool:
    return str(location or "").strip().casefold() == UNKNOWN_LOCATION.casefold()


def render_job(
    settings: dict[str, Any],
    *,
    job: str,
    lines: list[tuple[str, str, str, int]] | tuple[tuple[str, str, str, int], ...],
    queried: dict[str, str],
    overrides: dict[str, SkuOverride],
    hits: int,
    misses: int,
    copy_clipboard: bool,
    allow_prompt: bool,
    cache_enabled: bool,
) -> AutomationResult:
    """Turn scraped lines and looked-up locations into clipboard text and table rows."""
    job_name = str(job or "orders").lower()
    ebay_skus = list(dict.fromkeys(sku for _order, _buyer, sku, _qty in lines))
    resolved = {sku: resolve_sku(sku, overrides) for sku in ebay_skus}

    def location_of(sku: str) -> str:
        lookup, _factor, _display = resolved[sku]
        return queried.get(lookup, UNKNOWN_LOCATION)

    pending: list[str] = []
    alt_misses = 0
    for sku in ebay_skus:
        if not is_unknown_location(location_of(sku)):
            continue
        if sku in overrides:
            alt_misses += 1
        elif allow_prompt:
            pending.append(sku)

    unknowns = sum(1 for sku in ebay_skus if is_unknown_location(location_of(sku)))
    unknown_bit = f", {unknowns} unknown" if unknowns else ""
    alt_bit = (
        f", {alt_misses} alternative(s) had no location" if alt_misses else ""
    )

    if not copy_clipboard:
        if not cache_enabled:
            status = (
                f"Cycling — {len(ebay_skus)} SKU(s), "
                "cache is off — locations will not be stored"
            )
        else:
            status = (
                f"Cycling — {len(ebay_skus)} SKU(s) cached, "
                f"{misses} lookup(s), {hits} cache hit(s){unknown_bit}{alt_bit}"
            )
        return AutomationResult(status=status, job=job_name)

    if job_name == "picklist":
        return _render_pick(
            settings,
            lines=lines,
            queried=queried,
            resolved=resolved,
            hits=hits,
            misses=misses,
            unknown_bit=unknown_bit,
            alt_bit=alt_bit,
            pending=pending,
        )

    return _render_orders(
        lines=lines,
        queried=queried,
        resolved=resolved,
        hits=hits,
        misses=misses,
        unknown_bit=unknown_bit,
        alt_bit=alt_bit,
        pending=pending,
    )


def _popup_note(clipboard: str, omitted: int) -> str:
    copied = bool((clipboard or "").strip())
    if omitted <= 0:
        return "Copied to clipboard" if copied else "Nothing to copy"
    noun = "unknown" if omitted == 1 else "unknowns"
    if copied:
        return f"Copied to clipboard — {omitted} {noun} left off"
    return f"Nothing copied — {omitted} {noun} left off"


def _carry(
    pending: list[str],
    lines: list[tuple[str, str, str, int]] | tuple[tuple[str, str, str, int], ...],
    queried: dict[str, str],
) -> tuple[tuple[tuple[str, str, str, int], ...], tuple[tuple[str, str], ...]]:
    if not pending:
        return (), ()
    stored_lines = tuple(
        (str(order_id), str(buyer), str(sku), int(qty))
        for order_id, buyer, sku, qty in lines
    )
    stored_queried = tuple(
        (str(sku), str(location)) for sku, location in queried.items()
    )
    return stored_lines, stored_queried


def _empty_row(width: int) -> tuple[str, ...]:
    cells = ["Nothing to copy"]
    cells.extend("" for _ in range(width - 1))
    return tuple(cells)


def _render_orders(
    *,
    lines: list[tuple[str, str, str, int]] | tuple[tuple[str, str, str, int], ...],
    queried: dict[str, str],
    resolved: dict[str, tuple[str, int, str]],
    hits: int,
    misses: int,
    unknown_bit: str,
    alt_bit: str,
    pending: list[str],
) -> AutomationResult:
    rows: list[tuple[str, ...]] = []
    flags: list[bool] = []
    text_lines: list[str] = []
    for order_id, buyer, sku, _qty in lines:
        _lookup, _factor, display = resolved[sku]
        location = queried.get(_lookup, UNKNOWN_LOCATION)
        name = (buyer or "").strip() or "?"
        rows.append((str(order_id), name, display, location))
        flags.append(is_unknown_location(location))
        text_lines.append(f"{order_id} - {name} - {display} - {location}")

    clipboard = "\n".join(text_lines)
    if not rows:
        rows = [_empty_row(len(ORDER_HEADERS))]
        flags = [False]
    stored_lines, stored_queried = _carry(pending, lines, queried)
    return AutomationResult(
        status=(
            f"Success — {len(text_lines)} line(s), "
            f"{misses} lookup(s), {hits} cache hit(s){unknown_bit}{alt_bit}"
        ),
        clipboard_text=clipboard,
        job="orders",
        headers=ORDER_HEADERS,
        rows=tuple(rows),
        unknown_flags=tuple(flags),
        note=_popup_note(clipboard, 0),
        pending_unknowns=tuple(pending),
        defer_copy=bool(pending),
        lines=stored_lines,
        queried=stored_queried,
    )


def _render_pick(
    settings: dict[str, Any],
    *,
    lines: list[tuple[str, str, str, int]] | tuple[tuple[str, str, str, int], ...],
    queried: dict[str, str],
    resolved: dict[str, tuple[str, int, str]],
    hits: int,
    misses: int,
    unknown_bit: str,
    alt_bit: str,
    pending: list[str],
) -> AutomationResult:
    exclude_names = settings.get("pick_list_exclude_locations")
    exclude_prefixes = settings.get("pick_list_exclude_prefixes")
    exclude_misc = bool(settings.get("pick_list_exclude_misc", True))

    combined: dict[str, list] = {}
    for _order_id, _buyer, sku, qty in lines:
        lookup, factor, display = resolved[sku]
        location = queried.get(lookup, UNKNOWN_LOCATION)
        total = max(1, int(qty)) * max(1, int(factor))
        bucket = combined.get(display)
        if bucket is None:
            combined[display] = [total, location]
        else:
            bucket[0] += total

    walk: list[tuple[str, str, int]] = []
    unknown_items: list[tuple[str, int, str]] = []
    skipped = 0
    for display, (qty, location) in combined.items():
        if is_unknown_location(location):
            unknown_items.append((display, int(qty), location))
            continue
        if is_excluded_pick_location(
            location,
            exclude_names=exclude_names,
            exclude_prefixes=exclude_prefixes,
            exclude_misc=exclude_misc,
        ):
            skipped += 1
            logger.info(
                "Pick list skipped %s at %s (not a pick location)",
                display,
                location,
            )
            continue
        walk.append((display, location, int(qty)))

    clipboard = build_pick_list(
        walk,
        exclude_names=exclude_names,
        exclude_prefixes=exclude_prefixes,
        exclude_misc=exclude_misc,
    )
    rows: list[tuple[str, ...]] = []
    flags: list[bool] = []
    for title, qty, sku, location in ordered_pick_rows(
        walk,
        exclude_names=exclude_names,
        exclude_prefixes=exclude_prefixes,
        exclude_misc=exclude_misc,
    ):
        rows.append((title, f"{qty}x", sku, location))
        flags.append(False)
    for display, qty, location in sorted(unknown_items, key=lambda item: item[0]):
        rows.append(("Unknown", f"{qty}x", display, location))
        flags.append(True)

    omitted = len(unknown_items)
    if not rows:
        rows = [_empty_row(len(PICK_HEADERS))]
        flags = [False]

    skipped_bit = f", {skipped} skipped" if skipped else ""
    stored_lines, stored_queried = _carry(pending, lines, queried)
    return AutomationResult(
        status=(
            f"Success — pick list {len(walk)} SKU(s), "
            f"{misses} lookup(s), {hits} cache hit(s)"
            f"{unknown_bit}{alt_bit}{skipped_bit}"
        ),
        clipboard_text=clipboard,
        job="picklist",
        headers=PICK_HEADERS,
        rows=tuple(rows),
        unknown_flags=tuple(flags),
        note=_popup_note(clipboard, omitted),
        pending_unknowns=tuple(pending),
        defer_copy=bool(pending),
        lines=stored_lines,
        queried=stored_queried,
    )
