"""PySide6 floating dashboard — compact always-on-top tool window."""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tp_deck import app_version
from tp_deck.settings_manager import load_settings, update_settings
from tp_deck.theme import (
    NEUTRAL,
    _MOBILE_ICON_EDGE,
    action_qss,
    cycle_qss,
    dashboard_qss,
    fade_qss,
    palette_for,
    results_qss,
    slow_label_qss,
    pulse_qss,
    title_qss,
    update_pulse_qss,
    version_qss,
)

logger = logging.getLogger("tpdeck")

_DASHBOARD_HEIGHT = 372
_DASHBOARD_HEIGHT_SLOW = 404
# Laptop panel: one short row of square icon buttons, so the window is a wide,
# low strip instead of a 300 by 372 column. The QSS boxes come from
# theme.dashboard_qss(compact=True), which sizes them from the _MOBILE_ICON_EDGE
# this module imports from tp_deck.theme, so the panel, the QSS, and the drawn
# glyphs all agree on one button edge.
_MOBILE_WIDTH = 8 * _MOBILE_ICON_EDGE + 7 * 6 + 12 + 12
_MOBILE_HEIGHT = 112
_MOBILE_HEIGHT_SLOW = 128
# Idle transparency countdown. Interaction based only: auto-cycle, a running job,
# and the results windows do not hold the panel solid. The floor keeps the panel
# findable, and the lamp ring is drawn at full strength so it reads through the
# glass. Kept fully separate from the shutdown grey ease and from input_blocked(),
# which gates job starts and the auto-cycle loop.
_IDLE_ARM_SECONDS = 5.0
_IDLE_RAMP_SECONDS = 5.0
_IDLE_FLOOR = 0.35
# The laptop panel spells nothing out: each action is a square button carrying
# one symbol, and the words stay in the tooltip so the pointer still says what
# the square does. Symbols only replace the labels, never the object names, so
# the fade, pulse, and cycle rules keep matching every button.
_MOBILE_SYMBOLS = {
    "execute": "\U0001f50d",  # magnifier: scrape the eBay order list
    "pick": "\U0001f4cb",  # clipboard: generate the pick list
    "batch": "\U0001f523",  # barcodes: batch serial from a sales order
    "stop": "\U0001f6d1",  # stop sign: emergency stop
    "settings": "\u2699",  # gear
}
_RAINBOW = (
    "#EF4444",
    "#F97316",
    "#EAB308",
    "#22C55E",
    "#06B6D4",
    "#3B82F6",
    "#A855F7",
)
_URGENT_LAMP = "#A855F7"


def _mix_color(start: QColor, end: QColor, amount: float) -> QColor:
    """Blend start toward end. amount 0 leaves start unchanged."""
    t = max(0.0, min(1.0, amount))
    return QColor(
        int(start.red() + (end.red() - start.red()) * t),
        int(start.green() + (end.green() - start.green()) * t),
        int(start.blue() + (end.blue() - start.blue()) * t),
    )


def _palette_mix(p: dict[str, str], key: str, t: float, toward: str = "#94A3B8") -> QColor:
    """Palette color eased toward the shutdown grey."""
    return _mix_color(QColor(p[key]), QColor(toward), max(0.0, min(1.0, t)))


