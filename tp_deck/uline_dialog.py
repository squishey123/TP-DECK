"""Uline Order Builder — order lines, cached items, aliases, CSV/PDF export."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
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
    is_item_shape,
    load_cache,
    normalize_item,
    put_item,
    remove_alias,
    remove_item,
    resolve_key,
    save_cache as write_cache,
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
    save_draft as write_draft,
    sets_for,
    stamp,
    write_csv,
    write_pdf,
)
from tp_deck.uline_scraper import lookup_item, lookup_items_parallel

logger = logging.getLogger("tpdeck")

ORDER_COLUMNS = (
    "Item",
    "Alias",
    "Name",
    "Unit",
    "Units / Set",
    "Needed Units",
    "Sets To Buy",
    "Price / Unit",
    "Line Total",
)
CACHE_COLUMNS = (
    "Item",
    "Name",
    "Unit",
    "Units / Set",
    "Min Packs",
    "Price",
    "In Stock",
    "Aliases",
    "Checked",
)
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


def _header_widths(headers: tuple[str, ...], char_caps: dict[int, int]) -> list[int]:
    """Column widths that let more columns show: fixed chars, header + buffer.

    Item and Unit are measured in monospace characters (~7 and ~6 plus a
    buffer), so their narrow width fits the data. The later columns get the
    header text plus a buffer, so every header word is readable at a glance
    without the table getting too wide for one screen.
    """
    metrics = QFontMetrics(QFont("Consolas", 12))
    char_width = metrics.horizontalAdvance("W")
    widths: list[int] = []
    for index, header in enumerate(headers):
        cap = char_caps.get(index)
        if cap is None:
            width = metrics.horizontalAdvance(header)
        else:
            width = char_width * cap
        widths.append(width + 12)
    return widths


_CHEVRON_UP = (Path(__file__).resolve().parent / "assets" / "chevron-up.png").as_posix()
_CHEVRON_DOWN = (Path(__file__).resolve().parent / "assets" / "chevron-down.png").as_posix()

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
QPushButton#lazyBtn {
    background-color: #B45309; color: #F8FAFC;
    border: 1px solid #F59E0B; font-weight: bold;
}
QPushButton#lazyBtn:hover { background-color: #D97706; }
QPushButton#lazyBtn:checked { background-color: #15803D; border-color: #4ADE80; }
QPushButton#lazyBtn:checked:hover { background-color: #16A34A; }
QComboBox { padding-right: 18px; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox::down-arrow {
    image: url("__CHEVRON_DOWN__");
    width: 9px; height: 6px;
    border: none; background: transparent;
}
QComboBox QAbstractItemView {
    background-color: #1E293B; color: #F8FAFC;
    border: 1px solid #334155;
    selection-background-color: #0EA5E9; selection-color: #0F172A;
    outline: none;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #334155; border: 1px solid #475569; width: 16px;
}
QSpinBox::up-arrow {
    image: url("__CHEVRON_UP__");
    width: 9px; height: 6px;
    border: none; background: transparent;
}
QSpinBox::down-arrow {
    image: url("__CHEVRON_DOWN__");
    width: 9px; height: 6px;
    border: none; background: transparent;
}
QMenu { background-color: #1E293B; color: #F8FAFC; border: 1px solid #334155; }
QMenu::item { background: transparent; padding: 4px 22px; }
QMenu::item:selected { background-color: #0EA5E9; color: #0F172A; }
QMenu::separator { height: 1px; background: #334155; margin: 4px 8px; }
QToolTip { background-color: #1E293B; color: #F8FAFC; border: 1px solid #334155; padding: 4px; }
QMessageBox { background-color: #0F172A; }
QAbstractItemView {
    background-color: #0F172A; color: #F8FAFC;
    selection-background-color: #0EA5E9; selection-color: #0F172A;
}
QAbstractItemView::item { background-color: #1E293B; color: #F8FAFC; }
QTableWidget { selection-background-color: #0EA5E9; selection-color: #0F172A; }
QTableWidget::item { background-color: #1E293B; color: #F8FAFC; }
QTableWidget::item:selected { background-color: #0EA5E9; color: #0F172A; }
QAbstractButton { background-color: #1E293B; }
QHeaderView { background-color: #1E293B; }
QScrollBar:vertical, QScrollBar:horizontal {
    background-color: #1E293B; border: 1px solid #334155;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: #475569; border-radius: 3px;
    min-width: 12px; min-height: 12px;
}
QScrollBar::add-line:vertical, QScrollBar::add-line:horizontal,
QScrollBar::sub-line:vertical, QScrollBar::sub-line:horizontal {
    width: 0; height: 0; background: transparent;
}
QScrollBar::add-page, QScrollBar::sub-page { background: #0F172A; }
"""
DIALOG_QSS = (
    DIALOG_QSS
    .replace("__CHEVRON_UP__", _CHEVRON_UP)
    .replace("__CHEVRON_DOWN__", _CHEVRON_DOWN)
)


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
        self._lazy_keys: list[str] = []
        self._updating = False
        # The task the runner started (a lookup, a PDF print), kept so closeEvent
        # can cancel it, and _closing so a late result is dropped on the floor.
        self._task: Optional[Any] = None
        self._closing = False
        # The message from the last cache/draft write that hit a disk failure.
        # The caller shows it on the red label; _set_status does not wipe it.
        self._disk_failure = ""

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

        self._lazy_btn = QPushButton("Lazy")
        self._lazy_btn.setObjectName("lazyBtn")
        self._lazy_btn.setCheckable(True)
        self._lazy_btn.setToolTip(
            "Lazy mode: Add records what you typed, with no Uline page. "
            "Toggle Lazy off to open a tab for every lazy entry at once, and "
            "read the missing info in parallel."
        )
        self._lazy_btn.toggled.connect(self._on_lazy_toggled)
        bar.addWidget(self._lazy_btn)
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
        for column, width in enumerate(_header_widths(ORDER_COLUMNS, {0: 7, 3: 6})):
            if column != 2:
                header.resizeSection(column, width)
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
            saved = self._save_draft()
            self._refresh_order_table()
            self._set_status(
                f"{line.item}: price {'overridden at' if value > 0 else 'cleared to the tier price'} "
                f"${line.price_at(max(1, line.sets)):.2f} x {max(1, line.sets)} = ${line.total():.2f}"
            )
            if not saved:
                self._show_disk_failure()
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
        saved = self._save_draft()
        self._refresh_order_table()
        if not saved:
            self._show_disk_failure()

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
        saved = self._save_draft()
        self._refresh_order_table()
        if not saved:
            self._show_disk_failure()

    def _clear_order(self) -> None:
        self._lines = []
        saved = self._save_draft()
        self._refresh_order_table()
        self._set_status("Order cleared")
        if not saved:
            self._show_disk_failure()

    def _refresh_settings(self) -> dict[str, Any]:
        """Reload Settings so a Settings change is used by the next export/lookup.

        The dialog is not modal, so Settings can be edited while it is open; the
        snapshot from __init__ is replaced here on each action.
        """
        self._settings = load_settings()
        return self._settings

    def _export_csv(self) -> None:
        if not self._lines:
            self._set_error("The order is empty")
            return
        settings = self._refresh_settings()
        default = export_path(settings, ".csv")
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Export Uline order CSV",
            str(default),
            "CSV files (*.csv)",
        )
        if not target:
            return
        try:
            archive = write_csv(self._lines, settings, Path(target))
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
        if self._busy:
            self._set_error("A Uline action is already running")
            return
        settings = self._refresh_settings()
        default = export_path(settings, ".pdf")
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
        # The path and Settings are closed over, so a second export does not
        # overwrite them while the first print is running.
        target_path = Path(target)
        self._busy = True
        self._add_btn.setEnabled(False)
        self._set_status("Writing PDF…")

        def _finish(ok: bool, data, err: str) -> None:
            self._busy = False
            self._add_btn.setEnabled(True)
            if self._closing:
                # The window is closing: a late print result must not write a
                # file, change widgets, or touch the draft.
                return
            if not ok:
                self._set_error(f"PDF export failed: {err}")
                return
            try:
                archive = write_pdf(data, settings, target_path)
            except OSError as exc:
                self._set_error(f"PDF export failed: {exc}")
                return
            self._set_status(f"Archived in {archive.parent}: {archive.name}")

        self._task = self._runner(pdf_bytes(html, settings), _finish)

    def _open_folder(self) -> None:
        folder = export_dir(self._refresh_settings())
        # mkdir is inside the same try, so a bad export folder (network path,
        # drive not ready) is reported on the red label instead of escaping the
        # button as an unhandled exception.
        try:
            folder.mkdir(parents=True, exist_ok=True)
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
        if self._lazy_btn.isChecked():
            if cached is not None:
                self._commit(canonical, cached, self._alias_edit.text().strip(), key)
            else:
                self._add_lazy(key)
            return
        if cached is not None:
            self._commit(canonical, cached, self._alias_edit.text().strip(), key)
            return
        if self._runner is None:
            self._set_error(f"{key} is not cached and lookup is not available")
            return
        self._busy = True
        self._pending_key = key
        self._pending_alias = self._alias_edit.text().strip()
        self._add_btn.setEnabled(False)
        settings = self._refresh_settings()
        self._set_status(f"Looking up {key} on Uline…")
        # The task is kept so closing the dialog can cancel a lookup that is
        # still navigating.
        self._task = self._runner(lookup_item(key, settings), self._on_lookup)

    def _add_lazy(self, typed: str) -> None:
        """Add one line with exactly what the user typed, no Uline page opened."""
        raw = typed.strip()
        canonical = normalize_item(raw)
        # An Item # is stored in canonical form so the later fetch opens that
        # product page. A keyword or alias keeps its spaces and casing; stripping
        # them would search Uline for a different string than the user typed.
        if is_item_shape(canonical):
            item_text = canonical
            lookup = canonical
        else:
            item_text = raw
            lookup = raw
        alias = self._alias_edit.text().strip()
        qty = max(1, self._qty_spin.value())
        as_sets = self._mode_combo.currentText() == MODE_SETS
        line = OrderLine(
            item=item_text,
            name="",
            alias=alias,
            unit="",
            units_per_set=1,
            min_sets=1,
            base_unit="",
            price=0.0,
            tiers=(),
            needed_units=qty,
            sets=max(1, qty),
            lazy_pending=True,
            lazy_key=lookup,
            qty_as_sets=as_sets,
        )
        add_line(self._lines, line)
        saved = self._save_draft()
        self._refresh_order_table()
        self._key_edit.clear()
        self._alias_edit.clear()
        pending = sum(1 for line in self._lines if line.lazy_pending)
        self._set_status(
            f"{item_text} added as a lazy entry · "
            f"{pending} lazy item(s) on the order · "
            "toggle Lazy off to fetch the missing info from Uline"
        )
        if not saved:
            self._show_disk_failure()

    def _on_lazy_toggled(self, checked: bool) -> None:
        if checked:
            self._set_status(
                "Lazy mode: Add records what you typed, with no Uline page. "
                "Toggle Lazy off to fetch all lazy entries at once."
            )
            return
        keys: list[str] = []
        for line in self._lines:
            if line.lazy_pending:
                key = (line.lazy_key or line.item).strip()
                if key and key not in keys:
                    keys.append(key)
        if not keys:
            self._set_status("Lazy mode off · no lazy entries to fetch")
            return
        if self._busy:
            # The batch cannot start while a lookup, PDF, or lazy fetch is
            # running, so the toggle flips back and lazy entries stay pending.
            self._lazy_btn.blockSignals(True)
            self._lazy_btn.setChecked(True)
            self._lazy_btn.blockSignals(False)
            self._set_error(
                "A Uline action is already running. Toggle Lazy off when it finishes."
            )
            return
        if self._runner is None:
            self._lazy_btn.blockSignals(True)
            self._lazy_btn.setChecked(True)
            self._lazy_btn.blockSignals(False)
            self._set_error(
                "Lookup is not available, so lazy entries stay on the order."
            )
            return
        self._lazy_keys = keys
        self._busy = True
        self._add_btn.setEnabled(False)
        settings = self._refresh_settings()
        timeout_ms = int(settings.get("uline_lookup_timeout_ms", 20000)) * 5
        self._set_status(
            f"Lazy fetch: {len(keys)} tab(s) at once, "
            f"{timeout_ms // 1000} second timeout each…"
        )
        self._task = self._runner(lookup_items_parallel(keys, settings), self._on_lazy_results)

    def _on_lazy_results(self, ok: bool, results, err: str) -> None:
        if self._closing:
            return
        self._busy = False
        self._add_btn.setEnabled(True)
        if not ok:
            # Stay in lazy mode so the entries can be fetched again. Signals
            # are blocked so turning the button back on does not clear the error.
            self._lazy_btn.blockSignals(True)
            self._lazy_btn.setChecked(True)
            self._lazy_btn.blockSignals(False)
            self._set_error(f"Lazy fetch failed: {err}")
            return
        fetched = 0
        failed: list[str] = []
        for key, item, note in results:
            if item is None:
                failed.append(f"{key}: {note}")
                continue
            canonical = normalize_item(item.item)
            put_item(self._cache, item)
            fetched += 1
            for line in self._lines:
                if not self._lazy_line_matches(line, key):
                    continue
                line.item = canonical
                line.lazy_pending = False
                line.lazy_key = ""
                if line.alias:
                    add_alias(self._cache, canonical, line.alias)
        self._collapse_same_items()
        self._save_cache()
        self._refresh_cache_table()
        saved = self._sync_lines()
        if failed:
            self._set_error(
                "Lazy fetch: " + "; ".join(failed)
                + ". Toggle Lazy off again to retry the ones still waiting."
            )
        else:
            self._set_status(f"Lazy fetch: {fetched} item(s) read from Uline")
        if not saved:
            self._show_disk_failure()

    def _on_lookup(self, ok: bool, item, err: str) -> None:
        if self._closing:
            return
        self._busy = False
        self._add_btn.setEnabled(True)
        if not ok:
            self._set_error(f"Uline lookup failed: {err}")
            return
        put_item(self._cache, item)
        # The flag is recorded in _disk_failure; the last display call puts it
        # on the red label after the pack sentence, where it stays.
        self._save_cache()
        self._refresh_cache_table()
        canonical = normalize_item(item.item)
        typed = normalize_item(self._pending_key)
        if canonical != typed:
            self._set_status(
                f"Uline gave {item.item} for {self._pending_key!r}: {item.name}. "
                f"Added as {canonical}."
            )
        self._commit(canonical, item, self._pending_alias, self._pending_key)
        # _commit shows the pack sentence first, then any alias-ownership text
        # and any disk failure, so the last thing on screen is what the user
        # needs to see. _show_disk_failure is a no-op when nothing failed.
        self._show_disk_failure()

    def _sync_line(self, line: OrderLine) -> bool:
        """Refresh one order line from the cached item, keeping the typed quantity.

        Cached edits, re-checks, and pack-size changes update the line, then
        the packs are recomputed so Needed Units stays covered.
        """
        cached = self._cache.get(normalize_item(line.item))
        if cached is None:
            return False
        line.name = cached.name
        line.unit = cached.unit
        line.units_per_set = max(1, cached.units_per_set)
        line.min_sets = max(1, cached.min_sets)
        line.base_unit = cached.base_unit or cached.unit
        line.price = cached.price
        line.tiers = cached.tiers
        if line.qty_as_sets:
            # The typed number was packs, not units. The pack size is known now.
            line.needed_units = max(1, line.sets) * line.units_per_set
            line.qty_as_sets = False
        line.sets = sets_for(line.needed_units, line.units_per_set, line.min_sets)
        return True

    def _lazy_line_matches(self, line: OrderLine, key: str) -> bool:
        if not line.lazy_pending:
            return False
        if line.lazy_key and line.lazy_key == key:
            return True
        return normalize_item(line.item) == normalize_item(key)

    def _collapse_same_items(self) -> None:
        """One row per item after a lazy fetch resolves keywords to item numbers."""
        merged: list[OrderLine] = []
        for line in self._lines:
            add_line(merged, line)
        self._lines = merged

    def _sync_lines(self, note: str = "") -> bool:
        """Pull the order lines from the cache and redraw the totals.

        Returns the draft-save flag, so the caller can put the disk failure on
        the red label after the status sentence is written.
        """
        updated = sum(1 for line in self._lines if self._sync_line(line))
        saved = self._save_draft()
        self._refresh_order_table()
        if note:
            self._set_status(note)
        if updated:
            logger.info("Uline order lines synced from cache (%s line(s))", updated)
        return saved

    def _commit(self, canonical: str, item: UlineItem, alias_text: str, key: str = "") -> None:
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
        alias_note = ""
        # The alias in the alias box is added to the cache, and it is kept for
        # this line when it belongs to the added item. Adding by alias in the
        # key box registers that alias too, and the line carries it when the
        # alias box is empty.
        typed_alias = ""
        if key and normalize_item(key) != canonical:
            typed_alias = key.strip()
        changed = False
        if typed_alias:
            owner = add_alias(self._cache, canonical, typed_alias)
            changed = True
            if owner == canonical:
                line.alias = typed_alias
            else:
                alias_note = (
                    f"{typed_alias} already belongs to {owner}, so it was not "
                    f"saved for {canonical}. The line was added."
                )
        if alias_text:
            owner = add_alias(self._cache, canonical, alias_text)
            changed = True
            if owner == canonical:
                # The alias box is the name the user asked to store on the line.
                line.alias = alias_text
            else:
                # An alias owned by another item stays with that item. A key-box
                # alias that does belong to this item stays on the line.
                alias_note = (
                    f"{alias_text} already belongs to {owner}, so it was not "
                    f"saved for {canonical}. The line was added."
                )
        if changed:
            self._save_cache()
            self._refresh_cache_table()
        add_line(self._lines, line)
        self._sync_lines()
        self._key_edit.clear()
        self._alias_edit.clear()
        added = next((l for l in self._lines if l.item == canonical), line)
        pack = added.unit
        base = (added.base_unit or added.unit).lower()
        joiner = "of" if base == pack.lower() else "×"
        note = ""
        if self._mode_combo.currentText() == MODE_UNITS and added.needed_units < units * min_sets:
            note = f" (raised to the {min_sets} {pack} minimum)"
        if not item.in_stock:
            note += " (out of stock)"
        self._set_status(
            f"{canonical}: {added.sets} {pack}(s) {joiner} {units:,} {base}(s) "
            f"= {added.needed_units:,} {base}(s) at ${added.price_at(added.sets):.2f}/{pack} "
            f"= ${added.total():.2f}{note}"
        )
        # _set_status clears the red label, so ownership text and any disk
        # failure are applied after it; they are the last thing on screen.
        self._show_disk_failure(alias_note)

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
        for column, width in enumerate(_header_widths(CACHE_COLUMNS, {0: 7, 2: 6})):
            if column != 1:
                header.resizeSection(column, width)
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
                "Yes" if item.in_stock else "No",
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
        self._save_cache()
        self._refresh_cache_table()
        if owner != normalize_item(key):
            self._show_disk_failure(
                f"{alias} already belongs to {owner}. Remove it from {owner} "
                "before using it here."
            )
            return
        self._alias_input.clear()
        self._set_status(f"{alias} → {key}")
        self._show_disk_failure()

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
        self._save_cache()
        self._refresh_cache_table()
        self._alias_input.clear()
        self._show_disk_failure()

    def _edit_item(self) -> None:
        key = self._selected_item()
        if not key:
            return
        current = self._cache[normalize_item(key)]
        editor = UlineItemEditor(current, self)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        values = editor.values()
        typed_price = _float_text(values["price"])
        if typed_price is not None and abs(typed_price - current.price) > 0.01:
            # A hand-edited price must be what the order charges. With Uline's
            # tiers kept, OrderLine.price_at runs tier_price and holds the lower
            # tier, so raising the price never reaches the order line. An empty
            # tier list makes tier_price return the edited price for every pack
            # count. Re-Lookup brings Uline's own tiers back, and editing the
            # name, unit, or pack size leaves the tiers in place.
            values["tiers"] = ()
        updated = edit_item(self._cache, key, **values)
        self._save_cache()
        self._refresh_cache_table()
        self._sync_lines(f"{updated.item} updated in {len(self._lines)} order line(s)")
        self._show_disk_failure()

    def _re_lookup(self) -> None:
        key = self._selected_item()
        if not key:
            return
        if self._busy or self._runner is None:
            self._set_error("Lookup is not available right now")
            return
        self._busy = True
        self._add_btn.setEnabled(False)
        self._settings = load_settings()
        self._set_status(f"Re-checking {key} on Uline…")
        self._task = self._runner(lookup_item(key, self._settings), self._on_re_lookup)

    def _on_re_lookup(self, ok: bool, item, err: str) -> None:
        if self._closing:
            return
        self._busy = False
        self._add_btn.setEnabled(True)
        if not ok:
            self._set_error(f"Re-lookup failed: {err}")
            return
        put_item(self._cache, item)
        self._save_cache()
        self._refresh_cache_table()
        self._sync_lines(f"{item.item} re-checked from Uline")
        # The status sentence from the sync is written first, so a cache-draft
        # disk failure lands on the red label after it and stays visible.
        self._show_disk_failure()

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
        self._save_cache()
        self._refresh_cache_table()
        self._show_disk_failure()

    # ------------------------------------------------------------------ status

    def _set_status(self, text: str) -> None:
        self._error.setText("")
        self._status.setText(text)

    def _set_error(self, text: str) -> None:
        self._status.setText("")
        self._error.setText(text)
        logger.warning("Uline dialog: %s", text)

    def _save_cache(self) -> bool:
        """Write the cache. True on success, False on a disk failure.

        The message is recorded in `_disk_failure`; the caller decides what
        stays on screen, so a status write in the same action does not wipe the
        red label. `save_cache` itself shows nothing.
        """
        try:
            write_cache(self._cache)
        except OSError as exc:
            self._disk_failure = f"Cache not saved to disk: {exc}"
            logger.error("Uline cache write failed: %s", exc)
            return False
        return True

    def _save_draft(self) -> bool:
        """Write the draft. True on success, False on a disk failure. See `_save_cache`."""
        try:
            write_draft(self._lines)
        except OSError as exc:
            self._disk_failure = f"Order not saved to disk: {exc}"
            logger.error("Uline draft write failed: %s", exc)
            return False
        return True

    def _show_disk_failure(self, extra: str = "") -> None:
        """Put the disk failure (and any extra text) on the red label.

        No-op when the last save succeeded. `extra` is put first, so the
        alias-ownership sentence and the disk message both stay readable.
        """
        parts = [p for p in (extra, self._disk_failure) if p]
        self._disk_failure = ""
        if parts:
            self._set_error(" ".join(parts))

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._closing = True
        task = self._task
        if task is not None and not task.done():
            # The lookup is still navigating. Cancel it so a late result cannot
            # write to the cache, the draft, or the widgets after the close.
            task.cancel()
        self._save_draft()
        self._save_cache()
        super().closeEvent(event)
