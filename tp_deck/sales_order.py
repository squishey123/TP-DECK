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
from tp_deck.chrome_launcher import (
    capture_foreground,
    close_listening_browsers,
    restore_foreground,
)
from tp_deck.locators import load_locators

logger = logging.getLogger("tpdeck")

_SO_PREFIX = re.compile(r"^so-", re.IGNORECASE)
_EBAY_ORDER = re.compile(r"\d{2}-\d{5}-\d{5}")
_DIGITS = re.compile(r"^\d+$")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def order_key(value: str) -> str:
    """Compare service order numbers with one optional SO- prefix removed."""
    text = re.sub(r"\s+", " ", (value or "")).strip()
    text = _SO_PREFIX.sub("", text, count=1)
    return text.casefold()


def normalize_order_number(value: str) -> str:
    """Trim spaces and rewrite any so-/So-/SO- prefix as SO-."""
    text = re.sub(r"\s+", " ", (value or "")).strip()
    if not _SO_PREFIX.match(text):
        return text
    return "SO-" + _SO_PREFIX.sub("", text, count=1)


def classify_order(value: str) -> str:
    """Return service, ebay, or store.

    service: an SO number, with or without the SO- prefix (SO-12345, SO12345).
    ebay: a Seller Hub id such as 12-34567-89012.
    store: digits only. A bare sales-order number looks the same, so that
    search tries the store order column and then the sales-order column.
    """
    text = re.sub(r"\s+", "", (value or "").strip())
    if not text:
        return ""
    if _SO_PREFIX.match(text) or re.fullmatch(r"so\d+", text, flags=re.IGNORECASE):
        return "service"
    if _EBAY_ORDER.fullmatch(text):
        return "ebay"
    if _DIGITS.fullmatch(text):
        return "store"
    return "service"


_SOURCE_COLUMN = re.compile(r"\bsource(?:\s*type)?\b", re.IGNORECASE)
_NOISE_COLUMN = re.compile(
    r"\b(?:status|date|customer|sku|qty|quantity|rep|warehouse|"
    r"ship|tracking|amount|total|price|tax|balance)\b",
    re.IGNORECASE,
)
_ORDERISH = re.compile(
    r"order|reference|\bpo\b|p\.o\.|external|ebay|e-bay|web|store|magento",
    re.IGNORECASE,
)


