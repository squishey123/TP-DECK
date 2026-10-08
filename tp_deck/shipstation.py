"""Sync and scrape ShipStation awaiting-shipment store orders."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import Page

logger = logging.getLogger("tpdeck")

_UPDATED = re.compile(
    r"last updated\s+(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s*[AP]M)",
    re.IGNORECASE,
)
_FOOTER = re.compile(
    r"Viewing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)",
    re.IGNORECASE,
)
_SKU = re.compile(r"SKU:\s*(\S+)", re.IGNORECASE)
_QTY = re.compile(r"(?:Qty|Quantity)\s*[:x]?\s*(\d+)", re.IGNORECASE)
_FRESH_SECONDS = 120
_SYNC_CAP_SECONDS = 180

StoreProgress = Callable[[int, int, str], Awaitable[None]]


_ITEM_COUNT = re.compile(r"^\(\s*\d+\s+items?\s*\)$", re.IGNORECASE)
_COMBINED = re.compile(r"^\(?\s*multiple\s*\)?$", re.IGNORECASE)


def _label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def is_item_count_label(text: str) -> bool:
    """True for grid text like '(2 Items)', which is not a SKU."""
    return _ITEM_COUNT.match(_label(text)) is not None


def is_combined_order(order_number: str) -> bool:
    """True for a combined-shipment summary row, not an order number."""
    return _COMBINED.match(_label(order_number)) is not None


def is_usable_sku(sku: str) -> bool:
    """False for blanks and ShipStation placeholders such as '(2 Items)'."""
    text = _label(sku)
    if not text or is_item_count_label(text):
        return False
    if text.casefold() in {"(multiple)", "multiple", "item sku"}:
        return False
    return True


def classify_store_row(order_number: str, sku: str) -> str:
    """combined, multi, store, or skip.

    combined: a '(Multiple)' shipment summary, often with SKU '(2 Items)'.
    multi: a real store order whose SKU cell is an item count, not a SKU.
    store: a store order with a real SKU.
    skip: an eBay order or any other row the eBay scrape already covers.
    """
    if is_combined_order(order_number) or (
        is_item_count_label(sku) and not is_store_order(order_number)
    ):
        return "combined"
    if not is_store_order(order_number):
        return "skip"
    if not is_usable_sku(sku):
        return "multi"
    return "store"


def is_store_order(order_number: str) -> bool:
    """Store orders are order numbers with no hyphen.

    Combined-shipment rows are labeled '(Multiple)' and are not store orders.
    """
    text = _label(order_number)
    if not text or "-" in text or text.startswith("("):
        return False
    if is_combined_order(text) or is_item_count_label(text):
        return False
    return True


def parse_updated(text: str) -> Optional[datetime]:
    match = _UPDATED.search(re.sub(r"\s+", " ", text or ""))
    if match is None:
        return None
    raw = re.sub(r"\s+", " ", match.group(1)).strip().upper()
    try:
        return datetime.strptime(raw, "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None


def is_fresh(when: datetime, now: Optional[datetime] = None) -> bool:
    current = now or datetime.now()
    return abs((current - when).total_seconds()) <= _FRESH_SECONDS


def parse_shipment_items(text: str) -> list[tuple[str, int, bool]]:
    """Return (sku, qty, assumed) for each SKU: line in a drawer section."""
    found: list[tuple[str, int, bool]] = []
    parts = re.split(r"(?=SKU:\s*)", text or "", flags=re.IGNORECASE)
    for part in parts:
        match = _SKU.search(part)
        if match is None:
            continue
        sku = match.group(1).strip().rstrip(",.;")
        if not sku:
            continue
        qty_match = _QTY.search(part)
        if qty_match is None:
            found.append((sku, 1, True))
        else:
            found.append((sku, max(1, int(qty_match.group(1))), False))
    return found


def parse_footer_total(text: str) -> Optional[tuple[int, int]]:
    """Return (visible_end, total) from 'Viewing 1 - 5 of 5'."""
    match = _FOOTER.search(re.sub(r"\s+", " ", text or ""))
    if match is None:
        return None
    return int(match.group(2)), int(match.group(3))


_STORES_JS = """
() => {
    const stores = [...document.querySelectorAll('[class*="store-container"]')];
    return stores.map((el, index) => {
        const names = [...el.querySelectorAll('[class*="store-name-"]')]
            .map((node) => (node.innerText || '').replace(/\\s+/g, ' ').trim())
            .filter((text) => text && !/last updated/i.test(text));
        names.sort((a, b) => a.length - b.length);
        const status = el.querySelector('[class*="status-message"]');
        const text = status
            ? (status.innerText || '').replace(/\\s+/g, ' ').trim()
            : '';
        return { index, name: names[0] || ('Store ' + (index + 1)), text };
    });
}
"""

_GRID_JS = """
() => {
    const rows = new Map();
    for (const cell of document.querySelectorAll('[data-column][data-row-id]')) {
        const id = cell.getAttribute('data-row-id') || '';
        const column = cell.getAttribute('data-column') || '';
        if (!id) continue;
        let row = rows.get(id);
        if (!row) {
            row = { id, order: '', sku: '', buyer: '' };
            rows.set(id, row);
        }
        if (column === 'order-number') {
            const button = cell.querySelector('button');
            row.order = ((button && button.innerText) || cell.innerText || '')
                .replace(/\\s+/g, ' ').trim();
        } else if (column === 'item-sku') {
            row.sku = (cell.innerText || '').replace(/\\s+/g, ' ').trim();
        } else if (column === 'recipient') {
            const name = cell.querySelector('[class*="recipient-name"]');
            row.buyer = ((name && name.innerText) || '').replace(/\\s+/g, ' ').trim();
        }
    }
    return Array.from(rows.values());
}
"""


async def _read_stores(page: Page) -> list[dict[str, Any]]:
    try:
        snapshot = await page.evaluate(_STORES_JS)
    except Exception as exc:
        logger.info("ShipStation store list unreadable: %s", exc)
        return []
    return list(snapshot or [])


async def _click_update_all(page: Page, locators: dict[str, str]) -> bool:
    """Open the hover menu and click Update All without crossing the gap."""
    trigger = page.locator(locators["shipstation_store_status"]).first
    button = page.locator(locators["shipstation_update_all"]).first
    for _attempt in range(2):
        try:
            await trigger.hover(timeout=3000)
            await button.wait_for(state="visible", timeout=2000)
            await button.evaluate("el => el.click()")
            return True
        except Exception as exc:
            logger.info("Update All click attempt failed: %s", exc)
            await asyncio.sleep(0.2)
    return False


_BUSY_STATUS = re.compile(
    r"\b(updating|syncing|refreshing|queued|in progress)\b",
    re.IGNORECASE,
)


def _store_done(
    text: str,
    previous: Optional[datetime],
    now: datetime,
) -> bool:
    """A store is done when Last updated is recent and it is not still syncing.

    A timestamp that was already fresh before Update All counts immediately,
    so a second sync does not wait out the cap.
    """
    del previous
    if _BUSY_STATUS.search(text or ""):
        return False
    updated = parse_updated(text)
    return updated is not None and is_fresh(updated, now)


async def sync_stores(
    page: Page,
    locators: dict[str, str],
    on_store: Optional[StoreProgress] = None,
) -> None:
    """Update All, wait for each store, then reload the awaiting-shipment grid.

    A store still syncing at three minutes is logged. The reload still runs.
    """
    trigger = page.locator(locators["shipstation_store_status"]).first
    try:
        await trigger.hover(timeout=3000)
        await page.locator(locators["shipstation_update_all"]).first.wait_for(
            state="visible",
            timeout=2000,
        )
    except Exception as exc:
        logger.info("Store status menu did not open: %s", exc)
    before = await _read_stores(page)
    previous = {
        str(item.get("name") or ""): parse_updated(str(item.get("text") or ""))
        for item in before
    }
    if not await _click_update_all(page, locators):
        logger.warning("Could not click ShipStation Update All; reloading the grid")
    else:
        deadline = asyncio.get_running_loop().time() + _SYNC_CAP_SECONDS
        while True:
            await asyncio.sleep(0)
            try:
                await trigger.hover(timeout=1000)
            except Exception:
                pass
            now = datetime.now()
            stores = await _read_stores(page)
            if not stores:
                if asyncio.get_running_loop().time() >= deadline:
                    logger.warning(
                        "ShipStation store list was not visible after %ss",
                        _SYNC_CAP_SECONDS,
                    )
                    break
                await asyncio.sleep(0.5)
                continue
            total = max(1, len(stores))
            done = 0
            pending = ""
            for item in stores:
                name = str(item.get("name") or "")
                text = str(item.get("text") or "")
                if _store_done(text, previous.get(name), now):
                    done += 1
                elif not pending:
                    pending = name or "a store"
            if on_store is not None:
                await on_store(done, total, pending)
            if done >= len(stores) and stores:
                break
            if asyncio.get_running_loop().time() >= deadline:
                stuck = [
                    str(item.get("name") or "a store")
                    for item in stores
                    if not _store_done(
                        str(item.get("text") or ""),
                        previous.get(str(item.get("name") or "")),
                        datetime.now(),
                    )
                ]
                logger.warning(
                    "ShipStation sync still running after %ss: %s",
                    _SYNC_CAP_SECONDS,
                    ", ".join(stuck) or "unknown store",
                )
                break
            await asyncio.sleep(0.5)

    await asyncio.sleep(0.5)
    selector = locators["shipstation_reload"]
    buttons = page.locator(selector)
    try:
        count = await buttons.count()
    except Exception as exc:
        count = 0
        logger.warning("ShipStation refresh button could not be counted: %s", exc)
    logger.info("ShipStation refresh buttons matched: %s (%s)", count, selector)
    if count != 1:
        logger.warning("Expected 1 ShipStation refresh button, found %s", count)
    if count < 1:
        logger.warning("ShipStation reload was not clicked: button missing")
    else:
        try:
            button = buttons.first
            await button.wait_for(state="visible", timeout=5000)
            await button.evaluate("el => el.click()")
        except Exception as exc:
            logger.warning("ShipStation reload was not clicked: %s", exc)
    try:
        await page.locator('[data-column="order-number"]').first.wait_for(
            state="visible",
            timeout=15000,
        )
    except Exception:
        logger.info("ShipStation order rows were not visible after reload")
    await asyncio.sleep(0.75)


async def _grid_rows(page: Page) -> list[dict[str, str]]:
    try:
        snapshot = await page.evaluate(_GRID_JS)
    except Exception as exc:
        logger.info("ShipStation grid unreadable: %s", exc)
        return []
    rows: list[dict[str, str]] = []
    for item in snapshot or []:
        rows.append(
            {
                "id": str(item.get("id") or ""),
                "order": str(item.get("order") or "").strip(),
                "sku": str(item.get("sku") or "").strip(),
                "buyer": str(item.get("buyer") or "").strip(),
            }
        )
    return rows


async def _footer_counts(page: Page, footer_selector: str) -> Optional[tuple[int, int]]:
    footer = page.locator(footer_selector).first
    try:
        if await footer.count() == 0:
            return None
        text = await footer.inner_text()
    except Exception:
        return None
    return parse_footer_total(text)


async def _scroll_grid(page: Page) -> None:
    await page.evaluate(
        """() => {
            const cell = document.querySelector('[data-column="order-number"]');
            let node = cell ? cell.parentElement : null;
            while (node) {
                const style = getComputedStyle(node);
                if (
                    /(auto|scroll)/.test(style.overflowY)
                    && node.scrollHeight > node.clientHeight + 20
                ) {
                    node.scrollTop = node.scrollHeight;
                    return;
                }
                node = node.parentElement;
            }
        }"""
    )


async def _click_next_page(page: Page) -> bool:
    try:
        clicked = await page.evaluate(
            """() => {
                const footer = document.querySelector('[class*="paging-footer"]');
                if (!footer) return false;
                const button = [...footer.querySelectorAll('button')].find((el) => {
                    const label = (
                        el.getAttribute('aria-label') || el.innerText || ''
                    ).toLowerCase();
                    return label.includes('next') && !el.disabled;
                });
                if (!button) return false;
                button.click();
                return true;
            }"""
        )
    except Exception as exc:
        logger.info("ShipStation next page was not clicked: %s", exc)
        return False
    return bool(clicked)


async def _collect_grid(page: Page, footer_selector: str) -> list[dict[str, str]]:
    """Scroll, and follow next page when the footer total is larger than the rows."""
    collected: dict[str, dict[str, str]] = {}
    for _page_index in range(20):
        await _scroll_grid(page)
        await asyncio.sleep(0.2)
        for row in await _grid_rows(page):
            if row["id"]:
                collected[row["id"]] = row
        counts = await _footer_counts(page, footer_selector)
        if counts is None or len(collected) >= counts[1]:
            break
        if not await _click_next_page(page):
            logger.info(
                "ShipStation grid has %s of %s orders and no next page",
                len(collected),
                counts[1],
            )
            break
        try:
            await page.locator('[data-column="order-number"]').first.wait_for(
                state="visible",
                timeout=10000,
            )
        except Exception:
            break
        await asyncio.sleep(0.3)
    return list(collected.values())


async def _group_is_open(page: Page, row_id: str) -> bool:
    """True when a combined row is already expanded.

    ShipStation marks that grid row with a visible- or expanded- class.
    Clicking the expander again would collapse it.
    """
    try:
        opened = await page.evaluate(
            """(rowId) => {
                const cell = document.querySelector(
                    '[data-row-id="' + CSS.escape(rowId) + '"]'
                );
                let node = cell;
                while (node) {
                    const cls = String(node.className || '');
                    if (cls.includes('expandable')) {
                        return cls.includes('visible-') || cls.includes('expanded-');
                    }
                    node = node.parentElement;
                }
                return false;
            }""",
            row_id,
        )
    except Exception as exc:
        logger.info("ShipStation expand state unreadable for %s: %s", row_id, exc)
        return False
    return bool(opened)


async def _expand_row(page: Page, row_id: str) -> bool:
    """Open a combined row. The expander is a button-link, so match it by class."""
    if await _group_is_open(page, row_id):
        return False
    try:
        clicked = await page.evaluate(
            """(rowId) => {
                const cells = document.querySelectorAll(
                    '[data-row-id="' + CSS.escape(rowId) + '"]'
                );
                for (const cell of cells) {
                    for (const button of cell.querySelectorAll('button')) {
                        const label = (
                            button.getAttribute('aria-label') || ''
                        ).toLowerCase();
                        const cls = String(button.className || '');
                        if (
                            cls.includes('expander')
                            || cls.includes('expand')
                            || label.includes('expand')
                            || cls.includes('chevron')
                        ) {
                            button.click();
                            return true;
                        }
                    }
                }
                return false;
            }""",
            row_id,
        )
    except Exception as exc:
        logger.info("ShipStation expand failed for %s: %s", row_id, exc)
        return False
    return bool(clicked)


async def _has_order_button(page: Page, row_id: str) -> bool:
    button = page.locator(
        f'[data-column="order-number"][data-row-id="{row_id}"] button'
    )
    try:
        return await button.count() > 0
    except Exception:
        return False


async def _open_items(page: Page, row_id: str, items_selector: str) -> str:
    if not await _has_order_button(page, row_id):
        raise RuntimeError(f"store order {row_id} has no order button")
    button = page.locator(
        f'[data-column="order-number"][data-row-id="{row_id}"] button'
    ).first
    await button.click(timeout=5000)
    section = page.locator(items_selector).first
    await section.wait_for(state="visible", timeout=15000)
    try:
        return str(await section.inner_text() or "")
    except Exception:
        return ""


async def _close_items(page: Page, items_selector: str) -> None:
    try:
        await page.keyboard.press("Escape")
        await page.locator(items_selector).first.wait_for(state="hidden", timeout=3000)
    except Exception:
        if "/order/" in (page.url or ""):
            try:
                await page.go_back(wait_until="domcontentloaded", timeout=15000)
            except Exception as exc:
                logger.info("Could not leave the ShipStation order: %s", exc)
    try:
        await page.locator('[data-column="order-number"]').first.wait_for(
            state="visible",
            timeout=10000,
        )
    except Exception:
        logger.info("ShipStation order list did not return after the drawer")


async def _reveal_children(
    page: Page,
    row_id: str,
    seen: set[str],
) -> list[dict[str, str]]:
    """Expand a closed group and return rows that were not already collected."""
    if await _group_is_open(page, row_id):
        return []
    expanded = await _expand_row(page, row_id)
    if not expanded:
        return []
    await asyncio.sleep(0.3)
    found: list[dict[str, str]] = []
    for child in await _grid_rows(page):
        child_id = child["id"]
        if not child_id or child_id in seen:
            continue
        seen.add(child_id)
        found.append(child)
    return found


def _note(on_status: Optional[Callable[[str], None]], label: str) -> None:
    if on_status is not None:
        on_status(label)


async def _lines_from_drawer(
    page: Page,
    row_id: str,
    order_id: str,
    buyer: str,
    sku: str,
    items_selector: str,
) -> Optional[list[tuple[str, str, str, int]]]:
    """Read SKUs from the order drawer. None means the drawer was not opened."""
    if not await _has_order_button(page, row_id):
        return None
    try:
        text = await _open_items(page, row_id, items_selector)
    except Exception as exc:
        logger.warning("Could not open store order %s: %s", order_id, exc)
        return None
    parsed = [
        item for item in parse_shipment_items(text) if is_usable_sku(item[0])
    ]
    if is_usable_sku(sku) and parsed:
        matched = [item for item in parsed if item[0].casefold() == sku.casefold()]
        chosen = matched or parsed
    else:
        chosen = parsed
    if not chosen and is_usable_sku(sku):
        chosen = [(sku, 1, True)]
    lines: list[tuple[str, str, str, int]] = []
    for item_sku, qty, assumed in chosen:
        if assumed:
            logger.info(
                "Store order %s SKU %s quantity assumed as 1",
                order_id,
                item_sku,
            )
        lines.append((order_id, buyer, item_sku, qty))
    try:
        await _close_items(page, items_selector)
    except Exception as exc:
        logger.info("Drawer close for %s: %s", order_id, exc)
    return lines


async def scrape_store_orders(
    page: Page,
    locators: dict[str, str],
    *,
    need_qty: bool,
    on_status: Optional[Callable[[str], None]] = None,
) -> list[tuple[str, str, str, int]]:
    """Read store orders (no hyphen).

    A combined shipment is one grid row labeled '(Multiple)' with a SKU
    cell of '(2 Items)'. That text is not a SKU. Child orders are their
    own rows once the group is expanded, and eBay orders among them are
    left to the eBay scrape.
    """
    rows = await _collect_grid(page, locators["shipstation_footer"])
    lines: list[tuple[str, str, str, int]] = []
    items_selector = locators["shipstation_items"]
    pending = list(rows)
    seen = {row["id"] for row in pending if row["id"]}
    index = 0
    while index < len(pending):
        row = pending[index]
        index += 1
        order_id = row["order"]
        buyer = row["buyer"] or "?"
        sku = row["sku"]

        kind = classify_store_row(order_id, sku)
        if kind == "combined":
            _note(on_status, "Reading combined order")
            pending.extend(await _reveal_children(page, row["id"], seen))
            logger.info(
                "Skipping combined ShipStation row %s (%s)",
                order_id or "?",
                sku or "no sku",
            )
            continue

        if kind == "skip":
            continue

        _note(on_status, f"Reading store order {order_id}")
        if kind == "multi":
            drawer = await _lines_from_drawer(
                page,
                row["id"],
                order_id,
                buyer,
                "",
                items_selector,
            )
            if drawer:
                lines.extend(drawer)
                continue
            children = await _reveal_children(page, row["id"], seen)
            harvested = [
                (order_id, buyer, child["sku"], 1)
                for child in children
                if is_usable_sku(child["sku"])
            ]
            if harvested:
                lines.extend(harvested)
                if need_qty:
                    logger.info(
                        "Store order %s item quantities assumed as 1",
                        order_id,
                    )
                continue
            logger.warning(
                "ShipStation order %s showed %s and no SKU could be read",
                order_id,
                sku or "(no sku)",
            )
            continue

        if not need_qty:
            lines.append((order_id, buyer, sku, 1))
            continue

        drawer = await _lines_from_drawer(
            page,
            row["id"],
            order_id,
            buyer,
            sku,
            items_selector,
        )
        if drawer:
            lines.extend(drawer)
            continue
        lines.append((order_id, buyer, sku, 1))
        logger.info("Store order %s quantity assumed as 1", order_id)
    logger.info("ShipStation store orders: %s line(s)", len(lines))
    return lines
