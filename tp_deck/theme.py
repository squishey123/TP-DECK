"""Theme palettes for TP DECK — one family per color on the color wheel.

Each theme names one accent color (the primary/secondary wheel color the user
picks). Every other color in every window is derived from it, so a red theme
never shows amber toggle buttons and a yellow theme never shows cyan ones:
backgrounds, borders, secondary text, buttons, cycle on/off, warnings, and
success all come from this module. The neutral family keeps the original slate
palette that TP DECK shipped with.

The derived colors land on exact Tailwind stops, so "cyan" reproduces the
original theme pixel for pixel.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from tp_deck.settings_manager import load_settings, update_settings

logger = logging.getLogger("tpdeck")

# Edge of one square icon button on the laptop panel. The QSS box and the drawn
# glyph are both driven from this so a smaller button cannot overflow its art.
_MOBILE_ICON_EDGE = 28

# Tailwind stops each theme family is built from. "neutral" is the slate
# palette TP DECK shipped with; cyan reproduces it exactly.
FAMILIES: dict[str, dict[str, str]] = {
    "red": {
        "bg": "#180A0D",
        "surface": "#2A161B",
        "raised": "#3E272E",
        "border": "#4F353C",
        "strong_border": "#64474F",
        "text": "#FBEBED",
        "secondary_text": "#D8B4BC",
        "muted_text": "#A98089",
        "disabled_bg": "#3E272E",
        "disabled_text": "#85616A",
        "accent": "#DC2626",
        "accent_hover": "#EF4444",
        "accent_soft": "#F87171",
        "on_accent": "#1B0709",
        "title": "#F87171",
        "version": "#FCA5A5",
        "warning": "#F87171",
        "success_bg": "#3C7A46",
        "success_border": "#7ACC85",
        "success_hover": "#4C9B57",
        "danger_bg": "#3B1D3A",
        "danger_text": "#F5D0E6",
        "danger_hover": "#54244A",
        "danger_hover_text": "#FDF2F8",
        "cycle_off_bg": "#991B1B",
        "cycle_off_border": "#F87171",
        "cycle_off_hover": "#B91C1C",
        "ring": "#FECDD3",
        "track": "#180A0D",
        "sheen": "#FCA5A5",
        "cap": "#FEE2E2",
        "results_bg": "#FFF7F7",
        "results_text": "#1C090B",
        "results_border": "#E6C8CD",
        "results_gridline": "#F1DCE0",
        "results_header_bg": "#FBEFF1",
        "results_selection": "#FCE3E6",
        "results_flag": "#FEE2E2",
        "results_processed_bg": "#5A4047",
        "results_processed_text": "#FBEBED",
    },
    "orange": {
        "bg": "#180D07",
        "surface": "#2A1B12",
        "raised": "#3E2B1F",
        "border": "#4F3C2F",
        "strong_border": "#64503F",
        "text": "#FBEDDF",
        "secondary_text": "#D8BB9C",
        "muted_text": "#A98B6F",
        "disabled_bg": "#3E2B1F",
        "disabled_text": "#856D54",
        "accent": "#EA580C",
        "accent_hover": "#F97316",
        "accent_soft": "#FB923C",
        "on_accent": "#1B0A05",
        "title": "#FB923C",
        "version": "#FDBA74",
        "warning": "#FB923C",
        "success_bg": "#4F7A3C",
        "success_border": "#8ACC7A",
        "success_hover": "#639B4C",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#9A3412",
        "cycle_off_border": "#FB923C",
        "cycle_off_hover": "#C2410C",
        "ring": "#FED7AA",
        "track": "#180D07",
        "sheen": "#FDBA74",
        "cap": "#FFEDD5",
        "results_bg": "#FFF9F3",
        "results_text": "#1C0D06",
        "results_border": "#E6D2BE",
        "results_gridline": "#F1E2D3",
        "results_header_bg": "#FBEEE4",
        "results_selection": "#FCE6D5",
        "results_flag": "#FFEDD5",
        "results_processed_bg": "#5A4739",
        "results_processed_text": "#FBEDDF",
    },
    "yellow": {
        "bg": "#181207",
        "surface": "#2A2212",
        "raised": "#3E341F",
        "border": "#4F452F",
        "strong_border": "#64593F",
        "text": "#FBFAE0",
        "secondary_text": "#D8CFA3",
        "muted_text": "#A99E73",
        "disabled_bg": "#3E341F",
        "disabled_text": "#857D58",
        "accent": "#CA8A04",
        "accent_hover": "#EAB308",
        "accent_soft": "#FACC15",
        "on_accent": "#1B1605",
        "title": "#FACC15",
        "version": "#FDE047",
        "warning": "#EAB308",
        "success_bg": "#5C7A3C",
        "success_border": "#95CC7A",
        "success_hover": "#729B4C",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#854D0E",
        "cycle_off_border": "#FACC15",
        "cycle_off_hover": "#A16207",
        "ring": "#FEF9C3",
        "track": "#181207",
        "sheen": "#FDE047",
        "cap": "#FEFCE8",
        "results_bg": "#FFFCF2",
        "results_text": "#1C1706",
        "results_border": "#E6DEC2",
        "results_gridline": "#F1EBD9",
        "results_header_bg": "#FBFAEA",
        "results_selection": "#FCEDCF",
        "results_flag": "#FEF9C3",
        "results_processed_bg": "#5A5438",
        "results_processed_text": "#FBFAE0",
    },
    "green": {
        "bg": "#0B170C",
        "surface": "#1A291B",
        "raised": "#29392A",
        "border": "#384839",
        "strong_border": "#495A4A",
        "text": "#EAF7EB",
        "secondary_text": "#ACCBAF",
        "muted_text": "#81A084",
        "disabled_bg": "#29392A",
        "disabled_text": "#648267",
        "accent": "#16A34A",
        "accent_hover": "#22C55E",
        "accent_soft": "#4ADE80",
        "on_accent": "#061B09",
        "title": "#4ADE80",
        "version": "#86EFAC",
        "warning": "#4ADE80",
        "success_bg": "#15803D",
        "success_border": "#4ADE80",
        "success_hover": "#16A34A",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#166534",
        "cycle_off_border": "#4ADE80",
        "cycle_off_hover": "#15803D",
        "ring": "#DCFCE7",
        "track": "#0B170C",
        "sheen": "#86EFAC",
        "cap": "#F0FDF4",
        "results_bg": "#F4FBF5",
        "results_text": "#0A170B",
        "results_border": "#C9E3CC",
        "results_gridline": "#DDEFE0",
        "results_header_bg": "#E8F4EA",
        "results_selection": "#DCFCE7",
        "results_flag": "#DCFCE7",
        "results_processed_bg": "#465547",
        "results_processed_text": "#EAF7EB",
    },
    "cyan": {
        "bg": "#0F172A",
        "surface": "#1E293B",
        "raised": "#334155",
        "border": "#334155",
        "strong_border": "#475569",
        "text": "#F8FAFC",
        "secondary_text": "#94A3B8",
        "muted_text": "#64748B",
        "disabled_bg": "#334155",
        "disabled_text": "#94A3B8",
        "accent": "#0EA5E9",
        "accent_hover": "#38BDF8",
        "accent_soft": "#7DD3FC",
        "on_accent": "#0F172A",
        "title": "#0EA5E9",
        "version": "#22D3EE",
        "warning": "#FBBF24",
        "success_bg": "#15803D",
        "success_border": "#4ADE80",
        "success_hover": "#16A34A",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#B45309",
        "cycle_off_border": "#F59E0B",
        "cycle_off_hover": "#D97706",
        "ring": "#E0F2FE",
        "track": "#0F172A",
        "sheen": "#7DD3FC",
        "cap": "#F0F9FF",
        "results_bg": "#FFFFFF",
        "results_text": "#000000",
        "results_border": "#CBD5E1",
        "results_gridline": "#E2E8F0",
        "results_header_bg": "#F1F5F9",
        "results_selection": "#E0F2FE",
        "results_flag": "#FEE2E2",
        "results_processed_bg": "#4B5563",
        "results_processed_text": "#F8FAFC",
    },
    "blue": {
        "bg": "#0A1220",
        "surface": "#172234",
        "raised": "#283549",
        "border": "#364458",
        "strong_border": "#48566B",
        "text": "#EAF1FB",
        "secondary_text": "#ACBFDA",
        "muted_text": "#8092AE",
        "disabled_bg": "#283549",
        "disabled_text": "#637591",
        "accent": "#2563EB",
        "accent_hover": "#3B82F6",
        "accent_soft": "#60A5FA",
        "on_accent": "#040E1D",
        "title": "#60A5FA",
        "version": "#93C5FD",
        "warning": "#60A5FA",
        "success_bg": "#3C7A52",
        "success_border": "#7ACC94",
        "success_hover": "#4C9B63",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#1E3A8A",
        "cycle_off_border": "#60A5FA",
        "cycle_off_hover": "#1D4ED8",
        "ring": "#DBEAFE",
        "track": "#0A1220",
        "sheen": "#93C5FD",
        "cap": "#EFF6FF",
        "results_bg": "#F4F8FE",
        "results_text": "#070D1B",
        "results_border": "#C7D7ED",
        "results_gridline": "#DCE6F3",
        "results_header_bg": "#E9F0FA",
        "results_selection": "#DBEAFE",
        "results_flag": "#DBEAFE",
        "results_processed_bg": "#455162",
        "results_processed_text": "#EAF1FB",
    },
    "purple": {
        "bg": "#120A1C",
        "surface": "#22172D",
        "raised": "#352941",
        "border": "#443851",
        "strong_border": "#584B66",
        "text": "#F3EAFB",
        "secondary_text": "#CBAFE0",
        "muted_text": "#9D82B3",
        "disabled_bg": "#352941",
        "disabled_text": "#7A648F",
        "accent": "#9333EA",
        "accent_hover": "#A855F7",
        "accent_soft": "#C084FC",
        "on_accent": "#12071E",
        "title": "#C084FC",
        "version": "#D8B4FE",
        "warning": "#C084FC",
        "success_bg": "#3F7A48",
        "success_border": "#7FCC8B",
        "success_hover": "#519B5C",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#6B21A8",
        "cycle_off_border": "#C084FC",
        "cycle_off_hover": "#7E22CE",
        "ring": "#F3E8FF",
        "track": "#120A1C",
        "sheen": "#D8B4FE",
        "cap": "#FAF5FF",
        "results_bg": "#FBF6FE",
        "results_text": "#12071E",
        "results_border": "#DEC9EC",
        "results_gridline": "#EBDDF3",
        "results_header_bg": "#F6EDFA",
        "results_selection": "#F3E8FF",
        "results_flag": "#F3E8FF",
        "results_processed_bg": "#51425C",
        "results_processed_text": "#F3EAFB",
    },
    "magenta": {
        "bg": "#180A15",
        "surface": "#2A1624",
        "raised": "#3E2737",
        "border": "#4F3545",
        "strong_border": "#644758",
        "text": "#FBEBF4",
        "secondary_text": "#D8B4C9",
        "muted_text": "#A98098",
        "disabled_bg": "#3E2737",
        "disabled_text": "#856175",
        "accent": "#DB2777",
        "accent_hover": "#EC4899",
        "accent_soft": "#F9A8D4",
        "on_accent": "#1B0713",
        "title": "#F9A8D4",
        "version": "#FBCFE8",
        "warning": "#F472B6",
        "success_bg": "#3C7A52",
        "success_border": "#7ACC94",
        "success_hover": "#4C9B63",
        "danger_bg": "#7F1D1D",
        "danger_text": "#FCA5A5",
        "danger_hover": "#991B1B",
        "danger_hover_text": "#FEE2E2",
        "cycle_off_bg": "#9D174D",
        "cycle_off_border": "#F472B6",
        "cycle_off_hover": "#BE185D",
        "ring": "#FECDD3",
        "track": "#180A15",
        "sheen": "#FBCFE8",
        "cap": "#FDF2F8",
        "results_bg": "#FFF6FA",
        "results_text": "#1C0913",
        "results_border": "#E6C8D7",
        "results_gridline": "#F1DCE6",
        "results_header_bg": "#FBEFF5",
        "results_selection": "#FCE3EC",
        "results_flag": "#FCE3EC",
        "results_processed_bg": "#5A404C",
        "results_processed_text": "#FBEBF4",
    },
}

# The wheel order the Appearance tab shows, plus the neutral family.
THEME_KEYS: list[str] = [
    "red",
    "orange",
    "yellow",
    "green",
    "cyan",
    "blue",
    "purple",
    "magenta",
    "neutral",
]

THEME_LABELS: dict[str, str] = {
    "red": "Red",
    "orange": "Orange",
    "yellow": "Yellow",
    "green": "Green",
    "cyan": "Cyan",
    "blue": "Blue",
    "purple": "Purple",
    "magenta": "Magenta",
    "neutral": "Neutral slate (original)",
}

NEUTRAL = FAMILIES["cyan"]


def normalize_theme(value: object) -> str:
    """Map any stored value to a theme key; unknown values fall back to neutral."""
    key = str(value or "").strip().lower()
    if key == "slate":
        return "neutral"
    if key in THEME_KEYS:
        return key
    return "neutral"


def palette_for(theme_key: str) -> dict[str, str]:
    key = normalize_theme(theme_key)
    if key == "neutral":
        return NEUTRAL
    return FAMILIES[key]


def current_theme() -> str:
    settings = load_settings()
    return normalize_theme(settings.get("theme", "neutral"))


def current_palette() -> dict[str, str]:
    return palette_for(current_theme())


def mix_hex(start: str, end: str, amount: float) -> str:
    """Blend start toward end; amount 0 leaves start unchanged. Used for the shutdown fade."""
    t = max(0.0, min(1.0, amount))
    a = QColor(start)
    b = QColor(end)
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    ).name()


def _mix_text(p: dict[str, str], amount: float) -> str:
    """Text color as the panel eases to grey during Safe Shutdown."""
    return mix_hex(p["text"], "#94A3B8", amount)


def dashboard_qss(p: dict[str, str], compact: bool = False) -> str:
    """QSS for the floating dashboard window from a palette.

    compact draws the laptop panel: the same object names, so the fade, pulse,
    and cycle rules keep matching, only smaller boxes and type.
    """
    if compact:
        # Laptop panel: every action button is a square the size of the drawn
        # glyph boxes, so the row is 8 equal squares and the symbol inside it
        # is the only thing that has to be legible.
        title_px, version_px = 11, 7
        status_px, status_min = 9, 26
        button_pad, button_px = "3px 7px", 10
        icon_edge = _MOBILE_ICON_EDGE
        action_h, action_pad, action_px = icon_edge, "0px", 13
        action_min_w, action_max_w = icon_edge, 16777215
        settings_px = 13
        stop_px, stop_pad = 13, "0px"
        stop_h = icon_edge
        stop_min_w, stop_max_w = icon_edge, icon_edge
    else:
        title_px, version_px = 14, 9
        status_px, status_min = 11, 52
        button_pad, button_px = "6px 12px", 12
        icon_edge = 34
        action_h, action_pad, action_px = 28, "6px 6px", 12
        action_min_w, action_max_w = 0, 16777215
        settings_px = 12
        stop_px, stop_pad = 11, "4px 4px"
        stop_h = 0
        stop_min_w, stop_max_w = 0, 16777215
    return f"""
