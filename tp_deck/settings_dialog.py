"""Settings dialog — mode, ports, URL patterns, timeouts, and scrape behavior."""

from __future__ import annotations

import html
import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import tp_deck.runtime_state as runtime_state
from tp_deck.duration import cache_ttl_label, parse_duration
from tp_deck.settings_manager import load_settings, update_settings
from tp_deck.sku_override_dialog import SkuOverrideEditor
from tp_deck.theme import (
    THEME_KEYS,
    THEME_LABELS,
    dialog_qss,
    normalize_theme,
    palette_for,
    set_theme,
)
from tp_deck.uline_order import export_dir

logger = logging.getLogger("tpdeck")


def _clamp_int(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return low
    try:
        number = int(value)
    except ValueError:
        return low
    return max(low, min(high, number))

def _tip(text: str) -> str:
    body = html.escape(text).replace("\n", "<br>")
    return (
        '<qt><table width="280"><tr><td style="white-space: normal;">'
        f"{body}</td></tr></table></qt>"
    )


def _export_dir_tip() -> str:
    return (
        "Folder that holds exported Uline orders. Leave it blank for "
        "“Uline Exports” beside TP-DECK.bat. Browse picks any folder, "
        "including a network share. Exported CSV and PDF files are written "
        "there, and the order draft stays inside TP DECK."
    )


def _show_info(button: QToolButton) -> None:
    QToolTip.showText(
        button.mapToGlobal(button.rect().bottomLeft()),
        button.toolTip(),
        button,
    )


def _info_button(text: str) -> QToolButton:
    button = QToolButton()
    button.setObjectName("infoBtn")
    button.setText("i")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setToolTip(_tip(text))
    button.clicked.connect(lambda _checked=False, btn=button: _show_info(btn))
    return button


def _with_info(widget: QWidget, text: str) -> QWidget:
    host = QWidget()
    host.setObjectName("infoHost")
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(widget)
    row.addStretch(1)
    row.addWidget(_info_button(text))
    return host


class SettingsDialog(QDialog):
    """Toggle Single/Dual mode and edit ports, URL patterns, and scrape settings."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — Settings")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self._palette = palette_for(load_settings().get("theme", "neutral"))
        self.setStyleSheet(dialog_qss(self._palette))

        self._build_ui()
        self._load_into_form()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        first_tab = QWidget()
        first_layout = QVBoxLayout(first_tab)
        first_layout.setContentsMargins(4, 8, 4, 4)
        first_layout.setSpacing(10)

        mode_box = QGroupBox("Browser Connection Mode")
        mode_layout = QVBoxLayout(mode_box)
        self.mode_group = QButtonGroup(self)
        self.single_radio = QRadioButton("Single — one Chrome on the eBay port")
        self.dual_radio = QRadioButton(
            "Dual — eBay on one port, ERP on another"
        )
        self.mode_group.addButton(self.single_radio, 0)
        self.mode_group.addButton(self.dual_radio, 1)
        mode_layout.addWidget(
            _with_info(
                self.single_radio,
                "eBay and Razor share one Chromium window on the eBay port.",
            )
        )
        mode_layout.addWidget(
            _with_info(
                self.dual_radio,
                "eBay stays on the eBay port. Razor uses its own Chromium "
                "window on the ERP port.",
            )
        )
        self.single_radio.toggled.connect(self._on_mode_toggled)
        first_layout.addWidget(mode_box)

        ports_box = QGroupBox("Scrape & Automation")
        ports_form = QFormLayout(ports_box)
        self.ebay_port_spin = QSpinBox()
        self.ebay_port_spin.setRange(1024, 65535)
        ports_form.addRow(
            "eBay port:",
            _with_info(
                self.ebay_port_spin,
                "DevTools port for the eBay Chromium window.",
            ),
        )
        self.erp_port_spin = QSpinBox()
        self.erp_port_spin.setRange(1024, 65535)
        ports_form.addRow(
            "ERP port:",
            _with_info(
                self.erp_port_spin,
                "DevTools port for the Razor Chromium window. Used in Dual mode.",
            ),
        )
        self.ebay_pattern_edit = QLineEdit()
        ports_form.addRow(
            "eBay URL contains:",
            _with_info(
                self.ebay_pattern_edit,
                "Text that must appear in the Seller Hub tab address. "
                "TP DECK uses it to pick the right tab.",
            ),
        )
        self.erp_pattern_edit = QLineEdit()
        self.erp_pattern_edit.setPlaceholderText("required in single mode")
        ports_form.addRow(
            "ERP URL contains:",
            _with_info(
                self.erp_pattern_edit,
                "Text that must appear in the Razor tab address. In Single "
                "mode this is how the Razor tab is told apart from eBay.",
            ),
        )
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1000, 120000)
        self.timeout_spin.setSingleStep(500)
        self.timeout_spin.setSuffix(" ms")
        ports_form.addRow(
            "Wait timeout:",
            _with_info(
                self.timeout_spin,
                "How long to wait for a page element before that step fails.",
            ),
        )
        self.cache_enabled_check = QCheckBox("Cache SKU → location results")
        ports_form.addRow(
            "",
            _with_info(
                self.cache_enabled_check,
                "Turn off when bin locations are changing often so every run "
                "looks up live ERP data.",
            ),
        )
        self.cache_ttl_edit = QLineEdit()
        self.cache_ttl_edit.setPlaceholderText("12h")
        ports_form.addRow(
            "Cache lifetime:",
            _with_info(
                self.cache_ttl_edit,
                "How long a cached location stays valid. "
                "m minutes, h hours, d days, w weeks, M or mo months (30 days). "
                "Lowercase m is minutes. A bare number is hours. "
                "Examples: 30m, 4h, 12h, 1d.",
            ),
        )
        self.override_btn = QPushButton("Edit alternative SKUs")
        self.override_btn.clicked.connect(self._edit_overrides)
        ports_form.addRow(
            "",
            _with_info(
                self.override_btn,
                "Original SKUs that should use another SKU's location, "
                "including kit quantities.",
            ),
        )
        self.refresh_hold_check = QCheckBox("Skip eBay refresh when recent")
        self.refresh_hold_check.toggled.connect(self._on_refresh_hold_toggled)
        ports_form.addRow(
            "",
            _with_info(
                self.refresh_hold_check,
                "If Seller Hub was reloaded inside this window, Scrape eBay "
                "Orders and Pick List continue without waiting for another refresh.",
            ),
        )
        self.refresh_hold_spin = QSpinBox()
        self.refresh_hold_spin.setRange(1, 180)
        self.refresh_hold_spin.setSuffix(" min")
        ports_form.addRow(
            "eBay refresh hold:",
            _with_info(
                self.refresh_hold_spin,
                "How long a completed eBay refresh stays valid. "
                "Uncheck the option above to refresh on every run.",
            ),
        )
        self.results_popup_check = QCheckBox("Show results popup after copy")
        ports_form.addRow(
            "",
            _with_info(
                self.results_popup_check,
                "After Scrape eBay Orders or Generate Pick List, open a small "
                "window with the text that was copied to the clipboard.",
            ),
        )
        self.release_notes_check = QCheckBox("Show release notes after an update")
        ports_form.addRow(
            "",
            _with_info(
                self.release_notes_check,
                "After TP DECK installs an update and opens again, show that "
                "version's GitHub release notes.",
            ),
        )
        self.autocycle_spin = QSpinBox()
        self.autocycle_spin.setRange(1, 180)
        self.autocycle_spin.setSuffix(" min")
        ports_form.addRow(
            "Auto-cycle wait:",
            _with_info(
                self.autocycle_spin,
                "Wait between auto-cycle scrapes. The cycle button on the main "
                "window turns this on. Those passes fill the SKU cache only.",
            ),
        )
        self.shipstation_every_spin = QSpinBox()
        self.shipstation_every_spin.setRange(1, 99)
        self.shipstation_every_spin.setSuffix(" auto-cycles")
        ports_form.addRow(
            "Sync ShipStation every:",
            _with_info(
                self.shipstation_every_spin,
                "How many auto-cycle passes run between ShipStation syncs. "
                "The first pass after auto-cycle is turned on always syncs. "
                "1 syncs on every pass.",
            ),
        )
        self.serial_timeout_spin = QSpinBox()
        self.serial_timeout_spin.setRange(1000, 60000)
        self.serial_timeout_spin.setSingleStep(500)
        self.serial_timeout_spin.setSuffix(" ms")
        ports_form.addRow(
            "Serial timeout:",
            _with_info(
                self.serial_timeout_spin,
                "How long to wait for the serial box to clear before skipping "
                "that serial. Two skips in a row cancel the batch.",
            ),
        )
        self.serial_confirm_spin = QSpinBox()
        self.serial_confirm_spin.setRange(100, 5000)
        self.serial_confirm_spin.setSingleStep(100)
        self.serial_confirm_spin.setSuffix(" ms")
        ports_form.addRow(
            "Serial clear confirm:",
            _with_info(
                self.serial_confirm_spin,
                "After the serial box looks empty, wait this long and check "
                "again before entering the next serial.",
            ),
        )
        self.slow_razor_check = QCheckBox("Slow Razor mode")
        ports_form.addRow(
            "",
            _with_info(
                self.slow_razor_check,
                "Stretch ERP locator, grid, and serial waits by the multiplier. "
                "This checkbox turns off every time TP DECK starts. "
                "The multiplier is saved.",
            ),
        )
        self.slow_razor_spin = QSpinBox()
        self.slow_razor_spin.setRange(1, 20)
        ports_form.addRow(
            "Slow Razor multiplier:",
            _with_info(
                self.slow_razor_spin,
                "How many times longer those ERP waits become while Slow Razor "
                "is on. 10 turns the 30s locator into 300s, a 10s wait into 100s, "
                "and the 0.5s serial confirm into 5s.",
            ),
        )
        first_layout.addWidget(ports_box)
        first_layout.addStretch(1)
        self._tabs.addTab(first_tab, "Connection & Scrape")

        uline_tab = QWidget()
        uline_tab_layout = QVBoxLayout(uline_tab)
        uline_tab_layout.setContentsMargins(4, 8, 4, 4)
        uline_tab_layout.setSpacing(10)

        uline_box = QGroupBox("Uline Order Builder")
        uline_form = QFormLayout(uline_box)
        self.uline_port_spin = QSpinBox()
        self.uline_port_spin.setRange(1024, 65535)
        uline_form.addRow(
            "Uline port:",
            _with_info(
                self.uline_port_spin,
                "DevTools port of the Chromium window that has Uline open. "
                "Single mode uses the eBay port, so leave this the same.",
            ),
        )
        self.uline_pattern_edit = QLineEdit()
        uline_form.addRow(
            "Uline URL contains:",
            _with_info(
                self.uline_pattern_edit,
                "Text that must appear in the Uline tab address. TP DECK uses "
                "it to pick that tab; uline.com is the US site.",
            ),
        )
        self.uline_timeout_spin = QSpinBox()
        self.uline_timeout_spin.setRange(1000, 120000)
        self.uline_timeout_spin.setSingleStep(500)
        self.uline_timeout_spin.setSuffix(" ms")
        uline_form.addRow(
            "Uline lookup timeout:",
            _with_info(
                self.uline_timeout_spin,
                "How long to wait for a Uline page to load when an Item # is "
                "not in the cache.",
            ),
        )
        self.uline_export_edit = QLineEdit()
        self.uline_export_edit.setPlaceholderText("Uline Exports")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._pick_export_dir)
        export_host = QWidget()
        export_host.setObjectName("infoHost")
        export_row = QHBoxLayout(export_host)
        export_row.setContentsMargins(0, 0, 0, 0)
        export_row.setSpacing(6)
        export_row.addWidget(self.uline_export_edit)
        export_row.addWidget(browse_btn)
        uline_form.addRow(
            "Export folder:",
            _with_info(export_host, _export_dir_tip()),
        )
        uline_tab_layout.addWidget(uline_box)
        uline_tab_layout.addStretch(1)
        self._tabs.addTab(uline_tab, "Uline Builder")

        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout(appearance_tab)
        appearance_layout.setContentsMargins(4, 8, 4, 4)
        appearance_layout.setSpacing(10)

        theme_box = QGroupBox("Theme")
        theme_form = QFormLayout(theme_box)
        self.theme_combo = QComboBox()
        for key in THEME_KEYS:
            self.theme_combo.addItem(THEME_LABELS[key], key)
        swatch_host = QWidget()
        swatch_host.setObjectName("infoHost")
        swatch_row = QHBoxLayout(swatch_host)
        swatch_row.setContentsMargins(0, 0, 0, 0)
        swatch_row.setSpacing(6)
        self.theme_swatch = QLabel()
        self.theme_swatch.setFixedSize(20, 20)
        swatch_row.addWidget(self.theme_swatch)
        swatch_row.addWidget(self.theme_combo, 1)
        theme_form.addRow(
            "Color wheel:",
            _with_info(
                swatch_host,
                "One palette per color on the wheel. The accent, backgrounds, "
                "borders, buttons, and toggles are all derived from that one "
                "color so nothing clashes: a red theme has no amber cycle "
                "button, a yellow theme has no cyan highlights. Save applies "
                "the theme to every TP DECK window right away.",
            ),
        )
        self.theme_combo.currentData.connect(self._on_theme_preview)
        appearance_layout.addWidget(theme_box)

        preview_box = QGroupBox("Palette preview")
        preview_form = QFormLayout(preview_box)
        self.preview_title = QLabel("Scrape eBay Orders")
        self.preview_title.setObjectName("previewTitle")
        preview_form.addRow("Title text:", _with_info(
            self.preview_title,
            "Window titles use the bright accent of the palette.",
        ))
        self.preview_version = QLabel("v1.0 · build 250")
        self.preview_version.setObjectName("previewVersion")
        preview_form.addRow("Version text:", _with_info(
            self.preview_version,
            "The version line uses the softer accent next on the wheel.",
        ))
        self.preview_cycle_off = QPushButton("Cycle off")
        self.preview_cycle_off.setObjectName("previewCycleOff")
        preview_form.addRow("Cycle button off:", _with_info(
            self.preview_cycle_off,
            "Off is a muted tone of the theme color, never amber on yellow "
            "themes. On is a green that sits next to the wheel color.",
        ))
        self.preview_cycle_on = QPushButton("Cycle on")
        self.preview_cycle_on.setObjectName("previewCycleOn")
        preview_form.addRow("Cycle button on:", _with_info(
            self.preview_cycle_on,
            "Green toggles sit beside every wheel color, so they stay readable "
            "on red, orange, and yellow themes too.",
        ))
        self.preview_stop = QPushButton("Stop")
        self.preview_stop.setObjectName("previewStop")
        preview_form.addRow("Stop button:", _with_info(
            self.preview_stop,
            "The stop button is a dark alert color. On a red theme it shifts "
            "so it does not match the accent.",
        ))
        self.preview_results = QLabel("Results window: light paper")
        self.preview_results.setObjectName("previewResults")
        preview_form.addRow("Results popup:", _with_info(
            self.preview_results,
            "The results popup stays a light paper surface tinted with the "
            "theme color so copied text is easy to read.",
        ))
        appearance_layout.addWidget(preview_box)

        self._tabs.addTab(appearance_tab, "Appearance")

        layout.addWidget(self._tabs, 1)

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
        self.cache_ttl_edit.setText(cache_ttl_label(settings))
        self.slow_razor_check.setChecked(runtime_state.slow_razor_enabled)
        try:
            multiplier = int(settings.get("slow_razor_multiplier", 10))
        except (TypeError, ValueError):
            multiplier = 10
        self.slow_razor_spin.setValue(max(1, min(20, multiplier)))
        self.refresh_hold_check.setChecked(
            bool(settings.get("ebay_refresh_hold_enabled", True))
        )
        try:
            hold_minutes = int(settings.get("ebay_refresh_hold_minutes", 5))
        except (TypeError, ValueError):
            hold_minutes = 5
        self.refresh_hold_spin.setValue(max(1, min(180, hold_minutes)))
        self.results_popup_check.setChecked(
            bool(settings.get("show_results_popup", True))
        )
        self.release_notes_check.setChecked(
            bool(settings.get("show_release_notes", True))
        )
        try:
            cycle_minutes = int(settings.get("ebay_autocycle_minutes", 10))
        except (TypeError, ValueError):
            cycle_minutes = 10
        self.autocycle_spin.setValue(max(1, min(180, cycle_minutes)))
        self.shipstation_every_spin.setValue(
            _clamp_int(settings.get("shipstation_sync_every_cycles", 3), 1, 99)
        )
        self.serial_timeout_spin.setValue(
            _clamp_int(settings.get("serial_timeout_ms", 10000), 1000, 60000)
        )
        self.serial_confirm_spin.setValue(
            _clamp_int(settings.get("serial_clear_confirm_ms", 500), 100, 5000)
        )
        try:
            uline_port = int(settings.get("uline_port", settings.get("ebay_port", 9222)))
        except (TypeError, ValueError):
            uline_port = 9222
        self.uline_port_spin.setValue(_clamp_int(uline_port, 1024, 65535))
        self.uline_pattern_edit.setText(
            str(settings.get("uline_url_pattern", "uline.com"))
        )
        self.uline_timeout_spin.setValue(
            _clamp_int(settings.get("uline_lookup_timeout_ms", 20000), 1000, 120000)
        )
        self.uline_export_edit.setText(str(settings.get("uline_export_dir", "")))
        theme_key = normalize_theme(settings.get("theme", "neutral"))
        index = self.theme_combo.findData(theme_key)
        if index < 0:
            index = 0
        self.theme_combo.setCurrentIndex(index)
        self._paint_preview(palette_for(theme_key))
        self._on_mode_toggled()
        self._on_refresh_hold_toggled()

    def _paint_preview(self, p: dict[str, str]) -> None:
        """Paint the Appearance tab preview widgets with one palette."""
        self.theme_swatch.setStyleSheet(
            f"background-color: {p['accent']}; border: 1px solid {p['text']};"
        )
        self.preview_title.setStyleSheet(
            f"color: {p['title']}; background: transparent; font-weight: bold;"
        )
        self.preview_version.setStyleSheet(
            f"color: {p['version']}; background: transparent;"
        )
        self.preview_cycle_off.setStyleSheet(
            f"background-color: {p['cycle_off_bg']}; color: {p['text']}; "
            f"border: 1px solid {p['cycle_off_border']}; border-radius: 4px; padding: 6px 12px;"
        )
        self.preview_cycle_on.setStyleSheet(
            f"background-color: {p['success_bg']}; color: {p['text']}; "
            f"border: 1px solid {p['success_border']}; border-radius: 4px; padding: 6px 12px;"
        )
        self.preview_stop.setStyleSheet(
            f"background-color: {p['danger_bg']}; color: {p['danger_text']}; "
            f"border: 1px solid {p['danger_text']}; border-radius: 4px; padding: 6px 12px;"
        )
        self.preview_results.setStyleSheet(
            f"background-color: {p['results_bg']}; color: {p['results_text']}; "
            f"border: 1px solid {p['results_border']}; border-radius: 4px; padding: 6px 12px;"
        )

    def _on_theme_preview(self, key: object) -> None:
        """Show the picked palette on the swatch and preview as the combo changes."""
        self._paint_preview(palette_for(str(key)))

    def _on_mode_toggled(self) -> None:
        dual = self.dual_radio.isChecked()
        self.erp_port_spin.setEnabled(dual)

    def _on_refresh_hold_toggled(self) -> None:
        self.refresh_hold_spin.setEnabled(self.refresh_hold_check.isChecked())

    def _edit_overrides(self) -> None:
        SkuOverrideEditor(self).exec()

    def _pick_export_dir(self) -> None:
        start = self.uline_export_edit.text().strip()
        if not start:
            start = str(export_dir(load_settings()))
        chosen = QFileDialog.getExistingDirectory(self, "Uline export folder", start)
        if chosen:
            self.uline_export_edit.setText(chosen)

    def _save(self) -> None:
        ttl_text = self.cache_ttl_edit.text().strip()
        try:
            parse_duration(ttl_text)
        except ValueError as exc:
            QMessageBox.warning(self, "TP DECK", str(exc))
            return

        mode = "dual" if self.dual_radio.isChecked() else "single"
        update_settings(
            mode=mode,
            ebay_port=self.ebay_port_spin.value(),
            erp_port=self.erp_port_spin.value(),
            ebay_url_pattern=self.ebay_pattern_edit.text().strip(),
            erp_url_pattern=self.erp_pattern_edit.text().strip(),
            wait_timeout_ms=self.timeout_spin.value(),
            cache_enabled=self.cache_enabled_check.isChecked(),
            cache_ttl=ttl_text,
            slow_razor_multiplier=self.slow_razor_spin.value(),
            ebay_refresh_hold_enabled=self.refresh_hold_check.isChecked(),
            ebay_refresh_hold_minutes=self.refresh_hold_spin.value(),
            show_results_popup=self.results_popup_check.isChecked(),
            show_results_popup_user_set=True,
            show_release_notes=self.release_notes_check.isChecked(),
            ebay_autocycle_minutes=self.autocycle_spin.value(),
            shipstation_sync_every_cycles=self.shipstation_every_spin.value(),
            serial_timeout_ms=self.serial_timeout_spin.value(),
            serial_clear_confirm_ms=self.serial_confirm_spin.value(),
            uline_port=self.uline_port_spin.value(),
            uline_url_pattern=self.uline_pattern_edit.text().strip() or "uline.com",
            uline_lookup_timeout_ms=self.uline_timeout_spin.value(),
            uline_export_dir=self.uline_export_edit.text().strip(),
        )
        runtime_state.slow_razor_enabled = self.slow_razor_check.isChecked()
        theme_key = str(self.theme_combo.currentData() or "neutral")
        palette = set_theme(theme_key)
        parent = self.parent()
        if parent is not None:
            if hasattr(parent, "set_slow_razor"):
                parent.set_slow_razor(runtime_state.slow_razor_enabled)
            if hasattr(parent, "apply_palette"):
                parent.apply_palette(palette)
        logger.info(
            "Settings saved: mode=%s ebay_port=%s erp_port=%s slow_razor=%s theme=%s",
            mode,
            self.ebay_port_spin.value(),
            self.erp_port_spin.value(),
            runtime_state.slow_razor_enabled,
            theme_key,
        )
        self.accept()
