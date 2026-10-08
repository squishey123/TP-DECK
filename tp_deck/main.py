"""TP DECK entry point — Qt + asyncio bridge, hotkey kill-switch, dashboard."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sys
import time
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from tp_deck import __version__
from tp_deck.automation_engine import (
    copy_to_clipboard,
    lookup_missing_locations,
    run_automation,
    run_batch_serials,
)
from tp_deck.chrome_launcher import app_root, ensure_debug_chromium
from tp_deck.dashboard import Dashboard
from tp_deck.job_results import AutomationResult, render_job
from tp_deck.sales_order import close_detail_tab, shutdown_workday
from tp_deck.serial_dialog import SerialDialog
from tp_deck.settings_dialog import SettingsDialog
from tp_deck.settings_manager import load_settings
from tp_deck.updater import (
    download_and_spawn,
    fetch_latest,
    find_windows_asset,
    is_newer,
    is_release_layout,
)
from tp_deck.duration import cache_ttl_seconds
from tp_deck.sku_cache import SkuLocationCache
from tp_deck.sku_override_dialog import UnknownSkuDialog
from tp_deck.sku_overrides import load_overrides, mark_no_sisters, merge_overrides

LOG_PATH = Path(__file__).resolve().parent / "tpdeck.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 5
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
    """Keep tpdeck.log from growing without bound.

    The active file rolls at 1 MB. Five older files are kept
    (tpdeck.log.1 through tpdeck.log.5) and then deleted.
    """
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            LOG_PATH,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        ),
    ]
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _shipstation_every(settings: dict) -> int:
    try:
        every = int(settings.get("shipstation_sync_every_cycles", 3))
    except (TypeError, ValueError):
        every = 3
    return max(1, min(99, every))


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
        self._cycle_passes = 0
        self._serial_queue: list[tuple[list[str], str]] = []
        self._serial_dialog: Optional[SerialDialog] = None
        self._drain_task: Optional[asyncio.Task] = None
        self._draining = False
        self._shutdown_task: Optional[asyncio.Task] = None
        self._shutdown_abort = False

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
        if self._dashboard.input_blocked():
            return False
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
            self._cycle_passes = 0
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
        self._cycle_passes = 0
        self._autocycle = True
        self._dashboard.set_autocycle(True)
        self._cycle_task = asyncio.create_task(
            self._autocycle_loop(generation),
            name="tpdeck-autocycle",
        )
        logging.getLogger("tpdeck").info("Auto-cycle enabled")

    def stop(self) -> None:
        logger = logging.getLogger("tpdeck")
        self.hold_autocycle(False)
        self._dashboard.cancel_shutdown_arm()
        self._serial_queue.clear()
        self._dashboard.set_serial_queued(False)
        shutdown_running = (
            self._shutdown_task is not None and not self._shutdown_task.done()
        )
        if shutdown_running:
            self._shutdown_abort = True
            assert self._shutdown_task is not None
            self._shutdown_task.cancel()
        close_running = (
            self._close_task is not None and not self._close_task.done()
        )
        if close_running:
            self._cancel_detail_close()
        manual_running = (
            self._manual_task is not None and not self._manual_task.done()
        )
        cycle_running = self._cycle_task is not None and not self._cycle_task.done()
        drain_running = (
            self._drain_task is not None and not self._drain_task.done()
        )
        if not manual_running and not cycle_running and not drain_running:
            if shutdown_running:
                logger.info("Emergency stop — cancelling shutdown")
            elif not close_running:
                logger.info("Emergency stop — no active task")
            return
        logger.info("Emergency stop — cancelling task")
        self._autocycle = False
        self._cycle_passes = 0
        self._cycle_gen += 1
        self._dashboard.set_autocycle(False)
        self._wake_wait()
        if manual_running:
            assert self._manual_task is not None
            self._manual_task.cancel()
        if cycle_running:
            assert self._cycle_task is not None
            self._cycle_task.cancel()
        if drain_running:
            assert self._drain_task is not None
            self._drain_task.cancel()

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

    def open_batch_serial(self) -> None:
        """Show the serial dialog without freezing the auto-cycle pass."""
        if self._dashboard.input_blocked():
            return
        if self._serial_dialog is not None:
            self._serial_dialog.raise_()
            self._serial_dialog.activateWindow()
            return
        dialog = SerialDialog(self._dashboard)
        dialog.setModal(False)
        dialog.accepted.connect(lambda: self._accept_serial_dialog(dialog))
        dialog.finished.connect(self._finish_serial_dialog)
        self._serial_dialog = dialog
        self.hold_autocycle(True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _accept_serial_dialog(self, dialog: SerialDialog) -> None:
        serials = dialog.serials()
        if not serials:
            return
        self._serial_queue.append((serials, dialog.order_number()))
        self._dashboard.set_serial_queued(True)
        logging.getLogger("tpdeck").info("Batch serial queued (%s)", len(serials))

    def _finish_serial_dialog(self, _code: int) -> None:
        self._serial_dialog = None
        self.hold_autocycle(False)
        self._wake_wait()
        if not self._autocycle:
            self._kick_serial_drain()

    def _dismiss_serial_dialog(self) -> None:
        dialog = self._serial_dialog
        if dialog is None:
            return
        dialog.reject()

    def _kick_serial_drain(self) -> None:
        if self._autocycle or self._dashboard.input_blocked():
            return
        if self._draining:
            return
        if self._drain_task is not None and not self._drain_task.done():
            return
        if not self._serial_queue:
            return
        self._drain_task = asyncio.create_task(
            self._drain_serial_queue(),
            name="tpdeck-serial-drain",
        )

    def arm_shutdown(self) -> None:
        if self._dashboard.input_blocked():
            return
        self._dismiss_serial_dialog()
        self.hold_autocycle(True)
        self._shutdown_abort = False
        self._dashboard.begin_shutdown_arm()

    def begin_shutdown(self) -> None:
        if self._shutdown_abort:
            self._dashboard.cancel_shutdown_arm()
            return
        if self._shutdown_task is not None and not self._shutdown_task.done():
            return
        self._dashboard.mark_shutting_down()
        self._shutdown_task = asyncio.create_task(
            self._shutdown(),
            name="tpdeck-shutdown",
        )

    def _on_serial_progress(self, done: int, total: int) -> None:
        self._dashboard.set_status(f"Serials {done}/{total}")
        self._dashboard.set_serial_progress(done, total)

    def _on_run_progress(
        self,
        locked: float,
        sheen: Optional[tuple[float, float]],
        label: str,
    ) -> None:
        if label:
            self._dashboard.set_status(label)
        self._dashboard.set_run_progress(locked, sheen)

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
                cycling = self._autocycle
                return await run_automation(
                    settings,
                    job=job,
                    copy_clipboard=True,
                    refresh_ebay=not cycling,
                    honor_refresh_hold=True,
                    sync_shipstation=not cycling,
                    scrape_shipstation=True,
                    on_progress=self._on_run_progress,
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
                    rendered = replace(
                        rendered,
                        status=f"{rendered.status} — clipboard updated",
                    )
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
        self._dashboard.set_processing(False)
        if result is not None and result.pending_unknowns:
            if result.clipboard_text and result.clipboard_text.strip():
                copy_to_clipboard(result.clipboard_text)
            self._publish(result)
            self._offer_overrides(result)
            return

        self._publish(result)
        if self._serial_queue and not self._autocycle:
            self._kick_serial_drain()

    def _offer_overrides(self, result: AutomationResult) -> None:
        """Ask once for every unknown SKU, then look up any alternatives."""
        logger = logging.getLogger("tpdeck")
        count = len(result.pending_unknowns)
        noun = "SKU" if count == 1 else "SKUs"
        self._dashboard.set_status(f"{count} unknown {noun} — enter alternatives")
        self.hold_autocycle(True)
        try:
            dialog = UnknownSkuDialog(
                list(result.pending_unknowns),
                parent=self._dashboard,
            )
            accepted = bool(dialog.exec())
            self._mark_unknown_prompts(result.pending_unknowns)
            if not accepted:
                self._dashboard.set_status(result.status)
                return
            try:
                if dialog.no_sisters():
                    mark_no_sisters(dialog.no_sisters())
                mappings = dialog.mappings()
                if mappings:
                    merge_overrides(mappings)
            except (ValueError, OSError) as exc:
                message = str(exc).strip() or exc.__class__.__name__
                logger.exception("Could not save SKU overrides")
                self._dashboard.set_status(f"Error — {message}")
                return
            if not mappings:
                self._dashboard.set_status(result.status)
                return
            sisters = [sister for _original, sister, _qty in mappings]
            if not self.start_sister_lookup(result, sisters):
                self._dashboard.set_status("Error — busy, try again")
        finally:
            self.hold_autocycle(False)

    def _mark_unknown_prompts(self, skus: tuple[str, ...] | list[str]) -> None:
        cache = SkuLocationCache(ttl_seconds=cache_ttl_seconds(load_settings()))
        for sku in skus:
            cache.mark_prompted(str(sku))
        cache.save()

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

    async def _execute_serial_batch(
        self,
        serials: list[str],
        order_number: str,
    ) -> None:
        """Run one queued batch. Caller holds the decision to take the lock."""
        logger = logging.getLogger("tpdeck")
        self._cancel_detail_close()
        self._manual_pending = True
        self._dashboard.set_processing(True, job="serials")
        try:
            result = await run_batch_serials(
                load_settings(),
                serials,
                on_progress=self._on_serial_progress,
                order_number=order_number,
            )
        except asyncio.CancelledError:
            self._dashboard.set_processing(False)
            self._dashboard.set_status("Stopped")
            raise
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            logger.exception("Batch serial failed: %s", exc)
            self._dashboard.set_processing(False)
            self._dashboard.set_status(f"Error — {message}")
        else:
            self._dashboard.set_serial_queued(bool(self._serial_queue))
            self._dashboard.set_processing(False)
            self._publish(result)
        finally:
            self._manual_pending = False

    async def _drain_serial_queue_locked(self) -> None:
        """Run queued batches. Caller already holds self._lock."""
        if self._draining:
            return
        self._draining = True
        try:
            while self._serial_queue and not self._shutdown_abort:
                serials, order_number = self._serial_queue.pop(0)
                self._dashboard.set_serial_queued(bool(self._serial_queue))
                await self._execute_serial_batch(serials, order_number)
        finally:
            self._draining = False
            self._dashboard.set_serial_queued(bool(self._serial_queue))

    async def _drain_serial_queue(self) -> None:
        """Run queued batches, taking the automation lock for each one."""
        if self._draining:
            return
        self._draining = True
        try:
            while self._serial_queue and not self._shutdown_abort:
                serials, order_number = self._serial_queue.pop(0)
                self._dashboard.set_serial_queued(bool(self._serial_queue))
                async with self._lock:
                    await self._execute_serial_batch(serials, order_number)
        finally:
            self._draining = False
            self._dashboard.set_serial_queued(bool(self._serial_queue))

    async def _interruptible_sleep(self, seconds: float, generation: int) -> None:
        """Wait out the auto-cycle interval, running a queued batch without losing the remainder."""
        deadline = time.monotonic() + max(0.0, seconds)
        while self._autocycle and self._cycle_gen == generation:
            if self._serial_queue and not self._hold and not self._draining:
                await self._drain_serial_queue()
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._dashboard.start_cycle_countdown(remaining)
            self._wake = asyncio.Event()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            finally:
                self._wake = None
                self._dashboard.clear_cycle_countdown()
            if not self._autocycle or self._cycle_gen != generation:
                return
            if self._serial_queue:
                continue
            if self._hold:
                await asyncio.sleep(0.2)
                continue
            return

    async def _shutdown(self) -> None:
        """Finish the current task, close leftover order tabs, then exit."""
        logger = logging.getLogger("tpdeck")
        try:
            if self._shutdown_abort:
                self._dashboard.cancel_shutdown_arm()
                return
            logger.info("Safe shutdown started")
            self._autocycle = False
            self._cycle_gen += 1
            self._dashboard.set_autocycle(False)
            self._wake_wait()
            self._dashboard.set_active(True)
            self._dashboard.set_status("Finishing current task")
            pending = [
                task
                for task in (self._cycle_task, self._manual_task, self._drain_task)
                if task is not None and not task.done()
            ]
            if pending:
                await asyncio.wait(pending)
            if self._shutdown_abort:
                return
            await self._drain_serial_queue()
            if self._shutdown_abort:
                return
            self._cancel_detail_close()
            self._dashboard.set_active(True)
            self._dashboard.set_status("Closing order tabs")
            async with self._lock:
                await shutdown_workday(load_settings())
            if self._shutdown_abort:
                return
            logger.info("Safe shutdown complete")
            self._dashboard.close()
        except asyncio.CancelledError:
            logger.info("Safe shutdown cancelled")
            self.hold_autocycle(False)
            self._dashboard.cancel_shutdown_arm()
            raise
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            logger.exception("Safe shutdown failed: %s", exc)
            self.hold_autocycle(False)
            self._dashboard.cancel_shutdown_arm()
            self._dashboard.set_status(f"Error — {message}")
        finally:
            self._shutdown_abort = False

    async def _autocycle_loop(self, generation: int) -> None:
        logger = logging.getLogger("tpdeck")
        logger.info("Auto-cycle started")
        try:
            while self._autocycle and self._cycle_gen == generation:
                if (
                    self._hold
                    or self._manual_pending
                    or self._lock.locked()
                    or self._draining
                ):
                    await asyncio.sleep(0.2)
                    continue
                if self._serial_queue:
                    await self._drain_serial_queue()
                    continue
                minutes = 10
                status_text = ""
                async with self._lock:
                    if (
                        not self._autocycle
                        or self._cycle_gen != generation
                        or self._hold
                        or self._manual_pending
                        or self._serial_queue
                    ):
                        continue
                    self._job = "cycle"
                    self._cycle_busy = True
                    self._dashboard.set_processing(True, job="cycle")
                    self._dashboard.set_status("Processing")
                    try:
                        settings = load_settings()
                        minutes = _autocycle_minutes(settings)
                        self._cycle_passes += 1
                        every = _shipstation_every(settings)
                        sync_shipstation = (self._cycle_passes - 1) % every == 0
                        logger.info(
                            "ShipStation sync %s on auto-cycle pass %s (every %s)",
                            "due" if sync_shipstation else "skipped",
                            self._cycle_passes,
                            every,
                        )
                        result = await run_automation(
                            settings,
                            job="orders",
                            copy_clipboard=False,
                            refresh_ebay=True,
                            honor_refresh_hold=False,
                            sync_shipstation=sync_shipstation,
                            scrape_shipstation=sync_shipstation,
                            on_progress=self._on_run_progress,
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
                    if (
                        self._serial_queue
                        and self._autocycle
                        and self._cycle_gen == generation
                        and not self._shutdown_abort
                    ):
                        await self._drain_serial_queue_locked()
                    self._dashboard.set_processing(False)
                if not self._autocycle or self._cycle_gen != generation:
                    break
                if self._hold or self._serial_queue or not status_text:
                    continue
                self._dashboard.set_status(
                    f"{status_text} — next in {minutes} min"
                )
                await self._interruptible_sleep(minutes * 60, generation)
        except asyncio.CancelledError:
            logger.info("Auto-cycle cancelled")
            self._cycle_busy = False
            self._dashboard.set_processing(False)
            if self._cycle_gen == generation:
                self._autocycle = False
                self._dashboard.set_autocycle(False)
            if self._cycle_gen == generation or not self._autocycle:
                self._dashboard.set_status("Stopped")
        except Exception:
            logger.exception("Auto-cycle stopped unexpectedly")
            self._cycle_busy = False
            self._dashboard.set_processing(False)
            if self._cycle_gen == generation:
                self._dashboard.set_status("Error — auto-cycle stopped")
        finally:
            if self._cycle_gen == generation:
                self._autocycle = False
                self._dashboard.set_autocycle(False)
            if self._serial_queue and not self._autocycle and not self._dashboard.input_blocked():
                self._kick_serial_drain()


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
    dashboard.set_active(True)
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
        dashboard.set_active(False)
        dashboard.set_actions_enabled(True)


async def _startup(dashboard: Dashboard) -> None:
    """Install a newer public release when one exists, then start Chromium."""
    logger = logging.getLogger("tpdeck")
    updated = False
    try:
        updated = await _apply_update(dashboard)
    except Exception as exc:
        message = str(exc).strip() or exc.__class__.__name__
        logger.exception("Update failed: %s", exc)
        dashboard.set_status(f"Error — {message}")
    if updated:
        logger.info("Update helper started; closing")
        dashboard.close()
        return
    await _prepare_chromium(dashboard)


async def _apply_update(dashboard: Dashboard) -> bool:
    logger = logging.getLogger("tpdeck")
    if not is_release_layout():
        logger.info("Update check skipped; this is not a release install")
        return False
    settings = load_settings()
    repo = str(settings.get("update_repo") or "").strip()
    if "/" not in repo:
        logger.info("Update check skipped; update_repo is empty")
        return False
    dashboard.set_active(True)
    dashboard.set_status("Checking for updates")
    release = await asyncio.to_thread(fetch_latest, repo)
    if not release:
        return False
    tag = str(release.get("tag_name") or "")
    if not is_newer(tag, __version__):
        logger.info("No update; installed %s, latest %s", __version__, tag or "unknown")
        return False
    asset = find_windows_asset(release)
    if asset is None:
        logger.info("Release %s has no Windows zip", tag or "unknown")
        return False
    name, url = asset
    dashboard.set_status("Downloading update")
    logger.info("Downloading update %s", name)
    await asyncio.to_thread(download_and_spawn, name, url, app_root())
    return True


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
    logger.info(
        "TP DECK starting (%s). Log rolls at %s MB, keeping %s archives.",
        __version__,
        LOG_MAX_BYTES // 1_000_000,
        LOG_BACKUP_COUNT,
    )

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
        controller.open_batch_serial()

    def _cycle() -> None:
        controller.toggle_autocycle()

    def _stop() -> None:
        controller.stop()

    def _settings() -> None:
        _open_settings(dashboard)

    def _arm_shutdown() -> None:
        controller.arm_shutdown()

    def _shutdown() -> None:
        controller.begin_shutdown()

    dashboard = Dashboard(
        on_execute=_execute,
        on_pick_list=_pick_list,
        on_batch_serial=_batch_serial,
        on_cycle=_cycle,
        on_stop=_stop,
        on_open_settings=_settings,
        on_shutdown=_shutdown,
        on_arm_shutdown=_arm_shutdown,
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
    dashboard.set_active(True)
    dashboard.set_status("Starting Chromium")
    dashboard.show()

    with loop:
        loop.create_task(_startup(dashboard), name="tpdeck-startup")
        loop.run_forever()
    return 0


def _open_settings(dashboard: Dashboard) -> None:
    dialog = SettingsDialog(parent=dashboard)
    dialog.exec()


if __name__ == "__main__":
    raise SystemExit(main())