QMainWindow, QWidget#dashboardRoot {{
    background-color: {p["bg"]};
    color: {p["text"]};
}}
QToolTip {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 4px;
}}
QLabel#titleLabel {{
    color: {p["title"]};
    font-size: {title_px}px;
    font-weight: bold;
}}
QLabel#versionLabel {{
    color: {p["version"]};
    font-family: Consolas, "Courier New", monospace;
    font-size: {version_px}px;
    font-weight: 700;
    padding: 0px;
    margin: 0px;
}}
QLabel#statusLabel {{
    color: {p["text"]};
    font-size: {status_px}px;
    min-height: {status_min}px;
}}
QPushButton {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["accent"]};
    border-radius: 4px;
    padding: {button_pad};
    font-size: {button_px}px;
}}
QPushButton:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QPushButton:disabled {{
    background-color: {p["surface"]};
    color: {p["muted_text"]};
    border-color: {p["border"]};
}}
QPushButton#executeBtn, QPushButton#pickBtn, QPushButton#batchBtn {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    font-weight: bold;
    font-size: {action_px}px;
    min-height: {action_h}px;
    max-height: {action_h}px;
    min-width: {action_min_w}px;
    max-width: {action_max_w}px;
    padding: {action_pad};
}}
QPushButton#executeBtn:hover, QPushButton#pickBtn:hover, QPushButton#batchBtn:hover {{
    background-color: {p["accent_hover"]};
}}
QPushButton#executeBtn:disabled, QPushButton#pickBtn:disabled, QPushButton#batchBtn:disabled {{
    background-color: {p["disabled_bg"]};
    color: {p["disabled_text"]};
    border-color: {p["strong_border"]};
}}
QPushButton#cycleBtn {{
    background-color: {p["cycle_off_bg"]};
    color: {p["text"]};
    border: 1px solid {p["cycle_off_border"]};
    padding: 0px;
    min-width: {icon_edge}px;
    max-width: {icon_edge}px;
    min-height: {icon_edge}px;
    max-height: {icon_edge}px;
}}
QPushButton#cycleBtn:hover {{
    background-color: {p["cycle_off_hover"]};
    color: {p["text"]};
}}
QPushButton#cycleBtn[cycleOn="true"] {{
    background-color: {p["success_bg"]};
    border: 1px solid {p["success_border"]};
}}
QPushButton#cycleBtn[cycleOn="true"]:hover {{
    background-color: {p["success_hover"]};
    color: {p["text"]};
}}
QPushButton#cycleBtn:disabled {{
    background-color: {p["disabled_bg"]};
    color: {p["disabled_text"]};
    border-color: {p["strong_border"]};
}}
QPushButton#stopBtn {{
    background-color: {p["danger_bg"]};
    color: {p["danger_text"]};
    border: 1px solid {p["danger_text"]};
    font-weight: bold;
    font-size: {stop_px}px;
    min-height: {stop_h}px;
    max-height: {stop_h}px;
    min-width: {stop_min_w}px;
    max-width: {stop_max_w}px;
    padding: {stop_pad};
}}
QPushButton#stopBtn:hover {{
    background-color: {p["danger_hover"]};
    color: {p["danger_hover_text"]};
}}
QPushButton#settingsBtn, QPushButton#powerBtn, QPushButton#toolsBtn {{
    border-color: {p["strong_border"]};
    padding: 0px;
    font-size: {settings_px}px;
    min-width: {icon_edge}px;
    max-width: {icon_edge}px;
    min-height: {icon_edge}px;
    max-height: {icon_edge}px;
}}
QLabel#slowRazorLabel {{
    color: {p["warning"]};
    font-size: {stop_px}px;
    font-weight: bold;
}}
"""


def results_qss(p: dict[str, str]) -> str:
    """QSS for the results / failed-serials report window from a palette."""
    return f"""
