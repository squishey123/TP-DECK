"""Playwright CDP automation — connect, scrape, clipboard (v1)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from playwright.async_api import Browser, Page, Playwright, async_playwright

logger = logging.getLogger("tpdeck")


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
            f"Is Chrome running with --remote-debugging-port={port}? ({exc})"
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


async def find_focused_page(
    browser: Browser,
    url_pattern: str,
    *,
    label: str = "tab",
) -> Optional[Page]:
    """
    Scan every CDP context/page. Match URL pattern AND document.hasFocus()
    so extraction only targets the tab the user is actively viewing.
    """
    candidates = 0
    for context in browser.contexts:
        for page in context.pages:
            url = page.url or ""
            if url.startswith("chrome://") or url.startswith("devtools://"):
                continue
            if not _url_matches(url, url_pattern):
                continue
            candidates += 1
            focused = await _page_has_focus(page)
            logger.debug(
                "Candidate %s url=%s focused=%s",
                label,
                url,
                focused,
            )
            if focused:
                logger.info("Focused %s found: %s", label, url)
                return page

    logger.warning(
        "No focused %s matching %r (%s URL candidate(s))",
        label,
        url_pattern or "*",
        candidates,
    )
    return None


async def find_page_by_url(
    browser: Browser,
    url_pattern: str,
    *,
    label: str = "tab",
    prefer_focused: bool = True,
) -> Optional[Page]:
    """Find a page by URL substring; optionally prefer the focused match."""
    if prefer_focused:
        focused = await find_focused_page(browser, url_pattern, label=label)
        if focused is not None:
            return focused

    for context in browser.contexts:
        for page in context.pages:
            url = page.url or ""
            if url.startswith("chrome://") or url.startswith("devtools://"):
                continue
            if _url_matches(url, url_pattern):
                logger.info("%s matched by URL: %s", label, url)
                return page

    logger.warning("No %s matching %r", label, url_pattern or "*")
    return None


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
    except Exception as exc:
        raise RuntimeError(
            f"Timed out waiting for ERP location column ({cell_selector})."
        ) from exc

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
) -> list[tuple[str, str]]:
    """
    Collect every order + SKU on the focused /sh/ord list before any ERP work.
    Returns (order_id, sku) pairs, including multiple SKUs per order.
    """
    sku_sel = _require_selector(selectors, "ebay_sku")
    order_sel = _require_selector(selectors, "ebay_order_id")

    try:
        await page.locator("[id$='__order-info']").first.wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception as exc:
        raise RuntimeError(
            "No eBay order rows found on /sh/ord. Focus the order list tab."
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
        """(skuSel) => {
            const rows = [];
            const seen = new Set();
            const add = (orderId, sku) => {
                const key = orderId + '|' + sku;
                if (!orderId || !sku || seen.has(key)) return;
                seen.add(key);
                rows.push({ orderId, sku });
            };
            const parseHostId = (el) => {
                const host = el && el.closest && el.closest('[id^="orderid_"]');
                if (!host || !host.id) return null;
                const m = host.id.match(/^orderid_(.+?)__/);
                return m ? m[1] : null;
            };

            document.querySelectorAll('[id$="__order-info"]').forEach((info) => {
                const idMatch = info.id.match(/^orderid_(.+?)__/);
                if (!idMatch) return;
                let orderId = idMatch[1];
                const details = info.querySelector('.order-details');
                const detailText = details ? details.innerText : '';
                const orderMatch = detailText.match(/\\d{2}-\\d{5}-\\d{5}/);
                if (orderMatch) orderId = orderMatch[0];

                const items = document.querySelectorAll(
                    '[id^="orderid_' + orderId + '__item-info"]'
                );
                items.forEach((item) => {
                    item.querySelectorAll(skuSel).forEach((el) => {
                        add(
                            orderId,
                            (el.innerText || '').replace(/\\s+/g, ' ').trim()
                        );
                    });
                });
                info.querySelectorAll(skuSel).forEach((el) => {
                    add(
                        orderId,
                        (el.innerText || '').replace(/\\s+/g, ' ').trim()
                    );
                });
            });

            document.querySelectorAll(skuSel).forEach((el) => {
                let orderId = parseHostId(el);
                if (!orderId) return;
                const om = String(orderId).match(/\\d{2}-\\d{5}-\\d{5}/);
                if (om) orderId = om[0];
                add(
                    orderId,
                    (el.innerText || '').replace(/\\s+/g, ' ').trim()
                );
            });
            return rows;
        }""",
        sku_sel,
    )

    pairs: list[tuple[str, str]] = []
    for row in raw_rows or []:
        order_id = _clean_text(str(row.get("orderId") or ""))
        sku = _clean_text(str(row.get("sku") or ""))
        match = re.search(r"\d{2}-\d{5}-\d{5}", order_id)
        if match:
            order_id = match.group(0)
        if order_id and sku:
            pairs.append((order_id, sku))

    if not pairs:
        raise RuntimeError(
            f"eBay list scrape found orders but no SKUs "
            f"(sku selector {sku_sel!r}, order selector {order_sel!r})."
        )

    logger.info(
        "eBay scrape complete: %s line(s) across %s order(s)",
        len(pairs),
        len({order_id for order_id, _ in pairs}),
    )
    for order_id, sku in pairs:
        logger.info("  queued %s — %s", order_id, sku)
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
    """Type SKU into ERP, wait for grid rows, vote on the best Location."""
    input_sel = _require_selector(selectors, "erp_sku_input")
    location_sel = _require_selector(selectors, "erp_location")

    field = await _erp_sku_field(page, input_sel, timeout_ms)
    await field.click()
    await field.fill("")
    await field.fill(sku)

    key = (submit_key or "").strip()
    if key:
        await field.press(key)

    overlay = page.locator("#ag-grid-inventory-detail .ag-overlay-loading-wrapper")
    try:
        await overlay.wait_for(state="visible", timeout=1500)
        await overlay.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        await asyncio.sleep(0.4)

    values = await collect_grid_column_texts(
        page,
        location_sel,
        timeout_ms=timeout_ms,
    )
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


def format_output(order_id: str, sku: str, location: str) -> str:
    return f"{order_id} - {sku} - {location}"


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
            prefer_focused=True,
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
        prefer_focused=False,
    )
    if page is None:
        raise RuntimeError(
            f"Single mode: no ERP tab matching {erp_pattern!r}."
        )
    return page


async def run_automation(settings: dict[str, Any]) -> str:
    """
    Full v1 pipeline:
    focused eBay list → all Order/SKU lines → ERP lookups → clipboard.
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

        ebay_page = await find_focused_page(
            ebay_browser,
            ebay_pattern,
            label="eBay order tab",
        )
        if ebay_page is None:
            raise RuntimeError(
                f"No focused eBay tab matching {ebay_pattern!r}. "
                "Click the Seller Hub order tab, then Execute again."
            )

        lines_in = await scrape_ebay_orders(
            ebay_page,
            selectors,
            timeout_ms=timeout_ms,
        )
        await asyncio.sleep(0)

        if mode == "dual":
            erp_browser = await connect_over_cdp(playwright, erp_port)

        erp_page = await _resolve_erp_page(
            mode=mode,
            ebay_browser=ebay_browser,
            erp_browser=erp_browser,
            erp_pattern=erp_pattern,
        )
        await asyncio.sleep(0)

        location_by_sku: dict[str, str] = {}
        lines_out: list[str] = []
        for order_id, sku in lines_in:
            if sku not in location_by_sku:
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
            lines_out.append(
                format_output(order_id, sku, location_by_sku[sku])
            )
            await asyncio.sleep(0)

        output = "\n".join(lines_out)
        copy_to_clipboard(output)
        logger.info("Automation success: %s", output)
        return (
            f"Success — {len(lines_out)} line(s), "
            f"{len(location_by_sku)} SKU lookup(s)"
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
