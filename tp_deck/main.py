"""TP DECK entry point — event loop, logging, and floating dashboard."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from tp_deck.dashboard import Dashboard
from tp_deck.settings_dialog import SettingsDialog

LOG_PATH = Path(__file__).resolve().parent / "tpdeck.log"


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    _configure_logging()
    logger = logging.getLogger("tpdeck")
    logger.info("TP DECK starting (Phase 1 — Shell & State)")

    app = QApplication(sys.argv)
    app.setApplicationName("TP DECK")
    app.setQuitOnLastWindowClosed(True)

    dashboard = Dashboard(
        on_execute=lambda: _on_execute(dashboard),
        on_stop=lambda: _on_stop(dashboard),
        on_open_settings=lambda: _open_settings(dashboard),
    )
    dashboard.show()

    return app.exec()


def _on_execute(dashboard: Dashboard) -> None:
    """Phase 1: UI-only debounce demo. Real asyncio task arrives in Phase 2."""
    logger = logging.getLogger("tpdeck")
    logger.info("Execute clicked (stub — Phase 2 wires the automation task)")
    dashboard.set_processing(True)
    dashboard.set_status("Processing (stub)")
    # Immediately restore so Phase 1 stays usable without the async bridge.
    dashboard.set_processing(False)
    dashboard.set_status("Idle")


def _on_stop(dashboard: Dashboard) -> None:
    logger = logging.getLogger("tpdeck")
    logger.info("Emergency Stop clicked (stub — Phase 2 cancels the asyncio task)")
    dashboard.set_status("Stopped")
    dashboard.set_processing(False)


def _open_settings(dashboard: Dashboard) -> None:
    dialog = SettingsDialog(parent=dashboard)
    dialog.exec()


if __name__ == "__main__":
    raise SystemExit(main())