QWidget#resultsRoot {{
    background-color: {p["results_bg"]};
    color: {p["results_text"]};
}}
QToolTip {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 4px;
}}
QLabel#resultsNote {{
    background-color: {p["results_bg"]};
    color: {p["results_text"]};
    font-size: 16px;
}}
QTextEdit#resultsText {{
    background-color: {p["results_bg"]};
    color: {p["results_text"]};
    border: 1px solid {p["results_border"]};
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 22px;
    padding: 6px;
}}
QPushButton#closeBtn {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    border: 1px solid {p["accent"]};
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: bold;
    min-height: 28px;
}}
QPushButton#closeBtn:hover {{
    background-color: {p["accent_hover"]};
    color: {p["on_accent"]};
}}
QTableWidget#resultsTable {{
    background-color: {p["results_bg"]};
    color: {p["results_text"]};
    border: 1px solid {p["results_border"]};
    gridline-color: {p["results_gridline"]};
    font-family: Consolas, "Courier New", monospace;
    font-size: 18px;
    selection-background-color: {p["results_selection"]};
    selection-color: {p["results_text"]};
}}
QTableWidget#resultsTable QHeaderView::section {{
    background-color: {p["results_header_bg"]};
    color: {p["results_text"]};
    font-weight: bold;
    padding: 4px 6px;
    border: 1px solid {p["results_border"]};
}}
"""


def dialog_qss(p: dict[str, str]) -> str:
    """QSS shared by Settings, Batch Serial, SKU dialogs, and Uline editors."""
    return f"""
