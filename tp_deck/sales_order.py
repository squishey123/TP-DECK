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
    page = await context.new_page()
    logger.info("Opening sales order list: %s", url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as exc:
        raise RuntimeError(
            f"Could not open the sales order list ({exc})."
        ) from exc
    return page


async def _wait_for_filter_rows(
    page: Page,
    row_selector: str,
    original: str,
    timeout_ms: int,
    previous_text: str,
    previous_count: int,
) -> tuple[int, str, Optional[Any]]:
    rows = page.locator(row_selector)
    deadline = time.monotonic() + (max(1, timeout_ms) / 1000.0)
    seen: Optional[int] = None
    stable = 0
    count = 0
    while time.monotonic() < deadline:
        count = await rows.count()
        if count == seen:
            stable += 1
        else:
            seen = count
            stable = 0
        unchanged = count == previous_count and count != 1
        if stable >= 2 and count != 1 and not unchanged:
            return count, "", None
        if stable >= 2 and count == 1:
            text = await rows.first.locator(".ag-cell").first.inner_text()
            cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
            stale = (
                bool(previous_text)
                and cleaned == previous_text
                and not order_labels_match(original, cleaned)
            )
            if not stale:
                if order_labels_match(original, cleaned):
                    return count, cleaned, rows.first
                return count, cleaned, None
        await asyncio.sleep(0.25)
    return count, "", None


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
    rows = page.locator(locators["sales_order_rows"])
    previous_count = await rows.count()
    previous_text = ""
    if previous_count == 1:
        previous_text = re.sub(
            r"\s+",
            " ",
            str(await rows.first.locator(".ag-cell").first.inner_text() or ""),
        ).strip()
    query = order_number.strip()
    attempts = [query]
    if not query.upper().startswith("SO-"):
        attempts.append(f"SO-{query}")

    last_count = 0
    last_text = ""
    for attempt in attempts:
        await field.click()
        await field.fill("")
        await field.fill(attempt)
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
            previous_text,
            previous_count,
        )
        last_count, last_text = count, text
        if row is not None:
            return row
        if count != 0:
            break
        if attempt != attempts[-1]:
            logger.info(
                "No row for %r; trying %r",
                attempt,
                attempts[-1],
            )
    if last_count > 1:
        raise RuntimeError(f"More than one sales order matched {order_number}.")
    if last_count == 1 and last_text:
        raise RuntimeError(
            f"The order on screen does not match {order_number} ({last_text})."
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
    await row.locator(".ag-cell").first.click()
    return await _wait_for_detail_page(
        browser,
        list_page,
        known_urls,
        locators["sales_order_detail_pattern"],
        locators["sales_orders_url_pattern"],
        timeout_ms,
    )


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
        label = await read_service_order(
            keeper,
            locators["service_order_label"],
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
