"""Uline order lines, quantity math, CSV archive, and PDF export.

The draft order lives in tp_deck/uline_draft.json so building an order
survives closing the tool. Exports go to the folder set in Settings, which
defaults to "Uline Exports" beside TP-DECK.bat. PDF is printed by the same
debug Chromium TP DECK attaches to, so no extra packages are needed.
"""

from __future__ import annotations

import base64
import csv
import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Optional

from tp_deck.uline_cache import tier_price

logger = logging.getLogger("tpdeck")

DRAFT_PATH = Path(__file__).resolve().parent / "uline_draft.json"
DEFAULT_EXPORT_FOLDER = "Uline Exports"

CSV_HEADER = [
    "Item",
    "Alias",
    "Name",
    "Unit",
    "Units Per Set",
    "Min Packs",
    "Needed Units",
    "Sets To Buy",
    "Price Per Unit",
    "Line Total",
]


@dataclass
class OrderLine:
    item: str
    name: str = ""
    alias: str = ""
    unit: str = "Each"
    units_per_set: int = 1
    min_sets: int = 1
    base_unit: str = ""
    price: float = 0.0
    tiers: tuple[tuple[int, float], ...] = ()
    needed_units: int = 1
    sets: int = 1
    price_override: Optional[float] = None

    def price_at(self, sets: int) -> float:
        """Price per unit: the override if typed, else the tier this pack count reaches."""
        if self.price_override is not None:
            return float(self.price_override)
        return tier_price(self.tiers, self.price, sets)

    def total(self) -> float:
        sets = max(1, int(self.sets))
        return round(self.price_at(sets) * sets, 2)


def sets_for(needed_units: int, units_per_set: int, min_sets: int = 1) -> int:
    """Packs to buy so the needed units are covered, never below Uline's minimum."""
    units = max(1, int(units_per_set))
    needed = max(0, int(needed_units))
    minimum = max(1, int(min_sets))
    packs = 1 if needed <= 0 else int(math.ceil(needed / units))
    return max(minimum, packs)


def add_line(lines: list[OrderLine], line: OrderLine) -> list[OrderLine]:
    """Merge into the line for that item, otherwise append."""
    for existing in lines:
        if existing.item == line.item:
            existing.needed_units += line.needed_units
            existing.sets = sets_for(existing.needed_units, existing.units_per_set, existing.min_sets)
            if line.alias:
                existing.alias = line.alias
            return lines
    lines.append(line)
    return lines


def order_sets(lines: Iterable[OrderLine]) -> int:
    return sum(max(1, line.sets) for line in lines)


def order_total(lines: Iterable[OrderLine]) -> float:
    return round(sum(line.total() for line in lines), 2)


def stamp(when: Optional[datetime] = None) -> str:
    return (when or datetime.now()).strftime("%Y-%m-%d %H%M%S")


def safe_name(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", str(text or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:60]


def export_dir(settings: dict[str, Any]) -> Path:
    """Export folder from Settings, defaulting to "Uline Exports" beside the app."""
    from tp_deck.chrome_launcher import app_root

    raw = str(settings.get("uline_export_dir") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = app_root() / path
        return path
    return app_root() / DEFAULT_EXPORT_FOLDER


def export_path(settings: dict[str, Any], suffix: str = ".csv", when: Optional[datetime] = None) -> Path:
    folder = export_dir(settings)
    return folder / f"Uline Order {stamp(when)}{suffix}"


def csv_text(lines: list[OrderLine], created: str) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"TP DECK Uline Order — {created}"])
    writer.writerow(CSV_HEADER)
    for line in lines:
        sets = max(1, line.sets)
        writer.writerow([
            line.item,
            line.alias,
            line.name,
            line.unit,
            line.units_per_set,
            max(1, line.min_sets),
            line.needed_units,
            sets,
            f"{line.price_at(sets):.2f}",
            f"{line.total():.2f}",
        ])
    writer.writerow([])
    writer.writerow([
        "TOTAL", "", "", "", "", "", "", order_sets(lines), "", f"{order_total(lines):.2f}"
    ])
    return buffer.getvalue()


def write_csv(lines: list[OrderLine], settings: dict[str, Any], target: Optional[Path] = None) -> Path:
    """Write the archived CSV in the export folder. target adds a copy elsewhere."""
    created = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = csv_text(lines, created)
    archive = export_path(settings, ".csv")
    folder = archive.parent
    folder.mkdir(parents=True, exist_ok=True)
    archive.write_text(text, encoding="utf-8")
    if target is not None and Path(target) != archive:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    logger.info("Uline order CSV written: %s", archive)
    return archive