QDialog, QGroupBox {{
    background-color: {p["bg"]};
    color: {p["text"]};
}}
QToolTip {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 4px;
}}
QMenu {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
}}
QMenu::item {{
    background: transparent;
    padding: 4px 22px;
}}
QMenu::item:selected {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QMenu::separator {{
    height: 1px;
    background: {p["border"]};
    margin: 4px 8px;
}}
QMessageBox {{
    background-color: {p["bg"]};
}}
QGroupBox {{
    border: 1px solid {p["border"]};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {p["accent"]};
}}
QLabel, QRadioButton, QCheckBox {{
    color: {p["text"]};
    background: transparent;
}}
QLabel#errorLabel {{
    color: {p["danger_text"]};
    font-weight: bold;
}}
QRadioButton, QCheckBox {{
    spacing: 10px;
    padding: 6px 8px;
    border-radius: 4px;
}}
QRadioButton:hover, QCheckBox:hover {{
    background-color: {p["surface"]};
}}
QRadioButton:checked {{
    color: {p["accent_soft"]};
    font-weight: bold;
    background-color: {p["surface"]};
    border: 1px solid {p["accent"]};
}}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {p["strong_border"]};
    background-color: {p["bg"]};
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator {{
    border-radius: 3px;
}}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {{
    border: 2px solid {p["accent"]};
    background-color: {p["accent"]};
}}
QSpinBox, QLineEdit, QComboBox, QTextEdit {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    padding: 4px 6px;
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    selection-background-color: {p["accent"]};
    selection-color: {p["on_accent"]};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {p["raised"]};
    border: 1px solid {p["strong_border"]};
    width: 16px;
}}
QWidget#infoHost {{
    background: transparent;
}}
QToolButton#infoBtn {{
    background-color: transparent;
    color: {p["accent"]};
    border: 1px solid {p["accent"]};
    border-radius: 9px;
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
    padding: 0px;
    font-size: 11px;
    font-weight: bold;
    font-style: italic;
}}
QToolButton#infoBtn:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QTabBar::tab {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 6px 12px;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    font-weight: bold;
}}
QTableWidget, QTableView, QAbstractItemView {{
    background-color: {p["bg"]};
    color: {p["text"]};
    gridline-color: {p["border"]};
    border: 1px solid {p["border"]};
    selection-background-color: {p["accent"]};
    selection-color: {p["on_accent"]};
}}
QTableWidget::item, QAbstractItemView::item {{
    background-color: {p["surface"]};
    color: {p["text"]};
}}
QTableWidget::item:selected, QAbstractItemView::item:selected {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QHeaderView {{
    background-color: {p["surface"]};
}}
QHeaderView::section {{
    background-color: {p["surface"]};
    color: {p["accent_soft"]};
    border: 1px solid {p["border"]};
    font-weight: bold;
    padding: 4px;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background-color: {p["surface"]};
    border: 1px solid {p["border"]};
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: {p["raised"]};
    border-radius: 3px;
    min-width: 12px;
    min-height: 12px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    background-color: transparent;
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background-color: {p["bg"]};
}}
QPushButton {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["accent"]};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QPushButton#saveBtn, QPushButton#submitBtn, QPushButton#queueBtn, QPushButton#addBtn, QPushButton#closeBtn {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    font-weight: bold;
}}
QPushButton#saveBtn:hover, QPushButton#submitBtn:hover, QPushButton#queueBtn:hover, QPushButton#addBtn:hover, QPushButton#closeBtn:hover {{
    background-color: {p["accent_hover"]};
    color: {p["on_accent"]};
}}
QPushButton#ordersBtn {{
    background-color: {p["surface"]};
    color: {p["accent_soft"]};
    border: 1px solid {p["accent_soft"]};
}}
QPushButton#ordersBtn:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QPushButton:disabled {{
    background-color: {p["disabled_bg"]};
    color: {p["disabled_text"]};
    border-color: {p["strong_border"]};
}}
QPushButton#lazyBtn {{
    background-color: {p["cycle_off_bg"]};
    color: {p["text"]};
    border: 1px solid {p["cycle_off_border"]};
    font-weight: bold;
}}
QPushButton#lazyBtn:hover {{
    background-color: {p["cycle_off_hover"]};
    color: {p["text"]};
}}
QPushButton#lazyBtn:checked {{
    background-color: {p["success_bg"]};
    border-color: {p["success_border"]};
}}
QPushButton#lazyBtn:checked:hover {{
    background-color: {p["success_hover"]};
    color: {p["text"]};
}}
QComboBox::down-arrow {{
    image: url("__CHEVRON_DOWN__");
    width: 9px; height: 6px;
    border: none; background: transparent;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {p["raised"]};
    border: 1px solid {p["strong_border"]};
    width: 16px;
}}
QSpinBox::up-arrow {{
    image: url("__CHEVRON_UP__");
    width: 9px; height: 6px;
    border: none; background: transparent;
}}
QSpinBox::down-arrow {{
    image: url("__CHEVRON_DOWN__");
    width: 9px; height: 6px;
    border: none; background: transparent;
}}
QLabel#totalsLabel {{
    color: {p["accent_soft"]};
    font-weight: bold;
}}
QLabel#statusLabel {{
    color: {p["muted_text"]};
}}
QAbstractButton, QHeaderView {{
    background-color: {p["surface"]};
}}
"""


def _with_chevrons(qss: str) -> str:
    """Point the spin box and combo arrows at the shipped chevron assets."""
    asset_dir = Path(__file__).resolve().parent / "assets"
    up = (asset_dir / "chevron-up.png").as_posix()
    down = (asset_dir / "chevron-down.png").as_posix()
    return qss.replace("__CHEVRON_UP__", up).replace("__CHEVRON_DOWN__", down)


def uline_qss(p: dict[str, str]) -> str:
    """dialog_qss plus the chevron arrows for the Uline builder windows."""
    return _with_chevrons(dialog_qss(p))


def orders_qss(p: dict[str, str]) -> str:
    """QSS for the Current Orders picker inside Batch Serial."""
    return f"""