def _cycle_icon(color: QColor) -> QIcon:
    """Two arrows chasing each other around a circle."""
    size = 20
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color)
    pen.setWidthF(1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    center = size / 2
    radius = 6.2

    def arc_and_head(start_deg: float, sweep: float) -> None:
        rect = QRectF(center - radius, center - radius, radius * 2, radius * 2)
        painter.drawArc(rect, int(start_deg * 16), int(sweep * 16))
        end = math.radians(start_deg + sweep)
        tip_x = center + radius * math.cos(end)
        tip_y = center - radius * math.sin(end)
        tangent = end + (math.pi / 2)
        travel_x = math.cos(tangent)
        travel_y = -math.sin(tangent)
        head = 3.6
        for delta in (-0.62, 0.62):
            turn = math.cos(delta)
            side = math.sin(delta)
            back_x = travel_x * turn - travel_y * side
            back_y = travel_x * side + travel_y * turn
            painter.drawLine(
                QPointF(tip_x, tip_y),
                QPointF(tip_x - head * back_x, tip_y - head * back_y),
            )

    arc_and_head(25, 135)
    arc_and_head(205, 135)
    painter.end()
    return QIcon(pixmap)


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


# Techyparts enterprise hardware dark theme. Every color comes from the palette
# module, keyed off the wheel color picked in Settings ▸ Appearance; "neutral"
# (and the legacy "slate") maps to the original cyan-on-slate theme exactly.
def _startup_palette() -> dict[str, str]:
    try:
        settings = load_settings()
    except Exception:  # a damaged settings file must not stop the panel
        return NEUTRAL
    return palette_for(settings.get("theme", "neutral"))


def _ordinal(index: int) -> str:
    """1st, 2nd, 3rd, 4th — teens stay 'th'."""
    value = int(index)
    if 10 <= (value % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


class ResultsWindow(QWidget):
    """Always-on-top report shown when an operation finishes."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "TP DECK — Results",
        note: str = "Copied to clipboard",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("resultsRoot")
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setMinimumSize(420, 180)
        parent_dashboard = parent if isinstance(parent, Dashboard) else None
        self._palette = (
            parent_dashboard.palette() if parent_dashboard is not None
            else _startup_palette()
        )
        self.setStyleSheet(results_qss(self._palette))
        self._unknown_rows: list[bool] = []
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._note = QLabel(note)
        self._note.setObjectName("resultsNote")
        self._note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._table = QTableWidget()
        self._table.setObjectName("resultsTable")
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._table.setAlternatingRowColors(False)
        self._table.setWordWrap(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self._table.setVisible(False)
        self._table.itemChanged.connect(self._on_result_checked)
        layout.addWidget(self._table, 1)

        self._text = QTextEdit()
        self._text.setObjectName("resultsText")
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._text.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._text.setMinimumSize(0, 0)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(22)
        self._text.setFont(font)
        layout.addWidget(self._text)

        self._close = QPushButton("Close")
        self._close.setObjectName("closeBtn")
        self._close.clicked.connect(self.close)
        layout.addWidget(self._close)

    def present(self, text: str, *, note: Optional[str] = None) -> None:
        lines = text.splitlines() or [""]
        self.present_table(["Detail"], [[line] for line in lines], note=note)

    def present_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        unknown: Optional[list[bool]] = None,
        note: Optional[str] = None,
    ) -> None:
        if note is not None:
            self._note.setText(note)
        self._text.setVisible(False)
        self._table.setVisible(True)
        self._fill_table(headers, rows, unknown or [])
        self._fit_to_table()
        self._reveal()

    def _fill_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        unknown: list[bool],
    ) -> None:
        self._unknown_rows = [
            bool(row_index < len(unknown) and unknown[row_index])
            for row_index in range(len(rows))
        ]
        self._filling = True
        self._table.blockSignals(True)
        self._table.clear()
        self._table.setColumnCount(len(headers) + 1)
        self._table.setHorizontalHeaderLabels(["", *headers])
        self._table.setRowCount(len(rows))
        unknown_bg = QColor(self._palette["results_flag"])
        for row_index, row in enumerate(rows):
            flagged = self._unknown_rows[row_index]
            check = QTableWidgetItem("")
            check.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            )
            check.setCheckState(Qt.CheckState.Unchecked)
            if flagged:
                check.setBackground(unknown_bg)
            self._table.setItem(row_index, 0, check)
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )
                if flagged:
                    item.setBackground(unknown_bg)
                self._table.setItem(row_index, column + 1, item)
        self._table.blockSignals(False)
        self._filling = False
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.resizeColumnsToContents()
        body = QFont("Consolas")
        body.setPixelSize(18)
        body_metrics = QFontMetrics(body)
        header_font = QFont("Consolas")
        header_font.setPixelSize(18)
        header_font.setBold(True)
        header_metrics = QFontMetrics(header_font)
        buyer_at: Optional[int] = None
        char_w = max(body_metrics.horizontalAdvance("0"), 8)
        self._table.setColumnWidth(0, 36)
        for column, header in enumerate(headers, start=1):
            if header == "Buyer":
                longest = header_metrics.horizontalAdvance(header)
                data_at = column - 1
                for row in rows:
                    if data_at < len(row):
                        longest = max(
                            longest,
                            body_metrics.horizontalAdvance(str(row[data_at])),
                        )
                self._table.setColumnWidth(column, longest + 28)
                buyer_at = column
            elif header.casefold() in {"order", "sku", "location"}:
                width = self._table.columnWidth(column) + char_w
                self._table.setColumnWidth(column, max(48, width))
            else:
                width = self._table.columnWidth(column)
                self._table.setColumnWidth(column, min(max(width, 72), 420))
        self._shrink_buyer_to_screen(buyer_at)
        self._table.resizeRowsToContents()

    def _shrink_buyer_to_screen(self, buyer_at: Optional[int]) -> None:
        """Keep a long buyer name on one line unless the screen cannot hold it."""
        if buyer_at is None:
            return
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        max_w = max(420, screen.availableGeometry().width())
        margins = self.layout().contentsMargins()
        budget = max_w - margins.left() - margins.right() - 28
        table = self._table
        total = table.frameWidth() * 2
        for column in range(table.columnCount()):
            total += table.columnWidth(column)
        overflow = total - budget
        if overflow <= 0:
            return
        current = table.columnWidth(buyer_at)
        table.setColumnWidth(buyer_at, max(72, current - overflow))

    def set_palette(self, p: dict[str, str]) -> None:
        """Re-theme the report window and repaint any rows already on screen."""
        self._palette = p
        self.setStyleSheet(results_qss(p))
        if self._table.isVisible() and self._table.rowCount():
            for row in range(self._table.rowCount()):
                flagged = row < len(self._unknown_rows) and self._unknown_rows[row]
                processed = False
                check = self._table.item(row, 0)
                if check is not None:
                    processed = check.checkState() == Qt.CheckState.Checked
                background = QColor(p["results_processed_bg"]) if processed else (
                    QColor(p["results_flag"])
                    if flagged
                    else QColor(p["results_bg"])
                )
                foreground = (
                    QColor(p["results_processed_text"])
                    if processed
                    else QColor(p["results_text"])
                )
                self._table.blockSignals(True)
                try:
                    for column in range(self._table.columnCount()):
                        cell = self._table.item(row, column)
                        if cell is not None:
                            cell.setBackground(background)
                            cell.setForeground(foreground)
                finally:
                    self._table.blockSignals(False)

    def _on_result_checked(self, item: QTableWidgetItem) -> None:
        """Grey a row while its checkbox is on. The next results clear it."""
        if self._filling or item.column() != 0:
            return
        processed = item.checkState() == Qt.CheckState.Checked
        flagged = (
            item.row() < len(self._unknown_rows) and self._unknown_rows[item.row()]
        )
        background = QColor(self._palette["results_processed_bg"]) if processed else (
            QColor(self._palette["results_flag"])
            if flagged
            else QColor(self._palette["results_bg"])
        )
        foreground = (
            QColor(self._palette["results_processed_text"])
            if processed
            else QColor(self._palette["results_text"])
        )
        self._table.blockSignals(True)
        try:
            for column in range(self._table.columnCount()):
                cell = self._table.item(item.row(), column)
                if cell is None:
                    continue
                cell.setBackground(background)
                cell.setForeground(foreground)
        finally:
            self._table.blockSignals(False)
        QTimer.singleShot(0, self._table.clearSelection)

    def _screen_limits(self) -> tuple[int, int]:
        """Full usable size of the screen this window is on."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return 1400, 900
        avail = screen.availableGeometry()
        return max(360, avail.width()), max(180, avail.height())

    def _fit_to_table(self) -> None:
        max_w, max_h = self._screen_limits()
        table = self._table
        frame = table.frameWidth() * 2
        content_w = frame + 28
        for column in range(table.columnCount()):
            content_w += table.columnWidth(column)
        content_h = frame + table.horizontalHeader().height() + 8
        for row in range(table.rowCount()):
            content_h += table.rowHeight(row)

        margins = self.layout().contentsMargins()
        margin_x = margins.left() + margins.right()
        note_h = self._note.sizeHint().height()
        btn_h = max(self._close.sizeHint().height(), 36)
        spacing = self.layout().spacing()
        chrome_h = (
            margins.top()
            + margins.bottom()
            + note_h
            + btn_h
            + spacing * 2
            + 8
        )
        width = min(max_w, max(480, content_w + margin_x))
        height = min(max_h, max(220, content_h + chrome_h))
        self.setMaximumSize(16777215, 16777215)
        self.resize(width, height)

    def _reveal(self) -> None:
        self.show()
        self._settle_on_screen()
        # The title-bar size is not available until Windows maps the window.
        QTimer.singleShot(0, self._settle_on_screen)
        self.raise_()
        self.activateWindow()

    def _settle_on_screen(self) -> None:
        """Center on the current screen and keep the title bar on that screen."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_w = max(0, frame.width() - self.width())
        extra_h = max(0, frame.height() - self.height())
        fitted_w = min(self.width(), max(320, avail.width() - extra_w))
        fitted_h = min(self.height(), max(160, avail.height() - extra_h))
        if fitted_w != self.width() or fitted_h != self.height():
            self.resize(fitted_w, fitted_h)
            frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        if frame.top() < avail.top():
            frame.moveTop(avail.top())
        if frame.left() < avail.left():
            frame.moveLeft(avail.left())
        if frame.bottom() > avail.bottom():
            frame.moveBottom(avail.bottom())
        if frame.right() > avail.right():
            frame.moveRight(avail.right())
        self.move(frame.topLeft())


class ChassisLight(QWidget):
    """Small lamp beside the title. It breathes only while auto-cycle is waiting.

    A click starts an urgent sync. There is no label.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._edge = 12
        self._boost = 0.0
        self.setFixedSize(self._edge, self._edge)
        self._state = "idle"
        self._phase = 0.0
        self._fade = 0.0
        self._palette = NEUTRAL
        self._on_click: Optional[Callable[[], None]] = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_click_handler(self, handler: Optional[Callable[[], None]]) -> None:
        self._on_click = handler

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.button() == Qt.MouseButton.LeftButton and self._on_click is not None:
            self._on_click()
        super().mousePressEvent(event)

    def set_palette(self, p: dict[str, str]) -> None:
        self._palette = p
        self.update()

    def set_state(self, state: str) -> None:
        self._state = state or "idle"
        if self._state == "waiting":
            if not self._timer.isActive():
                self._timer.start(33)
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.05) % (math.tau)
        self.update()

    def set_edge(self, edge: int) -> None:
        """Lamp size for this panel shape. The drawing follows the box."""
        self._edge = max(8, int(edge))
        self.setFixedSize(self._edge, self._edge)
        self.update()

    def set_idle_boost(self, amount: float) -> None:
        """Contrast while the panel is transparent.

        Window opacity dims the whole panel at once, so the lamp cannot stay
        fully solid. A ring in the theme's text color is the strongest mark
        available, which keeps the lamp readable through the glass and shows
        an error state's red rather than hiding it.
        """
        self._boost = max(0.0, min(1.0, amount))
        self.update()

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {
            "idle": QColor(self._palette["danger_text"]),
            "running": QColor(self._palette["success_border"]),
            "error": QColor(self._palette["danger_text"]),
        }
        color = colors.get(self._state, QColor(self._palette["danger_text"]))
        if self._state == "urgent":
            color = QColor(_URGENT_LAMP)
        elif self._state == "waiting":
            wave = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._phase))
            color = QColor(self._palette["accent"])
            color.setAlphaF(wave)
        if self._fade > 0:
            color = _mix_color(
                QColor(color.red(), color.green(), color.blue()),
                QColor("#94A3B8"),
                self._fade,
            )
        margin = 1 if self._edge >= 12 else max(0.5, self._edge * 0.08)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(self.rect().adjusted(margin, margin, -margin, -margin))
        if self._boost > 0:
            ring = QPen(QColor(self._palette["text"]))
            ring.setWidthF(max(1.0, self._edge * 0.09))
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.rect().adjusted(margin, margin, -margin, -margin))
        painter.end()


