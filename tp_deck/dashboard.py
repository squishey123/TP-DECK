"""PySide6 floating dashboard — compact always-on-top tool window."""

from __future__ import annotations

import logging
import math
import time
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
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

from tp_deck.settings_manager import load_settings, update_settings

logger = logging.getLogger("tpdeck")


def _mix_color(start: QColor, end: QColor, amount: float) -> QColor:
    """Blend start toward end. amount 0 leaves start unchanged."""
    t = max(0.0, min(1.0, amount))
    return QColor(
        int(start.red() + (end.red() - start.red()) * t),
        int(start.green() + (end.green() - start.green()) * t),
        int(start.blue() + (end.blue() - start.blue()) * t),
    )


def _mix_hex(start: str, end: str, amount: float) -> str:
    return _mix_color(QColor(start), QColor(end), amount).name()


def _cycle_icon(color: QColor | None = None) -> QIcon:
    """Two arrows chasing each other around a circle."""
    size = 20
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color or QColor("#F8FAFC"))
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
QPushButton#executeBtn, QPushButton#pickBtn, QPushButton#batchBtn {
    background-color: #0EA5E9;
    color: #0F172A;
    font-weight: bold;
    min-height: 28px;
    padding: 6px 6px;
}
QPushButton#executeBtn:hover, QPushButton#pickBtn:hover, QPushButton#batchBtn:hover {
    background-color: #38BDF8;
}
QPushButton#executeBtn:disabled, QPushButton#pickBtn:disabled, QPushButton#batchBtn:disabled {
    background-color: #334155;
    color: #94A3B8;
    border-color: #475569;
}
QPushButton#cycleBtn {
    background-color: #B45309;
    color: #F8FAFC;
    border: 1px solid #F59E0B;
    padding: 0px;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
}
QPushButton#cycleBtn:hover {
    background-color: #D97706;
    color: #F8FAFC;
}
QPushButton#cycleBtn[cycleOn="true"] {
    background-color: #15803D;
    border: 1px solid #4ADE80;
}
QPushButton#cycleBtn[cycleOn="true"]:hover {
    background-color: #16A34A;
    color: #F8FAFC;
}
QPushButton#cycleBtn:disabled {
    background-color: #334155;
    color: #94A3B8;
    border-color: #475569;
}
QPushButton#stopBtn {
    background-color: #7F1D1D;
    color: #FCA5A5;
    border: 1px solid #FCA5A5;
    font-weight: bold;
    font-size: 11px;
    padding: 4px 4px;
}
QPushButton#stopBtn:hover {
    background-color: #991B1B;
    color: #FEE2E2;
}
QPushButton#settingsBtn, QPushButton#powerBtn {
    border-color: #64748B;
    padding: 0px;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
}
QLabel#slowRazorLabel {
    color: #FBBF24;
    font-size: 12px;
    font-weight: bold;
}
"""

RESULTS_QSS = """
QWidget#resultsRoot {
    background-color: #FFFFFF;
    color: #000000;
}
QLabel#resultsNote {
    background-color: #FFFFFF;
    color: #000000;
    font-size: 16px;
}
QTextEdit#resultsText {
    background-color: #FFFFFF;
    color: #000000;
    border: 1px solid #CBD5E1;
    border-radius: 4px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 22px;
    padding: 6px;
}
QPushButton#closeBtn {
    background-color: #0EA5E9;
    color: #0F172A;
    border: 1px solid #0EA5E9;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: bold;
    min-height: 28px;
}
QPushButton#closeBtn:hover {
    background-color: #38BDF8;
    color: #0F172A;
}
QTableWidget#resultsTable {
    background-color: #FFFFFF;
    color: #000000;
    border: 1px solid #CBD5E1;
    gridline-color: #E2E8F0;
    font-family: Consolas, "Courier New", monospace;
    font-size: 18px;
    selection-background-color: #E0F2FE;
    selection-color: #000000;
}
QTableWidget#resultsTable QHeaderView::section {
    background-color: #F1F5F9;
    color: #0F172A;
    font-weight: bold;
    padding: 4px 6px;
    border: 1px solid #CBD5E1;
}
"""


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
            | Qt.WindowType.Tool
        )
        self.setStyleSheet(RESULTS_QSS)

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
        layout.addWidget(self._table)

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
        if note is not None:
            self._note.setText(note)
        self._table.setVisible(False)
        self._text.setVisible(True)
        self._text.setPlainText(text)
        self._fit_to_text(text)
        self.show()
        self.raise_()
        self.activateWindow()

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
        self.show()
        self.raise_()
        self.activateWindow()

    def _fill_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        unknown: list[bool],
    ) -> None:
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(rows))
        unknown_bg = QColor("#FEE2E2")
        for row_index, row in enumerate(rows):
            flagged = row_index < len(unknown) and unknown[row_index]
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )
                if flagged:
                    item.setBackground(unknown_bg)
                self._table.setItem(row_index, column, item)
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
        for column, header in enumerate(headers):
            if header == "Buyer":
                longest = header_metrics.horizontalAdvance(header)
                for row in rows:
                    if column < len(row):
                        longest = max(
                            longest,
                            body_metrics.horizontalAdvance(str(row[column])),
                        )
                self._table.setColumnWidth(column, longest + 28)
                buyer_at = column
            elif header == "Location":
                cap = max(header_metrics.horizontalAdvance(header) + 24, 120)
                width = min(self._table.columnWidth(column), cap)
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
        max_w = max(420, int(screen.availableGeometry().width() * 0.9))
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

    def _fit_to_text(self, text: str) -> None:
        metrics = QFontMetrics(self._text.font())
        lines = text.splitlines() or [""]
        longest = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = max(360, int(avail.width() * 0.9))
            max_h = max(220, int(avail.height() * 0.85))
        else:
            max_w, max_h = 1400, 900

        margins = self.layout().contentsMargins()
        margin_x = margins.left() + margins.right()
        text_w = max(280, min(longest + 36, max_w - margin_x))
        self._text.document().setTextWidth(text_w)
        doc_h = math.ceil(self._text.document().size().height()) + 20

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
        width = min(max_w, text_w + margin_x)
        height = min(max_h, max(180, doc_h + chrome_h))
        self.setFixedSize(width, height)

    def _fit_to_table(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = max(420, int(avail.width() * 0.9))
            max_h = max(220, int(avail.height() * 0.85))
        else:
            max_w, max_h = 1400, 900

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
        self.setFixedSize(width, height)


class ChassisLight(QWidget):
    """Small lamp beside the title. It breathes only while auto-cycle is waiting."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._state = "idle"
        self._phase = 0.0
        self._fade = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

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

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {
            "idle": QColor("#FCA5A5"),
            "running": QColor("#4ADE80"),
            "error": QColor("#FCA5A5"),
        }
        color = colors.get(self._state, QColor("#FCA5A5"))
        if self._state == "waiting":
            wave = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._phase))
            color = QColor("#0EA5E9")
            color.setAlphaF(wave)
        if self._fade > 0:
            color = _mix_color(QColor(color.red(), color.green(), color.blue()), QColor("#94A3B8"), self._fade)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.end()


