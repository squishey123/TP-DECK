"""Popup menu launched from the dashboard toolbox button.

Tools register themselves on import order, so main.py owns the list. Each
entry is (label, callback). The menu is dark themed to match TP DECK.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu, QPushButton

MENU_QSS = """
QMenu {
    background-color: #0F172A;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 4px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 26px 6px 10px;
    border-radius: 3px;
}
QMenu::item:selected {
    background-color: #0EA5E9;
    color: #0F172A;
}
"""


def popup_tools_menu(button: QPushButton, entries: list[tuple[str, Callable[[], None]]]) -> None:
    """Show the tools menu anchored on the toolbox button."""
    menu = QMenu(button)
    menu.setStyleSheet(MENU_QSS)
    for label, callback in entries:
        action = menu.addAction(label)
        action.triggered.connect(callback)
    menu.popup(button.mapToGlobal(QPoint(0, -6)))
