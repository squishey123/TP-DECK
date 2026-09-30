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

from tp_deck.automation_engine import (
    copy_to_clipboard,
    lookup_missing_locations,
    run_automation,
    run_batch_serials,
)
from tp_deck.chrome_launcher import ensure_debug_chromium
from tp_deck.dashboard import Dashboard
from tp_deck.job_results import AutomationResult, render_job
from tp_deck.sales_order import close_detail_tab
from tp_deck.serial_dialog import SerialDialog
from tp_deck.settings_dialog import SettingsDialog
from tp_deck.settings_manager import load_settings
from tp_deck.sku_override_dialog import UnknownSkuDialog
from tp_deck.sku_overrides import load_overrides, merge_overrides

LOG_PATH = Path(__file__).resolve().parent / "tpdeck.log"
DETAIL_CLOSE_SECONDS = 30


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


def _autocycle_minutes(settings: dict) -> int:
    try:
        minutes = int(settings.get("ebay_autocycle_minutes", 10))
    except (TypeError, ValueError):
        minutes = 10
    return max(1, min(180, minutes))


class AutomationController:
    """Owns cancelable automation tasks, the cache cycle, and UI debounce state."""

    def __init__(self, dashboard: Dashboard) -> None:
        self._dashboard = dashboard
        self._manual_task: Optional[asyncio.Task] = None
        self._cycle_task: Optional[asyncio.Task] = None
        self._close_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()
        self._autocycle = False
        self._cycle_gen = 0
        self._cycle_busy = False
        self._hold = False
        self._manual_pending = False
        self._wake: Optional[asyncio.Event] = None
        self._job = "orders"

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def hold_autocycle(self, hold: bool) -> None:
        """Keep the cache cycle from starting a scrape while a dialog is open."""
        self._hold = hold

    def start(
        self,
        job: str = "orders",
        serials: Optional[list[str]] = None,
        order_number: str = "",
    ) -> bool:
        if job == "serials":
            self._cancel_detail_close()
        if self._lock.locked() or self._manual_pending:
            return False
        if self._manual_task is not None and not self._manual_task.done():
            return False

        self._manual_pending = True
        self._job = job
        self._dashboard.set_processing(True, job=job)
        self._dashboard.set_status("Processing")
        self._manual_task = asyncio.create_task(
            self._run_manual(job, serials, order_number),
            name="tpdeck-automation",
        )
        self._manual_task.add_done_callback(self._on_manual_done)
        logging.getLogger("tpdeck").info("Automation task started (%s)", job)
        return True

    def start_sister_lookup(
        self,
        result: AutomationResult,
        sisters: list[str],
    ) -> bool:
        """Look up alternatives chosen after Unknown, then rebuild the result."""
        if self._lock.locked() or self._manual_pending:
            return False
        if self._manual_task is not None and not self._manual_task.done():
            return False

        self._manual_pending = True
        self._job = result.job or "orders"
        self._dashboard.set_processing(True, job=self._job)
        self._dashboard.set_status("Processing")
        self._manual_task = asyncio.create_task(
            self._run_sister_lookup(result, sisters),
            name="tpdeck-sister-lookup",
        )
        self._manual_task.add_done_callback(self._on_manual_done)
        logging.getLogger("tpdeck").info(
            "Sister SKU lookup started (%s)",
            ", ".join(sisters),
        )
        return True

    def toggle_autocycle(self) -> None:
        if self._autocycle:
            self._autocycle = False
            self._cycle_gen += 1
            self._dashboard.set_autocycle(False)
            if (
                self._cycle_busy
                and self._cycle_task is not None
                and not self._cycle_task.done()
            ):
                self._cycle_task.cancel()
            else:
                self._wake_wait()
                if not self._cycle_busy:
                    self._dashboard.set_status("Idle")
            return

        self._cycle_gen += 1
        generation = self._cycle_gen
        self._autocycle = True
        self._dashboard.set_autocycle(True)
        self._cycle_task = asyncio.create_task(
            self._autocycle_loop(generation),
            name="tpdeck-autocycle",
        )
        logging.getLogger("tpdeck").info("Auto-cycle enabled")

    def stop(self) -> None:
        logger = logging.getLogger("tpdeck")
        close_running = (
            self._close_task is not None and not self._close_task.done()
        )
        if close_running:
            self._cancel_detail_close()
        manual_running = (
            self._manual_task is not None and not self._manual_task.done()
        )
        cycle_running = self._cycle_task is not None and not self._cycle_task.done()
        if not manual_running and not cycle_running:
            if not close_running:
                logger.info("Emergency stop — no active task")
            return
        logger.info("Emergency stop — cancelling task")
        self._autocycle = False
        self._cycle_gen += 1
        self._dashboard.set_autocycle(False)
        self._wake_wait()
        if manual_running:
            assert self._manual_task is not None
            self._manual_task.cancel()
        if cycle_running:
            assert self._cycle_task is not None
            self._cycle_task.cancel()

    def stop_from_other_thread(self) -> None:
        """Marshal Pause-key cancels onto the asyncio/Qt loop."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self.stop)

    def _cancel_detail_close(self) -> None:
        task = self._close_task
        if task is None or task.done():
            return
        task.cancel()
        logging.getLogger("tpdeck").info("Cancelled pending sales order tab close")

    def _wake_wait(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def _on_serial_progress(self, done: int, total: int) -> None:
        self._dashboard.set_status(f"Serials {done}/{total}")

    async def _run_manual(
        self,
        job: str,
        serials: Optional[list[str]],
        order_number: str = "",
    ):
        try:
            async with self._lock:
                settings = load_settings()
                if job == "serials":
                    return await run_batch_serials(
                        settings,
                        serials or [],
                        on_progress=self._on_serial_progress,
                        order_number=order_number,
                    )
                return await run_automation(
                    settings,
                    job=job,
                    copy_clipboard=True,
                )
        finally:
            self._manual_pending = False

    async def _run_sister_lookup(
        self,
        result: AutomationResult,
        sisters: list[str],
    ) -> AutomationResult:
        try:
            async with self._lock:
                settings = load_settings()
                known = dict(result.queried)
                unique_sisters = list(dict.fromkeys(sisters))
                missing = [sku for sku in unique_sisters if sku not in known]
                if missing:
                    found = await lookup_missing_locations(settings, missing)
                    known.update(found)
                hits = len(unique_sisters) - len(missing)
                rendered = render_job(
                    settings,
                    job=result.job,
                    lines=result.lines,
                    queried=known,
                    overrides=load_overrides(),
                    hits=max(0, hits),
                    misses=len(missing),
                    copy_clipboard=True,
                    allow_prompt=False,
                    cache_enabled=bool(settings.get("cache_enabled", True)),
                )
                if rendered.clipboard_text and rendered.clipboard_text.strip():
                    copy_to_clipboard(rendered.clipboard_text)
                return rendered
        finally:
            self._manual_pending = False

    def _on_manual_done(self, task: asyncio.Task) -> None:
        self._manual_pending = False
        logger = logging.getLogger("tpdeck")

        if task.cancelled():
            self._dashboard.set_processing(False)
            self._dashboard.set_status("Stopped")
            logger.info("Automation cancelled")
            return

        exc = task.exception()
        if exc is not None:
            self._dashboard.set_processing(False)
            message = str(exc).strip() or exc.__class__.__name__
            self._dashboard.set_status(f"Error — {message}")
            logger.exception("Automation failed: %s", exc)
            return

        result = task.result()
        if result is not None and result.pending_unknowns:
            self._offer_overrides(result)
            return

        self._dashboard.set_processing(False)
        self._publish(result)

    def _offer_overrides(self, result: AutomationResult) -> None:
        """Ask once for every unknown SKU, then look up any alternatives."""
        logger = logging.getLogger("tpdeck")
        count = len(result.pending_unknowns)
        noun = "SKU" if count == 1 else "SKUs"
        self._dashboard.set_status(f"{count} unknown {noun} — enter alternatives")
        self.hold_autocycle(True)
        mappings: list[tuple[str, str, int]] = []
        try:
            dialog = UnknownSkuDialog(
                list(result.pending_unknowns),
                parent=self._dashboard,
            )
            if dialog.exec():
                mappings = dialog.mappings()
            if not mappings:
                self._present_deferred(result)
                return
            try:
                merge_overrides(mappings)
            except (ValueError, OSError) as exc:
                message = str(exc).strip() or exc.__class__.__name__
                logger.exception("Could not save SKU overrides")
                self._present_deferred(result)
                self._dashboard.set_status(f"Error — {message}")
                return
            sisters = [sister for _original, sister, _qty in mappings]
            if not self.start_sister_lookup(result, sisters):
                self._present_deferred(result)
                self._dashboard.set_status("Error — busy, try again")
        finally:
            self.hold_autocycle(False)

    def _present_deferred(self, result: AutomationResult) -> None:
        """Copy and show a finished run whose clipboard waited on the prompt."""
        text = result.clipboard_text or ""
        if text.strip():
            copy_to_clipboard(text)
        self._dashboard.set_processing(False)
        self._publish(result)

    def _publish(self, result: Optional[AutomationResult]) -> None:
        logger = logging.getLogger("tpdeck")
        status = result.status if result else "Success"
        self._dashboard.set_status(status)
        logger.info("Automation completed: %s", status)
        if (
            result is not None
            and result.job in {"orders", "picklist"}
            and bool(load_settings().get("show_results_popup", False))
        ):
            self._dashboard.show_results(
                list(result.headers),
                [list(row) for row in result.rows],
                unknown=list(result.unknown_flags),
                note=result.note,
            )
        failures = result.failed_serials if result else ()
        if failures:
            self._dashboard.show_serial_failures(list(failures))
        if result is not None and result.close_detail_url:
            self._close_task = asyncio.create_task(
                self._close_detail_later(result.close_detail_url),
                name="tpdeck-close-order",
            )

    async def _close_detail_later(self, url: str) -> None:
        """Wait, then reconnect only long enough to close that detail tab."""
        logger = logging.getLogger("tpdeck")
        try:
            await asyncio.sleep(DETAIL_CLOSE_SECONDS)
            async with self._lock:
                await close_detail_tab(load_settings(), url)
        except asyncio.CancelledError:
            logger.info("Detail tab close cancelled")
            raise
        except Exception:
            logger.exception("Could not close the sales order tab")

    async def _interruptible_sleep(self, seconds: float) -> None:
        self._wake = asyncio.Event()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake = None

    async def _autocycle_loop(self, generation: int) -> None:
        logger = logging.getLogger("tpdeck")
        logger.info("Auto-cycle started")
        try:
            while self._autocycle and self._cycle_gen == generation:
                if self._hold or self._manual_pending or self._lock.locked():
                    await asyncio.sleep(0.2)
                    continue
                minutes = 10
                status_text = ""
                async with self._lock:
                    if (
                        not self._autocycle
                        or self._cycle_gen != generation
                        or self._hold
                        or self._manual_pending
                    ):
                        continue
                    self._job = "cycle"
                    self._cycle_busy = True
                    self._dashboard.set_processing(True, job="cycle")
                    self._dashboard.set_status("Processing")
                    try:
                        settings = load_settings()
                        minutes = _autocycle_minutes(settings)
                        result = await run_automation(
                            settings,
                            job="orders",
                            copy_clipboard=False,
                        )
                        status_text = result.status
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        message = str(exc).strip() or exc.__class__.__name__
                        logger.exception("Auto-cycle failed: %s", exc)
                        minutes = _autocycle_minutes(load_settings())
                        status_text = f"Error — {message}"
                    finally:
                        self._cycle_busy = False
                        self._dashboard.set_processing(False)
                if not self._autocycle or self._cycle_gen != generation:
                    break
                self._dashboard.set_status(
                    f"{status_text} — next in {minutes} min"
                )
                await self._interruptible_sleep(minutes * 60)
        except asyncio.CancelledError:
            logger.info("Auto-cycle cancelled")
            self._cycle_busy = False
            self._dashboard.set_processing(False)
            if self._cycle_gen == generation:
                self._autocycle = False
                self._dashboard.set_autocycle(False)
            if self._cycle_gen == generation or not self._autocycle:
                self._dashboard.set_status("Stopped")
        finally:
            if self._cycle_gen == generation:
                self._autocycle = False
                self._dashboard.set_autocycle(False)


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

    def _batch_serial() -> None:
        controller.hold_autocycle(True)
        serials: list[str] = []
        try:
            dialog = SerialDialog(parent=dashboard)
            order_number = ""
            if dialog.exec() == SerialDialog.DialogCode.Accepted:
                serials = dialog.serials()
                order_number = dialog.order_number()
            if serials and not controller.start("serials", serials, order_number):
                dashboard.set_status("Error — busy, try again")
        finally:
            controller.hold_autocycle(False)

    def _cycle() -> None:
        controller.toggle_autocycle()

    def _stop() -> None:
        controller.stop()

    def _settings() -> None:
        _open_settings(dashboard)

    dashboard = Dashboard(
        on_execute=_execute,
        on_pick_list=_pick_list,
        on_batch_serial=_batch_serial,
        on_cycle=_cycle,
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
