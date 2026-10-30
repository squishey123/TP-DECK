"""Uline product lookup over the TP DECK debug Chromium (CDP).

Uline ships two page layouts. The classic page has a chart table with
MODEL NO., QTY./ROLL, QTY./BOX, PRICE PER BOX columns and a tier row
(1, 5, 15+). The newer page has JSON-LD, price-step cards, and a
UnitMeasure code. Both are read here, and the JSON-LD name, sku, and price
are used when present so parsing survives layout changes.
"""

from __future__ import annotations

import json
import logging
import math
import re
from html import unescape
from typing import Any, Optional
from urllib.parse import quote_plus

from tp_deck.automation_engine import connect_over_cdp, find_page_by_url
from tp_deck.uline_cache import (
    UlineItem,
    is_item_shape,
    normalize_item,
    parse_price,
    today,
)

logger = logging.getLogger("tpdeck")

ULINE_BASE = "https://www.uline.com"
_NOT_FOUND = re.compile(r"not\s+found|no\s+(products|items)", re.IGNORECASE)
_PRICE_LABEL = re.compile(r"PRICE\s*(?:PER)?\s+([A-Za-z]+)", re.IGNORECASE)
_STEPS = re.compile(
    r"(?is)<li[^>]*class=[\"'][^\"']*card[^\"']*[\"'][^>]*>(.*?)</li>"
)
_NUMBER = re.compile(r"(?is)<span[^>]*class=[\"'][^\"']*number[^\"']*[\"'][^>]*>\s*(\d+)\s*<")
_NUMBER_PRICE = re.compile(r"(?is)numberPrice[^>]*>\s*(\d+(?:\.\d{1,2})?)\s*<")
_DETAIL_LINK = re.compile(r"(?is)href=[\"'](/Product/Detail/[^\"']+)\"")
# Pages Uline serves instead of the product: consent screen, bot check, login
# wall. The markers are checked against the committed page samples, where none
# of them appear, so a normal product page never trips this.
_BLOCKED = re.compile(
    r"(recaptcha|captcha|access denied|not a robot|permitted characters"
    r"|enable cookies|unusual traffic|security check|attention!|"
    r"verify you are a|access to this page is|sign in to continue)",
    re.IGNORECASE,
)

_UNIT_CODES = {
    "EA": "Each",
    "BX": "Box",
    "CS": "Case",
    "CT": "Carton",
    "PL": "Pallet",
    "RL": "Roll",
    "PK": "Pack",
    "ST": "Set",
    "C62": "Each",
}

_ENTITIES = {
    "&reg;": "\u00ae",
    "&trade;": "\u2122",
    "&deg;": "\u00b0",
    "&mdash;": "\u2014",
    "&ndash;": "\u2013",
    "&hellip;": "\u2026",
    "&ldquo;": '"',
    "&rdquo;": '"',
    "&apos;": "'",
    "&nbsp;": " ",
}


def _clean_text(text: str) -> str:
    """Uline double-escapes its page text, so unescape twice, then fix named glyphs."""
    out = unescape(str(text or ""))
    for _ in range(2):
        out = unescape(out)
    for key, value in _ENTITIES.items():
        out = out.replace(key, value)
    return re.sub(r"\s+", " ", out).strip()


def _strip_tags(fragment: str) -> str:
    return _clean_text(re.sub(r"<[^>]+>", " ", fragment))


def _table_rows(block: str) -> list[list[tuple[str, int]]]:
    rows: list[list[tuple[str, int]]] = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", block):
        cells = re.findall(r"(?is)<t([hd])([^>]*)>(.*?)</t\1>", tr)
        if cells:
            rows.append([(_strip_tags(body), _span_of(attrs)) for _, attrs, body in cells])
    return rows


def _span_of(attrs: str) -> int:
    match = re.search(r"colspan=[\"']?(\d+)", attrs, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _tables(html: str) -> list[list[list[tuple[str, int]]]]:
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    tables: list[list[list[tuple[str, int]]]] = []
    for block in re.findall(r"(?is)<table[^>]*>(.*?)</table>", cleaned):
        rows = _table_rows(block)
        if rows:
            tables.append(rows)
    return tables


def _jsonld_product(html: str) -> Optional[dict[str, Any]]:
    """First JSON-LD Product block on the page, decoded."""
    for block in re.findall(
        r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html
    ):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("@type") or "") == "Product":
            return data
    return None