class CycleButton(QPushButton):
    """Auto-cycle toggle with a ring that drains across the wait."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._deadline: Optional[float] = None
        self._total = 0.0
        self._fade = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.setIcon(_cycle_icon(_mix_color(QColor("#F8FAFC"), QColor("#94A3B8"), self._fade)))
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
        pen = QPen(QColor("#E0F2FE"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if self._fade > 0:
            pen.setColor(_mix_color(QColor("#E0F2FE"), QColor("#94A3B8"), self._fade))
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
        self.setFixedSize(36, 36)

    def set_fade(self, amount: float) -> None:
        self._fade = max(0.0, min(1.0, amount))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = _mix_color(QColor("#F8FAFC"), QColor("#94A3B8"), self._fade)
        pen = QPen(color)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        side = 14.0
        left = (self.width() - side) / 2
        top = (self.height() - side) / 2 + 1
        rect = QRectF(left, top, side, side)
        painter.drawArc(rect, int(125 * 16), int(290 * 16))
        center = rect.center()
        painter.drawLine(QPointF(center.x(), center.y() - 1), QPointF(center.x(), rect.top() - 2))
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
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

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
        border = _mix_color(QColor("#0EA5E9"), QColor("#94A3B8"), self._fade)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QColor("#1E293B"))
        painter.drawRoundedRect(bounds, 6, 6)

        text_bottom = 16 if self._show_track else 8
        text_rect = bounds.adjusted(8, 6, -8, -text_bottom)
        painter.setPen(_mix_color(QColor("#F8FAFC"), QColor("#94A3B8"), self._fade))
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
        painter.setBrush(QColor("#0F172A"))
        painter.drawRoundedRect(track, 3, 3)
        fill_width = track.width() * self._display
        if fill_width > 0:
            painter.setBrush(_mix_color(QColor("#0EA5E9"), QColor("#64748B"), self._fade))
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
            painter.setBrush(_mix_color(QColor("#7DD3FC"), QColor("#94A3B8"), self._fade))
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
            painter.setBrush(QColor("#F0F9FF"))
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
        on_shutdown: Optional[Callable[[], None]] = None,
        on_arm_shutdown: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_execute = on_execute
        self._on_pick_list = on_pick_list
        self._on_batch_serial = on_batch_serial
        self._on_cycle = on_cycle
        self._on_stop = on_stop
        self._on_open_settings = on_open_settings
        self._on_shutdown = on_shutdown
        self._on_arm_shutdown = on_arm_shutdown
        self._persist_enabled = False
        self._results: Optional[ResultsWindow] = None
        self._failures: Optional[ResultsWindow] = None
        self._active = False
        self._error = False
        self._serial_queued = False
        self._job = ""
        self._autocycle_on = False
        self._armed = False
        self._shutting_down = False
        self._arm_started = 0.0
        self._fade = 0.0

        self.setWindowTitle("TP DECK")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self._cycle_waiting = False
        self._arm_timer = QTimer(self)
        self._arm_timer.timeout.connect(self._tick_shutdown_arm)
        self.setFixedSize(300, 356)
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

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addStretch()
        self._light = ChassisLight()
        title_row.addWidget(self._light)
        self._title = QLabel("TP DECK")
        self._title.setObjectName("titleLabel")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_row.addWidget(self._title)
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
        layout.addWidget(self._well)

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
        self.cycle_btn.setIcon(_cycle_icon())
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

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setFixedSize(36, 36)
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
        raw = str(text or "").strip()
        self._well.set_text(_format_status(raw))
        self._well.setToolTip(raw)
        self._error = raw.startswith("Error")
        self._apply_lamp()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self._apply_lamp()

    def set_serial_queued(self, queued: bool) -> None:
        self._serial_queued = bool(queued)
        if not (self._active and self._job == "serials"):
            self.batch_btn.setText("Queued" if self._serial_queued else "Batch Serial")
        self._apply_lamp()

    def input_blocked(self) -> bool:
        """True while shutdown is armed or already running. Emergency Stop still works."""
        return self._armed or self._shutting_down

    def _apply_lamp(self) -> None:
        if self._active or self._serial_queued:
            state = "running"
        elif self._error:
            state = "error"
        elif self._cycle_waiting:
            state = "waiting"
        else:
            state = "idle"
        self._light.set_state(state)

    def begin_shutdown_arm(self) -> None:
        """Ease the panel to grey over 5 seconds, then start Safe Shutdown."""
        if self._armed or self._shutting_down:
            return
        self._armed = True
        self._arm_started = time.monotonic()
        self._apply_fade(0.0)
        if not self._arm_timer.isActive():
            self._arm_timer.start(33)

    def cancel_shutdown_arm(self) -> None:
        """Restore the normal colors and leave the app open."""
        self._armed = False
        self._shutting_down = False
        self._arm_timer.stop()
        self._apply_fade(0.0)

    def mark_shutting_down(self) -> None:
        self._armed = False
        self._shutting_down = True
        self._arm_timer.stop()
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
        if t <= 0:
            for widget in (
                self._title,
                self._slow_label,
                self.execute_btn,
                self.pick_btn,
                self.batch_btn,
                self.settings_btn,
                self.power_btn,
                self.cycle_btn,
            ):
                widget.setStyleSheet("")
            self.set_autocycle(self._autocycle_on)
            return
        grey_text = _mix_hex("#F8FAFC", "#94A3B8", t)
        self._title.setStyleSheet(
            f"color: {_mix_hex('#0EA5E9', '#94A3B8', t)}; background: transparent; font-weight: bold;"
        )
        self._slow_label.setStyleSheet(
            f"color: {_mix_hex('#FBBF24', '#94A3B8', t)}; background: transparent; font-weight: bold;"
        )
        blue_bg = _mix_hex("#0EA5E9", "#334155", t)
        blue_text = _mix_hex("#0F172A", "#94A3B8", t)
        action_qss = (
            f"background-color: {blue_bg}; color: {blue_text}; "
            f"border: 1px solid {blue_bg}; font-weight: bold;"
        )
        for button in (self.execute_btn, self.pick_btn, self.batch_btn):
            button.setStyleSheet(action_qss)
        self.settings_btn.setStyleSheet(
            f"background-color: {_mix_hex('#1E293B', '#334155', t)}; "
            f"color: {grey_text}; border: 1px solid {_mix_hex('#64748B', '#94A3B8', t)};"
        )
        self.power_btn.setStyleSheet(
            f"background-color: {_mix_hex('#1E293B', '#334155', t)}; "
            f"border: 1px solid {_mix_hex('#64748B', '#94A3B8', t)};"
        )
        if self._autocycle_on:
            cycle_bg = _mix_hex("#15803D", "#334155", t)
            cycle_border = _mix_hex("#4ADE80", "#94A3B8", t)
        else:
            cycle_bg = _mix_hex("#B45309", "#334155", t)
            cycle_border = _mix_hex("#F59E0B", "#94A3B8", t)
        self.cycle_btn.setStyleSheet(
            f"background-color: {cycle_bg}; color: {grey_text}; "
            f"border: 1px solid {cycle_border};"
        )

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
        self.setFixedHeight(388 if enabled else 356)

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

    def show_serial_failures(self, failures: list[tuple[int, str]]) -> None:
        """Report serials the batch did not accept, by scan order."""
        if not failures:
            return
        if self._failures is None:
            self._failures = ResultsWindow(
                self,
                title="TP DECK — Failed Serials",
                note="Serial numbers that failed",
            )
        count = len(failures)
        noun = "serial number" if count == 1 else "serial numbers"
        lines = [
            f"{_ordinal(index)} scanned: {serial}"
            for index, serial in failures
        ]
        self._failures.present("\n".join(lines), note=f"{count} {noun} failed")

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
            self.execute_btn.setText("Scrape eBay Orders")
            self.pick_btn.setText("Generate Pick List")
            self.batch_btn.setText("Queued" if self._serial_queued else "Batch Serial")
            self._apply_lamp()

    def _handle_execute(self) -> None:
        if self.input_blocked():
            return
        if self._on_execute:
            self._on_execute()

    def _handle_pick_list(self) -> None:
        if self.input_blocked():
            return
        if self._on_pick_list:
            self._on_pick_list()

    def _handle_batch_serial(self) -> None:
        if self.input_blocked():
            return
        if self._on_batch_serial:
            self._on_batch_serial()

    def _handle_cycle(self) -> None:
        if self.input_blocked():
            return
        if self._on_cycle:
            self._on_cycle()

    def _handle_stop(self) -> None:
        if self._on_stop:
            self._on_stop()

    def _handle_power(self) -> None:
        if self._armed or self._shutting_down:
            return
        if self._on_arm_shutdown:
            self._on_arm_shutdown()
            return
        self.begin_shutdown_arm()

    def _handle_settings(self) -> None:
        if self.input_blocked():
            return
        if self._on_open_settings:
            self._on_open_settings()
