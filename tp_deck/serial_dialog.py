"""Paste dialog for batch serial entry on an open ERP sales order."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
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
QLineEdit, QTextEdit {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    padding: 4px 6px;
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
QPushButton#ordersBtn {
    background-color: #1E293B;
    color: #38BDF8;
    border: 1px solid #38BDF8;
}
QPushButton#ordersBtn:hover {
    background-color: #0EA5E9;
    color: #0F172A;
}
"""

ORDERS_QSS = """
QDialog {
    background-color: #0F172A;
    color: #F8FAFC;
}
QLabel {
    color: #F8FAFC;
    background: transparent;
}
QTableWidget {
    background-color: #0F172A;
    color: #F8FAFC;
    gridline-color: #334155;
    border: 1px solid #334155;
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
}
QTableWidget::item:selected {
    background-color: #0EA5E9;
    color: #0F172A;
}
QHeaderView::section {
    background-color: #1E293B;
    color: #38BDF8;
    border: 1px solid #334155;
    font-weight: bold;
    padding: 4px;
}
QTableWidget QComboBox, QTableWidget { selection-background-color: #0EA5E9; }
QCheckBox {
    background-color: transparent;
    color: #F8FAFC;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background-color: #1E293B;
    border: 1px solid #334155;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: #475569;
    border-radius: 3px;
    min-height: 12px;
    min-width: 12px;
}
QScrollBar::add-line, QScrollBar::add-page {
    background-color: transparent;
    width: 0;
    height: 0;
}
QScrollBar::sub-line, QScrollBar::sub-page {
    background-color: transparent;
    width: 0;
    height: 0;
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
QPushButton#queueBtn {
    background-color: #0EA5E9;
    color: #0F172A;
    font-weight: bold;
}
QPushButton#queueBtn:hover {
    background-color: #38BDF8;
}
QToolTip {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #334155;
    padding: 4px;
}
"""


def parse_serials(text: str) -> list[str]:
    """One serial per line, blank lines dropped."""
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def order_summaries(lines) -> list[tuple[str, str, int, int]]:
    """Group scraped lines by order: (order, buyer, items seen, total qty).

    Orders keep the order they arrived in, so newest seen is last. Items are
    the distinct SKUs, and Qty sums the line quantities.
    """
    buyers: dict[str, str] = {}
    skus: dict[str, list[str]] = {}
    qtys: dict[str, int] = {}
    order_ids: list[str] = []
    for order, buyer, sku, qty in lines:
        key = str(order)
        if key not in buyers:
            buyers[key] = str(buyer)
            skus[key] = []
            qtys[key] = 0
            order_ids.append(key)
        if sku not in skus[key]:
            skus[key].append(str(sku))
        qtys[key] += max(1, int(qty))
    return [
        (key, buyers[key], len(skus[key]), qtys[key])
        for key in order_ids
    ]


class CurrentOrdersWindow(QDialog):
    """The orders seen in the last scrape, picked to work on.

    Double-click one row to prefill the order box and close. Tick rows for a
    multi-order queue: Submit then runs one batch serial job per ticked order.
    """

    def __init__(
        self,
        orders: list[tuple[str, str, int, int]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._orders = list(orders)
        self._picked: list[str] = []
        self.setWindowTitle("Current Orders")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(520, 380)
        self.setStyleSheet(ORDERS_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        hint = QLabel(
            "Double-click one order to put it in the order box and close. "
            "Tick rows to queue a batch serial run for each ticked order."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget(len(self._orders), 4)
        self._table.setHorizontalHeaderLabels(["", "Order", "Buyer", "Items / Qty"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, (order, buyer, items, qty) in enumerate(self._orders):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Unchecked)
            self._table.setItem(row, 0, check)
            self._table.setItem(row, 1, QTableWidgetItem(order))
            self._table.setItem(row, 2, QTableWidgetItem(buyer))
            self._table.setItem(row, 3, QTableWidgetItem(f"{items} item(s) · qty {qty}"))
        self._table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        queue = QPushButton("Queue Selected")
        queue.setObjectName("queueBtn")
        queue.clicked.connect(self._queue_selected)
        buttons.addWidget(queue)
        layout.addLayout(buttons)

    def _on_double_click(self, row: int, column: int) -> None:
        # A double-click picks exactly one order: prefill and close.
        if 0 <= row < len(self._orders):
            self._picked = [self._orders[row][0]]
            self.accept()

    def _queue_selected(self) -> None:
        picked = []
        for row in range(len(self._orders)):
            item = self._table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                picked.append(self._orders[row][0])
        if not picked:
            self.reject()
            return
        self._picked = picked
        self.accept()

    def selected_orders(self) -> list[str]:
        return list(self._picked)


class SerialDialog(QDialog):
    """Collect a pasted list of serial numbers. Stays open when the list is empty."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        orders: Optional[list[tuple[str, str, int, int]]] = None,
    ) -> None:
        super().__init__(parent)
        self._orders = list(orders or [])
        self._multi_orders: list[str] = []
        self.setWindowTitle("TP DECK — Batch Serial")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(360, 320)
        self.setStyleSheet(DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        hint = QLabel(
            "Paste one serial number per line. "
            "SO-12345 searches the SO # column. "
            "An eBay order (12-34567-89012) or a digits-only store order "
            "searches the PO column. A digits-only number also tries SO # "
            "when PO has no match. Leave it blank to use the one sales "
            "order already open."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._orders_btn = QPushButton("Current Orders")
        self._orders_btn.setObjectName("ordersBtn")
        self._orders_btn.setToolTip(
            "The orders seen in the last scrape. Pick one to fill the order box, "
            "or tick rows to queue a run for each one."
        )
        self._orders_btn.clicked.connect(self._open_current_orders)
        layout.addWidget(self._orders_btn)

        self._order = QLineEdit()
        self._order.setPlaceholderText("SO-12345, eBay order, or store order")
        layout.addWidget(self._order)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText("Serial Numbers")
        self._editor.setAcceptRichText(False)
        layout.addWidget(self._editor)

        self._error = QLabel("")
        self._error.setObjectName("errorLabel")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        submit_btn = QPushButton("Submit")
        submit_btn.setObjectName("submitBtn")
        submit_btn.clicked.connect(self._submit)
        buttons.addWidget(submit_btn)
        layout.addLayout(buttons)

    def order_number(self) -> str:
        return self._order.text().strip()

    def serials(self) -> list[str]:
        return parse_serials(self._editor.toPlainText())

    def multi_orders(self) -> list[str]:
        """Orders ticked in Current Orders, for a run per order."""
        return list(self._multi_orders)

    def _open_current_orders(self) -> None:
        if not self._orders:
            self._error.setText(
                "No orders seen in the last scrape. Run a scrape, then open "
                "Current Orders."
            )
            return
        window = CurrentOrdersWindow(self._orders, self)
        if window.exec() != QDialog.DialogCode.Accepted:
            return
        picked = window.selected_orders()
        self._error.setText("")
        if len(picked) == 1:
            # Single pick prefills the box; the queued run is one batch.
            self._order.setText(picked[0])
            self._multi_orders = []
        elif len(picked) > 1:
            # Ticked rows queue one run per order. Submit uses the same serial
            # list on every order, with that order number filled in for each run.
            self._multi_orders = picked
            self._order.setText(picked[0])
            self._error.setText(
                f"{len(picked)} orders selected: {', '.join(picked)}. "
                "Submit enters this serial list on each of them."
            )

    def _submit(self) -> None:
        if not self.serials():
            self._error.setText("Paste at least one serial number.")
            return
        self._error.setText("")
        self.accept()