def _hidden_value(html: str, name: str) -> str:
    match = re.search(
        r"(?is)<input[^>]*(?:name|id)=[\"']" + re.escape(name) + r"[\"'][^>]*value=[\"']([^\"']*)[\"']",
        html,
    )
    if not match:
        match = re.search(
            r"(?is)<input[^>]*value=[\"']([^\"']*)[\"'][^>]*(?:name|id)=[\"']"
            + re.escape(name) + r"[\"']",
            html,
        )
    return unescape(match.group(1)) if match else ""


def _norm_unit(word: str) -> str:
    """Lowercase unit word with trailing plural removed, for matching columns."""
    out = str(word or "").strip().lower()
    if len(out) > 3 and out.endswith("s"):
        out = out[:-1]
    return out


_TIER_QTY = re.compile(r"^(\d+(?:\.\d+)?(?:/\d+)?)\+?\s*([A-Za-z.]+)?$")
_QTY_HEADER = re.compile(r"(?:QTY\.?|QTY)\s*/\s*[A-Za-z.]+|[A-Za-z]+\s*/\s*[A-Za-z.]+")
_WEIGHT_WORDS = {"lb", "lbs", "oz", "kg", "wt", "weight", "cube", "sq", "dim", "dims"}


def _tier_quantity(label: str) -> Optional[tuple[float, str]]:
    """Tier cell: quantity and the unit word it counts, e.g. "1/2 CASE" -> (0.5, "case")."""
    cleaned = str(label or "").replace(",", "").strip()
    match = _TIER_QTY.match(cleaned)
    if not match:
        return None
    raw = match.group(1)
    if "/" in raw:
        first, second = raw.split("/", 1)
        try:
            quantity = float(first) / float(second)
        except (ValueError, ZeroDivisionError):
            return None
    else:
        quantity = float(raw)
    return quantity, _norm_unit(match.group(2) or "")


def _tier_row_cells(row: list[str]) -> Optional[list[tuple[float, str]]]:
    """Tier labels of one row, or None when the row is not a tier row."""
    cells = [cell for cell in row if str(cell).strip()]
    if len(cells) < 2:
        return None
    parsed = [_tier_quantity(cell) for cell in cells]
    if any(item is None for item in parsed):
        return None
    return [item for item in parsed if item is not None]


def _item_cell(text: str) -> str:
    """Canonical Item # when a table cell is one, else ""."""
    canonical = normalize_item(text)
    return canonical if is_item_shape(canonical) else ""


def _chart_table(tables: list[list[list[tuple[str, int]]]]) -> tuple[list[tuple[str, int]], list[tuple[float, str]], list[str]]:
    """Header cells (with spans), tier quantities, and the item row of the chart table."""
    for rows in tables:
        for index, header in enumerate(rows):
            if not any(
                re.search(r"MODEL\s*NO", cell, re.IGNORECASE) for cell, _span in header
            ):
                continue
            tier_cells: list[tuple[float, str]] = []
            item_row: list[str] = []
            for row in rows[index + 1:]:
                texts = [cell for cell, _span in row]
                if not texts:
                    continue
                if not item_row and _item_cell(texts[0]):
                    item_row = texts
                elif not tier_cells:
                    parsed = _tier_row_cells(texts)
                    if parsed:
                        tier_cells = parsed
            if item_row:
                return header, tier_cells, item_row
    return [], [], []


def _price_cell(header: list[tuple[str, int]]) -> tuple[int, str, int, str]:
    """Column the price starts at, the unit word Uline prices in, its span, and the label.

    The label is returned too, so the caller does not scan the header again.
    """
    for index, (label, span) in enumerate(header):
        match = _PRICE_LABEL.search(label)
        if match:
            return index, match.group(1).capitalize(), span, label
    for index, (label, span) in enumerate(header):
        if "PRICE" in label.upper():
            return index, "Each", span, label
    return -1, "Each", 1, ""