QDialog {{
    background-color: {p["bg"]};
    color: {p["text"]};
}}
QToolTip {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 4px;
}}
QLabel {{
    color: {p["text"]};
    background: transparent;
}}
QTableWidget {{
    background-color: {p["bg"]};
    color: {p["text"]};
    gridline-color: {p["border"]};
    border: 1px solid {p["border"]};
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    selection-background-color: {p["accent"]};
}}
QTableWidget::item:selected {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QHeaderView::section {{
    background-color: {p["surface"]};
    color: {p["accent_soft"]};
    border: 1px solid {p["border"]};
    font-weight: bold;
    padding: 4px;
}}
QCheckBox {{
    background-color: transparent;
    color: {p["text"]};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {p["strong_border"]};
    border-radius: 3px;
    background-color: {p["bg"]};
}}
QCheckBox::indicator:checked {{
    border: 2px solid {p["accent"]};
    background-color: {p["accent"]};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background-color: {p["surface"]};
    border: 1px solid {p["border"]};
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: {p["raised"]};
    border-radius: 3px;
    min-height: 12px;
    min-width: 12px;
}}
QScrollBar::add-line, QScrollBar::add-page {{
    background-color: transparent;
    width: 0;
    height: 0;
}}
QScrollBar::sub-line, QScrollBar::sub-page {{
    background-color: transparent;
    width: 0;
    height: 0;
}}
QPushButton {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["accent"]};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QPushButton#queueBtn {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    font-weight: bold;
}}
QPushButton#queueBtn:hover {{
    background-color: {p["accent_hover"]};
    color: {p["on_accent"]};
}}
"""


def sku_qss(p: dict[str, str]) -> str:
    """QSS for the Unknown SKUs dialog and the Alternative SKU editor."""
    return f"""
