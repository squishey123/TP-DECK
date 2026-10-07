"""Dialogs for unknown SKUs and the saved alternative-SKU list."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tp_deck.sku_overrides import (
    load_overrides,
    replace_overrides,
    validate_override,
)

DIALOG_QSS = """
QDialog {
    background-color: #0F172A;
    color: #F8FAFC;
}
QLabel {
    color: #F8FAFC;
    background: transparent;
}
QLabel#errorLabel {
    color: #FCA5A5;
}
QLineEdit, QSpinBox {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 4px 6px;
    min-height: 24px;
}
QTableWidget {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #334155;
    gridline-color: #334155;
    selection-background-color: #0EA5E9;
    selection-color: #0F172A;
}
QHeaderView::section {
    background-color: #0F172A;
    color: #0EA5E9;
    border: 1px solid #334155;
    padding: 4px;
    font-weight: bold;
}
QPushButton {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #0EA5E9;
    border-radius: 4px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #0EA5E9;
    color: #0F172A;
}
QPushButton#submitBtn {
    background-color: #0EA5E9;
    color: #0F172A;
    font-weight: bold;
}
QPushButton#submitBtn:hover {
    background-color: #38BDF8;
}
QCheckBox {
    color: #F8FAFC;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border: 2px solid #64748B;
    border-radius: 3px;
    background-color: #0F172A;
}
QCheckBox::indicator:checked {
    border: 2px solid #0EA5E9;
    background-color: #0EA5E9;
}
"""


class UnknownSkuDialog(QDialog):
    """One row per unknown SKU. An empty alternative skips that SKU."""

    def __init__(
        self,
        skus: list[str],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — Unknown SKUs")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(640, 280)
        self.setStyleSheet(DIALOG_QSS)
        self._mappings: list[tuple[str, str, int]] = []
        self._no_sisters: list[str] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        count = len(skus)
        noun = "SKU" if count == 1 else "SKUs"
        hint = QLabel(
            f"{count} {noun} came back Unknown. "
            "Enter an alternative SKU to look up instead, and how many of "
            "that SKU equal one of the original. Leave a row blank to skip it "
            "until tomorrow. Check No sister to stop asking for that SKU."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._error = QLabel("")
        self._error.setObjectName("errorLabel")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        self._table = QTableWidget(len(skus), 4, self)
        self._table.setHorizontalHeaderLabels(
            ["Original SKU", "Alternative SKU", "Qty", "No sister"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._edits: list[QLineEdit] = []
        self._spins: list[QSpinBox] = []
        self._nones: list[QCheckBox] = []
        for row, sku in enumerate(skus):
            item = QTableWidgetItem(sku)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, item)
            edit = QLineEdit()
            edit.setPlaceholderText("alternative SKU")
            spin = QSpinBox()
            spin.setRange(1, 9999)
            spin.setValue(1)
            spin.setToolTip("How many of the alternative equal one original")
            none_box = QCheckBox()
            none_box.setToolTip("This SKU has no alternative. Stop asking.")
            none_box.toggled.connect(
                lambda checked, edit=edit, spin=spin: self._toggle_none(
                    checked, edit, spin
                )
            )
            self._table.setCellWidget(row, 1, edit)
            self._table.setCellWidget(row, 2, spin)
            self._table.setCellWidget(row, 3, none_box)
            self._table.setRowHeight(row, 36)
            self._edits.append(edit)
            self._spins.append(spin)
            self._nones.append(none_box)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        skip = QPushButton("Skip")
        skip.clicked.connect(self.reject)
        buttons.addWidget(skip)
        save = QPushButton("Save")
        save.setObjectName("submitBtn")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        if self._edits:
            self._edits[0].setFocus()

    def mappings(self) -> list[tuple[str, str, int]]:
        return list(self._mappings)

    def no_sisters(self) -> list[str]:
        return list(self._no_sisters)

    def _toggle_none(self, checked: bool, edit: QLineEdit, spin: QSpinBox) -> None:
        edit.setEnabled(not checked)
        spin.setEnabled(not checked)

    def _save(self) -> None:
        found: list[tuple[str, str, int]] = []
        nones: list[str] = []
        for row, edit in enumerate(self._edits):
            original = self._table.item(row, 0).text().strip()
            if self._nones[row].isChecked():
                nones.append(original)
                continue
            sister = edit.text().strip()
            if not sister:
                continue
            qty = self._spins[row].value()
            error = validate_override(original, sister, qty)
            if error:
                self._error.setText(error)
                return
            found.append((original, sister, qty))
        self._mappings = found
        self._no_sisters = nones
        self._error.setText("")
        self.accept()


class SkuOverrideEditor(QDialog):
    """Add, change, or remove saved alternative SKUs."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — Alternative SKUs")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(680, 360)
        self.setStyleSheet(DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        hint = QLabel(
            "When a scrape finds the original SKU, TP DECK looks up the "
            "alternative and uses its location. Qty is how many of the "
            "alternative equal one original (a kit of 3 uses 3). "
            "No sister means that SKU is never asked again."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._error = QLabel("")
        self._error.setObjectName("errorLabel")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(
            ["Original SKU", "Alternative SKU", "Qty", "No sister"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self._table)

        for original, item in load_overrides().items():
            self._append_row(original, item.sister, item.qty, item.none)

        row_buttons = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._append_row("", "", 1, False))
        row_buttons.addWidget(add)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected)
        row_buttons.addWidget(remove)
        row_buttons.addStretch()
        layout.addLayout(row_buttons)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Save")
        save.setObjectName("submitBtn")
        save.clicked.connect(self._save)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _append_row(
        self,
        original: str,
        sister: str,
        qty: int,
        none: bool,
    ) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(original))
        sister_item = QTableWidgetItem("" if none else sister)
        self._table.setItem(row, 1, sister_item)
        spin = QSpinBox()
        spin.setRange(1, 9999)
        spin.setValue(max(1, int(qty)))
        self._table.setCellWidget(row, 2, spin)
        none_box = QCheckBox()
        none_box.setChecked(none)
        none_box.toggled.connect(
            lambda checked, item=sister_item, spin=spin: self._toggle_editor_none(
                checked, item, spin
            )
        )
        self._table.setCellWidget(row, 3, none_box)
        if none:
            spin.setEnabled(False)
        self._table.setRowHeight(row, 36)

    def _toggle_editor_none(
        self,
        checked: bool,
        sister_item: QTableWidgetItem,
        spin: QSpinBox,
    ) -> None:
        spin.setEnabled(not checked)
        if checked:
            sister_item.setText("")

    def _remove_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            row = self._table.rowCount() - 1
        if row >= 0:
            self._table.removeRow(row)

    def _cell_text(self, row: int, column: int) -> str:
        item = self._table.item(row, column)
        if item is None:
            return ""
        return item.text().strip()

    def _save(self) -> None:
        entries: list[tuple[str, str, int, bool]] = []
        for row in range(self._table.rowCount()):
            original = self._cell_text(row, 0)
            sister = self._cell_text(row, 1)
            none_box = self._table.cellWidget(row, 3)
            none = isinstance(none_box, QCheckBox) and none_box.isChecked()
            if not original and not sister and not none:
                continue
            spin = self._table.cellWidget(row, 2)
            qty = spin.value() if isinstance(spin, QSpinBox) else 1
            entries.append((original, sister, qty, none))
        try:
            replace_overrides(entries)
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "TP DECK", f"Could not save the list.\n{exc}")
            return
        self.accept()