def order_html(lines: list[OrderLine], created: str, title: str) -> str:
    rows = []
    for line in lines:
        sets = max(1, line.sets)
        rows.append(
            "<tr>"
            f"<td class='item'>{_esc(line.item)}</td>"
            f"<td>{_esc(line.alias)}</td>"
            f"<td>{_esc(line.name)}</td>"
            f"<td>{_esc(line.unit)}</td>"
            f"<td class='n'>{line.units_per_set}</td>"
            f"<td class='n'>{line.needed_units}</td>"
            f"<td class='n'>{sets}</td>"
            f"<td class='n'>${line.price_at(sets):.2f}</td>"
            f"<td class='n'>${line.total():.2f}</td>"
            "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{font-family:'Segoe UI',Arial,sans-serif;font-size:12px;margin:24px;color:#111}"
        "h2{margin:0 0 4px}.meta{color:#555;font-size:11px;margin-bottom:12px}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #bbb;padding:4px 6px}"
        "th{background:#eee;text-align:left}.n{text-align:right}"
        "th.item,td.item{width:88px;white-space:nowrap}"
        "tr.total td{font-weight:bold;background:#f7f7f7}"
        "</style></head><body>"
        f"<h2>{_esc(title)}</h2>"
        f"<div class='meta'>TP DECK — {created} — {len(lines)} items, "
        f"{order_sets(lines)} sets, total ${order_total(lines):.2f}</div>"
        "<table><thead><tr><th class='item'>Item</th><th>Alias</th><th>Name</th><th>Unit</th>"
        "<th>Units/Set</th><th>Needed</th><th>Sets</th><th>Price</th><th>Total</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + f"<tr class='total'><td colspan='6'>TOTAL</td><td>{order_sets(lines)}</td>"
        f"<td></td><td class='n'>${order_total(lines):.2f}</td></tr>"
        "</tbody></body></html>"
    )


def _esc(text: str) -> str:
    out = str(text or "")
    for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;")):
        out = out.replace(a, b)
    return out


async def pdf_bytes(html: str, settings: dict[str, Any]) -> bytes:
    """Print HTML to PDF using the debug Chromium TP DECK already runs."""
    from playwright.async_api import async_playwright

    port = int(settings.get("uline_port", settings.get("ebay_port", 9222)))
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        await page.set_content(html)
        session = await context.new_cdp_session(page)
        result = await session.send(
            "Page.printToPDF",
            {
                "landscape": False,
                "printBackground": True,
                "paperWidth": 8.5,
                "paperHeight": 11.0,
                "marginTop": 0.4,
                "marginBottom": 0.4,
                "marginLeft": 0.4,
                "marginRight": 0.4,
                "preferCSSPageSize": True,
            },
        )
        data = base64.b64decode(result.get("data", ""))
        await page.close()
        return data


def write_pdf(pdf_data: bytes, settings: dict[str, Any], target: Optional[Path] = None) -> Path:
    """Archive the PDF in the export folder, and copy it to target."""
    folder = export_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    archive = export_path(settings, ".pdf")
    archive.write_bytes(pdf_data)
    if target is not None and Path(target) != archive:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(pdf_data)
    logger.info("Uline order PDF written: %s", archive)
    return archive


def load_draft() -> list[OrderLine]:
    if not DRAFT_PATH.exists():
        return []
    try:
        data = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Uline draft unreadable (%s); starting empty", exc)
        return []
    lines: list[OrderLine] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("item") or ""):
            continue
        tiers = tuple(
            (int(row[0]), float(row[1]))
            for row in entry.get("tiers") or []
            if isinstance(row, (list, tuple)) and len(row) >= 2
        )
        raw_override = entry.get("price_override")
        lines.append(
            OrderLine(
                item=str(entry.get("item") or ""),
                name=str(entry.get("name") or ""),
                alias=str(entry.get("alias") or ""),
                unit=str(entry.get("unit") or "Each"),
                units_per_set=max(1, int(entry.get("units_per_set") or 1)),
                min_sets=max(1, int(entry.get("min_sets") or 1)),
                base_unit=str(entry.get("base_unit") or ""),
                price=float(entry.get("price") or 0.0),
                tiers=tiers,
                needed_units=max(0, int(entry.get("needed_units") or 0)),
                sets=max(1, int(entry.get("sets") or 1)),
                price_override=float(raw_override) if raw_override not in (None, "") else None,
            )
        )
    return lines


def save_draft(lines: list[OrderLine]) -> None:
    payload = [
        asdict(line) | {"tiers": [list(tier) for tier in line.tiers]}
        for line in lines
    ]
    try:
        DRAFT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write Uline draft: %s", exc)
        raise