class CycleButton(QPushButton):
    """Auto-cycle toggle with a ring that drains across the wait."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._deadline: Optional[float] = None
        self._total = 0.0
        self._fade = 0.0
        self._palette = NEUTRAL
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

    def set_palette(self, p: dict[str, str]) -> None:
        self._palette = p
        self.setIcon(_cycle_icon(_mix_color(QColor(p["text"]), QColor("#94A3B8"), self._fade)))
        self.update()

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.setIcon(_cycle_icon(_mix_color(QColor(self._palette["text"]), QColor("#94A3B8"), self._fade)))
        self.update()

    def start_countdown(self, seconds: float) -> None:
        self._total = max(0.001, float(seconds))
        self._deadline = time.monotonic() + self._total
        if not self._timer.isActive():
            self._timer.start(33)
        self.update()

    def clear_countdown(self) -> None:
        self._deadline = None
        self._timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().paintEvent(event)
        if self._deadline is None or self._total <= 0:
            return
        remaining = max(0.0, self._deadline - time.monotonic())
        fraction = remaining / self._total
        if fraction <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QColor(self._palette["ring"])
        pen = QPen(ring)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if self._fade > 0:
            pen.setColor(_mix_color(ring, QColor("#94A3B8"), self._fade))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        side = min(self.width(), self.height()) - 8
        if side <= 4:
            painter.end()
            return
        left = (self.width() - side) / 2
        top = (self.height() - side) / 2
        painter.drawArc(QRectF(left, top, side, side), 90 * 16, int(-fraction * 360 * 16))
        painter.end()


class PowerButton(QPushButton):
    """Safe Shutdown control. The glyph eases to grey while shutdown is armed."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fade = 0.0
        self._palette = NEUTRAL
        self._edge = 36
        self.setFixedSize(self._edge, self._edge)

    def set_palette(self, p: dict[str, str]) -> None:
        self._palette = p
        self.update()

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.update()

    def set_edge(self, edge: int) -> None:
        """Resize the button. The glyph is drawn from the box, so it follows."""
        self._edge = max(20, int(edge))
        self.setFixedSize(self._edge, self._edge)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = _palette_mix(self._palette, "text", self._fade)
        pen = QPen(color)
        pen.setWidthF(1.8 * self._edge / 36.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        side = 14.0 * self._edge / 36.0
        left = (self.width() - side) / 2
        top = (self.height() - side) / 2 + self._edge / 36.0
        rect = QRectF(left, top, side, side)
        painter.drawArc(rect, int(125 * 16), int(290 * 16))
        center = rect.center()
        painter.drawLine(
            QPointF(center.x(), center.y() - 1.0 * self._edge / 36.0),
            QPointF(center.x(), rect.top() - 2.0 * self._edge / 36.0),
        )
        painter.end()


class ToolboxButton(QPushButton):
    """Tools launcher. A toolbox drawn in the same line style as the power glyph."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fade = 0.0
        self._palette = NEUTRAL
        self._edge = 36
        self.setFixedSize(self._edge, self._edge)

    def set_palette(self, p: dict[str, str]) -> None:
        self._palette = p
        self.update()

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.update()

    def set_edge(self, edge: int) -> None:
        """Resize the button. The art is drawn at 36 and scaled, so a smaller
        box cannot overflow its coordinates.
        """
        self._edge = max(20, int(edge))
        self.setFixedSize(self._edge, self._edge)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._edge / 36.0
        color = _palette_mix(self._palette, "text", self._fade)
        pen = QPen(color)
        pen.setWidthF(1.6 * s)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(9 * s, 15 * s, 18 * s, 12 * s), 2 * s, 2 * s)
        painter.drawLine(QPointF(9 * s, 20 * s), QPointF(27 * s, 20 * s))
        painter.drawRoundedRect(QRectF(14 * s, 10 * s, 8 * s, 5 * s), 1.5 * s, 1.5 * s)
        painter.drawRoundedRect(QRectF(16 * s, 18 * s, 4 * s, 4 * s), 1 * s, 1 * s)
        painter.end()


class StatusWell(QWidget):
    """Recessed status readout with an optional eased progress track."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(68)
        self.setFont(QFont("Consolas", 9))
        self._text = "Idle"
        self._locked = 0.0
        self._display = 0.0
        self._sheen: Optional[tuple[float, float]] = None
        self._show_track = False
        self._serial = False
        self._phase = 0.0
        self._fade = 0.0
        self._palette = NEUTRAL
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_palette(self, p: dict[str, str]) -> None:
        self._palette = p
        self.update()

    def set_compact(self, compact: bool) -> None:
        """Shrink the readout for the laptop panel. The drawing follows the box.

        The track only draws inside the box, so a shorter well cannot clip it.
        """
        self.setMinimumHeight(30 if compact else 68)
        self.setFont(QFont("Consolas", 7 if compact else 9))
        self.update()

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def set_progress(
        self,
        locked: float,
        sheen: Optional[tuple[float, float]],
    ) -> None:
        self._serial = False
        self._locked = max(0.0, min(1.0, locked))
        self._sheen = sheen
        self._show_track = True
        self._ensure_timer()
        self.update()

    def set_serial(self, fraction: float) -> None:
        self._serial = True
        self._locked = max(0.0, min(1.0, fraction))
        self._sheen = None
        self._show_track = True
        self._ensure_timer()
        self.update()

    def clear_progress(self) -> None:
        self._show_track = False
        self._serial = False
        self._locked = 0.0
        self._display = 0.0
        self._sheen = None
        self._timer.stop()
        self.update()

    def _ensure_timer(self) -> None:
        if not self._timer.isActive():
            self._timer.start(33)

    def _tick(self) -> None:
        self._display += (self._locked - self._display) * 0.2
        if abs(self._display - self._locked) < 0.004:
            self._display = self._locked
        self._phase = (self._phase + 0.045) % 1.0
        moving = abs(self._display - self._locked) >= 0.004 or self._sheen is not None
        if self._show_track and (moving or self._serial):
            self.update()
            return
        self._timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        border = _palette_mix(self._palette, "accent", self._fade)
        painter.setPen(QPen(border, 1))
        painter.setBrush(_palette_mix(self._palette, "surface", self._fade, "#334155"))
        painter.drawRoundedRect(bounds, 6, 6)

        text_bottom = 16 if self._show_track else 8
        text_rect = bounds.adjusted(8, 6, -8, -text_bottom)
        painter.setPen(_palette_mix(self._palette, "text", self._fade))
        painter.setFont(self.font())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._text,
        )
        if not self._show_track:
            painter.end()
            return

        track = QRectF(10, self.height() - 14, max(1, self.width() - 20), 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_palette_mix(self._palette, "track", self._fade))
        painter.drawRoundedRect(track, 3, 3)
        fill_width = track.width() * self._display
        if fill_width > 0:
            painter.setBrush(_palette_mix(self._palette, "accent", self._fade, "#64748B"))
            painter.drawRoundedRect(
                QRectF(track.x(), track.y(), fill_width, track.height()),
                3,
                3,
            )
        if self._sheen is not None:
            start, end = self._sheen
            span = max(0.04, end - start)
            band = min(0.08, span * 0.45)
            travel = start + self._phase * max(0.0, span - band)
            painter.setBrush(_palette_mix(self._palette, "sheen", self._fade))
            painter.drawRoundedRect(
                QRectF(
                    track.x() + track.width() * travel,
                    track.y(),
                    track.width() * band,
                    track.height(),
                ),
                3,
                3,
            )
        if self._serial and fill_width > 0:
            painter.setBrush(_palette_mix(self._palette, "cap", self._fade))
            cap = QRectF(track.x() + fill_width - 3, track.y(), 4, track.height())
            painter.drawRoundedRect(cap, 2, 2)
        painter.end()