def _extra_before(header: list[tuple[str, int]], index: int) -> int:
    """Columns added by cells spanning before this header cell."""
    return sum(span - 1 for _label, span in header[:index])


def _qty_entries(header: list[tuple[str, int]], item_row: list[str]) -> dict[str, int]:
    """Units of sale from the QTY columns, e.g. bundle=25, bale=1800, case=36, box=14000."""
    entries: dict[str, int] = {}
    for index, (label, _span) in enumerate(header):
        upper = label.upper()
        if "/" not in label:
            continue
        if "QTY" not in upper:
            first = label.split("/")[0].strip().strip(".").lower()
            if first not in {"rolls", "cases", "packs", "bundles", "sets", "sheets"}:
                continue
        words = [
            _norm_unit(word.strip().strip("."))
            for word in label.split("/")
            if word.strip().strip(".")
            and "QTY" not in word.upper()
        ]
        words = [word for word in words if word and word not in _WEIGHT_WORDS]
        if not words:
            continue
        item_index = index + _extra_before(header, index)
        if item_index >= len(item_row):
            continue
        numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", item_row[item_index])]
        if not numbers:
            continue
        if len(numbers) == len(words):
            pairs = zip(words, numbers)
        elif len(numbers) == 1 and len(words) >= 2:
            pairs = [(words[-1], numbers[0])]
        else:
            pairs = list(zip(words, numbers))[: min(len(words), len(numbers))]
        for word, count in pairs:
            if count >= 1:
                entries[word] = count
    return entries


def _pack_rule(
    price_word: str,
    entries: dict[str, int],
    tier_unit: str,
    price_label: str,
) -> tuple[str, int, int, str]:
    """Unit the order is sold in, price units per pack, base units per pack, base unit word.

    "PRICE PER BOX" with a BUNDLE/ BALE QTY. line means boxes are priced and
    sold in bundles, so the pack is the smallest line (bundle, 25 boxes) and
    the per-bundle price is the per-box price x 25. "PRICE PER ROLL" with
    tiers named in cases (1/2, 1, 2+) means cases are the pack (36 rolls).
    """
    price_key = _norm_unit(price_word)
    tier_key = _norm_unit(tier_unit)
    if tier_key and tier_key in entries:
        pack_key = tier_key
    elif price_key in entries:
        pack_key = price_key
    elif entries:
        pack_key = min(entries, key=lambda word: entries[word])
    else:
        label_match = re.search(r"\(\s*(\d+)\s*/\s*([A-Za-z]+)\s*\)", price_label)
        if label_match:
            # 250 mailers per carton: the counted things are the items themselves.
            return label_match.group(2).capitalize(), 1, max(1, int(label_match.group(1))), "units"
        return price_word or "Each", 1, 1, price_word or "Each"
    units = entries[pack_key]
    factor = 1 if pack_key == price_key else units
    return pack_key.capitalize(), factor, units, price_word or pack_key.capitalize()


def _chart_tiers(
    tier_cells: list[tuple[float, str]],
    item_row: list[str],
    price_index: int,
    factor: int,
    pack_key: str,
    price_key: str,
) -> list[tuple[int, float]]:
    """Tiers in pack counts and per-pack prices."""
    tiers: list[tuple[int, float]] = []
    for offset, (quantity, unit_word) in enumerate(tier_cells):
        cell = item_row[price_index + offset] if price_index + offset < len(item_row) else ""
        price = parse_price(cell)
        if price is None:
            break
        key = _norm_unit(unit_word)
        base = quantity * factor if key and key == pack_key else quantity
        tiers.append((max(1, int(math.ceil(base / factor))), round(price * factor, 2)))
    return tiers


def _step_tiers(html: str) -> list[tuple[int, float]]:
    tiers: list[tuple[int, float]] = []
    for card in _STEPS.findall(html):
        number = _NUMBER.search(card)
        price = _NUMBER_PRICE.search(card)
        if number and price:
            tiers.append((max(1, int(number.group(1))), float(price.group(1))))
    return tiers


