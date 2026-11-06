"""Tests for the Batch Serial Current Orders queue, the eBay/ShipStation
failure fallback, and the Uline order-table layout (stdlib only, no pytest).

Run from the repo root:  python -m unittest discover -s tests -v
These are the checks for tasks the Uline suite does not cover:
 - an eBay scrape failure does not block the ShipStation store scrape
 - a multi-order queue runs every order, and an error on one does not stop
   the next order
 - Min Packs is not a column on the order table, but its data is stored
 - order-table columns are sized for one screen
 - the alias typed in the key box is recorded on the order line
"""

import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
from PySide6.QtWidgets import QApplication

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tp_deck import uline_cache, uline_order
from tp_deck.job_results import AutomationResult
from tp_deck.uline_cache import UlineItem
from tp_deck.uline_order import OrderLine, sets_for

# PySide6 aborts the process when a widget is built with no application
# instance, and again at shutdown when that application is dropped while
# widgets are still alive. Some of these tests build a real Serial
# Dialog, so one application at module level keeps the whole run in one
# piece. Tests that need it take the same instance.
SESSION_APP = QApplication.instance() or QApplication([])

EBAY_FAIL = "No eBay order rows found on /sh/ord. Open the Seller Hub order list."
STORE = ("INV-1000", "Bob", "S-4307", 2)


class _StubDashboard:
    """The controller calls these dashboard methods; we record what it says."""

    def __init__(self):
        self.statuses: list[str] = []
        self.failures: list[list] = []
        self.queued: list[bool] = []

    def input_blocked(self) -> bool:
        return False

    def set_status(self, text: str) -> None:
        self.statuses.append(text)

    def set_serial_queued(self, value) -> None:
        self.queued.append(bool(value))

    def set_processing(self, active: bool, job: str = "orders") -> None:
        pass

    def show_serial_failures(self, failures: list, order: str = "") -> None:
        self.failures.append(list(failures))