class Dashboard(QMainWindow):
    """Compact floating panel that stays on top and remembers screen position."""

    def __init__(
        self,
        on_execute: Optional[Callable[[], None]] = None,
        on_pick_list: Optional[Callable[[], None]] = None,
        on_batch_serial: Optional[Callable[[], None]] = None,
        on_cycle: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
        on_open_settings: Optional[Callable[[], None]] = None,
        on_open_tools: Optional[Callable[[], None]] = None,
        on_shutdown: Optional[Callable[[], None]] = None,
        on_arm_shutdown: Optional[Callable[[], None]] = None,
        on_urgent_sync: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_execute = on_execute
        self._on_pick_list = on_pick_list
        self._on_batch_serial = on_batch_serial
        self._on_cycle = on_cycle
        self._on_stop = on_stop
        self._on_open_settings = on_open_settings
        self._on_open_tools = on_open_tools
        self._on_shutdown = on_shutdown
        self._on_arm_shutdown = on_arm_shutdown
        self._on_urgent_sync = on_urgent_sync
        self._persist_enabled = False
        self._results: Optional[ResultsWindow] = None
        self._failures: Optional[ResultsWindow] = None
        self._failure_lines: list[str] = []
        self._active = False
        self._error = False
        self._status_text = ""
        self._serial_queued = False
        self._job = ""
        self._autocycle_on = False
        self._armed = False
        self._shutting_down = False
        self._arm_started = 0.0
        self._fade = 0.0
        self._update_kind = "app"
        self._update_warning = False
        self._update_cancelled = False
        self._update_started = 0.0
        self._urgent = False
        self._update_restore_enabled: Optional[list[bool]] = None
        self._quitting = False
        self._palette = _startup_palette()
        # The panel shape is chosen at launch: Settings says so, because the
        # window is fixed-sized and a mid-session resize would move it around
        # on the operator's screens. The idle fade is read here too, but
        # set_idle_fade_enabled turns it on or off for a session already open.
        launch_settings = load_settings()
        self._compact = bool(launch_settings.get("mobile_mode", False))
        self._idle_fade_enabled = False
        self._idle_touched = time.monotonic()
        self._idle_opacity = 1.0

        self.setWindowTitle("TP DECK")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self._cycle_waiting = False
        self._arm_timer = QTimer(self)
        self._arm_timer.timeout.connect(self._tick_shutdown_arm)
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._tick_update_warning)
        self._rainbow_timer = QTimer(self)
        self._rainbow_timer.timeout.connect(self._tick_rainbow)
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._tick_idle_fade)
        self.setFixedSize(
            _MOBILE_WIDTH if self._compact else 300,
            _MOBILE_HEIGHT if self._compact else _DASHBOARD_HEIGHT,
        )
        self.setStyleSheet(dashboard_qss(self._palette, compact=self._compact))

        self._build_ui()
        self._restore_position()
        self._persist_enabled = True
        self.set_idle_fade_enabled(bool(launch_settings.get("fade_out_main_ui", False)))
        self.set_status("Idle")

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("dashboardRoot")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        if self._compact:
            # A short strip: less vertical chrome, so the row of squares and
            # the shrunk title block are what the operator actually reads.
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addStretch()
        self._light = ChassisLight()
        self._light.set_click_handler(self._handle_urgent)
        if self._compact:
            self._light.set_edge(9)
        title_row.addWidget(self._light)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("TP DECK")
        self._title.setObjectName("titleLabel")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_col.addWidget(self._title)
        self._version = QLabel(f"// {app_version()} //")
        self._version.setObjectName("versionLabel")
        self._version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_font = QFont("Consolas", 7 if self._compact else 8)
        version_font.setBold(True)
        version_font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, 1.0 if self._compact else 2.2
        )
        self._version.setFont(version_font)
        self._version.setToolTip(f"TP DECK {app_version()}")
        title_col.addWidget(self._version)
        title_row.addLayout(title_col)
        title_row.addStretch()
        layout.addLayout(title_row)

        self._slow_label = QLabel("Slow Razor")
        self._slow_label.setObjectName("slowRazorLabel")
        self._slow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._slow_label.setToolTip(
            "ERP locator, grid, and serial waits are stretched "
            "until TP DECK is closed."
        )
        self._slow_label.setVisible(False)
        layout.addWidget(self._slow_label)

        self._well = StatusWell()
        if self._compact:
            self._well.set_compact(True)
        layout.addWidget(self._well)

        if self._compact:
            self._build_compact_buttons(layout)
            return

        scrape_row = QHBoxLayout()
        scrape_row.setSpacing(8)
        scrape_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.execute_btn = QPushButton("Scrape eBay Orders")
        self.execute_btn.setObjectName("executeBtn")
        self.execute_btn.clicked.connect(self._handle_execute)
        scrape_row.addWidget(self.execute_btn)

        self.cycle_btn = CycleButton()
        self.cycle_btn.setObjectName("cycleBtn")
        self.cycle_btn.setFixedSize(36, 36)
        self.cycle_btn.setIcon(_cycle_icon(QColor(self._palette["text"])))
        self.cycle_btn.setIconSize(QSize(18, 18))
        self.cycle_btn.setToolTip("Auto-cycle eBay scrape (off)")
        self.cycle_btn.clicked.connect(self._handle_cycle)
        scrape_row.addWidget(self.cycle_btn)
        layout.addLayout(scrape_row)
        self.set_autocycle(False)

        self.pick_btn = QPushButton("Generate Pick List")
        self.pick_btn.setObjectName("pickBtn")
        self.pick_btn.clicked.connect(self._handle_pick_list)
        layout.addWidget(self.pick_btn)

        self.batch_btn = QPushButton("Batch Serial")
        self.batch_btn.setObjectName("batchBtn")
        self.batch_btn.clicked.connect(self._handle_batch_serial)
        layout.addWidget(self.batch_btn)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.stop_btn = QPushButton("🛑 Emergency Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.clicked.connect(self._handle_stop)
        row.addWidget(self.stop_btn)

        self.power_btn = PowerButton()
        self.power_btn.setObjectName("powerBtn")
        self.power_btn.setToolTip("Safe Shutdown")
        self.power_btn.clicked.connect(self._handle_power)
        row.addWidget(self.power_btn)

        self.tools_btn = ToolboxButton()
        self.tools_btn.setObjectName("toolsBtn")
        self.tools_btn.setToolTip("Tools")
        self.tools_btn.clicked.connect(self._handle_tools)
        row.addWidget(self.tools_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setFixedSize(36, 36)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._handle_settings)
        row.addWidget(self.settings_btn)

        layout.addLayout(row)

    def _build_compact_buttons(self, layout: QVBoxLayout) -> None:
        """One row of square symbol buttons: the laptop panel's whole body.

        Eight squares, six pixels apart, inside the twelve-pixel margins, is
        exactly _MOBILE_WIDTH, so the row cannot be squeezed and the QSS boxes
        (all sized from _MOBILE_ICON_EDGE) agree with the window. The words
        only exist in the tooltips, which is what the pointer reads.
        """
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        edge = _MOBILE_ICON_EDGE
        specs = (
            ("execute_btn", QPushButton(_MOBILE_SYMBOLS["execute"]), "executeBtn",
             "Scrape eBay Orders", self._handle_execute),
            ("pick_btn", QPushButton(_MOBILE_SYMBOLS["pick"]), "pickBtn",
             "Generate Pick List", self._handle_pick_list),
            ("batch_btn", QPushButton(_MOBILE_SYMBOLS["batch"]), "batchBtn",
             "Batch Serial", self._handle_batch_serial),
            ("cycle_btn", CycleButton(), "cycleBtn",
             "Auto-cycle eBay scrape", self._handle_cycle),
            ("stop_btn", QPushButton(_MOBILE_SYMBOLS["stop"]), "stopBtn",
             "Emergency Stop", self._handle_stop),
            ("power_btn", PowerButton(), "powerBtn",
             "Safe Shutdown", self._handle_power),
            ("tools_btn", ToolboxButton(), "toolsBtn",
             "Tools", self._handle_tools),
            ("settings_btn", QPushButton(_MOBILE_SYMBOLS["settings"]), "settingsBtn",
             "Settings", self._handle_settings),
        )
        for name, widget, object_name, tip, handler in specs:
            widget.setObjectName(object_name)
            widget.setToolTip(tip)
            widget.clicked.connect(handler)
            # PowerButton and ToolboxButton draw their glyph from the box, so
            # they resize themselves; the rest are plain squares.
            if hasattr(widget, "set_edge"):
                widget.set_edge(edge)
            else:
                widget.setFixedSize(edge, edge)
            setattr(self, name, widget)
            row.addWidget(widget)
        self.cycle_btn.setIcon(_cycle_icon(QColor(self._palette["text"])))
        self.cycle_btn.setIconSize(QSize(edge - 10, edge - 10))
        layout.addLayout(row)

    def palette(self) -> dict[str, str]:
        """The palette this panel is themed with."""
        return self._palette

    def apply_palette(self, p: dict[str, str]) -> None:
        """Re-theme the panel, its painted widgets, and any report windows."""
        self._palette = p
        self.setStyleSheet(dashboard_qss(p, compact=self._compact))
        self._light.set_palette(p)
        self._well.set_palette(p)
        self.cycle_btn.set_palette(p)
        self.power_btn.set_palette(p)
        self.tools_btn.set_palette(p)
        if self._fade > 0:
            self._apply_fade(self._fade)
        if self._results is not None:
            self._results.set_palette(p)
        if self._failures is not None:
            self._failures.set_palette(p)

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
        if self._quitting:
            return
        self._quitting = True
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def set_status(self, text: str) -> None:
        raw = str(text or "").strip()
        self._status_text = raw
        self._well.set_text(_format_status(raw))
        self._well.setToolTip(raw)
        self._error = raw.startswith("Error")
        self._apply_lamp()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self._apply_lamp()

    def _action_labels(self) -> tuple[str, str, str]:
        """Labels the three action buttons carry now.

        The laptop panel keeps its symbols instead of words: a 28-pixel square
        cannot hold "Generate Pick List", and the tooltip already says it.
        """
        if self._compact:
            return (
                _MOBILE_SYMBOLS["execute"],
                _MOBILE_SYMBOLS["pick"],
                _MOBILE_SYMBOLS["batch"],
            )
        return "Scrape eBay Orders", "Generate Pick List", "Batch Serial"

    def set_serial_queued(self, queued: bool) -> None:
        self._serial_queued = bool(queued)
        if not (self._active and self._job == "serials"):
            if self._compact:
                # The lamp already reads "running" for a queued order, and the
                # symbol cannot say Queued, so the words are dropped here.
                self.batch_btn.setText(_MOBILE_SYMBOLS["batch"])
            else:
                self.batch_btn.setText("Queued" if self._serial_queued else "Batch Serial")
        self._apply_lamp()

    def input_blocked(self) -> bool:
        """True while shutdown or an update warning is in progress. Emergency Stop still works."""
        return self._armed or self._shutting_down or self._update_warning

    def _apply_lamp(self) -> None:
        if self._urgent:
            state = "urgent"
        elif self._active or self._serial_queued:
            state = "running"
        elif self._error:
            state = "error"
        elif self._cycle_waiting:
            state = "waiting"
        else:
            state = "idle"
        self._light.set_state(state)

    def _handle_urgent(self) -> None:
        """Hidden lamp click. Ignored while a job or an update warning is up."""
        self.mark_touched()
        if self._active or self.input_blocked():
            return
        if self._on_urgent_sync:
            self._on_urgent_sync()

    def begin_urgent_sync(self) -> None:
        """Purple lamp and a rainbow pulse on the three action buttons."""
        self.mark_touched()
        self._urgent = True
        self._paint_rainbow()
        if not self._rainbow_timer.isActive():
            self._rainbow_timer.start(500)
        self._apply_lamp()

    def end_urgent_sync(self) -> None:
        """Restore the lamp and the action buttons, including a pulse already running."""
        self._urgent = False
        self._rainbow_timer.stop()
        buttons = (self.execute_btn, self.pick_btn, self.batch_btn)
        if self._update_warning:
            self._paint_update_pulse(
                (math.sin((time.monotonic() - self._update_started) * math.tau / 0.8) + 1.0)
                / 2.0
            )
        elif self._fade > 0:
            self._apply_fade(self._fade)
        else:
            for button in buttons:
                button.setStyleSheet("")
        self._apply_lamp()

    def _tick_rainbow(self) -> None:
        if not self._urgent:
            return
        self._paint_rainbow()

    def _paint_rainbow(self) -> None:
        color = random.choice(_RAINBOW)
        rule = (
            f"background-color: {color}; color: #0F172A; "
            f"border: 1px solid {color}; font-weight: bold;"
        )
        qss = (
            "QPushButton#executeBtn, QPushButton#pickBtn, QPushButton#batchBtn, "
            "QPushButton#executeBtn:disabled, QPushButton#pickBtn:disabled, "
            f"QPushButton#batchBtn:disabled {{ {rule} }}"
        )
        for button in (self.execute_btn, self.pick_btn, self.batch_btn):
            button.setStyleSheet(qss)

    def begin_shutdown_arm(self) -> None:
        """Ease the panel to grey over 5 seconds, then start Safe Shutdown."""
        if self._armed or self._shutting_down:
            return
        self.mark_touched()
        self._armed = True
        self._arm_started = time.monotonic()
        self._apply_fade(0.0)
        if not self._arm_timer.isActive():
            self._arm_timer.start(33)

    def cancel_shutdown_arm(self) -> None:
        """Restore the normal colors and leave the app open."""
        self.mark_touched()
        self._armed = False
        self._shutting_down = False
        self._arm_timer.stop()
        self._apply_fade(0.0)

    def begin_update_warning(self, kind: str = "app") -> None:
        """Pulse the action buttons so an update can be cancelled.

        App updates pulse yellow and grey. Chrome pulses green and grey.
        Both alternate yellow and green.
        """
        self._update_kind = kind if kind in {"app", "chrome", "both"} else "app"
        self.mark_touched()
        buttons = (self.execute_btn, self.pick_btn, self.batch_btn)
        self._update_restore_enabled = [button.isEnabled() for button in buttons]
        self._update_cancelled = False
        self._update_warning = True
        self._update_started = time.monotonic()
        for button in buttons:
            button.setEnabled(True)
        self._paint_update_pulse(1.0)
        if not self._update_timer.isActive():
            self._update_timer.start(33)

    def cancel_update_warning(self) -> bool:
        """Stop the pulse. True only when a warning was still running."""
        self.mark_touched()
        if not self._update_warning:
            return False
        self._update_cancelled = True
        self.end_update_warning()
        return True

    def update_warning_cancelled(self) -> bool:
        return self._update_cancelled

    def end_update_warning(self) -> None:
        self._update_warning = False
        self._update_timer.stop()
        buttons = (self.execute_btn, self.pick_btn, self.batch_btn)
        for button in buttons:
            button.setStyleSheet("")
        saved = getattr(self, "_update_restore_enabled", None)
        if saved is not None:
            for button, enabled in zip(buttons, saved):
                button.setEnabled(enabled)
            self._update_restore_enabled = None

    def _tick_update_warning(self) -> None:
        if not self._update_warning:
            return
        elapsed = time.monotonic() - self._update_started
        amount = (math.sin(elapsed * math.tau / 0.8) + 1.0) / 2.0
        self._paint_update_pulse(amount)

    def _paint_update_pulse(self, amount: float) -> None:
        kind = self._update_kind
        if kind == "both":
            elapsed = time.monotonic() - self._update_started
            color = "#FBBF24" if int(elapsed / 0.5) % 2 == 0 else "#22C55E"
            qss = pulse_qss(
                color,
                color,
                self._palette["text"],
                self._palette["on_accent"],
                0.0,
            )
        elif kind == "chrome":
            qss = pulse_qss(
                "#22C55E",
                self._palette["strong_border"],
                self._palette["text"],
                self._palette["on_accent"],
                amount,
            )
        else:
            qss = update_pulse_qss(self._palette, amount)
        for button in (self.execute_btn, self.pick_btn, self.batch_btn):
            button.setStyleSheet(qss)

    def set_idle_fade_enabled(self, enabled: bool) -> None:
        """Turn the idle transparency on or off for a session already open."""
        self._idle_fade_enabled = bool(enabled)
        if self._idle_fade_enabled:
            if not self._idle_timer.isActive():
                self._idle_timer.start(250)
            return
        # Off also drops the watch: a timer that keeps ticking every 250 ms
        # would burn the rest of the session for nothing.
        if self._idle_timer.isActive():
            self._idle_timer.stop()
        if self._idle_opacity < 1.0:
            self._apply_idle_fade(0.0)

    def mark_touched(self) -> None:
        """The operator reached for the panel: it goes back solid."""
        self._idle_touched = time.monotonic()
        if self._idle_opacity < 1.0:
            self._apply_idle_fade(0.0)

    def _hovering(self) -> bool:
        """True while the pointer is inside the panel's box."""
        frame = self.frameGeometry()
        point = QCursor.pos()
        return frame.contains(point.x(), point.y())

    def _apply_idle_fade(self, amount: float) -> None:
        """Ease the whole window toward transparent, never fully clear.

        The whole panel dims at once, which is why ChassisLight draws a bright
        ring through set_idle_boost: the lamp is the mark that has to stay
        findable behind the glass. _IDLE_FLOOR is the floor, so the panel can
        always be seen and clicked. This is separate from the shutdown grey
        ease in _apply_fade, which changes colors, not window alpha.
        """
        t = max(0.0, min(1.0, amount))
        opacity = 1.0 - t * (1.0 - _IDLE_FLOOR)
        self._idle_opacity = opacity
        self.setWindowOpacity(opacity)
        self._light.set_idle_boost(t)

    def _tick_idle_fade(self) -> None:
        if not self._idle_fade_enabled:
            return
        if self.input_blocked():
            # Safe Shutdown and an update warning both ask for a click inside
            # five seconds, so they hold the panel solid while they run.
            if self._idle_opacity < 1.0:
                self._apply_idle_fade(0.0)
            return
        if self._hovering():
            self._idle_touched = time.monotonic()
            if self._idle_opacity < 1.0:
                self._apply_idle_fade(0.0)
            return
        elapsed = time.monotonic() - self._idle_touched
        ramp = (elapsed - _IDLE_ARM_SECONDS) / _IDLE_RAMP_SECONDS
        if ramp <= 0:
            return
        self._apply_idle_fade(min(1.0, ramp))

    def mark_shutting_down(self) -> None:
        self._armed = False
        self._shutting_down = True
        self._arm_timer.stop()
        # The grey ease is the last thing the operator sees, so the glass stays
        # solid while it plays.
        if self._idle_opacity < 1.0:
            self._apply_idle_fade(0.0)
        self._apply_fade(1.0)

    def _tick_shutdown_arm(self) -> None:
        elapsed = time.monotonic() - self._arm_started
        self._apply_fade(min(1.0, elapsed / 5.0))
        if elapsed < 5.0 or not self._armed:
            return
        self._arm_timer.stop()
        self._armed = False
        self._shutting_down = True
        if self._on_shutdown:
            self._on_shutdown()

    def _apply_fade(self, amount: float) -> None:
        t = max(0.0, min(1.0, amount))
        self._fade = t
        self._light.set_fade(t)
        self._well.set_fade(t)
        self.cycle_btn.set_fade(t)
        self.power_btn.set_fade(t)
        self.tools_btn.set_fade(t)
        if t <= 0:
            for widget in (
                self._title,
                self._version,
                self._slow_label,
                self.execute_btn,
                self.pick_btn,
                self.batch_btn,
                self.settings_btn,
                self.power_btn,
                self.tools_btn,
                self.cycle_btn,
            ):
                widget.setStyleSheet("")
            self.set_autocycle(self._autocycle_on)
            return
        self._title.setStyleSheet(title_qss(self._palette, t))
        self._version.setStyleSheet(version_qss(self._palette, t))
        self._slow_label.setStyleSheet(slow_label_qss(self._palette, t))
        action_qss_text = action_qss(self._palette, t)
        for button in (self.execute_btn, self.pick_btn, self.batch_btn):
            button.setStyleSheet(action_qss_text)
        fade_rule = fade_qss(self._palette, t)
        self.settings_btn.setStyleSheet(fade_rule)
        self.tools_btn.setStyleSheet(fade_rule)
        self.power_btn.setStyleSheet(fade_rule)
        self.cycle_btn.setStyleSheet(cycle_qss(self._palette, t, self._autocycle_on))

    def set_run_progress(
        self,
        locked: float,
        sheen: Optional[tuple[float, float]],
    ) -> None:
        self._well.set_progress(locked, sheen)

    def set_serial_progress(self, done: int, total: int) -> None:
        fraction = 0.0 if total <= 0 else done / total
        self._well.set_serial(fraction)

    def clear_progress(self) -> None:
        self._well.clear_progress()

    def start_cycle_countdown(self, seconds: float) -> None:
        self._cycle_waiting = True
        self.cycle_btn.start_countdown(seconds)
        self._apply_lamp()

    def clear_cycle_countdown(self) -> None:
        self._cycle_waiting = False
        self.cycle_btn.clear_countdown()
        self._apply_lamp()

    def set_autocycle(self, enabled: bool) -> None:
        """Amber when off, green when the cache cycle is running."""
        self._autocycle_on = bool(enabled)
        self.cycle_btn.setProperty("cycleOn", "true" if enabled else "false")
        self.cycle_btn.style().unpolish(self.cycle_btn)
        self.cycle_btn.style().polish(self.cycle_btn)
        self.cycle_btn.update()
        self.cycle_btn.setToolTip(
            "Auto-cycle eBay scrape (on)" if enabled else "Auto-cycle eBay scrape (off)"
        )
        if not enabled:
            self.clear_cycle_countdown()

    def set_slow_razor(self, enabled: bool) -> None:
        """Show the session flag. The window grows only while it is on."""
        self._slow_label.setVisible(bool(enabled))
        if self._compact:
            self.setFixedHeight(
                _MOBILE_HEIGHT_SLOW if enabled else _MOBILE_HEIGHT
            )
            return
        self.setFixedHeight(
            _DASHBOARD_HEIGHT_SLOW if enabled else _DASHBOARD_HEIGHT
        )

    def show_results(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        unknown: Optional[list[bool]] = None,
        note: str = "Copied to clipboard",
    ) -> None:
        if self._results is None:
            self._results = ResultsWindow(self)
        display_rows = rows
        if not display_rows:
            blank = [""] * max(0, len(headers) - 1)
            display_rows = [["Nothing to copy", *blank]] if headers else [["Nothing to copy"]]
            note = note or "Nothing to copy"
        self._results.present_table(
            headers,
            display_rows,
            unknown=unknown,
            note=note,
        )

    def show_serial_failures(
        self,
        failures: list[tuple[int, str]],
        order: str = "",
    ) -> None:
        """Report serials the batch did not accept, by scan order.

        A later order appends to the window already on screen, so a queue does
        not replace the notice for the order that just failed. Index 0 is a
        batch error (the run itself stopped) and is shown as a plain line.
        """
        if not failures:
            return
        prefix = f"{order}: " if str(order or "").strip() else ""
        lines = []
        for index, serial in failures:
            if int(index) <= 0:
                lines.append(f"{prefix}{serial}")
            else:
                lines.append(f"{prefix}{_ordinal(index)} scanned: {serial}")
        visible = self._failures is not None and self._failures.isVisible()
        if visible and self._failure_lines:
            self._failure_lines.extend(lines)
        else:
            self._failure_lines = lines
        if self._failures is None:
            self._failures = ResultsWindow(
                self,
                title="TP DECK — Failed Serials",
                note="Serial numbers that failed",
            )
        count = len(self._failure_lines)
        noun = "serial number" if count == 1 else "serial numbers"
        self._failures.present(
            "\n".join(self._failure_lines),
            note=f"{count} {noun} failed",
        )

    def set_actions_enabled(self, enabled: bool) -> None:
        """Enable or disable scrape, pick list, batch, and cycle without changing labels."""
        self.execute_btn.setEnabled(enabled)
        self.pick_btn.setEnabled(enabled)
        self.batch_btn.setEnabled(enabled)
        self.cycle_btn.setEnabled(enabled)

    def set_processing(self, active: bool, job: str = "orders") -> None:
        """Disable action buttons while a run is in flight; leave the cycle toggle clickable."""
        self._active = bool(active)
        self._job = job if active else self._job
        blocked = self.input_blocked()
        if active:
            if not blocked:
                self.execute_btn.setEnabled(False)
                self.pick_btn.setEnabled(False)
                self.batch_btn.setEnabled(job in {"cycle", "serials"})
            if not self._compact:
                # A 28-pixel square cannot hold "⏳ Processing...", so there the
                # greyed-out button, the lamp, and the well's "Processing" say
                # it instead. The button is still disabled above either way.
                if job == "picklist":
                    self.pick_btn.setText("⏳ Processing...")
                elif job == "serials":
                    self.batch_btn.setText("⏳ Processing...")
                else:
                    self.execute_btn.setText("⏳ Processing...")
                if job == "cycle" and self._serial_queued:
                    self.batch_btn.setText("Queued")
            self.set_status("Processing")
        else:
            self.clear_progress()
            if not blocked:
                self.execute_btn.setEnabled(True)
                self.pick_btn.setEnabled(True)
                self.batch_btn.setEnabled(True)
                self.cycle_btn.setEnabled(True)
            execute, pick, batch = self._action_labels()
            self.execute_btn.setText(execute)
            self.pick_btn.setText(pick)
            self.batch_btn.setText(batch)
            self._apply_lamp()

    def _handle_execute(self) -> None:
        self.mark_touched()
        if self.input_blocked():
            return
        if self._on_execute:
            self._on_execute()

    def _handle_pick_list(self) -> None:
        self.mark_touched()
        if self.input_blocked():
            return
        if self._on_pick_list:
            self._on_pick_list()

    def _handle_batch_serial(self) -> None:
        self.mark_touched()
        if self.input_blocked():
            return
        if self._on_batch_serial:
            self._on_batch_serial()

    def _handle_cycle(self) -> None:
        self.mark_touched()
        if self.input_blocked():
            return
        if self._on_cycle:
            self._on_cycle()

    def _handle_stop(self) -> None:
        self.mark_touched()
        if self._on_stop:
            self._on_stop()

    def _handle_power(self) -> None:
        self.mark_touched()
        if self._armed or self._shutting_down or self._update_warning:
            return
        if self._on_arm_shutdown:
            self._on_arm_shutdown()
            return
        self.begin_shutdown_arm()

    def _handle_tools(self) -> None:
        self.mark_touched()
        if self.input_blocked():
            return
        if self._on_open_tools:
            self._on_open_tools()

    def _handle_settings(self) -> None:
        self.mark_touched()
        if self.input_blocked():
            return
        if self._on_open_settings:
            self._on_open_settings()
