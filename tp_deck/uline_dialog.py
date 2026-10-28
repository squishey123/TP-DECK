"""Uline Order Builder — order lines, cached items, aliases, CSV/PDF export."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tp_deck.settings_manager import load_settings
from tp_deck.uline_cache import (
    UlineItem,
    add_alias,
    edit_item,
    load_cache,
    normalize_item,
    put_item,
    remove_alias,
    remove_item,
    resolve_key,
    save_cache,
)
from tp_deck.uline_order import (
    OrderLine,
    add_line,
    export_dir,
    export_path,
    load_draft,
    order_html,
    order_sets,
    order_total,
    pdf_bytes,
    safe_name,
    save_draft,
    sets_for,
    stamp,
    write_csv,
    write_pdf,
)
from tp_deck.uline_scraper import lookup_item

logger = logging.getLogger("tpdeck")

ORDER_COLUMNS = (
    "Item",
    "Alias",
    "Name",
    "Unit",
    "Units / Set",
    "Min Packs",
    "Needed Units",
    "Sets To Buy",
    "Price / Unit",
    "Line Total",
)
CACHE_COLUMNS = ("Item", "Name", "Unit", "Units / Set", "Min Packs", "Price", "Aliases", "Checked")
COL_NEEDED = ORDER_COLUMNS.index("Needed Units")
COL_SETS = ORDER_COLUMNS.index("Sets To Buy")
COL_PRICE = ORDER_COLUMNS.index("Price / Unit")
EDITABLE_COLUMNS = (COL_NEEDED, COL_SETS, COL_PRICE)
COLUMN_TIPS = {
    COL_NEEDED: "Total units you need. Sets To Buy rounds up to whole packs.",
    COL_SETS: "Packs to buy. Typing packs sets Needed Units to packs x units.",
    COL_PRICE: "Price per pack. Typing a price overrides the tier price; type 0 to clear it.",
}
MODE_UNITS = "Units needed"
MODE_SETS = "Sets (packs)"

DIALOG_QSS = """
QDialog, QGroupBox { background-color: #0F172A; color: #F8FAFC; }
QGroupBox { border: 1px solid #334155; border-radius: 4px; margin-top: 10px;
            padding-top: 8px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #0EA5E9; }
QLabel { color: #F8FAFC; background: transparent; }
QLabel#totalsLabel { color: #38BDF8; font-weight: bold; }
QLabel#statusLabel { color: #94A3B8; }
QLabel#errorLabel { color: #FCA5A5; font-weight: bold; }
QLineEdit, QSpinBox, QComboBox {
    background-color: #1E293B; color: #F8FAFC;
    border: 1px solid #334155; border-radius: 3px; padding: 3px 6px;
    font-family: Consolas, "Courier New", monospace;
}
QComboBox::drop-down { border: none; }
QTabBar::tab {
    background-color: #1E293B; color: #F8FAFC;
    border: 1px solid #334155; padding: 6px 12px; margin-right: 4px;
}
QTabBar::tab:selected { background-color: #0EA5E9; color: #0F172A; font-weight: bold; }
QTableWidget {
    background-color: #0F172A; color: #F8FAFC;
    gridline-color: #334155; border: 1px solid #334155; border-radius: 4px;
    font-family: Consolas, "Courier New", monospace; font-size: 12px;
}
QHeaderView::section {
    background-color: #1E293B; color: #38BDF8; border: 1px solid #334155;
    font-weight: bold; padding: 4px;
}
QPushButton {
    background-color: #1E293B; color: #F8FAFC;
    border: 1px solid #0EA5E9; border-radius: 4px; padding: 6px 12px;
}
QPushButton:hover { background-color: #0EA5E9; color: #0F172A; }
QPushButton#addBtn { background-color: #0EA5E9; color: #0F172A; font-weight: bold; }
QPushButton#addBtn:hover { background-color: #38BDF8; }
QPushButton:disabled { background-color: #334155; color: #94A3B8; border-color: #475569; }
"""


def _int_text(value: str) -> Optional[int]:
    cleaned = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return None


def _float_text(value: str) -> Optional[float]:
    cleaned = str(value or "").replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


class UlineItemEditor(QDialog):
    """Edit the cached fields of one Uline item."""

    def __init__(self, item: UlineItem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit {item.item}")
        self.setMinimumWidth(430)
        self.setStyleSheet(DIALOG_QSS)

        form = QFormLayout(self)
        locked = QLineEdit(item.item)
        locked.setReadOnly(True)
        self._name = QLineEdit(item.name)
        self._price = QLineEdit(f"{item.price:.2f}")
        self._unit = QLineEdit(item.unit)
        self._units = QSpinBox()
        self._units.setRange(1, 1_000_000)
        self._units.setValue(item.units_per_set)
        self._min = QSpinBox()
        self._min.setRange(1, 1_000_000)
        self._min.setValue(max(1, item.min_sets))
        self._base = QLineEdit(item.base_unit or item.unit)
        self._aliases = QLineEdit(", ".join(item.aliases))
        self._note = QLineEdit(item.note)
        form.addRow("Item #", locked)
        form.addRow("Full name", self._name)
        form.addRow("Price per set", self._price)
        form.addRow("Unit (Bundle, Case, Box…)", self._unit)
        form.addRow("Units per set", self._units)
        form.addRow("Minimum packs", self._min)
        form.addRow("Units are counted in", self._base)
        form.addRow("Aliases (comma separated)", self._aliases)
        form.addRow("Note", self._note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("Save")
        ok.setObjectName("addBtn")
        ok.clicked.connect(self._accept)
        buttons.addWidget(ok)
        form.addRow(buttons)

    def _accept(self) -> None:
        if not self._name.text().strip():
            QMessageBox.warning(self, "Uline", "Full name is required")
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        return {
            "name": self._name.text().strip(),
            "price": self._price.text().strip(),
            "unit": self._unit.text().strip(),
            "units_per_set": self._units.value(),
            "min_sets": self._min.value(),
            "base_unit": self._base.text().strip(),
            "aliases": [a.strip() for a in self._aliases.text().split(",") if a.strip()],
            "note": self._note.text().strip(),
        }


class UlineDialog(QDialog):
    """Order builder: type an Item # or alias, export CSV and PDF."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        runner: Optional[Callable] = None,
        settings: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._settings = settings or load_settings()
        self._cache = load_cache()
        self._lines = load_draft()
        self._busy = False
        self._pending_key = ""
        self._pending_alias = ""
        self._updating = False

        self.setWindowTitle("TP DECK — Uline Order Builder")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(820, 540)
        self.setStyleSheet(DIALOG_QSS)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        bar = QHBoxLayout()
        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("Item # (S-11633) or alias")
        self._key_edit.returnPressed.connect(self._add)
        bar.addWidget(self._key_edit, 2)

        self._alias_edit = QLineEdit()
        self._alias_edit.setPlaceholderText("Alias to save (optional)")
        bar.addWidget(self._alias_edit, 2)

        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 1_000_000)
        self._qty_spin.setValue(1)
        bar.addWidget(self._qty_spin)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems([MODE_UNITS, MODE_SETS])
        bar.addWidget(self._mode_combo)

        self._add_btn = QPushButton("Add")
        self._add_btn.setObjectName("addBtn")
        self._add_btn.clicked.connect(self._add)
        bar.addWidget(self._add_btn)
        root.addLayout(bar)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_order_tab(), "Order")
        self._tabs.addTab(self._build_cache_tab(), "Cached Items")
        root.addWidget(self._tabs)

        self._status = QLabel("Type an Item # or alias and press Add.")
        self._status.setObjectName("statusLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._error = QLabel("")
        self._error.setObjectName("errorLabel")
        self._error.setWordWrap(True)
        root.addWidget(self._error)

        self._refresh_order_table()
        self._refresh_cache_table()

    # ------------------------------------------------------------------ order tab

    def _build_order_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._order_table = QTableWidget(0, len(ORDER_COLUMNS))
        self._order_table.setHorizontalHeaderLabels(ORDER_COLUMNS)
        self._order_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._order_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._order_table.verticalHeader().setVisible(False)
        header = self._order_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._order_table.itemChanged.connect(self._on_order_changed)
        for column, tip in COLUMN_TIPS.items():
            header_item = self._order_table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setToolTip(tip)
        layout.addWidget(self._order_table)

        self._totals = QLabel("0 items · 0 sets · $0.00")
        self._totals.setObjectName("totalsLabel")
        layout.addWidget(self._totals)

        row = QHBoxLayout()
        for label, handler in (
            ("Remove Selected", self._remove_selected),
            ("Clear Order", self._clear_order),
            ("Export CSV", self._export_csv),
            ("Export PDF", self._export_pdf),
            ("Open Export Folder", self._open_folder),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        return widget

    def _refresh_order_table(self) -> None:
        self._updating = True
        self._order_table.setRowCount(len(self._lines))
        for row, line in enumerate(self._lines):
            cells = (
                line.item,
                line.alias,
                line.name,
                line.unit,
                str(line.units_per_set),
                str(max(1, line.min_sets)),
                str(line.needed_units),
                str(max(1, line.sets)),
                f"{line.price_at(max(1, line.sets)):.2f}",
                f"{line.total():.2f}",
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in EDITABLE_COLUMNS:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self._order_table.setItem(row, col, item)
        self._updating = False
        sets = order_sets(self._lines)
        self._totals.setText(
            f"{len(self._lines)} items · {sets} sets · ${order_total(self._lines):.2f}"
        )

    def _on_order_changed(self, cell) -> None:
        if self._updating or cell.row() >= len(self._lines):
            return
        line = self._lines[cell.row()]
        column = cell.column()
        if column == COL_PRICE:
            value = _float_text(cell.text())
            if value is None:
                self._refresh_order_table()
                return
            line.price_override = value if value > 0 else None
            save_draft(self._lines)
            self._refresh_order_table()
            self._set_status(
                f"{line.item}: price {'overridden at' if value > 0 else 'cleared to the tier price'} "
                f"${line.price_at(max(1, line.sets)):.2f} x {max(1, line.sets)} = ${line.total():.2f}"
            )
            return
        value = _int_text(cell.text())
        if value is None:
            self._refresh_order_table()
            return
        if column == COL_NEEDED:
            line.needed_units = max(0, value)
            line.sets = sets_for(line.needed_units, line.units_per_set, line.min_sets)
        elif column == COL_SETS:
            line.sets = max(max(1, line.min_sets), value)
            line.needed_units = line.sets * line.units_per_set
        else:
            return
        save_draft(self._lines)
        self._refresh_order_table()

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self._order_table.selectedItems()}, reverse=True)

    def _remove_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self._set_error("Select a row to remove")
            return
        for row in rows:
            if 0 <= row < len(self._lines):
                self._lines.pop(row)
        save_draft(self._lines)
        self._refresh_order_table()

    def _clear_order(self) -> None:
        self._lines = []
        save_draft(self._lines)
        self._refresh_order_table()
        self._set_status("Order cleared")

    def _export_csv(self) -> None:
        if not self._lines:
            self._set_error("The order is empty")
            return
        default = export_path(self._settings, ".csv")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Uline order CSV",
            str(default),
            "CSV files (*.csv)",
        )
        if not target:
            return
        try:
            archive = write_csv(self._lines, self._settings, Path(target))
        except OSError as exc:
            self._set_error(f"Export failed: {exc}")
            return
        self._set_status(f"Archived in {archive.parent}: {archive.name}")

    def _export_pdf(self) -> None:
        if not self._lines:
            self._set_error("The order is empty")
            return
        if self._runner is None:
            self._set_error("PDF export is not available")
            return
        default = export_path(self._settings, ".pdf")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Uline order PDF",
            str(default),
            "PDF files (*.pdf)",
        )
        if not target:
            return
        created = stamp()
        html = order_html(self._lines, created, safe_name("Uline Order"))
        self._set_status("Writing PDF…")
        self._pending_pdf_target = Path(target)
        self._runner(pdf_bytes(html, self._settings), self._on_pdf)

    def _on_pdf(self, ok: bool, data, _err: str) -> None:
        if not ok:
            self._set_error(f"PDF export failed: {_err}")
            return
        target = getattr(self, "_pending_pdf_target", None)
        archive = write_pdf(data, self._settings, target)
        self._set_status(f"Archived in {archive.parent}: {archive.name}")

    def _open_folder(self) -> None:
        folder = export_dir(self._settings)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except OSError as exc:
            self._set_error(f"Cannot open {folder}: {exc}")

    # ------------------------------------------------------------------ add / lookup

    def _add(self) -> None:
        key = self._key_edit.text().strip()
        if not key:
            self._set_error("Type an Item # or alias")
            return
        if self._busy:
            self._set_error("A Uline lookup is already running")
            return
        canonical, cached = resolve_key(self._cache, key)
        if cached is not None:
            self._commit(canonical, cached, self._alias_edit.text().strip())
            return
        if self._runner is None:
            self._set_error(f"{key} is not cached and lookup is not available")
            return
        self._busy = True
        self._pending_key = key
        self._pending_alias = self._alias_edit.text().strip()
        self._add_btn.setEnabled(False)
        self._set_status(f"Looking up {key} on Uline…")
        self._runner(lookup_item(key, self._settings), self._on_lookup)

    def _on_lookup(self, ok: bool, item, err: str) -> None:
        self._busy = False
        self._add_btn.setEnabled(True)
        if not ok:
            self._set_error(f"Uline lookup failed: {err}")
            return
        put_item(self._cache, item)
        save_cache(self._cache)
        self._refresh_cache_table()
        canonical, cached = resolve_key(self._cache, self._pending_key)
        if cached is None:
            self._set_error(f"{self._pending_key} did not resolve")
            return
        self._commit(canonical, cached, self._pending_alias)

    def _commit(self, canonical: str, item: UlineItem, alias_text: str) -> None:
        qty = max(1, self._qty_spin.value())
        units = max(1, item.units_per_set)
        min_sets = max(1, item.min_sets)
        if self._mode_combo.currentText() == MODE_SETS:
            sets = max(min_sets, qty)
            needed = sets * units
        else:
            needed = qty
            sets = sets_for(needed, units, min_sets)
        line = OrderLine(
            item=canonical,
            name=item.name,
            alias="",
            unit=item.unit,
            units_per_set=units,
            min_sets=min_sets,
            base_unit=item.base_unit or item.unit,
            price=item.price,
            tiers=item.tiers,
            needed_units=needed,
            sets=sets,
        )
        if alias_text:
            owner = add_alias(self._cache, canonical, alias_text)
            save_cache(self._cache)
            self._refresh_cache_table()
            line.alias = alias_text
            if owner != canonical:
                self._set_status(
                    f"{alias_text} already belongs to {owner}, so it was not "
                    f"saved for {canonical}. The line was added."
                )
        add_line(self._lines, line)
        save_draft(self._lines)
        self._refresh_order_table()
        self._key_edit.clear()
        self._alias_edit.clear()
        pack = item.unit
        base = (item.base_unit or item.unit).lower()
        joiner = "of" if base == pack.lower() else "×"
        note = ""
        if self._mode_combo.currentText() == MODE_UNITS and needed < units * min_sets:
            note = f" (raised to the {min_sets} {pack} minimum)"
        self._set_status(
            f"{canonical}: {sets} {pack}(s) {joiner} {units:,} {base}(s) "
            f"= {sets * units:,} {base}(s) at ${line.price_at(sets):.2f}/{pack} "
            f"= ${line.total():.2f}{note}"
        )

    # ------------------------------------------------------------------ cache tab

    def _build_cache_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._cache_table = QTableWidget(0, len(CACHE_COLUMNS))
        self._cache_table.setHorizontalHeaderLabels(CACHE_COLUMNS)
        self._cache_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cache_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cache_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._cache_table.verticalHeader().setVisible(False)
        header = self._cache_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._cache_table)

        self._alias_input = QLineEdit()
        self._alias_input.setPlaceholderText(
            "Alias to add to, or remove from, the selected item"
        )
        layout.addWidget(self._alias_input)

        row = QHBoxLayout()
        for label, handler in (
            ("Add Alias", self._add_alias),
            ("Remove Alias", self._remove_alias),
            ("Edit", self._edit_item),
            ("Re-Lookup", self._re_lookup),
            ("Remove From Cache", self._delete_item),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        return widget

    def _refresh_cache_table(self) -> None:
        rows = sorted(self._cache.values(), key=lambda item: item.item)
        self._cache_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = (
                item.item,
                item.name,
                item.unit,
                str(item.units_per_set),
                str(max(1, item.min_sets)),
                f"{item.price:.2f}",
                ", ".join(item.aliases),
                item.checked,
            )
            for col, text in enumerate(values):
                self._cache_table.setItem(row, col, QTableWidgetItem(text))

    def _selected_item(self) -> str:
        row = self._cache_table.currentRow()
        if row < 0:
            self._set_error("Select a cached item first")
            return ""
        cell = self._cache_table.item(row, 0)
        return cell.text() if cell else ""

    def _add_alias(self) -> None:
        key = self._selected_item()
        alias = self._alias_input.text().strip()
        if not key or not alias:
            self._set_error("Select an item and type an alias")
            return
        try:
            owner = add_alias(self._cache, key, alias)
        except ValueError as exc:
            self._set_error(str(exc))
            return
        save_cache(self._cache)
        self._refresh_cache_table()
        if owner != normalize_item(key):
            self._set_error(
                f"{alias} already belongs to {owner}. Remove it from {owner} "
                "before using it here."
            )
            return
        self._alias_input.clear()
        self._set_status(f"{alias} → {key}")

    def _remove_alias(self) -> None:
        key = self._selected_item()
        alias = self._alias_input.text().strip()
        if not key or not alias:
            self._set_error("Select an item and type the alias to remove")
            return
        try:
            remove_alias(self._cache, key, alias)
        except ValueError as exc:
            self._set_error(str(exc))
            return
        save_cache(self._cache)
        self._refresh_cache_table()
        self._alias_input.clear()

    def _edit_item(self) -> None:
        key = self._selected_item()
        if not key:
            return
        editor = UlineItemEditor(self._cache[normalize_item(key)], self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        edit_item(self._cache, key, **editor.values())
        save_cache(self._cache)
        self._refresh_cache_table()

    def _re_lookup(self) -> None:
        key = self._selected_item()
        if not key:
            return
        if self._busy or self._runner is None:
            self._set_error("Lookup is not available right now")
            return
        self._busy = True
        self._add_btn.setEnabled(False)
        self._set_status(f"Re-checking {key} on Uline…")
        self._runner(lookup_item(key, self._settings), self._on_re_lookup)

    def _on_re_lookup(self, ok: bool, item, err: str) -> None:
        self._busy = False
        self._add_btn.setEnabled(True)
        if not ok:
            self._set_error(f"Re-lookup failed: {err}")
            return
        put_item(self._cache, item)
        save_cache(self._cache)
        self._refresh_cache_table()

    def _delete_item(self) -> None:
        key = self._selected_item()
        if not key:
            return
        answer = QMessageBox.question(
            self,
            "Uline",
            f"Remove {key} and its aliases from the cache?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        remove_item(self._cache, key)
        save_cache(self._cache)
        self._refresh_cache_table()

    # ------------------------------------------------------------------ status

    def _set_status(self, text: str) -> None:
        self._error.setText("")
        self._status.setText(text)

    def _set_error(self, text: str) -> None:
        self._status.setText("")
        self._error.setText(text)
        logger.warning("Uline dialog: %s", text)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        save_draft(self._lines)
        save_cache(self._cache)
        super().closeEvent(event)
