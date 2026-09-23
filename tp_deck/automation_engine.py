"""Playwright CDP automation — connect, scrape, clipboard (v1)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

from tp_deck.pick_list import (
    build_pick_list,
    is_excluded_pick_location,
    parse_quantity,
)
from tp_deck.settings_manager import update_settings
from tp_deck.sku_cache import SkuLocationCache

logger = logging.getLogger("tpdeck")

UNKNOWN_LOCATION = "Unknown"
_DEFAULT_EBAY_QTY_SEL = "div.quantity strong"


def _cdp_endpoint(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


async def connect_over_cdp(playwright: Playwright, port: int) -> Browser:
    """Attach to an already-running Chrome via CDP. Never launches a browser."""
    endpoint = _cdp_endpoint(port)
    logger.info("Connecting over CDP: %s", endpoint)
    try:
        browser = await playwright.chromium.connect_over_cdp(endpoint)
    except Exception as exc:
        raise RuntimeError(
            f"CDP connect failed on port {port}. "
            f"Open TP DECK again so Chromium can start on that port. ({exc})"
        ) from exc
    logger.info(
        "CDP connected (port=%s, contexts=%s)",
        port,
        len(browser.contexts),
    )
    return browser


async def _page_has_focus(page: Page) -> bool:
    try:
        return bool(await page.evaluate("() => document.hasFocus()"))
    except Exception as exc:
        logger.debug("hasFocus() failed on %s: %s", page.url, exc)
        return False


def _url_matches(url: str, pattern: str) -> bool:
    if not pattern:
        return True
    return pattern.lower() in (url or "").lower()


def _iter_pages(browser: Browser):
    for context in browser.contexts:
        for page in context.pages:
            url = page.url or ""
            if url.startswith("chrome://") or url.startswith("devtools://"):
                continue
            yield page


def _matching_pages(browser: Browser, url_pattern: str) -> list[Page]:
    return [
        page
        for page in _iter_pages(browser)
        if _url_matches(page.url or "", url_pattern)
    ]


async def find_page_by_url(
    browser: Browser,
    url_pattern: str,
    *,
    label: str = "tab",
) -> Optional[Page]:
    """
    Find a page by URL substring. document.hasFocus() is only used when
    more than one tab matches, so a unique URL does not need to be focused.
    """
    matches = _matching_pages(browser, url_pattern)
    if not matches:
        logger.warning("No %s matching %r", label, url_pattern or "*")
        return None

    if len(matches) == 1:
        page = matches[0]
        logger.info("%s matched by unique URL: %s", label, page.url)
        return page

    focused: list[Page] = []
    for page in matches:
        is_focused = await _page_has_focus(page)
        logger.debug(
            "Candidate %s url=%s focused=%s",
            label,
            page.url,
            is_focused,
        )
        if is_focused:
            focused.append(page)

    if focused:
        page = focused[0]
        logger.info(
            "Focused %s selected among %s URL matches: %s",
            label,
            len(matches),
            page.url,
        )
        return page

    raise RuntimeError(
        f"Multiple {label}s match {url_pattern!r} ({len(matches)} tabs). "
        "Click the tab you want, then Execute again."
    )


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


_LETTER_DASH_LOCATION = re.compile(r"^[A-Za-z]-")


def pick_best_location(
    locations: list[str],
    *,
    blacklist: Optional[list[str]] = None,
    deprioritize: Optional[list[str]] = None,
    deprioritize_prefixes: Optional[list[str]] = None,
    lowest_priority: Optional[list[str]] = None,
) -> str:
    """
    Choose one location from many serial rows:
    - Drop blacklisted names entirely (e.g. Tech, Internal)
    - Prefer highest frequency among remaining rows
    - Then Andrei & Alex Office, then PR*, then HQ last
    - On a frequency tie, favor codes like A-12 (letter + dash)
    """
    cleaned = [_clean_text(x) for x in locations]
    cleaned = [x for x in cleaned if x]
    if not cleaned:
        raise RuntimeError("No ERP location values found in the grid")

    blocked = {_clean_text(x).lower() for x in (blacklist or []) if _clean_text(x)}
    soft_names = {
        _clean_text(x).lower() for x in (deprioritize or []) if _clean_text(x)
    }
    lowest_names = {
        _clean_text(x).lower() for x in (lowest_priority or []) if _clean_text(x)
    }
    prefixes = [
        _clean_text(x).upper()
        for x in (deprioritize_prefixes or ["PR"])
        if _clean_text(x)
    ]

    def _is_blocked(loc: str) -> bool:
        return loc.lower() in blocked

    def _is_prefix_deprioritized(loc: str) -> bool:
        upper = loc.upper()
        return any(upper.startswith(prefix) for prefix in prefixes)

    def _is_named_deprioritized(loc: str) -> bool:
        return loc.lower() in soft_names

    def _is_lowest(loc: str) -> bool:
        return loc.lower() in lowest_names

    kept = [loc for loc in cleaned if not _is_blocked(loc)]
    if not kept:
        raise RuntimeError(
            "All ERP locations were blacklisted "
            f"(values={sorted(set(cleaned))}, blacklist={sorted(blocked)})"
        )

    from collections import Counter

    counts = Counter(kept)
    names = list(counts.keys())
    preferred = [
        loc
        for loc in names
        if not _is_prefix_deprioritized(loc)
        and not _is_named_deprioritized(loc)
        and not _is_lowest(loc)
    ]
    named_soft = [
        loc
        for loc in names
        if _is_named_deprioritized(loc) and not _is_lowest(loc)
    ]
    prefix_soft = [
        loc
        for loc in names
        if _is_prefix_deprioritized(loc)
        and not _is_named_deprioritized(loc)
        and not _is_lowest(loc)
    ]
    lowest = [loc for loc in names if _is_lowest(loc)]
    pool = preferred or named_soft or prefix_soft or lowest

    max_freq = max(counts[loc] for loc in pool)
    top = [loc for loc in pool if counts[loc] == max_freq]
    letter_dash = [loc for loc in top if _LETTER_DASH_LOCATION.match(loc)]
    chosen = sorted(letter_dash or top)[0]
    logger.info(
        "Location vote counts=%s blocked=%s pool=%s picked=%s",
        dict(counts),
        sorted(blocked),
        pool,
        chosen,
    )
    return chosen


def _require_selector(selectors: dict[str, Any], key: str) -> str:
    raw = str(selectors.get(key, "") or "").strip()
    if not raw:
        raise RuntimeError(
            f"Missing CSS selector '{key}' in settings.json. "
            "Open Settings and fill the selector fields."
        )
    return raw


async def extract_text(
    page: Page,
    selector: str,
    *,
    timeout_ms: int,
    label: str = "element",
) -> str:
    """Read visible text (or input value) from the first matching element."""
    locator = page.locator(selector).first
    try:
        await locator.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:
        raise RuntimeError(
            f"Timed out waiting for {label} ({selector}). "
            "Open the correct page, then recopy the selector in Settings."
        ) from exc
    text = await locator.inner_text()
    cleaned = _clean_text(text)
    if cleaned:
        return cleaned
    value = await locator.input_value()
    return _clean_text(value)


async def collect_grid_column_texts(
    page: Page,
    cell_selector: str,
    *,
    timeout_ms: int,
) -> list[str]:
    """
    Read a column from an ag-Grid, scrolling the body so virtualized rows
    are included. Dedupes by row-id so the same row is not counted twice.
    """
    cells = page.locator(cell_selector)
    try:
        await cells.first.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        logger.info(
            "ERP location column not visible within %sms (%s)",
            timeout_ms,
            cell_selector,
        )
        return []

    viewport = page.locator(
        "#ag-grid-inventory-detail .ag-body-viewport"
    ).first
    by_row: dict[str, str] = {}

    async def _harvest() -> None:
        batch = await cells.evaluate_all(
            """(els) => els.map((el) => {
                const row = el.closest('.ag-row');
                const key = row
                    ? (row.getAttribute('row-id')
                        || row.getAttribute('row-index')
                        || '')
                    : '';
                const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                return { key, text };
            })"""
        )
        for i, item in enumerate(batch or []):
            text = _clean_text(str(item.get("text") or ""))
            if not text:
                continue
            key = str(item.get("key") or "") or f"anon-{i}-{text}"
            by_row[key] = text

    await _harvest()

    if await viewport.count() > 0:
        for _ in range(80):
            at_end = await viewport.evaluate(
                """(el) => {
                    const before = el.scrollTop;
                    el.scrollTop = Math.min(
                        el.scrollTop + el.clientHeight,
                        el.scrollHeight
                    );
                    return (
                        el.scrollTop === before
                        || el.scrollTop + el.clientHeight >= el.scrollHeight - 2
                    );
                }"""
            )
            await asyncio.sleep(0.05)
            await _harvest()
            if at_end:
                break
        # Reset scroll for the operator's view.
        await viewport.evaluate("(el) => { el.scrollTop = 0; }")

    return list(by_row.values())


async def _scroll_ebay_order_list(page: Page) -> None:
    """Load any lazy-rendered Seller Hub rows by scrolling the list container."""
    previous = -1
    for _ in range(25):
        count = await page.locator("[id$='__order-info']").count()
        if count > 0 and count == previous:
            break
        previous = count
        await page.evaluate(
            """() => {
                const el = document.querySelector('[id$="__order-info"]');
                let scroller = document.scrollingElement;
                let node = el ? el.parentElement : null;
                while (node) {
                    const style = getComputedStyle(node);
                    const scrollable =
                        /(auto|scroll)/.test(style.overflowY)
                        && node.scrollHeight > node.clientHeight + 20;
                    if (scrollable) {
                        scroller = node;
                        break;
                    }
                    node = node.parentElement;
                }
                if (scroller) {
                    scroller.scrollTop = scroller.scrollHeight;
                }
                window.scrollBy(0, window.innerHeight);
            }"""
        )
        await asyncio.sleep(0.2)


async def scrape_ebay_orders(
    page: Page,
    selectors: dict[str, Any],
    *,
    timeout_ms: int,
) -> list[tuple[str, str, str, int]]:
    """
    Collect every order, buyer, SKU, and ordered qty on the matched /sh/ord list
    before any ERP work.
    """
    sku_sel = _require_selector(selectors, "ebay_sku")
    order_sel = _require_selector(selectors, "ebay_order_id")
    buyer_sel = str(selectors.get("ebay_buyer") or "").strip()
    if not buyer_sel:
        buyer_sel = "div.user-details.details span > button > span:nth-child(1)"
    qty_sel = (
        str(selectors.get("ebay_qty") or "").strip() or _DEFAULT_EBAY_QTY_SEL
    )

    try:
        await page.locator("[id$='__order-info']").first.wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception as exc:
        raise RuntimeError(
            "No eBay order rows found on /sh/ord. Open the Seller Hub order list."
        ) from exc

    await _scroll_ebay_order_list(page)

    diagnostics = await page.evaluate(
        """() => {
            const infos = [...document.querySelectorAll('[id$="__order-info"]')]
                .map((el) => el.id);
            const items = [...document.querySelectorAll('[id*="__item-info"]')]
                .map((el) => el.id);
            return { infos, items };
        }"""
    )
    logger.info(
        "eBay DOM: %s order-info, %s item-info (no clicks on the list)",
        len(diagnostics.get("infos") or []),
        len(diagnostics.get("items") or []),
    )
    logger.debug("order-info ids=%s", diagnostics.get("infos"))

    raw_rows = await page.evaluate(
        """({ skuSel, buyerSel, qtySel }) => {
            const rows = [];
            const seen = new Set();
            const buyers = {};
            const add = (orderId, buyer, sku, qtyText, lineKey) => {
                const key = lineKey || (orderId + '|' + sku);
                if (!orderId || !sku || seen.has(key)) return;
                seen.add(key);
                rows.push({
                    orderId,
                    buyer: buyer || '',
                    sku,
                    qtyText: qtyText || '1',
                });
            };
            const parseHostId = (el) => {
                const host = el && el.closest && el.closest('[id^="orderid_"]');
                if (!host || !host.id) return null;
                const m = host.id.match(/^orderid_(.+?)__/);
                return m ? m[1] : null;
            };
            const readBuyer = (info) => {
                if (!info || !buyerSel) return '';
                const el = info.querySelector(buyerSel);
                return ((el && el.innerText) || '').replace(/\\s+/g, ' ').trim();
            };
            const readQty = (root) => {
                if (!root) return '1';
                const textOf = (el) => {
                    if (!el) return '';
                    const strong = (el.tagName === 'STRONG')
                        ? el
                        : el.querySelector('strong');
                    return ((strong && strong.innerText) || el.innerText || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                };
                if (qtySel) {
                    const fromSel = textOf(root.querySelector(qtySel));
                    if (fromSel) return fromSel;
                }
                const fallback = root.querySelector(
                    'div.quantity strong, td.item-quantity, div.quantity'
                );
                return textOf(fallback) || '1';
            };

            document.querySelectorAll('[id$="__order-info"]').forEach((info) => {
                const idMatch = info.id.match(/^orderid_(.+?)__/);
                if (!idMatch) return;
                let orderId = idMatch[1];
                const details = info.querySelector('.order-details');
                const detailText = details ? details.innerText : '';
                const orderMatch = detailText.match(/\\d{2}-\\d{5}-\\d{5}/);
                if (orderMatch) orderId = orderMatch[0];
                const buyer = readBuyer(info);
                buyers[orderId] = buyer;

                const items = document.querySelectorAll(
                    '[id^="orderid_' + orderId + '__item-info"]'
                );
                items.forEach((item) => {
                    const qtyText = readQty(item);
                    item.querySelectorAll(skuSel).forEach((el) => {
                        const skuText = (el.innerText || '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        add(
                            orderId,
                            buyer,
                            skuText,
                            qtyText,
                            (item.id || (orderId + '|item')) + '|' + skuText
                        );
                    });
                });
                if (!items.length) {
                    info.querySelectorAll(skuSel).forEach((el) => {
                        add(
                            orderId,
                            buyer,
                            (el.innerText || '').replace(/\\s+/g, ' ').trim(),
                            readQty(info),
                            orderId + '|info|' + ((el.innerText || '').trim())
                        );
                    });
                }
            });
            return rows;
        }""",
        {"skuSel": sku_sel, "buyerSel": buyer_sel, "qtySel": qty_sel},
    )

    pairs: list[tuple[str, str, str, int]] = []
    for row in raw_rows or []:
        order_id = _clean_text(str(row.get("orderId") or ""))
        buyer = _clean_text(str(row.get("buyer") or ""))
        sku = _clean_text(str(row.get("sku") or ""))
        qty = parse_quantity(str(row.get("qtyText") or "1"))
        match = re.search(r"\d{2}-\d{5}-\d{5}", order_id)
        if match:
            order_id = match.group(0)
        if order_id and sku:
            pairs.append((order_id, buyer, sku, qty))

    if not pairs:
        raise RuntimeError(
            f"eBay list scrape found orders but no SKUs "
            f"(sku selector {sku_sel!r}, order selector {order_sel!r})."
        )

    logger.info(
        "eBay scrape complete: %s line(s) across %s order(s)",
        len(pairs),
        len({order_id for order_id, _, _, _ in pairs}),
    )
    for order_id, buyer, sku, qty in pairs:
        logger.info("  queued %s — %s — %sx %s", order_id, buyer or "?", qty, sku)
    return pairs


async def _erp_sku_field(page: Page, input_sel: str, timeout_ms: int):
    """Resolve the SKU filter input even if the selector points at a wrapper div."""
    root = page.locator(input_sel).first
    try:
        await root.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:
        raise RuntimeError(
            f"Timed out waiting for ERP SKU input ({input_sel}). "
            "Focus Inventory Detail, then recopy the filter box selector."
        ) from exc

    inner = root.locator("input:not([type='hidden'])")
    if await inner.count() > 0:
        field = inner.first
        await field.wait_for(state="visible", timeout=timeout_ms)
        return field
    return root


async def lookup_erp_location(
    page: Page,
    sku: str,
    selectors: dict[str, Any],
    *,
    timeout_ms: int,
    submit_key: str,
    location_blacklist: Optional[list[str]] = None,
    location_deprioritize: Optional[list[str]] = None,
    location_deprioritize_prefixes: Optional[list[str]] = None,
    location_lowest_priority: Optional[list[str]] = None,
) -> str:
    """Type SKU into ERP, wait for grid rows, vote on the best Location.

    Kit SKUs, unknown SKUs, and empty inventory grids return Unknown
    instead of aborting the rest of the run.
    """
    input_sel = _require_selector(selectors, "erp_sku_input")
    location_sel = _require_selector(selectors, "erp_location")

    field = await _erp_sku_field(page, input_sel, timeout_ms)
    try:
        await field.click()
        await field.fill("")
        await field.fill(sku)

        key = (submit_key or "").strip()
        if key:
            await field.press(key)

        overlay = page.locator(
            "#ag-grid-inventory-detail .ag-overlay-loading-wrapper"
        )
        no_rows = page.locator(
            "#ag-grid-inventory-detail .ag-overlay-no-rows-wrapper"
        )
        try:
            await overlay.wait_for(state="visible", timeout=1500)
            await overlay.wait_for(state="hidden", timeout=timeout_ms)
        except Exception:
            await asyncio.sleep(0.4)

        try:
            if await no_rows.is_visible():
                logger.warning(
                    "ERP grid has no rows for sku=%s; using %s",
                    sku,
                    UNKNOWN_LOCATION,
                )
                return UNKNOWN_LOCATION
        except Exception:
            pass

        values = await collect_grid_column_texts(
            page,
            location_sel,
            timeout_ms=timeout_ms,
        )
        if not values:
            logger.warning(
                "ERP location timeout/empty for sku=%s; using %s",
                sku,
                UNKNOWN_LOCATION,
            )
            return UNKNOWN_LOCATION

        location = pick_best_location(
            values,
            blacklist=location_blacklist,
            deprioritize=location_deprioritize,
            deprioritize_prefixes=location_deprioritize_prefixes,
            lowest_priority=location_lowest_priority,
        )
        logger.info(
            "ERP location selected for sku=%s: %s (from %s row(s))",
            sku,
            location,
            len(values),
        )
        return location
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "ERP location lookup failed for sku=%s: %s — using %s",
            sku,
            exc,
            UNKNOWN_LOCATION,
        )
        return UNKNOWN_LOCATION


def format_output(order_id: str, buyer: str, sku: str, location: str) -> str:
    name = buyer.strip() or "?"
    return f"{order_id} - {name} - {sku} - {location}"


def copy_to_clipboard(text: str) -> None:
    """Copy via Qt clipboard (safe on the qasync/Qt thread)."""
    from PySide6.QtGui import QGuiApplication

    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        raise RuntimeError("Qt clipboard unavailable")
    clipboard.setText(text)
    logger.info("Copied to clipboard: %s", text)


async def _resolve_erp_page(
    *,
    mode: str,
    ebay_browser: Browser,
    erp_browser: Optional[Browser],
    erp_pattern: str,
) -> Page:
    if mode == "dual":
        assert erp_browser is not None
        page = await find_page_by_url(
            erp_browser,
            erp_pattern,
            label="ERP tab",
        )
        if page is None:
            raise RuntimeError(
                f"Dual mode: no ERP tab on the ERP browser"
                + (f" matching {erp_pattern!r}" if erp_pattern else "")
                + "."
            )
        return page

    if not erp_pattern:
        raise RuntimeError(
            "Single mode requires erp_url_pattern in settings "
            "so TP DECK can find the ERP tab in the same Chrome."
        )
    page = await find_page_by_url(
        ebay_browser,
        erp_pattern,
        label="ERP tab (same browser)",
    )
    if page is None:
        raise RuntimeError(
            f"Single mode: no ERP tab matching {erp_pattern!r}."
        )
    return page


def _ebay_refresh_hold_seconds(settings: dict[str, Any]) -> float:
    if not bool(settings.get("ebay_refresh_hold_enabled", True)):
        return 0.0
    try:
        minutes = float(settings.get("ebay_refresh_hold_minutes", 5))
    except (TypeError, ValueError):
        minutes = 5.0
    return max(0.0, minutes) * 60.0


def _skip_ebay_refresh(settings: dict[str, Any]) -> bool:
    """True when Seller Hub was reloaded inside the configured hold window."""
    hold = _ebay_refresh_hold_seconds(settings)
    if hold <= 0:
        return False
    try:
        last = float(settings.get("ebay_last_refresh_epoch") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if last <= 0:
        return False
    elapsed = time.time() - last
    if elapsed < hold:
        logger.info(
            "Skipping eBay refresh; last refresh %.0fs ago (hold %.0fs)",
            elapsed,
            hold,
        )
        return True
    return False


def _mark_ebay_refreshed() -> None:
    update_settings(ebay_last_refresh_epoch=time.time())


async def _refresh_ebay_order_page(page: Page, timeout_ms: int) -> None:
    """Reload Seller Hub so the scrape includes newly arrived orders."""
    reload_timeout = max(int(timeout_ms), 30000)
    logger.info("Refreshing eBay order tab: %s", page.url)
    try:
        await page.reload(wait_until="domcontentloaded", timeout=reload_timeout)
    except Exception as exc:
        raise RuntimeError(
            f"eBay refresh failed ({exc}). Keep Seller Hub open and try again."
        ) from exc
    try:
        await page.wait_for_load_state("load", timeout=reload_timeout)
    except Exception:
        logger.info("eBay load event not observed; waiting for order rows")
    try:
        await page.locator("[id$='__order-info']").first.wait_for(
            state="visible", timeout=reload_timeout
        )
    except Exception as exc:
        raise RuntimeError(
            "eBay order list did not appear after refresh."
        ) from exc
    logger.info("eBay order tab refreshed")
    _mark_ebay_refreshed()


def _unknown_count(location_by_sku: dict[str, str]) -> int:
    return sum(
        1
        for loc in location_by_sku.values()
        if str(loc).strip().casefold() == UNKNOWN_LOCATION.casefold()
    )


async def run_automation(
    settings: dict[str, Any],
    *,
    job: str = "orders",
) -> str:
    """
    job=orders: clipboard Order - Buyer - SKU - Location
    job=picklist: combined qty pick walk list
    """
    mode = str(settings.get("mode", "single")).lower()
    ebay_port = int(settings.get("ebay_port", 9222))
    erp_port = int(settings.get("erp_port", 9223))
    ebay_pattern = str(settings.get("ebay_url_pattern", "ebay.com/sh/ord"))
    erp_pattern = str(settings.get("erp_url_pattern", ""))
    timeout_ms = int(settings.get("wait_timeout_ms", 10000))
    submit_key = str(settings.get("erp_submit_key", "Enter"))
    selectors = settings.get("selectors") or {}
    if not isinstance(selectors, dict):
        raise RuntimeError("settings.selectors must be an object")

    playwright: Optional[Playwright] = None
    ebay_browser: Optional[Browser] = None
    erp_browser: Optional[Browser] = None

    try:
        playwright = await async_playwright().start()
        ebay_browser = await connect_over_cdp(playwright, ebay_port)

        ebay_page = await find_page_by_url(
            ebay_browser,
            ebay_pattern,
            label="eBay order tab",
        )
        if ebay_page is None:
            raise RuntimeError(
                f"No eBay tab matching {ebay_pattern!r}."
            )

        if not _skip_ebay_refresh(settings):
            await _refresh_ebay_order_page(ebay_page, timeout_ms)
        lines_in = await scrape_ebay_orders(
            ebay_page,
            selectors,
            timeout_ms=timeout_ms,
        )
        await asyncio.sleep(0)

        cache_enabled = bool(settings.get("cache_enabled", True))
        cache = SkuLocationCache(
            ttl_hours=float(settings.get("cache_ttl_hours", 12))
        )
        location_by_sku: dict[str, str] = {}
        unique_skus = list(dict.fromkeys(sku for _, _, sku, _ in lines_in))
        misses: list[str] = []
        for sku in unique_skus:
            cached = cache.get(sku) if cache_enabled else None
            if cached:
                location_by_sku[sku] = cached
                logger.info("SKU cache hit %s → %s", sku, cached)
            else:
                misses.append(sku)

        if not cache_enabled:
            logger.info("SKU location cache disabled; looking up all SKUs")

        if misses:
            if mode == "dual":
                erp_browser = await connect_over_cdp(playwright, erp_port)

            erp_page = await _resolve_erp_page(
                mode=mode,
                ebay_browser=ebay_browser,
                erp_browser=erp_browser,
                erp_pattern=erp_pattern,
            )
            await asyncio.sleep(0)

            for sku in misses:
                location_by_sku[sku] = await lookup_erp_location(
                    erp_page,
                    sku,
                    selectors,
                    timeout_ms=timeout_ms,
                    submit_key=submit_key,
                    location_blacklist=settings.get("location_blacklist"),
                    location_deprioritize=settings.get("location_deprioritize"),
                    location_deprioritize_prefixes=settings.get(
                        "location_deprioritize_prefixes"
                    ),
                    location_lowest_priority=settings.get(
                        "location_lowest_priority"
                    ),
                )
                if cache_enabled:
                    cache.put(sku, location_by_sku[sku])
                await asyncio.sleep(0)
            if cache_enabled:
                cache.save()
        else:
            logger.info("All SKUs served from cache; skipping ERP lookup")
            if cache_enabled:
                cache.save()

        hits = len(unique_skus) - len(misses)
        unknowns = _unknown_count(location_by_sku)
        unknown_bit = f", {unknowns} unknown" if unknowns else ""
        job_name = str(job or "orders").lower()

        if job_name == "picklist":
            combined: dict[str, list] = {}
            for _order_id, _buyer, sku, qty in lines_in:
                loc = location_by_sku.get(sku, UNKNOWN_LOCATION)
                if sku not in combined:
                    combined[sku] = [0, loc]
                combined[sku][0] += max(1, int(qty))
                combined[sku][1] = loc
            exclude_names = settings.get("pick_list_exclude_locations")
            exclude_prefixes = settings.get("pick_list_exclude_prefixes")
            exclude_misc = bool(settings.get("pick_list_exclude_misc", True))
            pick_items = []
            skipped = 0
            for sku, (qty, loc) in combined.items():
                if is_excluded_pick_location(
                    loc,
                    exclude_names=exclude_names,
                    exclude_prefixes=exclude_prefixes,
                    exclude_misc=exclude_misc,
                ):
                    skipped += 1
                    logger.info(
                        "Pick list skipped %s at %s (not a pick location)",
                        sku,
                        loc,
                    )
                    continue
                pick_items.append((sku, loc, qty))
            output = build_pick_list(
                pick_items,
                exclude_names=exclude_names,
                exclude_prefixes=exclude_prefixes,
                exclude_misc=exclude_misc,
            )
            copy_to_clipboard(output)
            logger.info("Pick list success:\n%s", output)
            skipped_bit = f", {skipped} skipped" if skipped else ""
            return (
                f"Success — pick list {len(pick_items)} SKU(s), "
                f"{len(misses)} lookup(s), {hits} cache hit(s)"
                f"{unknown_bit}{skipped_bit}"
            )

        lines_out: list[str] = []
        for order_id, buyer, sku, _qty in lines_in:
            lines_out.append(
                format_output(
                    order_id,
                    buyer,
                    sku,
                    location_by_sku.get(sku, UNKNOWN_LOCATION),
                )
            )

        output = "\n".join(lines_out)
        copy_to_clipboard(output)
        logger.info("Automation success: %s", output)
        return (
            f"Success — {len(lines_out)} line(s), "
            f"{len(misses)} lookup(s), {hits} cache hit(s){unknown_bit}"
        )

    except asyncio.CancelledError:
        logger.info("Automation cancelled")
        raise
    finally:
        # Disconnect Playwright from CDP only — do not kill the user's Chrome.
        for browser in (erp_browser, ebay_browser):
            if browser is None:
                continue
            try:
                await browser.close()
            except Exception as exc:
                logger.debug("Browser disconnect: %s", exc)
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as exc:
                logger.debug("Playwright stop: %s", exc)
