"""TP DECK entry point — Qt + asyncio bridge, hotkey kill-switch, dashboard."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from tp_deck.automation_engine import run_automation
from tp_deck.chrome_launcher import ensure_debug_chromium
from tp_deck.dashboard import Dashboard
from tp_deck.settings_dialog import SettingsDialog
from tp_deck.settings_manager import load_settings

LOG_PATH = Path(__file__).resolve().parent / "tpdeck.log"


def _detach_windows_console() -> None:
    """
    Close the attached console so a double-clicked python.exe launch
    does not leave a shell window open. Set TPDECK_DEBUG=1 to keep it.
    """
    if sys.platform != "win32":
        return
    if os.environ.get("TPDECK_DEBUG", "").strip() in {"1", "true", "True"}:
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def _configure_logging(*, console: bool) -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ]
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


class AutomationController:
    """Owns the cancelable asyncio automation task and UI debounce state."""

    def __init__(self, dashboard: Dashboard) -> None:
        self._dashboard = dashboard
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def start(self, job: str = "orders") -> None:
        if self._task is not None and not self._task.done():
            return

        self._job = job
        self._dashboard.set_processing(True, job=job)
        self._dashboard.set_status("Processing")
        self._task = asyncio.create_task(
            self._run(job),
            name="tpdeck-automation",
        )
        self._task.add_done_callback(self._on_task_done)
        logging.getLogger("tpdeck").info("Automation task started (%s)", job)

    def stop(self) -> None:
        if self._task is None or self._task.done():
            logging.getLogger("tpdeck").info("Emergency stop — no active task")
            return
        logging.getLogger("tpdeck").info("Emergency stop — cancelling task")
        self._task.cancel()

    def stop_from_other_thread(self) -> None:
        """Marshal Pause-key cancels onto the asyncio/Qt loop."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self.stop)

    async def _run(self, job: str = "orders") -> str:
        settings = load_settings()
        return await run_automation(settings, job=job)

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._dashboard.set_processing(False)
        logger = logging.getLogger("tpdeck")

        if task.cancelled():
            self._dashboard.set_status("Stopped")
            logger.info("Automation cancelled")
            return

        exc = task.exception()
        if exc is not None:
            message = str(exc).strip() or exc.__class__.__name__
            self._dashboard.set_status(f"Error — {message}")
            logger.exception("Automation failed: %s", exc)
            return

        result = task.result()
        if result:
            self._dashboard.set_status(str(result))
        else:
            self._dashboard.set_status("Success")
        logger.info("Automation completed: %s", result)


def _register_emergency_hotkey(controller: AutomationController) -> None:
    """Register Pause (or settings override) for emergency abort only."""
    import keyboard

    settings = load_settings()
    hotkey = str(settings.get("emergency_hotkey", "pause")).strip() or "pause"

    keyboard.add_hotkey(
        hotkey,
        controller.stop_from_other_thread,
        suppress=False,
    )
    atexit.register(keyboard.unhook_all)
    logging.getLogger("tpdeck").info(
        "Emergency hotkey registered: %s", hotkey
    )


async def _prepare_chromium(dashboard: Dashboard) -> None:
    """Start minimized Chromium before scrape and pick list can run."""
    logger = logging.getLogger("tpdeck")
    try:
        await ensure_debug_chromium(load_settings(), dashboard.set_status)
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        dashboard.set_status(f"Error — {message}")
        logger.exception("Chromium startup failed")
    else:
        dashboard.set_status("Idle")
        logger.info("Chromium ready")
    finally:
        dashboard.set_actions_enabled(True)


def main() -> int:
    keep_console = os.environ.get("TPDECK_DEBUG", "").strip() in {
        "1",
        "true",
        "True",
    }
    if not keep_console:
        _detach_windows_console()

    _configure_logging(console=keep_console)
    logger = logging.getLogger("tpdeck")
    logger.info("TP DECK starting (v1 — Shell, Kill Switch, CDP, Scrape)")

    app = QApplication(sys.argv)
    app.setApplicationName("TP DECK")
    app.setQuitOnLastWindowClosed(True)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    def _execute() -> None:
        controller.start("orders")

    def _pick_list() -> None:
        controller.start("picklist")

    def _stop() -> None:
        controller.stop()

    def _settings() -> None:
        _open_settings(dashboard)

    dashboard = Dashboard(
        on_execute=_execute,
        on_pick_list=_pick_list,
        on_stop=_stop,
        on_open_settings=_settings,
    )
    controller = AutomationController(dashboard)
    controller.bind_loop(loop)

    try:
        _register_emergency_hotkey(controller)
    except Exception as exc:
        logger.error(
            "Failed to register emergency hotkey (Stop button still works): %s",
            exc,
        )

    dashboard.set_actions_enabled(False)
    dashboard.set_status("Starting Chromium")
    dashboard.show()

    with loop:
        loop.create_task(_prepare_chromium(dashboard), name="tpdeck-chromium")
        loop.run_forever()
    return 0


def _open_settings(dashboard: Dashboard) -> None:
    dialog = SettingsDialog(parent=dashboard)
    dialog.exec()


if __name__ == "__main__":
    raise SystemExit(main())