class SerialQueue(unittest.TestCase):
    """A queued run per order runs in order, and an error does not block the rest."""

    def _drain(self, orders, runs):
        from tp_deck import main as m

        dashboard = _StubDashboard()
        controller = m.AutomationController(dashboard)
        seen: list[str] = []

        async def fake_run(settings, serials, on_progress=None, order_number=""):
            seen.append(order_number)
            result = runs.get(order_number)
            if isinstance(result, Exception):
                raise result
            return result

        controller._serial_queue = [(["111", "222"], order) for order in orders]
        with mock.patch.object(m, "run_batch_serials", fake_run), mock.patch.object(
            m, "load_settings", lambda: {"show_results_popup": True}
        ):
            asyncio.run(controller._drain_serial_queue())
        return seen, dashboard

    def test_every_queued_order_runs(self):
        runs = {
            "SO-1": AutomationResult(status="Stopped — 1/2, 2 timed out", job="serials"),
            "SO-2": AutomationResult(status="Success — 2/2 serials", job="serials"),
            "SO-3": AutomationResult(status="Success — 2/2 serials", job="serials"),
        }
        seen, _ = self._drain(["SO-1", "SO-2", "SO-3"], runs)
        self.assertEqual(seen, ["SO-1", "SO-2", "SO-3"])
        self.assertEqual(runs["SO-2"].status, "Success — 2/2 serials")

    def test_error_on_one_order_does_not_stop_the_next(self):
        ok = AutomationResult(status="Success — 2/2 serials", job="serials",
                              failed_serials=(("1", "111"),))
        runs = {"SO-1": RuntimeError("Serial box never cleared"), "SO-2": ok}
        seen, dashboard = self._drain(["SO-1", "SO-2"], runs)
        self.assertEqual(seen, ["SO-1", "SO-2"])  # the second order ran anyway
        self.assertIn("Error — SO-1: Serial box never cleared", dashboard.statuses)
        self.assertEqual(dashboard.failures[0], [(0, "Serial box never cleared")])
        self.assertIn("Success — 2/2 serials", dashboard.statuses)
        # the failure is reported for the order that ran, as usual
        self.assertEqual(dashboard.failures[1], [("1", "111")])

    def test_auto_cycle_scrape_remembers_orders_for_current_orders(self):
        # The cache pass never shows a popup, so the orders it sees have to be
        # remembered for the Current Orders window as well.
        from tp_deck import main as m

        dashboard = _StubDashboard()
        controller = m.AutomationController(dashboard)
        cycle_result = AutomationResult(
            status="Cycling — 2 SKU(s) cached, 0 lookup(s), 2 cache hit(s)",
            job="orders",
            lines=(("SO-1", "Ann", "S-4307", 2), ("SO-1", "Ann", "S-11369", 1),
                   ("SO-2", "Bo", "S-4307", 3)),
            queried=(("S-4307", "A101"),),
        )
        controller._remember_orders(cycle_result)
        self.assertEqual(
            controller._last_orders,
            [("SO-1", "Ann", 2, 3), ("SO-2", "Bo", 1, 3)],
        )

    def test_the_batch_serial_dialog_opens_with_the_orders_seen(self):
        from tp_deck import main as m

        captured: dict = {}

        class _Sig:
            def connect(self, fn):
                pass

        class _FakeDialog:
            accepted = _Sig()
            finished = _Sig()
            order_queued = _Sig()

            def __init__(self, parent, orders):
                captured["orders"] = list(orders)

            def setModal(self, value):
                pass

            def show(self):
                pass

            def raise_(self):
                pass

            def activateWindow(self):
                pass

        original = m.SerialDialog
        m.SerialDialog = _FakeDialog
        try:
            dashboard = _StubDashboard()
            controller = m.AutomationController(dashboard)
            controller._remember_orders(AutomationResult(
                status="Cycling", job="orders",
                lines=(("SO-1", "Ann", "S-4307", 2),),
            ))
            controller.open_batch_serial()
        finally:
            m.SerialDialog = original
        self.assertEqual(captured["orders"], [("SO-1", "Ann", 1, 2)])

    def test_two_timeouts_report_the_skipped_serials(self):
        ok = AutomationResult(status="Incomplete — 1/2 serials, 1 skipped",
                              job="serials", failed_serials=(("2", "222"),))
        seen, dashboard = self._drain(["SO-1"], {"SO-1": ok})
        self.assertIn("Incomplete — 1/2 serials, 1 skipped", dashboard.statuses)
        self.assertEqual(dashboard.failures, [[("2", "222")]])

    def test_the_multi_pick_queues_a_separate_prompt_per_order(self):
        from PySide6.QtWidgets import QDialog

        from tp_deck import main as m
        from tp_deck import serial_dialog as dmod

        orders = [("SO-1", "A", 1, 1), ("SO-2", "B", 1, 1)]
        dashboard = _StubDashboard()
        controller = m.AutomationController(dashboard)
        dialog = dmod.SerialDialog(orders=orders)
        dialog.order_queued.connect(controller._queue_serial_order)
        dialog.accepted.connect(lambda: controller._accept_serial_dialog(dialog))
        original = dmod.CurrentOrdersWindow
        dmod.CurrentOrdersWindow = lambda *_a, **_kw: _Picked(["SO-1", "SO-2"])
        try:
            dialog._open_current_orders()
            dialog._editor.setPlainText("111\n222")
            dialog._submit()
            self.assertEqual(controller._serial_queue, [(["111", "222"], "SO-1")])
            self.assertEqual(dialog.order_number(), "SO-2")
            self.assertEqual(dialog.serials(), [])
            self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
            dialog._editor.setPlainText("333")
            dialog._submit()
        finally:
            dmod.CurrentOrdersWindow = original
        self.assertEqual(
            controller._serial_queue,
            [(["111", "222"], "SO-1"), (["333"], "SO-2")],
        )
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)


class _Picked:
    def __init__(self, picked):
        self._picked = picked

    def exec(self):
        return 1

    def selected_orders(self):
        return list(self._picked)


