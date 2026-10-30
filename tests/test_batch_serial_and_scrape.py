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

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tp_deck import uline_cache, uline_order
from tp_deck.job_results import AutomationResult
from tp_deck.uline_cache import UlineItem
from tp_deck.uline_order import OrderLine, sets_for

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

    def test_the_multi_pick_queues_the_same_serials_per_order(self):
        from tp_deck import main as m
        from tp_deck import serial_dialog as dmod

        orders = [("SO-1", "A", 1, 1), ("SO-2", "B", 1, 1)]
        dashboard = _StubDashboard()
        controller = m.AutomationController(dashboard)
        dialog = dmod.SerialDialog(orders=orders)
        dialog.accepted.connect(lambda: controller._accept_serial_dialog(dialog))
        original = dmod.CurrentOrdersWindow
        dmod.CurrentOrdersWindow = lambda *_a, **_kw: _Picked(["SO-1", "SO-2"])
        try:
            dialog._open_current_orders()
            dialog._editor.setPlainText("111\n222")
            dialog._submit()
        finally:
            dmod.CurrentOrdersWindow = original
        self.assertEqual(
            controller._serial_queue,
            [(["111", "222"], "SO-1"), (["111", "222"], "SO-2")],
        )


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
        from PySide6.QtGui import QFont, QFontMetrics
        from PySide6.QtWidgets import QHeaderView

        from tp_deck.uline_dialog import ORDER_COLUMNS, _header_widths

        dialog = self.dialog()
        header = dialog._order_table.horizontalHeader()
        metrics = QFontMetrics(QFont("Consolas", 12))
        char = metrics.horizontalAdvance("W")
        widths = _header_widths(ORDER_COLUMNS, {0: 7, 3: 6})
        self.assertEqual(header.sectionSize(0), widths[0])  # Item: 7 chars
        self.assertEqual(header.sectionSize(3), widths[3])  # Unit: 6 chars
        self.assertEqual(header.sectionSize(8), widths[8])  # header text + buffer
        self.assertEqual(header.sectionResizeMode(2), QHeaderView.ResizeMode.Stretch)
        self.assertEqual(header.sectionResizeMode(8), QHeaderView.ResizeMode.Interactive)

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


if __name__ == "__main__":
    unittest.main()
