"""Popup menu launched from the dashboard toolbox button.

Tools register themselves on import order, so main.py owns the list. Each
entry is (label, callback). The menu is dark themed to match TP DECK.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu, QPushButton

from tp_deck.theme import current_palette, tools_menu_qss


def popup_tools_menu(button: QPushButton, entries: list[tuple[str, Callable[[], None]]]) -> None:
    """Show the tools menu anchored on the toolbox button."""
    menu = QMenu(button)
    menu.setStyleSheet(tools_menu_qss(current_palette()))
    for label, callback in entries:
        action = menu.addAction(label)
        action.triggered.connect(callback)
    menu.popup(button.mapToGlobal(QPoint(0, -6)))
