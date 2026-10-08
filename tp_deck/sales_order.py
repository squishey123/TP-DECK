"""Find a Razor sales order and close detail tabs around batch serial entry."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Browser, Page

from tp_deck.automation_engine import (
    _disconnect_browsers,
    _iter_pages,
    _url_matches,
    connect_over_cdp,
    erp_wait_pair,
)
from tp_deck.chrome_launcher import capture_foreground, restore_foreground
from tp_deck.locators import load_locators

logger = logging.getLogger("tpdeck")

_SO_PREFIX = re.compile(r"^so-", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def order_key(value: str) -> str:
    """Compare service order numbers with one optional SO- prefix removed."""
    text = re.sub(r"\s+", " ", (value or "")).strip()
    text = _SO_PREFIX.sub("", text, count=1)
    return text.casefold()


def order_labels_match(typed: str, label: str) -> bool:
    """True when the label is the typed order, or contains that token alone."""
    wanted = order_key(typed)
    if not wanted:
        return False
    if order_key(label) == wanted:
        return True
    for token in _TOKEN.findall(label or ""):
        if order_key(token) == wanted:
            return True
    return False


def _order_id(url: str) -> str:
    query = parse_qs(urlparse(url or "").query)
    values = query.get("orderId") or query.get("orderid") or []
    return str(values[0]).strip() if values else ""


def _is_detail_url(url: str, detail_pattern: str, list_pattern: str) -> bool:
    if list_pattern and _url_matches(url, list_pattern):
        return False
    return bool(detail_pattern) and _url_matches(url, detail_pattern)


def _detail_pages(
    browser: Browser,
    detail_pattern: str,
    list_pattern: str,
) -> list[Page]:
    return [
        page
        for page in _iter_pages(browser)
        if _is_detail_url(page.url or "", detail_pattern, list_pattern)
    ]


async def _input_field(page: Page, selector: str, timeout_ms: int):
    root = page.locator(selector).first
    try:
        await root.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:
        raise RuntimeError(
            f"Timed out waiting for the sales order filter ({selector}). "
            "Open the sales order list and check locators.json."
        ) from exc
    inner = root.locator("input:not([type='hidden'])")
    if await inner.count() > 0:
        field = inner.first
        await field.wait_for(state="visible", timeout=timeout_ms)
        return field
    return root


async def read_service_order(page: Page, selector: str, timeout_ms: int) -> str:
    label = page.locator(selector).first
    try:
        await label.wait_for(state="visible", timeout=timeout_ms)
        text = await label.inner_text()
    except Exception as exc:
        logger.info("Service order label not read on %s: %s", page.url, exc)
        return ""
    return re.sub(r"\s+", " ", str(text or "")).strip()


async def wait_for_service_order_match(
    page: Page,
    selector: str,
    expected: str,
    timeout_ms: int,
) -> str:
    """Poll until the service order label matches. Always wait at least 15s."""
    wait_ms = max(15000, int(timeout_ms))
    deadline = time.monotonic() + (wait_ms / 1000.0)
    label = page.locator(selector).first
    last = ""
    while time.monotonic() < deadline:
        try:
            if await label.count() > 0 and await label.is_visible():
                text = await label.inner_text()
                last = re.sub(r"\s+", " ", str(text or "")).strip()
                if order_labels_match(expected, last):
                    return last
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return last


async def _browser_context(browser: Browser):
    for context in browser.contexts:
        return context
    raise RuntimeError("The ERP browser has no page to open a sales order in.")


async def _open_list_page(
    browser: Browser,
    locators: dict[str, str],
    timeout_ms: int,
) -> Page:
    pattern = locators["sales_orders_url_pattern"]
    matches = [
        page
        for page in _iter_pages(browser)
        if _url_matches(page.url or "", pattern)
    ]
    if matches:
        logger.info("Sales order list tab: %s", matches[0].url)
        return matches[0]
    url = locators["sales_orders_url"]
    context = await _browser_context(browser)
    foreground = capture_foreground()
    try:
        page = await context.new_page()
        logger.info("Opening sales order list: %s", url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            raise RuntimeError(
                f"Could not open the sales order list ({exc})."
            ) from exc
        return page
    finally:
        restore_foreground(*foreground)


_GRID_ROWS_JS = """
() => {
    const grid = document.querySelector('#ag-grid');
    if (!grid) return [];
    const onScreen = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (rect.width <= 0 || rect.height <= 0) return false;
        if (el.getAttribute('aria-hidden') === 'true') return false;
        const view = el.closest('.ag-pinned-left-cols-viewport')
            || el.closest('.ag-center-cols-viewport')
            || el.closest('.ag-body-viewport');
        if (!view) return true;
        const viewRect = view.getBoundingClientRect();
        return rect.bottom > viewRect.top + 1
            && rect.top < viewRect.bottom - 1
            && rect.right > viewRect.left
            && rect.left < viewRect.right;
    };
    const groups = new Map();
    const parts = grid.querySelectorAll(
        '.ag-pinned-left-cols-container .ag-row, .ag-center-cols-container .ag-row'
    );
    for (const el of parts) {
        const rowId = el.getAttribute('row-id') || '';
        const rowIndex = el.getAttribute('row-index') || '';
        const key = rowId || ('index:' + rowIndex);
        let group = groups.get(key);
        if (!group) {
            group = { rowId, visible: false, cells: [] };
            groups.set(key, group);
        }
        if (onScreen(el)) group.visible = true;
        for (const cell of el.querySelectorAll('.ag-cell')) {
            const text = (
                cell.innerText || cell.getAttribute('title') || ''
            ).replace(/\\s+/g, ' ').trim();
            if (text) group.cells.push(text);
        }
        const rowText = (el.innerText || '').replace(/\\s+/g, ' ').trim();
        if (rowText) group.cells.push(rowText);
    }
    return Array.from(groups.values());
}
"""


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _matched_rows(
    snapshot: list[dict[str, Any]],
    original: str,
    *,
    visible_only: bool,
) -> list[tuple[str, str, list[str]]]:
    """Rows whose pinned or center cells contain the order number."""
    found: list[tuple[str, str, list[str]]] = []
    for item in snapshot or []:
        if visible_only and not item.get("visible"):
            continue
        cells = [str(cell or "").strip() for cell in (item.get("cells") or [])]
        cells = [cell for cell in cells if cell]
        matched = next(
            (cell for cell in cells if order_labels_match(original, cell)),
            "",
        )
        if not matched:
            continue
        found.append((str(item.get("rowId") or "").strip(), matched, cells))
    return found


def _row_target(page: Page, row_id: str, row_selector: str) -> Any:
    if row_id:
        return page.locator(
            f'#ag-grid .ag-row[row-id="{_css_attr(row_id)}"]'
        ).first
    return page.locator(row_selector).first


async def _scroll_row_into_view(page: Page, row_id: str) -> None:
    if not row_id:
        return
    try:
        await page.evaluate(
            """(rowId) => {
                const row = document.querySelector(
                    '#ag-grid .ag-row[row-id="' + CSS.escape(rowId) + '"]'
                );
                if (row) {
                    row.scrollIntoView({ block: 'center', inline: 'nearest' });
                }
            }""",
            row_id,
        )
    except Exception as exc:
        logger.info("Could not scroll sales order row %s: %s", row_id, exc)


async def _read_order_grid(page: Page) -> list[dict[str, Any]]:
    try:
        snapshot = await page.evaluate(_GRID_ROWS_JS)
    except Exception as exc:
        logger.info("Sales order grid snapshot failed: %s", exc)
        return []
    return list(snapshot or [])


def _visible_cells(snapshot: list[dict[str, Any]]) -> list[list[str]]:
    shown: list[list[str]] = []
    for item in snapshot or []:
        if not item.get("visible"):
            continue
        cells = [
            str(cell or "").strip()
            for cell in (item.get("cells") or [])
            if str(cell or "").strip()
        ]
        if cells:
            shown.append(cells)
    return shown


async def _wait_for_filter_rows(
    page: Page,
    row_selector: str,
    original: str,
    timeout_ms: int,
) -> tuple[int, str, Optional[Any]]:
    """Wait until a pinned or center row shows the order number.

    The order number lives in the pinned column, which is a separate row
    element from the center cells. One matching row is enough, even when
    other rows are still in the grid.
    """
    loading = page.locator("#ag-grid .ag-overlay-loading-wrapper")
    deadline = time.monotonic() + (max(1, timeout_ms) / 1000.0)
    last_snapshot: list[dict[str, Any]] = []
    announced = False
    scrolled: set[str] = set()
    while time.monotonic() < deadline:
        try:
            if await loading.count() > 0 and await loading.is_visible():
                await asyncio.sleep(0.25)
                continue
        except Exception:
            pass
        last_snapshot = await _read_order_grid(page)
        visible_matches = _matched_rows(
            last_snapshot, original, visible_only=True
        )
        if len(visible_matches) == 1:
            row_id, matched, _cells = visible_matches[0]
            logger.info("Sales order row ready for %s: %s", original, matched)
            return 1, matched, _row_target(page, row_id, row_selector)
        if len(visible_matches) > 1:
            return len(visible_matches), "", None
        hidden = _matched_rows(last_snapshot, original, visible_only=False)
        if len(hidden) == 1:
            row_id = hidden[0][0]
            if row_id and row_id not in scrolled:
                scrolled.add(row_id)
                await _scroll_row_into_view(page, row_id)
        if not announced and _visible_cells(last_snapshot):
            logger.info(
                "Sales order grid showing %s row(s); waiting for %s",
                len(_visible_cells(last_snapshot)),
                original,
            )
            announced = True
        await asyncio.sleep(0.25)

    visible_matches = _matched_rows(last_snapshot, original, visible_only=True)
    if len(visible_matches) == 1:
        row_id, matched, _cells = visible_matches[0]
        logger.info("Sales order row ready for %s: %s", original, matched)
        return 1, matched, _row_target(page, row_id, row_selector)
    matches = _matched_rows(last_snapshot, original, visible_only=False)
    if len(matches) == 1:
        row_id, matched, _cells = matches[0]
        await _scroll_row_into_view(page, row_id)
        logger.info("Sales order row ready for %s: %s", original, matched)
        return 1, matched, _row_target(page, row_id, row_selector)
    if len(matches) > 1:
        return len(matches), "", None
    visible = [item for item in last_snapshot if item.get("visible")]
    if len(visible) == 1:
        text = " ".join(
            str(cell or "").strip()
            for cell in (visible[0].get("cells") or [])
            if str(cell or "").strip()
        )
        return 1, text, None
    logger.info(
        "No sales order row showed %s (%s visible row(s)): %s",
        original,
        len(_visible_cells(last_snapshot)),
        _visible_cells(last_snapshot)[:8],
    )
    return 0, "", None


async def _click_order_row(row: Any, order_number: str) -> None:
    """Click the cell that shows the order number, including a pinned column."""
    row_id = str(await row.get_attribute("row-id") or "").strip()
    if row_id:
        cells = row.page.locator(
            f'#ag-grid .ag-row[row-id="{_css_attr(row_id)}"] .ag-cell'
        )
    else:
        cells = row.locator(".ag-cell")
    count = await cells.count()
    for index in range(count):
        cell = cells.nth(index)
        try:
            text = await cell.inner_text()
        except Exception:
            continue
        if order_labels_match(order_number, text):
            await cell.click()
            return
    await row.locator(".ag-cell").first.click()


async def _filter_order_list(
    page: Page,
    order_number: str,
    locators: dict[str, str],
    *,
    timeout_ms: int,
    overlay_ms: int,
    submit_key: str,
) -> Any:
    field = await _input_field(page, locators["sales_order_filter"], timeout_ms)
    query = order_number.strip()
    await field.click()
    await field.fill("")
    await field.fill(query)
    key = (submit_key or "").strip()
    if key:
        await field.press(key)
    overlay = page.locator("#ag-grid .ag-overlay-loading-wrapper")
    try:
        await overlay.wait_for(state="visible", timeout=overlay_ms)
        await overlay.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        await asyncio.sleep(0.4)
    count, text, row = await _wait_for_filter_rows(
        page,
        locators["sales_order_rows"],
        order_number,
        timeout_ms,
    )
    if row is not None:
        return row
    if count > 1:
        raise RuntimeError(f"More than one sales order matched {order_number}.")
    if count == 1 and text:
        raise RuntimeError(
            f"The order on screen does not match {order_number} ({text})."
        )
    raise RuntimeError(f"No sales order matched {order_number}.")


async def _wait_for_detail_page(
    browser: Browser,
    list_page: Page,
    known_urls: set[str],
    detail_pattern: str,
    list_pattern: str,
    timeout_ms: int,
) -> Page:
    deadline = time.monotonic() + (max(1, timeout_ms) / 1000.0)
    while time.monotonic() < deadline:
        if _is_detail_url(list_page.url or "", detail_pattern, list_pattern):
            return list_page
        for page in _iter_pages(browser):
            url = page.url or ""
            if url in known_urls:
                continue
            if _is_detail_url(url, detail_pattern, list_pattern):
                logger.info("Sales order opened in a new tab: %s", url)
                return page
        await asyncio.sleep(0.25)
    raise RuntimeError("The sales order page did not open.")


async def _open_order_from_list(
    browser: Browser,
    order_number: str,
    locators: dict[str, str],
    *,
    timeout_ms: int,
    action_ms: int,
    overlay_ms: int,
    submit_key: str,
) -> Page:
    list_page = await _open_list_page(browser, locators, action_ms)
    list_page.set_default_timeout(action_ms)
    row = await _filter_order_list(
        list_page,
        order_number,
        locators,
        timeout_ms=timeout_ms,
        overlay_ms=overlay_ms,
        submit_key=submit_key,
    )
    known_urls = {(page.url or "") for page in _iter_pages(browser)}
    foreground = capture_foreground()
    try:
        await _click_order_row(row, order_number)
        return await _wait_for_detail_page(
            browser,
            list_page,
            known_urls,
            locators["sales_order_detail_pattern"],
            locators["sales_orders_url_pattern"],
            timeout_ms,
        )
    finally:
        restore_foreground(*foreground)


async def _close_other_details(
    browser: Browser,
    keeper: Page,
    detail_pattern: str,
    list_pattern: str,
) -> None:
    keeper_url = keeper.url or ""
    keeper_order_id = _order_id(keeper_url)
    for page in list(_detail_pages(browser, detail_pattern, list_pattern)):
        page_url = page.url or ""
        if page == keeper or page_url == keeper_url:
            continue
        if keeper_order_id and _order_id(page_url) == keeper_order_id:
            continue
        url = page_url
        try:
            await page.close()
        except Exception as exc:
            logger.info("Could not close sales order tab %s: %s", url, exc)
        else:
            logger.info("Closed sales order tab %s", url)


async def prepare_sales_order(
    browser: Browser,
    order_number: str,
    *,
    timeout_ms: int,
    action_ms: int,
    overlay_ms: int,
    submit_key: str,
) -> Page:
    """Return the detail tab to type into, after closing every other detail tab."""
    locators = load_locators()
    detail_pattern = locators["sales_order_detail_pattern"]
    list_pattern = locators["sales_orders_url_pattern"]
    typed = (order_number or "").strip()
    details = _detail_pages(browser, detail_pattern, list_pattern)
    keeper: Optional[Page] = None

    if typed:
        for page in details:
            label = await read_service_order(
                page,
                locators["service_order_label"],
                timeout_ms,
            )
            if order_labels_match(typed, label):
                keeper = page
                logger.info("Reusing sales order tab %s (%s)", page.url, label)
                break
        if keeper is None:
            keeper = await _open_order_from_list(
                browser,
                typed,
                locators,
                timeout_ms=timeout_ms,
                action_ms=action_ms,
                overlay_ms=overlay_ms,
                submit_key=submit_key,
            )
        label = await wait_for_service_order_match(
            keeper,
            locators["service_order_label"],
            typed,
            timeout_ms,
        )
        if not order_labels_match(typed, label):
            raise RuntimeError(
                f"Opened order does not match {typed}"
                + (f" ({label})." if label else ".")
            )
    else:
        if not details:
            raise RuntimeError("No sales order tab is open.")
        if len(details) > 1:
            raise RuntimeError("Type a service order number.")
        keeper = details[0]
        logger.info("Using the open sales order tab: %s", keeper.url)

    await _close_other_details(browser, keeper, detail_pattern, list_pattern)
    return keeper


async def _close_all_details(browser: Browser, locators: dict[str, str]) -> None:
    detail_pattern = locators["sales_order_detail_pattern"]
    list_pattern = locators["sales_orders_url_pattern"]
    for page in list(_detail_pages(browser, detail_pattern, list_pattern)):
        url = page.url or ""
        try:
            await page.close()
        except Exception as exc:
            logger.info("Could not close sales order tab %s: %s", url, exc)
        else:
            logger.info("Closed sales order tab %s", url)


async def _clear_order_filter(
    browser: Browser,
    locators: dict[str, str],
    submit_key: str,
) -> None:
    """Empty the sales-order search so the restored list is not stuck on one order."""
    list_pattern = locators["sales_orders_url_pattern"]
    detail_pattern = locators["sales_order_detail_pattern"]
    selector = locators["sales_order_filter"]
    foreground = capture_foreground()
    try:
        for page in _iter_pages(browser):
            url = page.url or ""
            if not _url_matches(url, list_pattern):
                continue
            if _is_detail_url(url, detail_pattern, list_pattern):
                continue
            field = page.locator(selector).first
            try:
                await field.fill("", timeout=3000)
                key = (submit_key or "").strip()
                if key:
                    await field.press(key)
            except Exception as exc:
                logger.info("Could not clear the sales order filter on %s: %s", url, exc)
            else:
                logger.info("Cleared sales order filter on %s", url)
    finally:
        restore_foreground(*foreground)


async def shutdown_workday(settings: dict[str, Any]) -> None:
    """Close leftover order tabs, clear the order filter, then close Chromium.

    Playwright's browser.close() on a CDP connection only disconnects. Browser.close
    over CDP asks Chromium to write the session and exit.
    """
    from playwright.async_api import async_playwright

    from tp_deck.chrome_launcher import _debug_port_ready

    locators = load_locators()
    mode = str(settings.get("mode", "single")).lower()
    ports = [int(settings.get("ebay_port", 9222))]
    if mode == "dual":
        erp_port = int(settings.get("erp_port", 9223))
        if erp_port not in ports:
            ports.append(erp_port)
    ready = [port for port in ports if _debug_port_ready(port)]
    if not ready:
        logger.info("No Chromium debug port is open")
        return

    submit_key = str(settings.get("erp_submit_key") or "Enter")
    playwright = await async_playwright().start()
    browsers: list[Browser] = []
    try:
        for port in ready:
            browsers.append(await connect_over_cdp(playwright, port))
        for browser in browsers:
            await _close_all_details(browser, locators)
            await _clear_order_filter(browser, locators, submit_key)
        for browser in browsers:
            try:
                session = await browser.new_browser_cdp_session()
                await session.send("Browser.close")
            except Exception as exc:
                raise RuntimeError(f"Could not close Chromium ({exc}).") from exc
            logger.info("Chromium closed")
    finally:
        for browser in browsers:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await playwright.stop()
        except Exception:
            pass


async def close_detail_tab(settings: dict[str, Any], url: str) -> None:
    """Reconnect and close the detail tab for this order id, if it is still open."""
    order_id = _order_id(url)
    if not order_id:
        logger.info("No orderId to close in %s", url)
        return
    locators = load_locators()
    detail_pattern = locators["sales_order_detail_pattern"]
    list_pattern = locators["sales_orders_url_pattern"]
    mode = str(settings.get("mode", "single")).lower()
    ebay_port = int(settings.get("ebay_port", 9222))
    erp_port = int(settings.get("erp_port", 9223))

    from playwright.async_api import async_playwright

    playwright = None
    ebay_browser = None
    erp_browser = None
    try:
        playwright = await async_playwright().start()
        ebay_browser = await connect_over_cdp(playwright, ebay_port)
        browser = ebay_browser
        if mode == "dual":
            erp_browser = await connect_over_cdp(playwright, erp_port)
            browser = erp_browser
        for page in _detail_pages(browser, detail_pattern, list_pattern):
            if _order_id(page.url or "") != order_id:
                continue
            tab_url = page.url
            await page.close()
            logger.info("Closed sales order tab after delay: %s", tab_url)
            return
        logger.info("Sales order tab already closed: %s", url)
    finally:
        await _disconnect_browsers(playwright, erp_browser, ebay_browser)
