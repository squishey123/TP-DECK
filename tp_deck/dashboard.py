"""PySide6 floating dashboard — compact always-on-top tool window."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tp_deck.settings_manager import load_settings, update_settings

logger = logging.getLogger("tpdeck")


def _format_status(text: str) -> str:
    """Split long success/error strings onto readable lines."""
    raw = str(text or "").strip() or "Idle"
    if raw.startswith("Success"):
        _, _, rest = raw.partition("—")
        parts = [p.strip() for p in rest.split(",") if p.strip()]
        return "Success\n" + "\n".join(parts) if parts else "Success"
    if raw.startswith("Error"):
        _, sep, rest = raw.partition("—")
        detail = rest.strip() if sep else raw.replace("Error", "", 1).strip(" —")
        return f"Error\n{detail}" if detail else "Error"
    return raw


# Techyparts enterprise hardware dark theme
THEME_QSS = """
QMainWindow, QWidget#dashboardRoot {
    background-color: #0F172A;
    color: #F8FAFC;
}
QLabel#titleLabel {
    color: #0EA5E9;
    font-size: 14px;
    font-weight: bold;
}
QLabel#statusLabel {
    color: #F8FAFC;
    font-size: 11px;
    min-height: 52px;
}
QPushButton {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #0EA5E9;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
}
QPushButton:hover {
    background-color: #0EA5E9;
    color: #0F172A;
}
QPushButton:disabled {
    background-color: #1E293B;
    color: #64748B;
    border-color: #334155;
}
QPushButton#executeBtn, QPushButton#pickBtn {
    background-color: #0EA5E9;
    color: #0F172A;
    font-weight: bold;
    min-height: 28px;
}
QPushButton#executeBtn:hover, QPushButton#pickBtn:hover {
    background-color: #38BDF8;
}
QPushButton#executeBtn:disabled, QPushButton#pickBtn:disabled {
    background-color: #334155;
    color: #94A3B8;
    border-color: #475569;
}
QPushButton#stopBtn {
    background-color: #7F1D1D;
    color: #FCA5A5;
    border: 1px solid #FCA5A5;
    font-weight: bold;
}
QPushButton#stopBtn:hover {
    background-color: #991B1B;
    color: #FEE2E2;
}
QPushButton#settingsBtn {
    border-color: #64748B;
}
"""


class Dashboard(QMainWindow):
    """Compact floating panel that stays on top and remembers screen position."""

    def __init__(
        self,
        on_execute: Optional[Callable[[], None]] = None,
        on_pick_list: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
        on_open_settings: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_execute = on_execute
        self._on_pick_list = on_pick_list
        self._on_stop = on_stop
        self._on_open_settings = on_open_settings
        self._persist_enabled = False

        self.setWindowTitle("TP DECK")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setFixedSize(300, 250)
        self.setStyleSheet(THEME_QSS)

        self._build_ui()
        self._restore_position()
        self._persist_enabled = True
        self.set_status("Idle")

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("dashboardRoot")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = QLabel("TP DECK")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(52)
        layout.addWidget(self.status_label)

        self.execute_btn = QPushButton("Scrape eBay Orders")
        self.execute_btn.setObjectName("executeBtn")
        self.execute_btn.clicked.connect(self._handle_execute)
        layout.addWidget(self.execute_btn)

        self.pick_btn = QPushButton("Generate Pick List")
        self.pick_btn.setObjectName("pickBtn")
        self.pick_btn.clicked.connect(self._handle_pick_list)
        layout.addWidget(self.pick_btn)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.stop_btn = QPushButton("🛑 Emergency Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.clicked.connect(self._handle_stop)
        row.addWidget(self.stop_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setFixedWidth(36)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._handle_settings)
        row.addWidget(self.settings_btn)

        layout.addLayout(row)

    def _restore_position(self) -> None:
        settings = load_settings()
        x = int(settings.get("window_x", 100))
        y = int(settings.get("window_y", 100))
        self.move(x, y)
        logger.info("Restored window position to (%s, %s)", x, y)

    def _persist_position(self) -> None:
        if not self._persist_enabled:
            return
        pos = self.pos()
        update_settings(window_x=pos.x(), window_y=pos.y())
        logger.debug("Saved window position (%s, %s)", pos.x(), pos.y())

    def moveEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().moveEvent(event)
        self._persist_position()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt override
        self._persist_position()
        super().closeEvent(event)

    def set_status(self, text: str) -> None:
        self.status_label.setText(_format_status(text))
        self.status_label.setToolTip(str(text or "").strip())

    def set_processing(self, active: bool, job: str = "orders") -> None:
        """Disable action buttons while a run is in flight; restore in finally."""
        if active:
            self.execute_btn.setEnabled(False)
            self.pick_btn.setEnabled(False)
            if job == "picklist":
                self.pick_btn.setText("⏳ Processing...")
            else:
                self.execute_btn.setText("⏳ Processing...")
            self.set_status("Processing")
        else:
            self.execute_btn.setEnabled(True)
            self.pick_btn.setEnabled(True)
            self.execute_btn.setText("Scrape eBay Orders")
            self.pick_btn.setText("Generate Pick List")

    def _handle_execute(self) -> None:
        if self._on_execute:
            self._on_execute()

    def _handle_pick_list(self) -> None:
        if self._on_pick_list:
            self._on_pick_list()

    def _handle_stop(self) -> None:
        if self._on_stop:
            self._on_stop()

    def _handle_settings(self) -> None:
        if self._on_open_settings:
            self._on_open_settings()
