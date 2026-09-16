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


def _require_selector(selectors: dict[str, Any], key: str) -> str:
    raw = str(selectors.get(key, "") or "").strip()
    if not raw:
        raise RuntimeError(
            f"Missing CSS selector '{key}' in settings.json. "
            "Open Settings and fill the selector fields."
        )
    return raw


async def extract_text(page: Page, selector: str, *, timeout_ms: int) -> str:
    """Read visible text (or input value) from the first matching element."""
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=timeout_ms)
    text = await locator.inner_text()
    cleaned = _clean_text(text)
    if cleaned:
        return cleaned
    value = await locator.input_value()
    return _clean_text(value)


async def scrape_ebay_order(
    page: Page,
    selectors: dict[str, Any],
    *,
    timeout_ms: int,
) -> tuple[str, str]:
    order_sel = _require_selector(selectors, "ebay_order_id")
    sku_sel = _require_selector(selectors, "ebay_sku")
    order_id = await extract_text(page, order_sel, timeout_ms=timeout_ms)
    sku = await extract_text(page, sku_sel, timeout_ms=timeout_ms)
    if not order_id or not sku:
        raise RuntimeError(
            f"eBay scrape returned empty values (order={order_id!r}, sku={sku!r})"
        )
    logger.info("eBay scraped order=%s sku=%s", order_id, sku)
    return order_id, sku


async def lookup_erp_location(
    page: Page,
    sku: str,
    selectors: dict[str, Any],
    *,
    timeout_ms: int,
    submit_key: str,
) -> str:
    """Type SKU into ERP, optional submit key, wait for and scrape Location."""
    input_sel = _require_selector(selectors, "erp_sku_input")
    location_sel = _require_selector(selectors, "erp_location")

    field = page.locator(input_sel).first
    await field.wait_for(state="visible", timeout=timeout_ms)
    await field.click()
    await field.fill("")
    await field.fill(sku)

    key = (submit_key or "").strip()
    if key:
        await field.press(key)

    location = await extract_text(page, location_sel, timeout_ms=timeout_ms)
    if not location:
        raise RuntimeError("ERP location scrape returned empty text")
    logger.info("ERP location scraped: %s", location)
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
    focused eBay tab → Order/SKU → ERP SKU lookup → Location → clipboard.
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

        order_id, sku = await scrape_ebay_order(
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

        location = await lookup_erp_location(
            erp_page,
            sku,
            selectors,
            timeout_ms=timeout_ms,
            submit_key=submit_key,
        )
        await asyncio.sleep(0)

        output = format_output(order_id, sku, location)
        copy_to_clipboard(output)
        logger.info("Automation success: %s", output)
        return f"Success — {output}"

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
