"""Settings dialog — browser mode toggle and CDP setup instructions."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tp_deck.settings_manager import load_settings, update_settings

logger = logging.getLogger("tpdeck")

DIALOG_QSS = """
QDialog, QGroupBox {
    background-color: #0F172A;
    color: #F8FAFC;
}
QGroupBox {
    border: 1px solid #334155;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #0EA5E9;
}
QLabel, QRadioButton {
    color: #F8FAFC;
    background: transparent;
}
QSpinBox {
    background-color: #1E293B;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 3px;
    padding: 3px 6px;
}
QTextEdit {
    background-color: #1E293B;
    color: #CBD5E1;
    border: 1px solid #334155;
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 11px;
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
QPushButton#saveBtn {
    background-color: #0EA5E9;
    color: #0F172A;
    font-weight: bold;
}
QPushButton#saveBtn:hover {
    background-color: #38BDF8;
}
"""

SETUP_INSTRUCTIONS = """\
Chrome CDP launch (run once per browser profile):

Single mode (port 9222):
  chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\tpdeck-chrome"

Dual mode:
  eBay  → chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\tpdeck-ebay"
  ERP   → chrome.exe --remote-debugging-port=9223 --user-data-dir="%TEMP%\\tpdeck-erp"

Then open your eBay Seller Hub order tab and ERP tab.
TP DECK connects over CDP — it never launches a new browser.
"""


class SettingsDialog(QDialog):
    """Toggle Single/Dual browser mode and edit CDP ports."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — Settings")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumSize(420, 420)
        self.setStyleSheet(DIALOG_QSS)

        self._build_ui()
        self._load_into_form()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        mode_box = QGroupBox("Browser Connection Mode")
        mode_layout = QVBoxLayout(mode_box)

        self.mode_group = QButtonGroup(self)
        self.single_radio = QRadioButton("Single — one Chrome on the eBay port")
        self.dual_radio = QRadioButton(
            "Dual — eBay on one port, ERP on another"
        )
        self.mode_group.addButton(self.single_radio, 0)
        self.mode_group.addButton(self.dual_radio, 1)
        mode_layout.addWidget(self.single_radio)
        mode_layout.addWidget(self.dual_radio)
        self.single_radio.toggled.connect(self._on_mode_toggled)
        layout.addWidget(mode_box)

        ports_box = QGroupBox("CDP Ports")
        ports_form = QFormLayout(ports_box)

        self.ebay_port_spin = QSpinBox()
        self.ebay_port_spin.setRange(1024, 65535)
        ports_form.addRow("eBay port:", self.ebay_port_spin)

        self.erp_port_spin = QSpinBox()
        self.erp_port_spin.setRange(1024, 65535)
        ports_form.addRow("ERP port:", self.erp_port_spin)
        layout.addWidget(ports_box)

        help_box = QGroupBox("Setup Instructions")
        help_layout = QVBoxLayout(help_box)
        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setPlainText(SETUP_INSTRUCTIONS)
        self.help_text.setMinimumHeight(160)
        help_layout.addWidget(self.help_text)
        layout.addWidget(help_box)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _load_into_form(self) -> None:
        settings = load_settings()
        mode = str(settings.get("mode", "single")).lower()
        if mode == "dual":
            self.dual_radio.setChecked(True)
        else:
            self.single_radio.setChecked(True)

        self.ebay_port_spin.setValue(int(settings.get("ebay_port", 9222)))
        self.erp_port_spin.setValue(int(settings.get("erp_port", 9223)))
        self._on_mode_toggled()

    def _on_mode_toggled(self) -> None:
        dual = self.dual_radio.isChecked()
        self.erp_port_spin.setEnabled(dual)

    def _save(self) -> None:
        mode = "dual" if self.dual_radio.isChecked() else "single"
        update_settings(
            mode=mode,
            ebay_port=self.ebay_port_spin.value(),
            erp_port=self.erp_port_spin.value(),
        )
        logger.info(
            "Settings saved: mode=%s ebay_port=%s erp_port=%s",
            mode,
            self.ebay_port_spin.value(),
            self.erp_port_spin.value(),
        )
        self.accept()
