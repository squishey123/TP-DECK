"""Settings dialog — mode, ports, URL patterns, CSS selectors, setup help."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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


def _clamp_int(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return low
    try:
        number = int(value)
    except ValueError:
        return low
    return max(low, min(high, number))

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
QLabel, QRadioButton, QCheckBox {
    color: #F8FAFC;
    background: transparent;
}
QRadioButton, QCheckBox {
    spacing: 10px;
    padding: 6px 8px;
    border-radius: 4px;
}
QRadioButton:hover, QCheckBox:hover {
    background-color: #1E293B;
}
QRadioButton:checked {
    color: #38BDF8;
    font-weight: bold;
    background-color: #1E293B;
    border: 1px solid #0EA5E9;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #64748B;
    background-color: #0F172A;
}
QRadioButton::indicator {
    border-radius: 9px;
}
QCheckBox::indicator {
    border-radius: 3px;
}
QRadioButton::indicator:checked {
    border: 2px solid #0EA5E9;
    background-color: #0EA5E9;
}
QCheckBox::indicator:checked {
    border: 2px solid #0EA5E9;
    background-color: #0EA5E9;
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
Opening TP DECK starts Chromium minimized and restores the last session.
Log into eBay Seller Hub and the ERP in that window once. The profile stays
in the browser folder next to the app, separate from installed Chrome.
The first open downloads Chromium (network required once).

If that automatic start fails, launch a browser yourself:

Single mode (port 9222):
  chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\tpdeck-chrome"

Dual mode:
  eBay  → chrome.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\tpdeck-ebay"
  ERP   → chrome.exe --remote-debugging-port=9223 --user-data-dir="%TEMP%\\tpdeck-erp"

Fill CSS selectors below (DevTools → Copy → Copy selector).
Execute scrapes Order + SKU from the eBay order tab (focus only if several match), types SKU into ERP,
reads Location, then copies: [Order] - [Buyer] - [SKU] - [Location]
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
        self.cache_enabled_check = QCheckBox(
            "Cache SKU → location results (12 hours)"
        )
        self.cache_enabled_check.setToolTip(
            "Turn off when bin locations are changing often so every run "
            "looks up live ERP data."
        )
        ports_form.addRow("", self.cache_enabled_check)
        self.refresh_hold_check = QCheckBox("Skip eBay refresh when recent")
        self.refresh_hold_check.setToolTip(
            "If Seller Hub was reloaded inside this window, Execute and "
            "Pick List continue without waiting for another refresh."
        )
        self.refresh_hold_check.toggled.connect(self._on_refresh_hold_toggled)
        ports_form.addRow("", self.refresh_hold_check)
        self.refresh_hold_spin = QSpinBox()
        self.refresh_hold_spin.setRange(1, 180)
        self.refresh_hold_spin.setSuffix(" min")
        self.refresh_hold_spin.setToolTip(
            "How long a completed eBay refresh stays valid. "
            "Uncheck the option above to refresh on every run."
        )
        ports_form.addRow("eBay refresh hold:", self.refresh_hold_spin)
        self.results_popup_check = QCheckBox("Show results popup after copy")
        self.results_popup_check.setToolTip(
            "After Scrape eBay Orders or Generate Pick List, open a small "
            "window with the text that was copied to the clipboard."
        )
        ports_form.addRow("", self.results_popup_check)
        self.autocycle_spin = QSpinBox()
        self.autocycle_spin.setRange(1, 180)
        self.autocycle_spin.setSuffix(" min")
        self.autocycle_spin.setToolTip(
            "Wait between auto-cycle scrapes. The cycle button on the main "
            "window turns this on. Those passes fill the SKU cache only."
        )
        ports_form.addRow("Auto-cycle wait:", self.autocycle_spin)
        self.sales_order_pattern_edit = QLineEdit()
        self.sales_order_pattern_edit.setPlaceholderText("SalesOrder.aspx")
        self.sales_order_pattern_edit.setToolTip(
            "Substring match for any open sales order. "
            "SalesOrder.aspx matches .../SalesOrder.aspx?orderId=12345 "
            "and any other orderId — the number is not compared."
        )
        ports_form.addRow("Sales order URL contains:", self.sales_order_pattern_edit)
        self.serial_timeout_spin = QSpinBox()
        self.serial_timeout_spin.setRange(1000, 60000)
        self.serial_timeout_spin.setSingleStep(500)
        self.serial_timeout_spin.setSuffix(" ms")
        self.serial_timeout_spin.setToolTip(
            "How long to wait for the serial box to clear before skipping "
            "that serial. Two skips in a row cancel the batch."
        )
        ports_form.addRow("Serial timeout:", self.serial_timeout_spin)
        self.serial_confirm_spin = QSpinBox()
        self.serial_confirm_spin.setRange(100, 5000)
        self.serial_confirm_spin.setSingleStep(100)
        self.serial_confirm_spin.setSuffix(" ms")
        self.serial_confirm_spin.setToolTip(
            "After the serial box looks empty, wait this long and check "
            "again before entering the next serial."
        )
        ports_form.addRow("Serial clear confirm:", self.serial_confirm_spin)
        layout.addWidget(ports_box)

        sel_box = QGroupBox("CSS Selectors")
        sel_form = QFormLayout(sel_box)
        self.sel_order = QLineEdit()
        self.sel_buyer = QLineEdit()
        self.sel_sku = QLineEdit()
        self.sel_qty = QLineEdit()
        self.sel_erp_input = QLineEdit()
        self.sel_erp_location = QLineEdit()
        self.sel_erp_serial = QLineEdit()
        sel_form.addRow("eBay order id:", self.sel_order)
        sel_form.addRow("eBay buyer:", self.sel_buyer)
        sel_form.addRow("eBay SKU:", self.sel_sku)
        sel_form.addRow("eBay qty:", self.sel_qty)
        sel_form.addRow("ERP SKU input:", self.sel_erp_input)
        sel_form.addRow("ERP location:", self.sel_erp_location)
        sel_form.addRow("ERP serial input:", self.sel_erp_serial)
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
        self.cache_enabled_check.setChecked(
            bool(settings.get("cache_enabled", True))
        )
        self.refresh_hold_check.setChecked(
            bool(settings.get("ebay_refresh_hold_enabled", True))
        )
        try:
            hold_minutes = int(settings.get("ebay_refresh_hold_minutes", 5))
        except (TypeError, ValueError):
            hold_minutes = 5
        self.refresh_hold_spin.setValue(max(1, min(180, hold_minutes)))
        self.results_popup_check.setChecked(
            bool(settings.get("show_results_popup", False))
        )
        try:
            cycle_minutes = int(settings.get("ebay_autocycle_minutes", 10))
        except (TypeError, ValueError):
            cycle_minutes = 10
        self.autocycle_spin.setValue(max(1, min(180, cycle_minutes)))
        self.sales_order_pattern_edit.setText(
            str(settings.get("sales_order_url_pattern", "SalesOrder.aspx"))
        )
        self.serial_timeout_spin.setValue(
            _clamp_int(settings.get("serial_timeout_ms", 10000), 1000, 60000)
        )
        self.serial_confirm_spin.setValue(
            _clamp_int(settings.get("serial_clear_confirm_ms", 500), 100, 5000)
        )

        selectors = settings.get("selectors") or {}
        self.sel_order.setText(str(selectors.get("ebay_order_id", "")))
        self.sel_buyer.setText(str(selectors.get("ebay_buyer", "")))
        self.sel_sku.setText(str(selectors.get("ebay_sku", "")))
        self.sel_qty.setText(str(selectors.get("ebay_qty", "")))
        self.sel_erp_input.setText(str(selectors.get("erp_sku_input", "")))
        self.sel_erp_location.setText(str(selectors.get("erp_location", "")))
        self.sel_erp_serial.setText(str(selectors.get("erp_serial_input", "")))
        self._on_mode_toggled()
        self._on_refresh_hold_toggled()

    def _on_mode_toggled(self) -> None:
        dual = self.dual_radio.isChecked()
        self.erp_port_spin.setEnabled(dual)

    def _on_refresh_hold_toggled(self) -> None:
        self.refresh_hold_spin.setEnabled(self.refresh_hold_check.isChecked())

    def _save(self) -> None:
        mode = "dual" if self.dual_radio.isChecked() else "single"
        update_settings(
            mode=mode,
            ebay_port=self.ebay_port_spin.value(),
            erp_port=self.erp_port_spin.value(),
            ebay_url_pattern=self.ebay_pattern_edit.text().strip(),
            erp_url_pattern=self.erp_pattern_edit.text().strip(),
            wait_timeout_ms=self.timeout_spin.value(),
            cache_enabled=self.cache_enabled_check.isChecked(),
            ebay_refresh_hold_enabled=self.refresh_hold_check.isChecked(),
            ebay_refresh_hold_minutes=self.refresh_hold_spin.value(),
            show_results_popup=self.results_popup_check.isChecked(),
            ebay_autocycle_minutes=self.autocycle_spin.value(),
            sales_order_url_pattern=self.sales_order_pattern_edit.text().strip(),
            serial_timeout_ms=self.serial_timeout_spin.value(),
            serial_clear_confirm_ms=self.serial_confirm_spin.value(),
            selectors={
                "ebay_order_id": self.sel_order.text().strip(),
                "ebay_buyer": self.sel_buyer.text().strip(),
                "ebay_sku": self.sel_sku.text().strip(),
                "ebay_qty": self.sel_qty.text().strip(),
                "erp_sku_input": self.sel_erp_input.text().strip(),
                "erp_location": self.sel_erp_location.text().strip(),
                "erp_serial_input": self.sel_erp_serial.text().strip(),
            },
        )
        logger.info(
            "Settings saved: mode=%s ebay_port=%s erp_port=%s",
            mode,
            self.ebay_port_spin.value(),
            self.erp_port_spin.value(),
        )
        self.accept()
