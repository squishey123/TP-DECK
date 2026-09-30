"""Paste dialog for batch serial entry on an open ERP sales order."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
"""


def parse_serials(text: str) -> list[str]:
    """One serial per line, blank lines dropped."""
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


class SerialDialog(QDialog):
    """Collect a pasted list of serial numbers. Stays open when the list is empty."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — Batch Serial")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(360, 320)
        self.setStyleSheet(DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        hint = QLabel(
            "Paste one serial number per line. A service order number opens "
            "that order first. Leave it blank to use the one sales order "
            "already open."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._order = QLineEdit()
        self._order.setPlaceholderText("Service order number")
        layout.addWidget(self._order)

        self._editor = QTextEdit()
        self._editor.setPlaceholderText("Serial numbers from Google Sheets")
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

    def _submit(self) -> None:
        if not self.serials():
            self._error.setText("Paste at least one serial number.")
            return
        self._error.setText("")
        self.accept()