class ScrapeFallback(unittest.TestCase):
    """An eBay scrape failure must not stop the ShipStation store scrape."""

    def run_automation(self, ebay_side, store_lines, *, scrape=True):
        from tp_deck import automation_engine as ae

        class _Page:
            url = "https://www.ebay.com/sh/ord"

            def set_default_timeout(self, ms):
                pass

        class _Browser:
            async def close(self):
                pass

        class _PW:
            def start(self):
                async def _start():
                    return _Browser()
                return _start()

            async def stop(self):
                return None

        class _Hit:
            location = "A101"
            definitive_unknown = False

        class _StubCache:
            def get(self, sku):
                return None

            def put(self, sku, location, *, allow_unknown=False):
                pass

            def prompt_due(self, sku):
                return True

            def save(self):
                pass

        async def fake_connect(pw, port):
            return _Browser()

        async def fake_find(browser, pattern, label=""):
            return _Page()

        async def fake_refresh(page, timeout_ms):
            return None

        async def fake_sync(browser, settings, selectors, sync=False, progress=None,
                            ebay_span=0.0):
            return ""

        async def ebay_scrape(page, selectors, timeout_ms=0):
            if isinstance(ebay_side, Exception):
                raise ebay_side
            return list(ebay_side)

        async def store_scrape(browser, settings, selectors, need_qty=False,
                               on_status=None):
            return list(store_lines)

        async def fake_erp(**_kw):
            return _Page()

        async def fake_lookup(page, sku, selectors, **_kw):
            return _Hit()

        settings = {
            "mode": "single",
            "ebay_port": 9222,
            "erp_port": 9223,
            "ebay_url_pattern": "ebay.com/sh/ord",
            "erp_url_pattern": "",
            "wait_timeout_ms": 10000,
            "erp_submit_key": "Enter",
            "cache_enabled": False,
        }
        copied: list[str] = []

        replacements = {
            "async_playwright": lambda: _PW(),
            "connect_over_cdp": fake_connect,
            "find_page_by_url": fake_find,
            "_refresh_ebay_order_page": fake_refresh,
            "_skip_ebay_refresh": lambda s: False,
            "_sync_and_maybe_note": fake_sync,
            "scrape_ebay_orders": ebay_scrape,
            "_scrape_shipstation_lines": store_scrape,
            "_resolve_erp_page": fake_erp,
            "lookup_erp_location": fake_lookup,
            "load_locators": lambda: {},
            "erp_wait_pair": lambda s: (100, 200, 50),
            "load_overrides": lambda: {},
            "SkuLocationCache": lambda ttl_seconds=0: _StubCache(),
            "copy_to_clipboard": lambda text: copied.append(text),
        }
        with mock.patch.multiple(ae, **replacements):
            return asyncio.run(
                ae.run_automation(
                    settings,
                    job="orders",
                    copy_clipboard=True,
                    refresh_ebay=False,
                    honor_refresh_hold=False,
                    sync_shipstation=True,
                    scrape_shipstation=scrape,
                )
            )

    def _rows(self, result):
        return [tuple(row) for row in result.rows]

    def test_ebay_failure_keeps_store_orders(self):
        result = self.run_automation(RuntimeError(EBAY_FAIL), [STORE])
        self.assertIn(("INV-1000", "Bob", "S-4307", "A101"), self._rows(result))
        self.assertIn("eBay scrape failed, store orders kept", result.status)

    def test_ebay_failure_with_nothing_else_is_the_error(self):
        with self.assertRaises(RuntimeError) as box:
            self.run_automation(RuntimeError(EBAY_FAIL), [])
        self.assertIn("No eBay order rows", str(box.exception))

    def test_store_scrape_is_skipped_when_the_cycle_is_not_due(self):
        # A cycle pass that is not due for ShipStation leaves the eBay failure
        # as the job error, since there are no store lines to fall back on.
        with self.assertRaises(RuntimeError):
            self.run_automation(RuntimeError(EBAY_FAIL), [], scrape=False)

    def test_ebay_rows_are_kept_when_the_store_is_empty(self):
        result = self.run_automation([("SO-12345", "Ann", "S-4307", 1)], [])
        self.assertNotIn("eBay scrape failed", result.status)
        self.assertIn(("SO-12345", "Ann", "S-4307", "A101"), self._rows(result))