def _column_text(name: str, col_id: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", name or "").strip()
    return text, str(col_id or "")


def _is_source_column(name: str, col_id: str) -> bool:
    text, ident = _column_text(name, col_id)
    return _SOURCE_COLUMN.search(f"{text} {ident}") is not None


def _noise_column(name: str) -> bool:
    text = re.sub(r"\s+", " ", name or "").strip()
    if not text or _ORDERISH.search(text):
        return False
    return _NOISE_COLUMN.search(text) is not None


def service_column_score(name: str, col_id: str) -> int:
    """How strongly this header is the Razor sales-order number.

    Source Type is never a match. External, eBay, reference, and PO columns
    are not the sales-order number either.
    """
    if _is_source_column(name, col_id) or _noise_column(name):
        return 0
    text, ident = _column_text(name, col_id)
    blob = f"{text} {ident}"
    if re.search(
        r"external|ebay|e-bay|reference|marketplace|\bpo\b|p\.o\.|purchase",
        blob,
        flags=re.IGNORECASE,
    ):
        return 0
    score = 0
    if re.fullmatch(r"(?:so|s\.o\.?)\s*#?", text, flags=re.IGNORECASE) or re.search(
        r"\b(?:so|s\.o\.?)\s*(?:#|no\.?|number|num)\b",
        text,
        flags=re.IGNORECASE,
    ):
        score = max(score, 100)
    if re.search(r"\b(?:sales|service)\s+order\b", text, flags=re.IGNORECASE):
        score = max(score, 100)
    if re.search(
        r"sono|so_number|sonumber|salesorder|serviceorder|autoname",
        ident,
        flags=re.IGNORECASE,
    ):
        score = max(score, 90)
    if score == 0 and re.search(
        r"\border\s*(?:#|no\.?|number|num)\b",
        text,
        flags=re.IGNORECASE,
    ):
        score = 40
    if score == 0 and re.fullmatch(r"order", text, flags=re.IGNORECASE):
        score = 35
    return score


def marketplace_column_score(name: str, col_id: str, kind: str) -> int:
    """How strongly this header holds an eBay or store order number.

    Source Type is the channel name (eBay, store, manual) and scores 0.
    """
    if _is_source_column(name, col_id) or _noise_column(name):
        return 0
    if service_column_score(name, col_id) >= 90:
        return 0
    text, ident = _column_text(name, col_id)
    low_text = text.casefold()
    low_id = ident.casefold()
    score = 0
    ebayish = bool(
        re.search(r"ebay|e-bay", low_text) or re.search(r"ebay", low_id)
    )
    if ebayish:
        looks_like_number = bool(
            re.search(r"order|number|\bno\b|#", low_text)
            or re.search(r"order|number|id", low_id)
        )
        if looks_like_number:
            score = max(score, 100 if kind == "ebay" else 60)
        elif kind == "ebay":
            score = max(score, 55)
    if re.search(r"external", low_text) or re.search(r"external", low_id):
        score = max(score, 90)
    if re.search(
        r"web\s*order|store\s*order|magento|e-?commerce|online\s*order|"
        r"channel\s*order|third.party|3rd.party",
        low_text,
    ) or re.search(r"magento|weborder|storeorder|increment|channelorder", low_id):
        score = max(score, 85)
    if "reference" in low_text or "reference" in low_id or low_id in {"reference", "ref"}:
        score = max(score, 80)
    if re.search(r"\bext\b", low_text):
        score = max(score, 75)
    if re.search(r"\bpo\b|p\.o\.|purchase\s*order", low_text) or re.search(
        r"ponumber|po_number|pono|po_no",
        low_id,
    ):
        score = max(score, 70)
    if score == 0 and re.search(
        r"\border\s*(?:#|no\.?|number|num|id)\b",
        low_text,
    ):
        score = 50
    if (
        kind == "store"
        and ebayish
        and score
        and not re.search(r"external|reference|\bpo\b", low_text)
    ):
        score = max(1, score - 40)
    return score


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


async def wait_for_service_order_text(
    page: Page,
    selector: str,
    timeout_ms: int,
) -> str:
    """Poll until the service order label has any text. Always wait at least 15s."""
    wait_ms = max(15000, int(timeout_ms))
    deadline = time.monotonic() + (wait_ms / 1000.0)
    label = page.locator(selector).first
    last = ""
    while time.monotonic() < deadline:
        try:
            if await label.count() > 0 and await label.is_visible():
                text = await label.inner_text()
                last = re.sub(r"\s+", " ", str(text or "")).strip()
                if last:
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
    *,
    accept_lone_row: bool = False,
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
    lone_seen = 0
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
        visible_rows = [item for item in last_snapshot if item.get("visible")]
        if accept_lone_row and len(visible_rows) == 1 and not visible_matches:
            lone_seen += 1
            if lone_seen >= 2:
                row_id = str(visible_rows[0].get("rowId") or "").strip()
                logger.info("Using the only visible order row for %s", original)
                return 1, original, _row_target(page, row_id, row_selector)
        else:
            lone_seen = 0
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
    if accept_lone_row and len(visible) == 1:
        row_id = str(visible[0].get("rowId") or "").strip()
        logger.info("Using the only visible order row for %s", original)
        return 1, original, _row_target(page, row_id, row_selector)
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


_SCROLL_GRID_JS = """
(left) => {
    const root = document.querySelector('#ag-grid');
    if (!root) return false;
    const nodes = [
        '.ag-body-horizontal-scroll-viewport',
        '.ag-center-cols-viewport',
        '.ag-header-viewport',
    ].map((sel) => root.querySelector(sel)).filter(Boolean);
    if (!nodes.length) return false;
    for (const node of nodes) {
        node.scrollLeft = left;
    }
    for (const node of nodes) {
        node.dispatchEvent(new Event('scroll', { bubbles: true }));
    }
    return true;
}
"""

_GRID_SCROLL_METRICS_JS = """
() => {
    const root = document.querySelector('#ag-grid');
    if (!root) return { max: 0, step: 200 };
    const nodes = [
        '.ag-body-horizontal-scroll-viewport',
        '.ag-center-cols-viewport',
        '.ag-header-viewport',
    ].map((sel) => root.querySelector(sel)).filter(Boolean);
    if (!nodes.length) return { max: 0, step: 200 };
    let max = 0;
    let step = 200;
    for (const node of nodes) {
        max = Math.max(max, node.scrollWidth - node.clientWidth);
        const width = node.clientWidth || 0;
        if (width > 40) {
            step = Math.min(step, Math.max(120, Math.floor(width * 0.45)));
        }
    }
    return { max: Math.max(0, max), step };
}
"""

_FILTER_ON_SCREEN_JS = """
(selector) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (rect.width < 4 || rect.height < 4) return false;
    const view = el.closest('.ag-header-viewport')
        || el.closest('.ag-pinned-left-header')
        || el.closest('.ag-header');
    if (!view) return true;
    const viewRect = view.getBoundingClientRect();
    return rect.right > viewRect.left + 2 && rect.left < viewRect.right - 2;
}
"""

# Filter rows have been ag-header-row-floating-filter, ag-header-row-column-filter,
# and ag-header-row-filter. The collector accepts all three, plus any header
# cell that contains a text input.
_FILTER_READY_JS = """
() => {
    const header = document.querySelector('#ag-grid .ag-header');
    if (!header) return false;
    return !!header.querySelector(
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea'
    );
}
"""

_HEADER_DEBUG_JS = """
() => {
    const root = document.querySelector('#ag-grid');
    if (!root) return { grid: false };
    const header = root.querySelector('.ag-header');
    const rows = header
        ? Array.from(header.querySelectorAll('.ag-header-row')).map((row) => row.className)
        : [];
    const sampleInput = header && header.querySelector(
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea'
    );
    const cell = sampleInput && (
        sampleInput.closest('[col-id]') || sampleInput.closest('.ag-header-cell') || sampleInput.parentElement
    );
    return {
        grid: true,
        rows,
        floating: root.querySelectorAll('.ag-floating-filter').length,
        sampleClass: cell ? String(cell.className || '') : '',
        sampleColId: cell ? (cell.getAttribute('col-id') || '') : '',
    };
}
"""

_FILTER_COLUMNS_JS = """
() => {
    const root = document.querySelector('#ag-grid');
    if (!root) return [];
    const header = root.querySelector('.ag-header') || root;
    const filterRowSelector = (
        '.ag-header-row-floating-filter, .ag-header-row-column-filter, .ag-header-row-filter'
    );
    const byId = new Map();
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const touch = (colId) => {
        let row = byId.get(colId);
        if (!row) {
            row = { colId, name: '', hasInput: false, selector: '' };
            byId.set(colId, row);
        }
        return row;
    };
    const inputOf = (cell) => cell.querySelector(
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea'
    );
    const usableInput = (input) => {
        if (!input) return false;
        const type = (input.getAttribute('type') || 'text').toLowerCase();
        return type !== 'hidden' && type !== 'checkbox' && type !== 'radio'
            && type !== 'button' && type !== 'submit';
    };
    const cellSelector = (cell) => {
        const colId = cell.getAttribute('col-id') || '';
        if (colId) {
            return '#ag-grid .ag-header [col-id="' + CSS.escape(colId) + '"]';
        }
        const row = cell.parentElement;
        if (!row) return '';
        const index = Array.prototype.indexOf.call(row.children, cell) + 1;
        if (index < 1) return '';
        let rowClass = '';
        if (row.classList.contains('ag-header-row-floating-filter')) {
            rowClass = '.ag-header-row-floating-filter';
        } else if (row.classList.contains('ag-header-row-column-filter')) {
            rowClass = '.ag-header-row-column-filter';
        } else if (row.classList.contains('ag-header-row-filter')) {
            rowClass = '.ag-header-row-filter';
        } else {
            return '';
        }
        let container = '';
        if (cell.closest('.ag-pinned-left-header')) container = '.ag-pinned-left-header ';
        else if (cell.closest('.ag-pinned-right-header')) container = '.ag-pinned-right-header ';
        else if (cell.closest('.ag-header-viewport')) container = '.ag-header-viewport ';
        return '#ag-grid ' + container + rowClass + ' > :nth-child(' + index + ')';
    };
    const noteInput = (record, cell) => {
        if (!usableInput(inputOf(cell))) return;
        record.hasInput = true;
        if (!record.selector) {
            const sel = cellSelector(cell);
            if (sel) {
                record.selector = sel
                    + ' input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])';
            }
        }
    };

    for (const cell of header.querySelectorAll('[col-id]')) {
        const colId = cell.getAttribute('col-id') || '';
        if (!colId) continue;
        const record = touch(colId);
        const filterish = cell.classList.contains('ag-floating-filter')
            || !!cell.closest(filterRowSelector)
            || usableInput(inputOf(cell));
        if (!filterish) {
            const text = clean(cell.innerText || cell.textContent || '');
            if (text && !record.name) record.name = text;
            continue;
        }
        noteInput(record, cell);
        if (!record.name) {
            const label = clean(cell.getAttribute('aria-label') || '');
            if (label) record.name = label;
        }
    }

    const containers = header.querySelectorAll(
        '.ag-pinned-left-header, .ag-header-viewport, .ag-pinned-right-header, .ag-header'
    );
    containers.forEach((container, containerIndex) => {
        const rows = Array.from(container.querySelectorAll(':scope > .ag-header-row, :scope > .ag-header-container .ag-header-row'));
        if (!rows.length && container.classList.contains('ag-header')) return;
        const filterRow = rows.find((row) => usableInput(inputOf(row)));
        if (!filterRow) return;
        const headerRow = container.querySelector('.ag-header-row-column');
        const filters = Array.from(filterRow.children);
        const headers = headerRow ? Array.from(headerRow.children) : [];
        filters.forEach((cell, index) => {
            const headerCell = headers[index];
            const colId = cell.getAttribute('col-id')
                || (headerCell && headerCell.getAttribute('col-id'))
                || ('idx-' + containerIndex + '-' + index);
            const record = touch(colId);
            if (!record.name && headerCell) {
                const text = clean(headerCell.innerText || headerCell.textContent || '');
                if (text) record.name = text;
            }
            noteInput(record, cell);
        });
    });

    return Array.from(byId.values()).filter((row) => row.name || row.hasInput);
}
"""

_ENSURE_COLUMN_JS = """
(colId) => {
    const root = document.querySelector('#ag-grid');
    if (!root || !colId) return false;
    const nodes = [root];
    const wrap = root.querySelector('.ag-root-wrapper');
    const agRoot = root.querySelector('.ag-root');
    if (wrap) nodes.push(wrap);
    if (agRoot) nodes.push(agRoot);
    let api = null;
    for (const node of nodes) {
        let names = [];
        try { names = Object.getOwnPropertyNames(node); } catch (e) { continue; }
        for (const key of names) {
            if (key !== 'api' && key !== 'gridApi' && key.indexOf('__ag') !== 0) continue;
            let value;
            try { value = node[key]; } catch (e) { continue; }
            if (!value || typeof value !== 'object') continue;
            if (typeof value.ensureColumnVisible === 'function') {
                api = value;
                break;
            }
            if (value.api && typeof value.api.ensureColumnVisible === 'function') {
                api = value.api;
                break;
            }
            if (value.gridApi && typeof value.gridApi.ensureColumnVisible === 'function') {
                api = value.gridApi;
                break;
            }
        }
        if (api) break;
    }
    if (!api) return false;
    try {
        api.ensureColumnVisible(colId);
        return true;
    } catch (e) {
        return false;
    }
}
"""


def _col_filter_selector(col_id: str) -> str:
    safe = _css_attr(col_id)
    return (
        "#ag-grid .ag-header "
        f'[col-id="{safe}"] '
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
    )


def _filter_selector(column: dict[str, Any]) -> str:
    custom = str(column.get("selector") or "").strip()
    if custom:
        return custom
    return _col_filter_selector(str(column.get("colId") or ""))


async def _scroll_grid_to(page: Page, left: float) -> None:
    try:
        await page.evaluate(_SCROLL_GRID_JS, max(0, float(left)))
    except Exception as exc:
        logger.info("Could not scroll the sales order grid: %s", exc)


async def _grid_scroll_metrics(page: Page) -> dict[str, float]:
    try:
        metrics = await page.evaluate(_GRID_SCROLL_METRICS_JS)
    except Exception:
        return {"max": 0.0, "step": 200.0}
    data = metrics or {}
    return {
        "max": float(data.get("max") or 0),
        "step": float(data.get("step") or 200),
    }


async def _filter_on_screen(page: Page, selector: str) -> bool:
    if not selector:
        return False
    try:
        return bool(await page.evaluate(_FILTER_ON_SCREEN_JS, selector))
    except Exception:
        return False


async def _scroll_grid_home(page: Page) -> None:
    """Bring the left edge of the sales order grid back into view."""
    await _scroll_grid_to(page, 0)
    await asyncio.sleep(0.08)


async def _ensure_column_visible(page: Page, col_id: str) -> None:
    """Ask AG Grid to render a column that is outside the current viewport."""
    if not col_id or col_id.startswith("idx-"):
        return
    try:
        shown = await page.evaluate(_ENSURE_COLUMN_JS, col_id)
    except Exception as exc:
        logger.info("Could not show sales order column %s: %s", col_id, exc)
        return
    if shown:
        await asyncio.sleep(0.12)


async def _scroll_filter_into_view(page: Page, selector: str, col_id: str = "") -> None:
    """Scroll sideways until a virtualized filter column is actually rendered."""
    if not selector:
        return
    await _ensure_column_visible(page, col_id)
    if await _filter_on_screen(page, selector):
        return
    await _scroll_grid_to(page, 0)
    await asyncio.sleep(0.12)
    if await _filter_on_screen(page, selector):
        return
    metrics = await _grid_scroll_metrics(page)
    max_left = metrics["max"]
    step = metrics["step"]
    left = 0.0
    for _ in range(40):
        if left >= max_left - 1:
            break
        left = min(max_left, left + max(step, 80))
        await _scroll_grid_to(page, left)
        await asyncio.sleep(0.12)
        if await _filter_on_screen(page, selector):
            logger.info(
                "Scrolled sales order grid to %s to load the filter column",
                int(left),
            )
            return
        metrics = await _grid_scroll_metrics(page)
        max_left = max(max_left, metrics["max"])
    logger.info("Sales order filter column stayed off screen: %s", selector)


def _merge_column(found: dict[str, dict[str, Any]], col: dict[str, Any]) -> None:
    col_id = str(col.get("colId") or "").strip()
    if not col_id:
        return
    current = found.get(col_id)
    name = str(col.get("name") or "").strip()
    has_input = bool(col.get("hasInput"))
    selector = str(col.get("selector") or "").strip()
    if current is None:
        found[col_id] = {
            "colId": col_id,
            "name": name,
            "hasInput": has_input,
            "selector": selector,
        }
        return
    if name and not current.get("name"):
        current["name"] = name
    if has_input:
        current["hasInput"] = True
    if selector and not current.get("selector"):
        current["selector"] = selector


async def _wait_for_header_filters(page: Page, timeout_s: float = 8.0) -> bool:
    """Wait until the sales order header has a filter box, not just column titles."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            ready = bool(await page.evaluate(_FILTER_READY_JS))
        except Exception:
            ready = False
        if ready:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.2)


async def _collect_filter_columns(page: Page) -> list[dict[str, Any]]:
    """Read every floating-filter header, including columns outside the viewport."""
    found: dict[str, dict[str, Any]] = {}
    ready = await _wait_for_header_filters(page)
    if not ready:
        logger.info("Sales order header had no filter boxes before the scan")

    async def absorb() -> None:
        try:
            columns = await page.evaluate(_FILTER_COLUMNS_JS)
        except Exception as exc:
            logger.info("Could not read sales order filter headers: %s", exc)
            return
        for col in columns or []:
            if isinstance(col, dict):
                _merge_column(found, col)

    await _scroll_grid_to(page, 0)
    await asyncio.sleep(0.12)
    await absorb()
    metrics = await _grid_scroll_metrics(page)
    max_left = metrics["max"]
    step = metrics["step"]
    left = 0.0
    for _ in range(40):
        if left >= max_left - 1:
            break
        left = min(max_left, left + max(step, 80))
        await _scroll_grid_to(page, left)
        await asyncio.sleep(0.12)
        await absorb()
        metrics = await _grid_scroll_metrics(page)
        max_left = max(max_left, metrics["max"])
    await _scroll_grid_home(page)
    columns = list(found.values())
    logger.info(
        "Sales order filter columns: %s",
        ", ".join(
            f"{col.get('name') or '?'} ({col.get('colId')})"
            for col in columns
        )
        or "none",
    )
    if not columns:
        try:
            detail = await page.evaluate(_HEADER_DEBUG_JS)
        except Exception as exc:
            detail = str(exc)
        logger.info("Sales order filter scan details: %s", detail)
    return columns


def _best_service_column(columns: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_score = 0
    for col in columns:
        if not col.get("hasInput") or not col.get("colId"):
            continue
        score = service_column_score(str(col.get("name") or ""), str(col.get("colId")))
        if score > best_score:
            best = col
            best_score = score
    return best


def _marketplace_columns(columns: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for col in columns:
        if not col.get("hasInput") or not col.get("colId"):
            continue
        score = marketplace_column_score(
            str(col.get("name") or ""),
            str(col.get("colId")),
            kind,
        )
        if score > 0:
            ranked.append((score, str(col.get("colId")), col))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [col for _score, _col_id, col in ranked[:3]]


def _search_targets(
    kind: str,
    columns: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Columns to try, in order. Source Type is never included."""
    service = _best_service_column(columns)
    if kind == "ebay":
        return [("marketplace", col) for col in _marketplace_columns(columns, "ebay")]
    if kind == "store":
        targets = [("marketplace", col) for col in _marketplace_columns(columns, "store")]
        if service is not None:
            targets.append(("service", service))
        return targets
    if service is not None:
        return [("service", service)]
    return []


def _columns_to_clear(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Source Type plus every order-number column, so a bad search cannot stick."""
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    for col in columns:
        col_id = str(col.get("colId") or "")
        if not col_id or not col.get("hasInput") or col_id in seen:
            continue
        name = str(col.get("name") or "")
        if (
            _is_source_column(name, col_id)
            or service_column_score(name, col_id) > 0
            or marketplace_column_score(name, col_id, "ebay") > 0
            or marketplace_column_score(name, col_id, "store") > 0
        ):
            chosen.append(col)
            seen.add(col_id)
    return chosen


def _column_names(columns: list[dict[str, Any]]) -> str:
    names = [
        str(col.get("name") or col.get("colId") or "?")
        for col in columns
    ]
    return ", ".join(names) or "none"


def _missing_column_message(kind: str, columns: list[dict[str, Any]]) -> str:
    seen = _column_names(columns)
    if not columns:
        return (
            "The sales order list has no filter columns. "
            "Open the sales order list and wait for the grid to finish loading."
        )
    if kind == "ebay":
        return (
            "Could not find the eBay order number column. "
            "Source Type is the channel, not the order number. "
            f"Columns: {seen}."
        )
    if kind == "store":
        return (
            "Could not find a store-order column or the sales order number column. "
            f"Columns: {seen}."
        )
    return f"Could not find the sales order number column. Columns: {seen}."


async def _read_filter_value(field) -> str:
    try:
        return str(await field.input_value(timeout=1000) or "")
    except Exception:
        return ""


async def _type_filter_query(field, query: str, *, allow_bare_key: bool) -> str:
    """Type an order number. A service-order box that rejects SO- gets the bare key."""
    await field.click()
    await field.fill("")
    await field.fill(query)
    current = await _read_filter_value(field)
    if query.casefold() not in current.casefold():
        await field.fill("")
        await field.press_sequentially(query, delay=15)
        current = await _read_filter_value(field)
    bare = order_key(query)
    if (
        allow_bare_key
        and bare
        and bare.casefold() != query.casefold()
        and query.casefold() not in current.casefold()
    ):
        logger.info(
            "Order filter did not keep %r (value=%r); trying %r",
            query,
            current,
            bare,
        )
        await field.fill("")
        await field.fill(bare)
        current = await _read_filter_value(field)
        if bare.casefold() not in current.casefold():
            await field.fill("")
            await field.press_sequentially(bare, delay=15)
        return bare
    return query


async def _clear_filter_if_set(page: Page, selector: str, submit_key: str) -> bool:
    """Empty one floating filter when a previous search left text in it."""
    if not selector:
        return False
    root = page.locator(selector).first
    try:
        if await root.count() == 0:
            return False
        inner = root.locator("input:not([type='hidden'])")
        field = inner.first if await inner.count() > 0 else root
        current = await field.input_value(timeout=1000)
        if not str(current or "").strip():
            return False
        await field.fill("", timeout=3000)
        key = (submit_key or "").strip()
        if key:
            await field.press(key)
    except Exception as exc:
        logger.info("Could not clear order filter %s: %s", selector, exc)
        return False
    return True


async def _wait_grid_overlay(page: Page, overlay_ms: int, timeout_ms: int) -> None:
    overlay = page.locator("#ag-grid .ag-overlay-loading-wrapper")
    try:
        await overlay.wait_for(state="visible", timeout=overlay_ms)
        await overlay.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        await asyncio.sleep(0.4)


async def _clear_columns(
    page: Page,
    columns: list[dict[str, Any]],
    submit_key: str,
    *,
    overlay_ms: int,
    timeout_ms: int,
) -> None:
    for col in columns:
        col_id = str(col.get("colId") or "")
        if not col_id:
            continue
        selector = _filter_selector(col)
        await _scroll_filter_into_view(page, selector, col_id)
        if await _clear_filter_if_set(page, selector, submit_key):
            logger.info("Cleared filter %s (%s)", col.get("name") or "?", col_id)
            await _wait_grid_overlay(page, overlay_ms, timeout_ms)


async def _run_column_search(
    page: Page,
    order_number: str,
    column: dict[str, Any],
    role: str,
    locators: dict[str, str],
    *,
    timeout_ms: int,
    overlay_ms: int,
    submit_key: str,
) -> tuple[int, str, Optional[Any], str]:
    """Type into one column. Returns count, text, row, and the text that was kept."""
    selector = _filter_selector(column)
    await _scroll_filter_into_view(page, selector, str(column.get("colId") or ""))
    field = await _input_field(page, selector, timeout_ms)
    query = order_number.strip()
    typed = await _type_filter_query(field, query, allow_bare_key=role == "service")
    key = (submit_key or "").strip()
    if key:
        await field.press(key)
    await _wait_grid_overlay(page, overlay_ms, timeout_ms)
    if role != "service":
        await _scroll_grid_home(page)
    count, text, row = await _wait_for_filter_rows(
        page,
        locators["sales_order_rows"],
        order_number,
        timeout_ms,
        accept_lone_row=role != "service",
    )
    bare = order_key(order_number)
    if (
        row is None
        and count == 0
        and role == "service"
        and bare
        and bare.casefold() != typed.casefold()
    ):
        logger.info(
            "No sales order row for %s; retrying the filter as %s",
            order_number.strip(),
            bare,
        )
        await _scroll_grid_home(page)
        field = await _input_field(page, selector, timeout_ms)
        typed = await _type_filter_query(field, bare, allow_bare_key=False)
        if key:
            await field.press(key)
        await _wait_grid_overlay(page, overlay_ms, timeout_ms)
        count, text, row = await _wait_for_filter_rows(
            page,
            locators["sales_order_rows"],
            order_number,
            timeout_ms,
            accept_lone_row=False,
        )
    return count, text, row, typed


async def _filter_order_list(
    page: Page,
    order_number: str,
    locators: dict[str, str],
    *,
    timeout_ms: int,
    overlay_ms: int,
    submit_key: str,
) -> tuple[Any, str]:
    """Filter the list and return the matching row plus service or marketplace."""
    kind = classify_order(order_number)
    columns = await _collect_filter_columns(page)
    targets = _search_targets(kind, columns)
    if not targets:
        raise RuntimeError(_missing_column_message(kind, columns))
    logger.info(
        "Order %s is a %s order; trying %s",
        order_number.strip(),
        kind,
        ", ".join(
            f"{role} {col.get('name') or col.get('colId')}"
            for role, col in targets
        ),
    )
    await _clear_columns(
        page,
        _columns_to_clear(columns),
        submit_key,
        overlay_ms=overlay_ms,
        timeout_ms=timeout_ms,
    )
    tried: list[str] = []
    last_count = 0
    last_text = ""
    for role, column in targets:
        label = str(column.get("name") or column.get("colId") or "?")
        tried.append(label)
        logger.info(
            "Searching %s column %s (%s) for %s",
            "sales order" if role == "service" else "order number",
            label,
            column.get("colId"),
            order_number.strip(),
        )
        count, text, row, _typed = await _run_column_search(
            page,
            order_number,
            column,
            role,
            locators,
            timeout_ms=timeout_ms,
            overlay_ms=overlay_ms,
            submit_key=submit_key,
        )
        if row is not None:
            return row, role
        if count > 1:
            raise RuntimeError(f"More than one sales order matched {order_number}.")
        if count == 1 and text and role == "service":
            raise RuntimeError(
                f"The order on screen does not match {order_number} ({text})."
            )
        last_count = count
        last_text = text
        await _clear_columns(
            page,
            [column],
            submit_key,
            overlay_ms=overlay_ms,
            timeout_ms=timeout_ms,
        )
    if last_count == 1 and last_text:
        raise RuntimeError(
            f"The order on screen does not match {order_number} ({last_text})."
        )
    where = ", ".join(tried)
    raise RuntimeError(
        f"No sales order matched {order_number}. Searched {where}."
    )


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
) -> tuple[Page, str]:
    list_page = await _open_list_page(browser, locators, action_ms)
    list_page.set_default_timeout(action_ms)
    row, role = await _filter_order_list(
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
        page = await _wait_for_detail_page(
            browser,
            list_page,
            known_urls,
            locators["sales_order_detail_pattern"],
            locators["sales_orders_url_pattern"],
            timeout_ms,
        )
        return page, role
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
    typed = normalize_order_number(order_number)
    kind = classify_order(typed)
    details = _detail_pages(browser, detail_pattern, list_pattern)
    keeper: Optional[Page] = None
    role = "service"

    if typed:
        if kind == "service":
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
            keeper, role = await _open_order_from_list(
                browser,
                typed,
                locators,
                timeout_ms=timeout_ms,
                action_ms=action_ms,
                overlay_ms=overlay_ms,
                submit_key=submit_key,
            )
        if role == "service":
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
            label = await wait_for_service_order_text(
                keeper,
                locators["service_order_label"],
                timeout_ms,
            )
            if not label:
                raise RuntimeError(
                    f"Opened order for {typed} did not show a service order number."
                )
            logger.info("Opened %s as service order %s", typed, label)
    else:
        if not details:
            raise RuntimeError("No sales order tab is open.")
        if len(details) > 1:
            raise RuntimeError("Type an SO number, eBay order, or store order.")
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
    foreground = capture_foreground()
    try:
        for page in _iter_pages(browser):
            url = page.url or ""
            if not _url_matches(url, list_pattern):
                continue
            if _is_detail_url(url, detail_pattern, list_pattern):
                continue
            try:
                columns = await _collect_filter_columns(page)
                await _clear_columns(
                    page,
                    _columns_to_clear(columns),
                    submit_key,
                    overlay_ms=1500,
                    timeout_ms=8000,
                )
            except Exception as exc:
                logger.info("Could not clear the order filter on %s: %s", url, exc)
            else:
                logger.info("Cleared order filters on %s", url)
            await _scroll_grid_home(page)
    finally:
        restore_foreground(*foreground)


async def shutdown_workday(settings: dict[str, Any]) -> None:
    """Close leftover order tabs, clear the order filter, then close Chromium.

    DevTools is disconnected before the window is closed. Closing through
    CDP Browser.close while a debugger is attached marks the profile crashed,
    and the next launch asks for a manual tab restore.
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
    cancelled = False
    failure: Optional[Exception] = None
    try:
        for port in ready:
            browsers.append(await connect_over_cdp(playwright, port))
        for browser in browsers:
            await _close_all_details(browser, locators)
            await _clear_order_filter(browser, locators, submit_key)
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:
        failure = exc
    finally:
        await _disconnect_browsers(
            playwright,
            *browsers,
            close_browser=False,
            stop_timeout_s=8,
        )
    if cancelled:
        raise asyncio.CancelledError()
    await close_listening_browsers(ready)
    if failure is not None:
        raise failure


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