def _deep_availability(node: Any) -> str:
    """First availability value found in a decoded JSON block."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == "availability" and isinstance(value, str):
                return value
            found = _deep_availability(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _deep_availability(item)
            if found:
                return found
    return ""


def _jsonld_blocks(html: str) -> list[Any]:
    blocks: list[Any] = []
    for raw in re.findall(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html):
        try:
            blocks.append(json.loads(raw.strip()))
        except json.JSONDecodeError:
            continue
    return blocks


def _availability(html: str, jsonld: Optional[dict[str, Any]]) -> bool:
    """In stock from schema.org data, else from the page's stock wording."""
    for block in _jsonld_blocks(html):
        raw = _deep_availability(block)
        if raw:
            return "instock" in raw.lower().replace(" ", "")
    text = _clean_text(html)
    if re.search(r"availability[\"']?\s*[:=]\s*[\"']?\S*InStock", text, re.IGNORECASE):
        return True
    if re.search(r"out\s+of\s+stock|backorder|back\s+order", text, re.IGNORECASE):
        return False
    return True


def _blocked_message(html: str) -> str:
    """Explicit message when Uline served a consent, bot-check, or login page."""
    match = _BLOCKED.search(_clean_text(html))
    if not match:
        return ""
    return (
        "Uline asked for a verification step (login, bot check, or cookie "
        f"consent — page says {match.group(0)!r}). Open the Uline tab in the "
        "TP DECK browser, complete the prompt, then look the item up again."
    )


def parse_product(html: str, page_url: str) -> Optional[UlineItem]:
    """Name, item #, price, tiers, and units per set out of a Uline product page.

    The item number Uline's own page reports is the one returned; the caller
    compares it with the key typed.
    """
    if not html:
        return None
    jsonld = _jsonld_product(html)
    tables = _tables(html)
    header, tier_cells, item_row = _chart_table(tables)

    item = ""
    if item_row:
        item = normalize_item(item_row[0])
    if not item and jsonld:
        item = normalize_item(str(jsonld.get("sku") or ""))
    if not item:
        hidden = _hidden_value(html, "szItemNum")
        item = normalize_item(hidden)
    if not item:
        match = re.search(r"/Product/Detail/([^/?#]+)", page_url or "")
        item = normalize_item(match.group(1)) if match else ""
    if not item:
        return None

    name = ""
    if jsonld:
        name = _clean_text(str(jsonld.get("name") or ""))
    if not name:
        h1 = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", html)
        if h1:
            name = _strip_tags(h1.group(1))
    if not name:
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        if title:
            name = re.sub(r"(?i)\s*[-|]\s*ULINE.*$", "", _strip_tags(title.group(1)))

    price_index, price_word, _span, price_label = _price_cell(header) if header else (-1, "", 1, "")
    if not price_label:
        text = _strip_tags(html)
        label_match = re.search(r"Price\s+Per\s+[A-Za-z]+(?:\s*\(\d+\s*/\s*[A-Za-z]+\))?", text, re.IGNORECASE)
        price_label = label_match.group(0) if label_match else ""
        code_match = _PRICE_LABEL.search(price_label)
        if code_match:
            price_word = code_match.group(1).capitalize()
    if not price_word:
        code = _hidden_value(html, "UnitMeasure").upper()
        price_word = _UNIT_CODES.get(code, "Each")

    entries = _qty_entries(header, item_row) if header and item_row else {}
    tier_unit = ""
    if tier_cells:
        words = {word for _qty, word in tier_cells if word}
        tier_unit = next(iter(words)) if len(words) == 1 else ""

    pack_word, factor, units, base_word = _pack_rule(price_word, entries, tier_unit, price_label)

    tiers: list[tuple[int, float]] = []
    if price_index >= 0 and tier_cells:
        tiers = _chart_tiers(
            tier_cells,
            item_row,
            price_index + _extra_before(header, price_index),
            factor,
            _norm_unit(pack_word),
            _norm_unit(price_word),
        )
    if not tiers:
        # Step cards show per-pack prices, so the chart pack rule is kept: a
        # price stated in base units (per roll, per box) scales by the pack
        # factor, and the step counts stay in packs.
        tiers = [(max(1, qty), round(price * factor, 2)) for qty, price in _step_tiers(html)]

    min_sets = min((minimum for minimum, _price in tiers), default=1)

    base_price = tiers[0][1] if tiers else 0.0
    if base_price <= 0:
        offers = (jsonld or {}).get("offers") if jsonld else None
        if isinstance(offers, dict):
            try:
                base_price = float(offers.get("price") or 0.0)
            except (TypeError, ValueError):
                base_price = 0.0
    if base_price <= 0:
        base_price = parse_price(_hidden_value(html, "productPrice") or "") or 0.0
    if base_price <= 0:
        js = re.search(r"['\"]productPrice['\"]\s*:\s*['\"]([0-9.]+)['\"]", html)
        base_price = float(js.group(1)) if js else 0.0
    if base_price <= 0:
        return None

    return UlineItem(
        item=item,
        name=name,
        price=base_price,
        units_per_set=units,
        unit=pack_word or "Each",
        min_sets=min_sets,
        base_unit=(base_word or price_word or pack_word),
        tiers=tuple(tiers),
        aliases=(),
        url=page_url or "",
        in_stock=_availability(html, jsonld),
        note="chart table" if header else "price steps",
        added=today(),
        checked=today(),
    )