class OrderTableLayout(unittest.TestCase):
    """Min Packs is not a column on the order table; widths fit one screen."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"
        uline_order.DRAFT_PATH = workdir / "uline_draft.json"

    def dialog(self):
        from tp_deck import uline_dialog as dmod

        def stub_runner(coro, on_done):
            coro.close()

        return dmod.UlineDialog(parent=None, runner=stub_runner,
                                settings={"uline_export_dir": "Ex"})

    def test_min_packs_is_not_an_order_column(self):
        from tp_deck.uline_dialog import ORDER_COLUMNS

        self.assertNotIn("Min Packs", ORDER_COLUMNS)
        dialog = self.dialog()
        labels = [
            dialog._order_table.horizontalHeaderItem(i).text()
            for i in range(dialog._order_table.columnCount())
        ]
        self.assertNotIn("Min Packs", labels)
        self.assertEqual(len(labels), len(ORDER_COLUMNS))

    def test_min_packs_data_is_stored_and_used(self):
        dialog = self.dialog()
        line = OrderLine(item="S-4307", name="Foam", unit="Pack", units_per_set=10,
                         min_sets=2, base_unit="Pack", price=12.0, tiers=(),
                         needed_units=10, sets=2)
        dialog._lines.append(line)
        dialog._save_draft()
        dialog._refresh_order_table()
        # the columns shown are the ones that remain
        self.assertEqual(dialog._order_table.item(0, 3).text(), "Pack")
        self.assertEqual(dialog._order_table.item(0, 5).text(), "10")  # needed units
        self.assertEqual(dialog._order_table.item(0, 6).text(), "2")  # sets to buy
        self.assertEqual(sets_for(5, 10, 2), 2)  # the minimum is honoured
        payload = json.loads(uline_order.DRAFT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["min_sets"], 2)
        self.assertEqual(uline_order.load_draft()[0].min_sets, 2)

    def test_order_table_columns_are_sized_for_one_screen(self):
        from PySide6.QtWidgets import QHeaderView

        from tp_deck.uline_dialog import (
            ALIAS_FLOOR,
            CACHE_COLUMNS,
            NAME_FLOOR,
            ORDER_COLUMNS,
            _header_widths,
        )

        dialog = self.dialog()
        header = dialog._order_table.horizontalHeader()
        widths = _header_widths(ORDER_COLUMNS, {0: 7, 3: 6})
        self.assertEqual(header.sectionSize(0), widths[0])  # Item: 7 chars
        self.assertGreaterEqual(header.sectionSize(1), ALIAS_FLOOR)
        self.assertGreaterEqual(header.sectionSize(2), NAME_FLOOR)
        self.assertEqual(header.sectionSize(3), widths[3])  # Unit: 6 chars
        self.assertEqual(header.sectionSize(8), widths[8])  # header text + buffer
        self.assertEqual(header.sectionResizeMode(1), QHeaderView.ResizeMode.Interactive)
        self.assertEqual(header.sectionResizeMode(2), QHeaderView.ResizeMode.Stretch)
        self.assertEqual(header.sectionResizeMode(8), QHeaderView.ResizeMode.Interactive)
        self.assertGreaterEqual(dialog.minimumWidth(), 1100)

        cache = dialog._cache_table.horizontalHeader()
        name_column = CACHE_COLUMNS.index("Name")
        alias_column = CACHE_COLUMNS.index("Aliases")
        self.assertGreaterEqual(cache.sectionSize(name_column), NAME_FLOOR)
        self.assertGreaterEqual(cache.sectionSize(alias_column), ALIAS_FLOOR)
        self.assertEqual(
            cache.sectionResizeMode(alias_column),
            QHeaderView.ResizeMode.Interactive,
        )

    def test_the_columns_that_follow_the_dropped_one_are_in_order(self):
        from tp_deck.uline_dialog import COL_NEEDED, COL_SETS, COL_PRICE, ORDER_COLUMNS

        self.assertEqual(ORDER_COLUMNS[COL_NEEDED], "Needed Units")
        self.assertEqual(ORDER_COLUMNS[COL_SETS], "Sets To Buy")
        self.assertEqual(ORDER_COLUMNS[COL_PRICE], "Price / Unit")


class AliasOnLine(unittest.TestCase):
    """The alias typed in the key box is recorded on the order line."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"
        uline_order.DRAFT_PATH = workdir / "uline_draft.json"
        self.calls: list = []

    def dialog(self):
        from tp_deck import uline_dialog as dmod

        def stub_runner(coro, on_done):
            coro.close()
            self.calls.append(on_done)

        return dmod.UlineDialog(parent=None, runner=stub_runner,
                                settings={"uline_export_dir": "Ex"})

    def test_alias_typed_in_the_key_box_is_recorded_on_the_line(self):
        dialog = self.dialog()
        found = UlineItem(item="S-4307", name="Conductive Foam", price=12.0,
                          units_per_set=10, unit="Pack", min_sets=2,
                          base_unit="Pack", tiers=((1, 12.0),), aliases=(),
                          url="", in_stock=True, note="", added="", checked="")
        dialog._key_edit.setText("conductive foam")
        dialog._add()
        self.calls[0](True, found, "")
        line = dialog._lines[0]
        self.assertEqual(line.item, "S-4307")
        self.assertEqual(line.alias, "conductive foam")
        self.assertIn("conductive foam", uline_cache.load_cache()["S-4307"].aliases)
        self.assertEqual(uline_order.load_draft()[0].alias, "conductive foam")
        # the table shows the alias on the line
        self.assertEqual(dialog._order_table.item(0, 1).text(), "conductive foam")


class UrgentLamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _dashboard(self):
        from tp_deck.dashboard import Dashboard

        return Dashboard()

    def test_click_is_ignored_while_busy_or_updating(self):
        dashboard = self._dashboard()
        called: list[int] = []
        dashboard._on_urgent_sync = lambda: called.append(1)
        dashboard.set_processing(True)
        dashboard._handle_urgent()
        dashboard.set_processing(False)
        dashboard.begin_update_warning()
        dashboard._handle_urgent()
        dashboard.end_update_warning()
        self.assertEqual(called, [])
        dashboard._light._on_click()
        self.assertEqual(called, [1])

    def test_purple_lamp_and_rainbow_leave_the_other_buttons(self):
        dashboard = self._dashboard()
        dashboard.start_cycle_countdown(30)
        dashboard.begin_urgent_sync()
        self.assertEqual(dashboard._light._state, "urgent")
        self.assertIn("background-color", dashboard.execute_btn.styleSheet())
        self.assertIn("background-color", dashboard.pick_btn.styleSheet())
        self.assertIn("background-color", dashboard.batch_btn.styleSheet())
        self.assertEqual(dashboard.cycle_btn.styleSheet(), "")
        self.assertEqual(dashboard.stop_btn.styleSheet(), "")
        self.assertEqual(dashboard.settings_btn.styleSheet(), "")
        self.assertEqual(dashboard.tools_btn.styleSheet(), "")
        dashboard._update_warning = True
        dashboard._update_started = __import__("time").monotonic()
        dashboard.end_urgent_sync()
        self.assertFalse(dashboard._urgent)
        self.assertIn("background-color", dashboard.execute_btn.styleSheet())
        self.assertEqual(dashboard._light._state, "waiting")
        dashboard._update_warning = False
        dashboard.end_urgent_sync()
        self.assertEqual(dashboard.execute_btn.styleSheet(), "")
        self.assertEqual(dashboard._light._state, "waiting")

    def test_urgent_sync_refreshes_ebay_and_syncs_shipstation(self):
        from tp_deck.job_results import AutomationResult
        from tp_deck.main import AutomationController

        captured: dict = {}

        async def fake_run(*_args, **kwargs):
            captured["kwargs"] = kwargs
            return AutomationResult(status="Urgent", job="orders")

        async def body():
            dashboard = self._dashboard()
            controller = AutomationController(dashboard)
            with mock.patch("tp_deck.main.run_automation", fake_run):
                self.assertTrue(controller.start_urgent())
                await controller._manual_task
            self.assertFalse(captured["kwargs"]["honor_refresh_hold"])
            self.assertTrue(captured["kwargs"]["sync_shipstation"])
            self.assertTrue(captured["kwargs"]["refresh_ebay"])
            self.assertFalse(dashboard._urgent)
            dashboard.begin_update_warning()
            self.assertFalse(controller.start_urgent())
            dashboard.end_update_warning()

        asyncio.run(body())


