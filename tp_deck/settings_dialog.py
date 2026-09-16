"""Settings dialog — mode, ports, URL patterns, CSS selectors, setup help."""

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
    QLineEdit,
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
QSpinBox, QLineEdit {
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

Fill CSS selectors below (DevTools → Copy → Copy selector).
Execute scrapes Order + SKU from the focused eBay tab, types SKU into ERP,
reads Location, then copies: [Order] - [SKU] - [Location]
"""


class SettingsDialog(QDialog):
    """Toggle Single/Dual mode and edit ports, URL patterns, and selectors."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — Settings")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumSize(480, 640)
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

        ports_box = QGroupBox("CDP Ports & Patterns")
        ports_form = QFormLayout(ports_box)
        self.ebay_port_spin = QSpinBox()
        self.ebay_port_spin.setRange(1024, 65535)
        ports_form.addRow("eBay port:", self.ebay_port_spin)
        self.erp_port_spin = QSpinBox()
        self.erp_port_spin.setRange(1024, 65535)
        ports_form.addRow("ERP port:", self.erp_port_spin)
        self.ebay_pattern_edit = QLineEdit()
        ports_form.addRow("eBay URL contains:", self.ebay_pattern_edit)
        self.erp_pattern_edit = QLineEdit()
        self.erp_pattern_edit.setPlaceholderText("required in single mode")
        ports_form.addRow("ERP URL contains:", self.erp_pattern_edit)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1000, 120000)
        self.timeout_spin.setSingleStep(500)
        self.timeout_spin.setSuffix(" ms")
        ports_form.addRow("Wait timeout:", self.timeout_spin)
        layout.addWidget(ports_box)

        sel_box = QGroupBox("CSS Selectors")
        sel_form = QFormLayout(sel_box)
        self.sel_order = QLineEdit()
        self.sel_sku = QLineEdit()
        self.sel_erp_input = QLineEdit()
        self.sel_erp_location = QLineEdit()
        sel_form.addRow("eBay order id:", self.sel_order)
        sel_form.addRow("eBay SKU:", self.sel_sku)
        sel_form.addRow("ERP SKU input:", self.sel_erp_input)
        sel_form.addRow("ERP location:", self.sel_erp_location)
        layout.addWidget(sel_box)

        help_box = QGroupBox("Setup Instructions")
        help_layout = QVBoxLayout(help_box)
        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setPlainText(SETUP_INSTRUCTIONS)
        self.help_text.setMinimumHeight(120)
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
        self.ebay_pattern_edit.setText(
            str(settings.get("ebay_url_pattern", "ebay.com/sh/ord"))
        )
        self.erp_pattern_edit.setText(str(settings.get("erp_url_pattern", "")))
        self.timeout_spin.setValue(int(settings.get("wait_timeout_ms", 10000)))

        selectors = settings.get("selectors") or {}
        self.sel_order.setText(str(selectors.get("ebay_order_id", "")))
        self.sel_sku.setText(str(selectors.get("ebay_sku", "")))
        self.sel_erp_input.setText(str(selectors.get("erp_sku_input", "")))
        self.sel_erp_location.setText(str(selectors.get("erp_location", "")))
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
            ebay_url_pattern=self.ebay_pattern_edit.text().strip(),
            erp_url_pattern=self.erp_pattern_edit.text().strip(),
            wait_timeout_ms=self.timeout_spin.value(),
            selectors={
                "ebay_order_id": self.sel_order.text().strip(),
                "ebay_sku": self.sel_sku.text().strip(),
                "erp_sku_input": self.sel_erp_input.text().strip(),
                "erp_location": self.sel_erp_location.text().strip(),
            },
        )
        logger.info(
            "Settings saved: mode=%s ebay_port=%s erp_port=%s",
            mode,
            self.ebay_port_spin.value(),
            self.erp_port_spin.value(),
        )
        self.accept()