def _first_detail_link(html: str) -> str:
    match = re.search(r"(?is)href=[\"'](/Product/Detail/[^\"']+)\"", html)
    if not match:
        return ""
    return ULINE_BASE + match.group(1)


def looks_like_item(text: str) -> bool:
    """True when the typed text is a Uline Item #, not a keyword search."""
    return is_item_shape(normalize_item(text))


async def _window_state(page: Any) -> tuple[Any, str, Any]:
    """Window id, window state, and the CDP session that read it."""
    session = await page.context.new_cdp_session(page)
    try:
        info = await session.send("Browser.getWindowForTarget")
        bounds = info.get("bounds") or {}
        return info.get("windowId"), str(bounds.get("windowState") or ""), session
    except Exception as exc:
        logger.debug("Uline window state unreadable: %s", exc)
        return None, "", session


async def _reassert_minimized(window_id: Any, state: str, session: Any) -> None:
    """Put the window back to minimized after navigation, if it started minimized."""
    if window_id is None or state != "minimized":
        return
    try:
        await session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "minimized"}},
        )
        logger.debug("Uline window re-minimized")
    except Exception as exc:
        logger.debug("Uline window could not be re-minimized: %s", exc)


async def _lookup_url(key: str, page: Any, timeout_ms: int, window: tuple) -> str:
    """Open the right Uline page for one key on one tab, and return its url."""
    window_id, state, session = window
    typed = normalize_item(key)
    if looks_like_item(typed):
        return f"{ULINE_BASE}/Product/Detail/{typed}"
    await page.goto(
        f"{ULINE_BASE}/Product/AdvSearchResult?keywords={quote_plus(key)}",
        timeout=timeout_ms,
        wait_until="domcontentloaded",
    )
    await _reassert_minimized(window_id, state, session)
    search_html = await page.content()
    url = _first_detail_link(search_html)
    if not url:
        raise RuntimeError(
            f"Uline search for {key!r} did not list an item link. "
            "Open the page, pick the item, and type its Item #."
        )
    return url


async def _read_product(key: str, page: Any, url: str, timeout_ms: int, window: tuple) -> UlineItem:
    """Navigate one tab to the product page and read the cached data."""
    window_id, state, session = window
    await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    html = await page.content()
    await _reassert_minimized(window_id, state, session)
    blocked = _blocked_message(html)
    if blocked:
        raise RuntimeError(blocked)
    if _NOT_FOUND.search(html) and "/Product/Detail/" not in (page.url or ""):
        raise RuntimeError(f"Uline has no item {key!r}")
    item = parse_product(html, page.url)
    if item is None:
        raise RuntimeError(
            f"Uline page for {key!r} is not a single product page "
            f"(address {page.url}). Open the product page, and type its "
            "Item #."
        )
    if looks_like_item(normalize_item(key)) and item.item != normalize_item(key):
        raise RuntimeError(
            f"Uline returned {item.item} ({item.name}) for {normalize_item(key)!r}, "
            f"not {normalize_item(key)}. The page Uline served is a different item. "
            f"Type {item.item} to add that one, or fix the Item # so it "
            f"opens {item.item}."
        )
    return item