class ThemePalette(unittest.TestCase):
    def test_cycle_off_and_warning_follow_the_accent(self):
        from tp_deck.theme import FAMILIES, NEUTRAL

        green = FAMILIES["green"]
        blue = FAMILIES["blue"]
        cyan = FAMILIES["cyan"]
        red = FAMILIES["red"]
        self.assertNotEqual(green["cycle_off_bg"].upper(), "#7C4620")
        self.assertNotEqual(green["warning"].upper(), "#C27A3F")
        self.assertNotEqual(blue["cycle_off_bg"].upper(), "#8A4630")
        self.assertNotEqual(blue["cycle_off_border"].upper(), "#D07C5C")
        self.assertNotEqual(blue["warning"].upper(), "#C2873F")
        self.assertEqual(cyan["cycle_off_bg"].upper(), "#B45309")
        self.assertEqual(cyan["warning"].upper(), "#FBBF24")
        self.assertEqual(NEUTRAL["warning"].upper(), "#FBBF24")
        self.assertNotEqual(red["danger_bg"].upper(), red["accent"].upper())
        self.assertNotEqual(red["danger_bg"].upper(), "#7F1D1D")
        self.assertEqual(cyan["danger_bg"].upper(), "#7F1D1D")


class OrderColumnTargets(unittest.TestCase):
    """eBay and store numbers search the PO header. Service numbers search SO #."""

    def _columns(self):
        return [
            {"name": "SO #", "colId": "SoNo", "hasInput": True},
            {"name": "Source Type", "colId": "SourceType", "hasInput": True},
            {"name": "Direction", "colId": "Direction", "hasInput": True},
            {"name": "PO", "colId": "PoNumber", "hasInput": True},
            {"name": "Ref", "colId": "Ref", "hasInput": True},
        ]

    def test_classification_and_column_choice(self):
        from tp_deck.locators import DEFAULTS
        from tp_deck.sales_order import (
            _configured_targets,
            _search_targets,
            classify_order,
        )

        columns = self._columns()
        self.assertEqual(classify_order("SO-99858"), "service")
        self.assertEqual(classify_order("SO99858"), "service")
        self.assertEqual(classify_order("18-15368-46357"), "ebay")
        self.assertEqual(classify_order("48559"), "store")
        self.assertEqual(classify_order("#48559"), "store")

        ebay = _search_targets("ebay", columns)
        store = _search_targets("store", columns)
        service = _search_targets("service", columns)
        self.assertEqual([col["name"] for _role, col in ebay], ["PO"])
        self.assertEqual([col["name"] for _role, col in store], ["PO"])
        self.assertEqual([role for role, _col in store], ["marketplace"])
        self.assertEqual([col["colId"] for _role, col in service], ["SoNo"])
        self.assertNotIn("service", [role for role, _col in store])
        self.assertEqual(
            _configured_targets("store", {"sales_order_filter": "#so"}),
            [],
        )
        self.assertEqual(
            _configured_targets("ebay", {"sales_order_filter": "#so"}),
            [],
        )
        self.assertNotIn("sales_order_external_filter", DEFAULTS)


if __name__ == "__main__":
    unittest.main()