QDialog {{
    background-color: {p["bg"]};
    color: {p["text"]};
}}
QToolTip {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 4px;
}}
QLabel {{
    color: {p["text"]};
    background: transparent;
}}
QLabel#errorLabel {{
    color: {p["danger_text"]};
}}
QLineEdit, QSpinBox {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 4px;
    padding: 4px 6px;
    min-height: 24px;
}}
QTableWidget {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    gridline-color: {p["border"]};
    selection-background-color: {p["accent"]};
    selection-color: {p["on_accent"]};
}}
QHeaderView::section {{
    background-color: {p["bg"]};
    color: {p["accent"]};
    border: 1px solid {p["border"]};
    padding: 4px;
    font-weight: bold;
}}
QPushButton {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["accent"]};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QPushButton#submitBtn {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    font-weight: bold;
}}
QPushButton#submitBtn:hover {{
    background-color: {p["accent_hover"]};
    color: {p["on_accent"]};
}}
QCheckBox {{
    color: {p["text"]};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 2px solid {p["strong_border"]};
    border-radius: 3px;
    background-color: {p["bg"]};
}}
QCheckBox::indicator:checked {{
    border: 2px solid {p["accent"]};
    background-color: {p["accent"]};
}}
"""


def release_notes_qss(p: dict[str, str]) -> str:
    """QSS for the What's New window."""
    return f"""
QDialog {{
    background-color: {p["bg"]};
    color: {p["text"]};
}}
QToolTip {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    padding: 4px;
}}
QLabel#notesTitle {{
    color: {p["text"]};
    background: transparent;
    font-size: 16px;
    font-weight: 600;
}}
QTextBrowser {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 4px;
    padding: 8px 10px;
    font-size: 13px;
}}
QPushButton {{
    background-color: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["accent"]};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
QPushButton#closeBtn {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
    font-weight: bold;
}}
QPushButton#closeBtn:hover {{
    background-color: {p["accent_hover"]};
    color: {p["on_accent"]};
}}
"""