async def lookup_items_parallel(keys: list[str], settings: dict[str, Any]) -> list[tuple[str, Optional[UlineItem], str]]:
    """Read several items at once, one tab per key, with a 5x timeout.

    The lazy batch opens a tab per key, so every Uline page loads at the same
    time. Each result is (key, item, note); note is "" on success, and the
    failure text when that key never read. Returns one entry per key, in the
    order the keys were given.
    """
    import asyncio

    timeout_ms = int(settings.get("uline_lookup_timeout_ms", 20000)) * 5
    port = int(settings.get("uline_port", settings.get("ebay_port", 9222)))
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = None
    pages: list[Any] = []
    windows: list[tuple] = []
    try:
        browser = await connect_over_cdp(playwright, port)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        for key in keys:
            page = await context.new_page()
            pages.append(page)
            windows.append(await _window_state(page))

        async def fetch(index: int) -> tuple[str, Optional[UlineItem], str]:
            key = keys[index]
            page = pages[index]
            window_id, state, session = windows[index]
            try:
                url = await _lookup_url(key, page, timeout_ms, (window_id, state, session))
                item = await _read_product(key, page, url, timeout_ms, (window_id, state, session))
                logger.info(
                    "Uline lazy lookup %s → %s (%s per %s, min %s, $%s)",
                    key,
                    item.item,
                    item.units_per_set,
                    item.unit,
                    item.min_sets,
                    item.price,
                )
                return key, item, ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Uline lazy lookup %s failed: %s", key, exc)
                return key, None, str(exc)

        tasks = [fetch(index) for index in range(len(keys))]
        results = list(await asyncio.gather(*tasks))
        return results
    finally:
        for window in windows:
            session = window[2]
            if session is not None:
                try:
                    await session.detach()
                except Exception as exc:
                    logger.debug("Uline lazy CDP session detach failed: %s", exc)
        for page in pages:
            try:
                await page.close()
            except Exception as exc:
                logger.debug("Uline lazy tab close failed: %s", exc)
        if browser is not None:
            try:
                await browser.close()
            except Exception as exc:
                logger.debug("Uline lazy browser disconnect failed: %s", exc)
        try:
            await playwright.stop()
        except Exception as exc:
            logger.debug("Uline lazy playwright stop failed: %s", exc)


async def lookup_item(key: str, settings: dict[str, Any]) -> UlineItem:
    """Resolve an Item #, or a keyword search, into cache-ready data.

    The page Uline served is checked against the typed Item #: Uline redirects
    an Item # to its canonical page, so a page that reports a different item
    number is reported as a mismatch, not cached under the typed key.
    """
    from playwright.async_api import async_playwright

    timeout_ms = int(settings.get("uline_lookup_timeout_ms", 20000))
    port = int(settings.get("uline_port", settings.get("ebay_port", 9222)))
    pattern = str(settings.get("uline_url_pattern", "uline.com"))

    playwright = await async_playwright().start()
    page: Optional[Any] = None
    created = False
    window: tuple = (None, "", None)
    try:
        browser = await connect_over_cdp(playwright, port)
        page = await find_page_by_url(browser, pattern, label="Uline tab")
        created = page is None
        if page is None:
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
        window = await _window_state(page)

        url = await _lookup_url(key, page, timeout_ms, window)
        item = await _read_product(key, page, url, timeout_ms, window)

        logger.info(
            "Uline lookup %s → %s (%s per %s, min %s, $%s)",
            key,
            item.item,
            item.units_per_set,
            item.unit,
            item.min_sets,
            item.price,
        )
        return item
    finally:
        # The tab opened for this lookup is closed, and the CDP session
        # detached, on success, failure, and cancel alike, so a failed lookup
        # does not leave a tab open in the operator's browser.
        session = window[2]
        if session is not None:
            try:
                await session.detach()
            except Exception as exc:
                logger.debug("Uline CDP session detach failed: %s", exc)
        if created and page is not None:
            try:
                await page.close()
            except Exception as exc:
                logger.debug("Uline tab close failed: %s", exc)
        try:
            await playwright.stop()
        except Exception as exc:
            logger.debug("Uline playwright stop failed: %s", exc)
