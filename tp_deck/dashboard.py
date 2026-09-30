"""PySide6 floating dashboard — compact always-on-top tool window."""

from __future__ import annotations

import logging
import math
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
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


def _cycle_icon() -> QIcon:
    """Two arrows chasing each other around a circle."""
    size = 20
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#F8FAFC"))
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
    padding: 4px;
    min-width: 36px;
    max-width: 36px;
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
}
QPushButton#stopBtn:hover {
    background-color: #991B1B;
    color: #FEE2E2;
}
QPushButton#settingsBtn {
    border-color: #64748B;
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
        self._table.horizontalHeader().setStretchLastSection(True)
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
        self._table.resizeColumnsToContents()
        for column in range(self._table.columnCount()):
            width = self._table.columnWidth(column)
            self._table.setColumnWidth(column, min(max(width, 72), 420))
        self._table.resizeRowsToContents()

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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_execute = on_execute
        self._on_pick_list = on_pick_list
        self._on_batch_serial = on_batch_serial
        self._on_cycle = on_cycle
        self._on_stop = on_stop
        self._on_open_settings = on_open_settings
        self._persist_enabled = False
        self._results: Optional[ResultsWindow] = None
        self._failures: Optional[ResultsWindow] = None

        self.setWindowTitle("TP DECK")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setFixedSize(300, 340)
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

        self._slow_label = QLabel("Slow Razor")
        self._slow_label.setObjectName("slowRazorLabel")
        self._slow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._slow_label.setToolTip(
            "ERP locator, grid, and serial waits are stretched "
            "until TP DECK is closed."
        )
        self._slow_label.setVisible(False)
        layout.addWidget(self._slow_label)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(52)
        layout.addWidget(self.status_label)

        scrape_row = QHBoxLayout()
        scrape_row.setSpacing(8)

        self.execute_btn = QPushButton("Scrape eBay Orders")
        self.execute_btn.setObjectName("executeBtn")
        self.execute_btn.clicked.connect(self._handle_execute)
        scrape_row.addWidget(self.execute_btn)

        self.cycle_btn = QPushButton()
        self.cycle_btn.setObjectName("cycleBtn")
        self.cycle_btn.setFixedWidth(36)
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

    def set_autocycle(self, enabled: bool) -> None:
        """Amber when off, green when the cache cycle is running."""
        self.cycle_btn.setProperty("cycleOn", "true" if enabled else "false")
        self.cycle_btn.style().unpolish(self.cycle_btn)
        self.cycle_btn.style().polish(self.cycle_btn)
        self.cycle_btn.update()
        self.cycle_btn.setToolTip(
            "Auto-cycle eBay scrape (on)" if enabled else "Auto-cycle eBay scrape (off)"
        )

    def set_slow_razor(self, enabled: bool) -> None:
        """Show the session flag. The window grows only while it is on."""
        self._slow_label.setVisible(bool(enabled))
        self.setFixedHeight(372 if enabled else 340)

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
        if active:
            self.execute_btn.setEnabled(False)
            self.pick_btn.setEnabled(False)
            self.batch_btn.setEnabled(False)
            if job == "picklist":
                self.pick_btn.setText("⏳ Processing...")
            elif job == "serials":
                self.batch_btn.setText("⏳ Processing...")
            else:
                self.execute_btn.setText("⏳ Processing...")
            self.set_status("Processing")
        else:
            self.execute_btn.setEnabled(True)
            self.pick_btn.setEnabled(True)
            self.batch_btn.setEnabled(True)
            self.execute_btn.setText("Scrape eBay Orders")
            self.pick_btn.setText("Generate Pick List")
            self.batch_btn.setText("Batch Serial")

    def _handle_execute(self) -> None:
        if self._on_execute:
            self._on_execute()

    def _handle_pick_list(self) -> None:
        if self._on_pick_list:
            self._on_pick_list()

    def _handle_batch_serial(self) -> None:
        if self._on_batch_serial:
            self._on_batch_serial()

    def _handle_cycle(self) -> None:
        if self._on_cycle:
            self._on_cycle()

    def _handle_stop(self) -> None:
        if self._on_stop:
            self._on_stop()

    def _handle_settings(self) -> None:
        if self._on_open_settings:
            self._on_open_settings()