def notes_document_css(p: dict[str, str]) -> str:
    """HTML CSS for the release-notes document body inside the browser."""
    return (
        f"body {{ color: {p['text']}; background-color: {p['surface']}; "
        "font-family: 'Segoe UI', sans-serif; font-size: 14px; }"
        f"h1, h2, h3 {{ color: {p['text']}; font-size: 16px; margin: 12px 0 6px; }}"
        f"p, li {{ color: {p['text']}; }}"
        f"a {{ color: {p['accent_soft']}; }}"
        f"code {{ color: {p['version']}; font-family: Consolas, 'Courier New', monospace; }}"
        "ul { margin: 4px 0 8px 18px; }"
        "li { margin: 0 0 6px; }"
    )


def tools_menu_qss(p: dict[str, str]) -> str:
    """QSS for the popup launched from the dashboard toolbox button."""
    return f"""
QMenu {{
    background-color: {p["bg"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 4px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 26px 6px 10px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background-color: {p["accent"]};
    color: {p["on_accent"]};
}}
"""


def fade_qss(p: dict[str, str], t: float) -> str:
    """Inline QSS for the shutdown fade on one button group.

    Returns empty rules that reset to the palette when t is 0; the dashboard
    clears per-widget styles at t == 0 itself.
    """
    grey_text = _mix_text(p, t)
    bg = mix_hex(p["surface"], "#334155", t)
    border = mix_hex(p["strong_border"], "#94A3B8", t)
    return (
        f"background-color: {bg}; color: {grey_text}; "
        f"border: 1px solid {border};"
    )


def action_qss(p: dict[str, str], t: float) -> str:
    """Fade rule for the three big action buttons."""
    bg = mix_hex(p["accent"], "#334155", t)
    text = mix_hex(p["on_accent"], "#94A3B8", t)
    return (
        f"background-color: {bg}; color: {text}; "
        f"border: 1px solid {bg}; font-weight: bold;"
    )


def cycle_qss(p: dict[str, str], t: float, on: bool) -> str:
    """Fade rule for the cycle toggle; keeps green when on, the theme's off color when off."""
    if on:
        bg = mix_hex(p["success_bg"], "#334155", t)
        border = mix_hex(p["success_border"], "#94A3B8", t)
    else:
        bg = mix_hex(p["cycle_off_bg"], "#334155", t)
        border = mix_hex(p["cycle_off_border"], "#94A3B8", t)
    grey_text = _mix_text(p, t)
    return (
        f"background-color: {bg}; color: {grey_text}; "
        f"border: 1px solid {border};"
    )


def pulse_qss(color: str, grey: str, text: str, on_accent: str, amount: float) -> str:
    """Pulse the three action buttons between a color and the theme's grey."""
    bg = mix_hex(color, grey, amount)
    fg = mix_hex(on_accent, text, amount)
    rule = (
        f"background-color: {bg}; color: {fg}; "
        f"border: 1px solid {bg}; font-weight: bold;"
    )
    return (
        "QPushButton#executeBtn, QPushButton#pickBtn, QPushButton#batchBtn, "
        "QPushButton#executeBtn:disabled, QPushButton#pickBtn:disabled, "
        f"QPushButton#batchBtn:disabled {{ {rule} }}"
    )


def update_pulse_qss(p: dict[str, str], amount: float) -> str:
    """Pulse rule for the action buttons while an update warning runs."""
    bg = mix_hex(p["warning"], p["strong_border"], amount)
    fg = mix_hex(p["on_accent"], p["text"], amount)
    rule = (
        f"background-color: {bg}; color: {fg}; "
        f"border: 1px solid {bg}; font-weight: bold;"
    )
    return (
        "QPushButton#executeBtn, QPushButton#pickBtn, QPushButton#batchBtn, "
        "QPushButton#executeBtn:disabled, QPushButton#pickBtn:disabled, "
        f"QPushButton#batchBtn:disabled {{ {rule} }}"
    )


def title_qss(p: dict[str, str], t: float) -> str:
    return (
        f"color: {mix_hex(p['title'], '#94A3B8', t)}; "
        "background: transparent; font-weight: bold;"
    )


def version_qss(p: dict[str, str], t: float) -> str:
    return (
        f"color: {mix_hex(p['version'], '#94A3B8', t)}; "
        "background: transparent; font-weight: bold;"
    )


def slow_label_qss(p: dict[str, str], t: float) -> str:
    return (
        f"color: {mix_hex(p['warning'], '#94A3B8', t)}; "
        "background: transparent; font-weight: bold;"
    )


def apply_app_palette(p: dict[str, str]) -> None:
    """Theme native title bars, dialogs, menus, and message boxes app-wide.

    Windows draws its chrome from the app palette, so this is what makes the
    Settings, Batch Serial, Uline, SKU, and release-notes windows follow the
    theme in their title bars and popup widgets.
    """

    def qhex(value: str) -> QColor:
        color = QColor(value)
        if not color.isValid():
            logger.warning("Theme color %r is invalid; using grey", value)
            return QColor("#808080")
        return color

    app = QApplication.instance()
    if app is None:
        return
    pal = QPalette()
    bg = qhex(p["bg"])
    surface = qhex(p["surface"])
    text = qhex(p["text"])
    accent = qhex(p["accent"])
    disabled = qhex(p["disabled_text"])
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, surface)
    pal.setColor(QPalette.ColorRole.AlternateBase, qhex(p["raised"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, surface)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, surface)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, qhex(p["danger_text"]))
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, qhex(p["on_accent"]))
    pal.setColor(QPalette.ColorRole.Link, qhex(p["accent_soft"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, qhex(p["muted_text"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    app.setPalette(pal)


def apply_theme() -> dict[str, str]:
    """Push the saved theme onto QApplication; returns the palette applied."""
    p = current_palette()
    apply_app_palette(p)
    return p


def set_theme(theme_key: str) -> dict[str, str]:
    """Persist a theme key and push it app-wide; returns the applied palette."""
    key = normalize_theme(theme_key)
    update_settings(theme=key)
    p = palette_for(key)
    apply_app_palette(p)
    logger.info("Theme set to %s", key)
    return p
