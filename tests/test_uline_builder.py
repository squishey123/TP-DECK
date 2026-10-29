"""Tests for the Uline Order Builder (stdlib only, no pytest).

Run from the repo root:  python -m unittest discover -s tests -v
The committed page samples in uline_samples/ are the parser fixtures.
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
from tp_deck.uline_cache import (
    UlineItem,
    add_alias,
    edit_item,
    is_item_shape,
    normalize_item,
    parse_price,
    put_item,
    remove_alias,
    resolve_key,
    tier_price,
    today,
)
from tp_deck.uline_order import (
    OrderLine,
    csv_text,
    order_html,
    order_sets,
    order_total,
    sets_for,
)
from tp_deck.uline_scraper import _blocked_message, _first_detail_link, looks_like_item, parse_product

SAMPLES = REPO / "uline_samples"


def sample(name: str) -> str:
    return SAMPLES.joinpath(name).read_text(encoding="utf-8", errors="replace")


def item_of(html: str, typed: str, url: str = "https://www.uline.com/Product/Detail/S-11633"):
    """Parse a page sample. typed stays in the call so the fixture name says what was asked."""
    return parse_product(html, url)


class NormalizeItem(unittest.TestCase):
    def test_comma_thousands_is_not_truncated(self):
        # Uline prints 11,633 in its tables and labels.
        self.assertEqual(normalize_item("s-11,633"), "S-11633")
        self.assertEqual(normalize_item("S-11,633"), "S-11633")
        self.assertTrue(looks_like_item("s-11,633"))

    def test_canonical_form(self):
        self.assertEqual(normalize_item(" s 11633 "), "S-11633")
        self.assertEqual(normalize_item("H-7945"), "H-7945")
        self.assertEqual(normalize_item("H-992-ROLLER"), "H-992-ROLLER")
        self.assertEqual(normalize_item("11,633"), "11633")
        self.assertFalse(looks_like_item("11633"))
        self.assertFalse(looks_like_item("conductive foam"))
        self.assertTrue(is_item_shape("S-11633"))
        self.assertFalse(is_item_shape("CONDUCTIVEFOAM"))

    def test_price_text(self):
        self.assertEqual(parse_price("$37"), 37.0)
        self.assertEqual(parse_price("$37.00"), 37.0)
        self.assertEqual(parse_price("$0.80"), 0.8)
        self.assertIsNone(parse_price("ADD"))


class TierRule(unittest.TestCase):
    def test_highest_qualifying_tier_wins(self):
        tiers = ((1, 20.0), (4, 18.25), (10, 17.0))
        self.assertEqual(tier_price(tiers, 25.0, 1), 20.0)
        self.assertEqual(tier_price(tiers, 25.0, 4), 18.25)
        self.assertEqual(tier_price(tiers, 25.0, 10), 17.0)
        self.assertEqual(tier_price(tiers, 25.0, 11), 17.0)
        self.assertEqual(tier_price((), 25.0, 5), 25.0)

    def test_tie_is_broken_by_the_cheaper_price_not_page_order(self):
        # 1/2 CASE and 1 CASE both round up to one pack. The 1-case price wins.
        tiers = ((1, 171.0), (1, 162.0), (2, 153.0))
        self.assertEqual(tier_price(tiers, 171.0, 1), 162.0)
        self.assertEqual(tier_price(tiers, 171.0, 2), 153.0)
        # reversed page order picks the same price
        self.assertEqual(tier_price(tuple(reversed(tiers)), 171.0, 1), 162.0)

    def test_pack_count_rounds_up_never_below_minimum(self):
        self.assertEqual(sets_for(1, 25, 1), 1)
        self.assertEqual(sets_for(100, 25, 1), 4)
        self.assertEqual(sets_for(1, 10, 2), 2)
        self.assertEqual(sets_for(21, 20, 1), 2)
        self.assertEqual(sets_for(0, 20, 1), 1)


class ParsedSamples(unittest.TestCase):
    """The committed page samples, parsed as the scraper parses them."""

    def test_boxes_bundle_pack(self):
        item = item_of(sample("S-11369.html"), "S-11369",
                       "https://www.uline.com/Product/Detail/S-11369")
        self.assertEqual(item.item, "S-11369")
        # PRICE PER BOX with a BUNDLE/ BALE QTY. line: the bundle is the pack,
        # 25 boxes per bundle, so the per-bundle price is $0.80 x 25.
        self.assertEqual(item.unit, "Bundle")
        self.assertEqual(item.units_per_set, 25)
        self.assertEqual(item.base_unit, "Box")
        self.assertEqual(item.price, 20.0)
        self.assertEqual(item.tiers, ((1, 20.0), (4, 18.25), (10, 17.0), (20, 15.0), (40, 14.0)))
        self.assertEqual(item.min_sets, 1)
        self.assertTrue(item.in_stock)

    def test_tape_case_pack_and_half_case_tier(self):
        item = item_of(sample("S-15575.html"), "S-15575",
                       "https://www.uline.com/Product/Detail/S-15575")
        self.assertEqual(item.item, "S-15575")
        self.assertEqual(item.unit, "Case")
        self.assertEqual(item.units_per_set, 36)
        self.assertEqual(item.base_unit, "Roll")
        self.assertEqual(item.tiers[0][0], 1)
        self.assertEqual(item.tiers[2], (2, 153.0))
        line = OrderLine(item="S-15575", unit=item.unit, units_per_set=item.units_per_set,
                         min_sets=item.min_sets, base_unit=item.base_unit,
                         price=item.price, tiers=item.tiers, needed_units=36, sets=1)
        self.assertEqual(line.price_at(1), 162.0)  # 1 case at $4.50/roll
        self.assertEqual(line.total(), 162.0)
        line.sets = 2
        self.assertEqual(line.total(), 306.0)

    def test_step_cards_keep_the_pack_multiplier(self):
        item = item_of(sample("qty per case.html"), "S-22454",
                       "https://www.uline.com/Product/Detail/S-22454")
        self.assertEqual(item.item, "S-22454")
        self.assertEqual(item.note, "price steps")
        self.assertEqual(item.unit, "Case")
        self.assertEqual(item.units_per_set, 250)
        self.assertEqual(item.price, 44.0)
        self.assertEqual(item.tiers, ((1, 44.0), (3, 42.0), (5, 39.0), (10, 37.0)))

    def test_labels_chart_prices_per_box(self):
        item = item_of(sample("S-11633.html"), "S-11633",
                       "https://www.uline.com/Product/Detail/S-11633")
        self.assertEqual(item.item, "S-11633")
        self.assertEqual(item.unit, "Box")
        self.assertEqual(item.units_per_set, 14000)
        self.assertEqual(item.price, 37.0)
        self.assertEqual(item.tiers, ((1, 37.0), (5, 36.0), (15, 34.0)))

    def test_rendered_suffix_is_the_canonical_item(self):
        item = item_of(sample("S-11633-rendered.html"), "S-11633-rendered")
        self.assertEqual(item.item, "S-11633")

    def test_pages_that_are_not_one_product(self):
        self.assertIsNone(item_of(sample("not found page.html"), "not", "https://uline.com/x"))
        self.assertIsNone(item_of(sample("Multi-SKU page - Boxes.html"), "boxes",
                                  "https://www.uline.com/Product/List/boxes"))
        self.assertIsNone(item_of(sample("search result.html"), "search", "https://uline.com/x"))
        self.assertEqual(
            _first_detail_link(sample("Multi-SKU page - Boxes.html")),
            "https://www.uline.com/Product/Detail/S-4307"
            "/Corrugated-Boxes-200-Test/6-x-6-x-2-Corrugated-Boxes",
        )

    def test_blocked_markers_are_clean_on_every_sample(self):
        # no committed product page is a consent/bot/login page
        for path in sorted(SAMPLES.glob("*.html")):
            self.assertEqual(
            _blocked_message(path.read_text(encoding="utf-8", errors="replace")), "", path.name
            )

    def test_blocked_message_for_a_consent_page(self):
        page = "<html><title>x</title><p>We must verify you are a human</p>"
        self.assertIn("verification step", _blocked_message(page))


class CacheLogic(unittest.TestCase):
    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"

    def cache(self, *items):
        data: dict[str, UlineItem] = {}
        for item in items:
            put_item(data, item)
        return data

    def base(self) -> UlineItem:
        return UlineItem(
            item="S-11633",
            name="Monarch Labels",
            price=37.0,
            units_per_set=14000,
            unit="Box",
            min_sets=1,
            base_unit="Box",
            tiers=((1, 37.0), (5, 36.0)),
            aliases=("labels",),
            url="u",
            in_stock=True,
            note="chart",
            added="2026-01-01",
            checked="2026-01-01",
        )

    def test_put_keeps_aliases_and_added(self):
        cache = self.cache(self.base())
        merged = put_item(
            cache,
            UlineItem(
                item="S-11633",
                name="New name",
                price=34.0,
                units_per_set=14000,
                unit="Box",
                min_sets=1,
                base_unit="Box",
                tiers=(),
                aliases=(),
                url="",
                in_stock=False,
                note="",
                added="",
                checked="2026-02-01",
            ),
        )
        self.assertEqual(merged.aliases, ("labels",))
        self.assertEqual(merged.added, today())  # first put dates the item today
        self.assertEqual(merged.price, 34.0)
        self.assertEqual(merged.name, "New name")
        self.assertEqual(merged.checked, "2026-02-01")
        # a later re-check keeps the date it was first added
        merged = put_item(cache, UlineItem(item="S-11633", name="New name", price=34.0,
                                           units_per_set=14000, unit="Box", min_sets=1,
                                           base_unit="Box", tiers=(), aliases=(), url="",
                                           in_stock=True, note="", added="", checked="2026-03-01"))
        self.assertEqual(merged.added, today())
        self.assertEqual(merged.checked, "2026-03-01")

    def test_alias_owner_and_removal(self):
        cache = self.cache(self.base())
        owner = add_alias(cache, "labels", "S-11633")
        self.assertEqual(owner, "S-11633")
        owner = add_alias(cache, "labels", "H-999")
        self.assertEqual(owner, "S-11633")  # labels already owned, not moved
        # aliases are stored in canonical (case-folded) form
        self.assertEqual(cache["S-11633"].aliases, ("labels", "s-11633", "h-999"))
        # an alias in use by another item is not re-assigned
        cache["H-999"] = UlineItem(item="H-999", name="Other", price=1.0, units_per_set=1,
                                   unit="Each", aliases=("s-4307",), added="", checked="")
        owner = add_alias(cache, "labels", "s-4307")
        self.assertEqual(owner, "H-999")
        self.assertEqual(cache["S-11633"].aliases, ("labels", "s-11633", "h-999"))
        remove_alias(cache, "labels", "H-999")
        self.assertEqual(cache["S-11633"].aliases, ("labels", "s-11633"))

    def test_edit_keeps_aliases(self):
        cache = self.cache(self.base())
        updated = edit_item(cache, "labels", price="8.50", tiers=[])
        self.assertEqual(updated.price, 8.5)
        self.assertEqual(updated.aliases, ("labels",))
        self.assertEqual(updated.item, "S-11633")
        self.assertEqual(resolve_key(cache, "labels")[0], "S-11633")

    def test_cache_round_trip(self):
        cache = self.cache(self.base())
        uline_cache.save_cache(cache)
        again = uline_cache.load_cache()
        self.assertEqual(again["S-11633"].units_per_set, 14000)
        self.assertEqual(again["S-11633"].tiers, ((1, 37.0), (5, 36.0)))
        self.assertEqual(again["S-11633"].aliases, ("labels",))


class OrderMath(unittest.TestCase):
    def line(self, **kw) -> OrderLine:
        base = dict(
            item="S-4307",
            name="Foam",
            unit="Pack",
            units_per_set=10,
            min_sets=2,
            base_unit="Pack",
            price=12.0,
            tiers=((1, 12.0), (5, 10.0)),
            needed_units=1,
            sets=1,
        )
        base.update(kw)
        return OrderLine(**base)

    def test_price_override_and_tiers(self):
        line = self.line(sets=5)
        self.assertEqual(line.price_at(5), 10.0)
        line.price_override = 7.0
        self.assertEqual(line.price_at(5), 7.0)
        self.assertEqual(line.total(), 35.0)

    def test_csv_and_html_totals(self):
        lines = [
            self.line(needed_units=1, sets=2),
            self.line(item="H-7945", tiers=((1, 27.0),), price=27.0, needed_units=3,
                      sets=3, unit="Each", units_per_set=1, min_sets=1, base_unit="Each"),
        ]
        # csv has plain numbers, html has $ signs
        text = csv_text(lines, "2026-01-01 10:00")
        self.assertIn("TOTAL", text)
        self.assertIn("24.00", text)
        self.assertIn("81.00", text)  # 3 x $27, second line
        self.assertIn("105.00", text)  # 24 + 81
        html = order_html(lines, "2026-01-01", "Uline Order")
        self.assertIn("$24.00", html)
        self.assertIn("$81.00", html)
        self.assertIn("$105.00", html)
        self.assertEqual(order_sets(lines), 2 + 3)
        self.assertEqual(order_total(lines), 24.0 + 81.0)

    def test_html_is_escaped(self):
        lines = [self.line(name="A & B < 5")]
        html = order_html(lines, "x", "Uline Order")
        self.assertIn("A &amp; B &lt; 5", html)


class SyncFromCache(unittest.TestCase):
    """Phase 3: order lines follow cached edits, re-checks, and pack-size changes.

    The sync path mirrors what UlineDialog._sync_line does: pull fields from the
    cache, then recompute the pack count.
    """

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"
        uline_order.DRAFT_PATH = workdir / "uline_draft.json"

    def sync(self, line: OrderLine, cache: dict[str, UlineItem]) -> OrderLine:
        cached = cache[normalize_item(line.item)]
        line.name = cached.name
        line.unit = cached.unit
        line.units_per_set = max(1, cached.units_per_set)
        line.min_sets = max(1, cached.min_sets)
        line.base_unit = cached.base_unit or cached.unit
        line.price = cached.price
        line.tiers = cached.tiers
        line.sets = sets_for(line.needed_units, line.units_per_set, line.min_sets)
        return line

    def test_line_follows_pack_and_price_changes(self):
        line = OrderLine(item="S-4307", name="Foam", unit="Pack", units_per_set=10,
                         min_sets=2, base_unit="Pack", price=12.0,
                         tiers=((1, 12.0), (5, 10.0)), needed_units=1, sets=2)
        cache = {"S-4307": UlineItem(item="S-4307", name="Foam New", price=10.0,
                                     units_per_set=20, unit="Case", min_sets=1,
                                     base_unit="Case", tiers=((1, 10.0),), aliases=(),
                                     url="", in_stock=True, note="steps", added="",
                                     checked="")}
        synced = self.sync(line, cache)
        self.assertEqual((synced.unit, synced.units_per_set, synced.price), ("Case", 20, 10.0))
        self.assertEqual(synced.name, "Foam New")
        self.assertEqual(synced.sets, 1)
        self.assertEqual(synced.total(), 10.0)

    def test_needed_units_still_covered_after_a_smaller_pack(self):
        line = OrderLine(item="S-4307", unit="Pack", units_per_set=10, min_sets=1,
                         base_unit="Pack", price=12.0, tiers=((1, 12.0),),
                         needed_units=45, sets=5)
        cache = {"S-4307": UlineItem(item="S-4307", name="Foam", price=12.0,
                                     units_per_set=10, unit="Pack", min_sets=1,
                                     base_unit="Pack", tiers=((1, 12.0),), aliases=(),
                                     url="", in_stock=True, note="chart", added="",
                                     checked="")}
        synced = self.sync(line, cache)
        # 45 units of 10-per-pack needs 5 packs, unchanged by the re-check
        self.assertEqual(synced.sets, 5)
        self.assertEqual(synced.needed_units, 45)

    def test_draft_round_trip(self):
        lines = [OrderLine(item="S-4307", needed_units=1, sets=2)]
        uline_order.save_draft(lines)
        again = uline_order.load_draft()
        self.assertEqual(again[0].sets, 2)
        self.assertEqual(again[0].item, "S-4307")


class _FakeSession:
    def __init__(self, events: list[str]):
        self.events = events

    async def send(self, _name: str, _payload: dict) -> dict:
        return {"windowId": 1, "bounds": {"windowState": "minimized"}}

    async def detach(self) -> None:
        self.events.append("detached")


class _PrintSession:
    """CDP session for the PDF print. Page.printToPDF raises, as a real
    Chromium print failure does, so the `finally` path is exercised."""

    def __init__(self, events: list[str], *, boom: bool = True):
        self.events = events
        self.boom = boom

    async def send(self, name: str, payload: dict) -> dict:
        self.events.append(name)
        if name == "Page.printToPDF" and self.boom:
            raise RuntimeError("printToPDF failed")
        if name == "Page.printToPDF":
            return {"data": "YQ=="}  # base64 of b"a", as Chromium sends it
        return {"windowId": 1, "bounds": {"windowState": "minimized"}}

    async def detach(self) -> None:
        self.events.append("detached")


class _FakePage:
    def __init__(self, html: str, events: list[str], url: str = "about:blank"):
        self.url = url
        self._html = html
        self.events = events
        self.context = None

    async def content(self) -> str:
        return self._html

    async def set_content(self, html: str) -> None:
        self._html = html
        self.events.append("content")

    async def goto(self, url: str, **_kw) -> None:
        self.events.append(f"goto:{url}")
        self.url = url

    async def close(self) -> None:
        self.events.append("closed")


class _FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self._page = page
        page.context = self

    async def new_page(self):
        self._page.context = self
        return self._page

    async def new_cdp_session(self, page):
        return _FakeSession(self.events)


class _FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]

    async def close(self) -> None:
        pass


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def connect_over_cdp(self, _url: str):
        return self._browser


class _FakePlaywright:
    def __init__(self, events: list[str], browser=None):
        self.events = events
        if browser is not None:
            self.chromium = _FakeChromium(browser)

    async def start(self):
        return self

    async def stop(self) -> None:
        self.events.append("playwright-stop")

    # pdf_bytes uses `async with async_playwright() as pw`, so the fake needs
    # the async context-manager protocol.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.stop()


class _FakeTask:
    """Stand-in for the asyncio.Task the dialog is told to keep."""

    def __init__(self):
        self.canceled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.canceled = True


class _AcceptedEditor:
    """The dialog test fills the item editor; exec() accepts without a window."""

    def __init__(self, values: dict):
        self._values = values

    def exec(self):
        return 1  # QDialog.DialogCode.Accepted

    def values(self) -> dict:
        return dict(self._values)



class IdentityCheck(unittest.TestCase):
    """Phase 1: lookup_item reports what Uline served, and cleans up its tab.

    A fake page drives lookup_item without Playwright, so the identity, consent,
    and cleanup paths are checked against the committed samples.
    """

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"
        self.events: list[str] = []

    def _lookup(self, html: str, typed: str, created: bool):
        from tp_deck import uline_scraper as scraper

        page = _FakePage(html, self.events)
        context = _FakeContext(page)
        session = _FakeSession(self.events)
        browser = _FakeBrowser(context)

        async def fake_connect(_playwright, _port):
            return browser

        async def fake_find(_browser, _pattern, *, label="tab"):
            return None if created else page

        async def fake_window_state(_page):
            return 1, "minimized", session

        async def fake_reassert(_window_id, _state, _session):
            self.events.append("re-minimized")

        def fake_playwright():
            return _FakePlaywright(self.events)

        with mock.patch.object(scraper, "connect_over_cdp", fake_connect), \
             mock.patch.object(scraper, "find_page_by_url", fake_find), \
             mock.patch.object(scraper, "_window_state", fake_window_state), \
             mock.patch.object(scraper, "_reassert_minimized", fake_reassert), \
             mock.patch("playwright.async_api.async_playwright", new=fake_playwright):
            return asyncio.run(scraper.lookup_item(typed, {"uline_port": 9222}))

    def test_identity_match_is_accepted(self):
        result = self._lookup(sample("S-11633.html"), "S-11633", created=False)
        self.assertEqual(result.item, "S-11633")
        self.assertIn("detached", self.events)

    def test_identity_mismatch_is_reported(self):
        # The page Uline serves for a typed S-4307 reports item S-11633
        with self.assertRaises(RuntimeError) as ctx:
            self._lookup(sample("S-11633.html"), "S-4307", created=False)
        message = str(ctx.exception)
        self.assertIn("Uline returned S-11633", message)
        self.assertIn("not S-4307", message)

    def test_consent_page_gets_its_own_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._lookup("<html><title>Check</title><p>Access denied</p>", "S-11633", created=True)
        self.assertIn("verification step", str(ctx.exception))

    def test_created_tab_is_closed_when_the_lookup_fails(self):
        # a tab opened for the lookup is closed on failure, not left in the browser
        with self.assertRaises(RuntimeError):
            self._lookup("<html><p>Access denied</p>", "S-11633", created=True)
        self.assertIn("closed", self.events)
        self.assertIn("detached", self.events)
        self.assertIn("playwright-stop", self.events)

    def test_list_page_says_type_an_item_number(self):
        # a category page is not one product page
        with self.assertRaises(RuntimeError) as ctx:
            self._lookup(sample("Multi-SKU page - Boxes.html"), "boxes", created=True)
        self.assertIn("Item #", str(ctx.exception))
        self.assertIn("closed", self.events)


class DialogLogic(unittest.TestCase):
    """The dialog keeps order lines in step with the cache, uses live Settings,
    and reports disk failures instead of raising out of the widget."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"
        uline_order.DRAFT_PATH = workdir / "uline_draft.json"
        self.calls: list = []

    def dialog(self, settings=None):
        from tp_deck import uline_dialog as dmod

        def stub_runner(coro, on_done):
            coro.close()
            self.calls.append(on_done)

        return dmod.UlineDialog(parent=None, runner=stub_runner,
                                settings=settings or {"uline_export_dir": "Original"})

    def test_keyword_search_caches_under_the_item_it_found(self):
        dialog = self.dialog()
        found = UlineItem(item="S-4307", name="Conductive Foam", price=12.0, units_per_set=10,
                          unit="Pack", min_sets=2, base_unit="Pack", tiers=((1, 12.0),),
                          aliases=(), url="", in_stock=True, note="chart", added="", checked="")
        dialog._key_edit.setText("conductive foam")
        dialog._add()
        self.calls[0](True, found, "")
        line = dialog._lines[0]
        self.assertEqual(line.item, "S-4307")
        self.assertIn("S-4307", dialog._status.text())
        # cached under the number Uline served, not the words typed
        self.assertIn("S-4307", uline_cache.load_cache())
        self.assertEqual(uline_order.load_draft()[0].item, "S-4307")

    def test_recheck_and_edit_update_the_order_line(self):
        dialog = self.dialog()
        first = UlineItem(item="S-4307", name="Foam", price=12.0, units_per_set=10, unit="Pack",
                          min_sets=2, base_unit="Pack", tiers=((1, 12.0),), aliases=(),
                          url="", in_stock=True, note="chart", added="", checked="")
        dialog._key_edit.setText("S-4307")
        dialog._add()
        self.calls[0](True, first, "")
        self.assertEqual(dialog._lines[0].total(), 24.0)

        recheck = UlineItem(item="S-4307", name="Foam v2", price=10.0, units_per_set=20,
                            unit="Case", min_sets=1, base_unit="Case", tiers=((1, 10.0),),
                            aliases=(), url="", in_stock=True, note="steps", added="",
                            checked="")
        dialog._on_re_lookup(True, recheck, "")
        line = dialog._lines[0]
        self.assertEqual((line.unit, line.units_per_set, line.price), ("Case", 20, 10.0))
        self.assertIn("re-checked", dialog._status.text())

        edited = edit_item(dialog._cache, "S-4307", price="8.50", tiers=[])
        dialog._sync_lines("edited")
        self.assertEqual(dialog._lines[0].total(), 8.5)

    def test_settings_change_flows_to_the_export_folder(self):
        from tp_deck import uline_dialog as dmod

        dialog = self.dialog({"uline_export_dir": "Original"})
        original = dmod.load_settings
        dmod.load_settings = lambda: {"uline_export_dir": "Changed"}
        try:
            settings = dialog._refresh_settings()
            self.assertEqual(uline_order.export_dir(settings).name, "Changed")
        finally:
            dmod.load_settings = original

    def test_disk_failure_uses_the_returned_flag(self):
        from tp_deck import uline_dialog as dmod

        dialog = self.dialog()
        original = dmod.write_cache

        def boom(_cache):
            raise OSError("disk full")

        dmod.write_cache = boom
        try:
            # The save reports the failure as a flag, and the caller decides
            # what stays on screen.
            self.assertFalse(dialog._save_cache())
            self.assertIn("disk full", dialog._disk_failure)
            dialog._show_disk_failure()
            self.assertIn("disk full", dialog._error.text())
        finally:
            dmod.write_cache = original

    def _fill_editor(self, values: dict):
        """Drive the item editor without opening a modal dialog."""
        from tp_deck import uline_dialog as dmod

        original = dmod.UlineItemEditor
        dmod.UlineItemEditor = lambda *_a, **_kw: _AcceptedEditor(values)
        return original

    def test_price_edit_makes_the_order_pay_the_edited_price(self):
        from tp_deck import uline_dialog as dmod

        dialog = self.dialog()
        first = UlineItem(item="S-4307", name="Foam", price=20.0, units_per_set=10,
                          unit="Pack", min_sets=1, base_unit="Pack",
                          tiers=((1, 20.0), (10, 14.0)), aliases=(), url="",
                          in_stock=True, note="chart", added="", checked="")
        dialog._key_edit.setText("S-4307")
        dialog._add()
        self.calls[0](True, first, "")
        self.assertEqual(dialog._lines[0].total(), 20.0)  # 1 pack at $20

        original = self._fill_editor({
            "name": "Foam", "price": "25", "unit": "Pack", "units_per_set": 10,
            "min_sets": 1, "base_unit": "Pack", "aliases": [], "note": "",
        })
        try:
            dialog._cache_table.setCurrentCell(0, 0)
            dialog._edit_item()
        finally:
            dmod.UlineItemEditor = original

        line = dialog._lines[0]
        self.assertEqual(line.price, 25.0)
        self.assertEqual(line.tiers, ())  # the tiers are cleared
        self.assertEqual(line.price_at(1), 25.0)
        self.assertEqual(line.price_at(10), 25.0)  # no tier wins on a big order
        self.assertEqual(line.total(), 25.0)

        # A typed grid price still overrides the cache price, and 0 clears it.
        line.price_override = 7.0
        dialog._sync_lines("")
        self.assertEqual(line.price_at(1), 7.0)
        line.price_override = None
        dialog._sync_lines("")
        self.assertEqual(line.price_at(1), 25.0)

        # Re-Lookup brings Uline's own volume prices back.
        recheck = UlineItem(item="S-4307", name="Foam", price=22.0, units_per_set=10,
                            unit="Pack", min_sets=1, base_unit="Pack",
                            tiers=((1, 22.0),), aliases=(), url="",
                            in_stock=True, note="steps", added="", checked="")
        dialog._on_re_lookup(True, recheck, "")
        self.assertEqual(dialog._lines[0].price, 22.0)
        self.assertEqual(dialog._lines[0].total(), 22.0)

    def test_name_edit_leaves_the_tiers_in_place(self):
        from tp_deck import uline_dialog as dmod

        dialog = self.dialog()
        first = UlineItem(item="S-4307", name="Foam", price=20.0, units_per_set=10,
                          unit="Pack", min_sets=1, base_unit="Pack",
                          tiers=((1, 20.0), (10, 14.0)), aliases=(), url="",
                          in_stock=True, note="chart", added="", checked="")
        dialog._key_edit.setText("S-4307")
        dialog._add()
        self.calls[0](True, first, "")

        original = self._fill_editor({
            "name": "New foam name", "price": "20", "unit": "Case",
            "units_per_set": 10, "min_sets": 1, "base_unit": "Pack",
            "aliases": [], "note": "",
        })
        try:
            dialog._cache_table.setCurrentCell(0, 0)
            dialog._edit_item()
        finally:
            dmod.UlineItemEditor = original

        self.assertEqual(dialog._cache["S-4307"].tiers, ((1, 20.0), (10, 14.0)))
        self.assertEqual(dialog._lines[0].name, "New foam name")

    def test_foreign_alias_stays_off_the_line_and_on_the_error(self):
        dialog = self.dialog()
        first = UlineItem(item="S-4307", name="Foam", price=12.0, units_per_set=10,
                          unit="Pack", min_sets=1, base_unit="Pack", tiers=((1, 12.0),),
                          aliases=("foam",), url="", in_stock=True, note="chart",
                          added="", checked="")
        dialog._key_edit.setText("S-4307")
        dialog._add()
        self.calls[0](True, first, "")

        second = UlineItem(item="H-999", name="Other item", price=5.0, units_per_set=1,
                           unit="Each", min_sets=1, base_unit="Each", tiers=((1, 5.0),),
                           aliases=(), url="", in_stock=True, note="chart",
                           added="", checked="")
        dialog._key_edit.setText("H-999")
        dialog._alias_edit.setText("foam")  # the alias belongs to S-4307
        dialog._add()
        self.calls[1](True, second, "")

        self.assertEqual(len(dialog._lines), 2)
        self.assertEqual(dialog._lines[1].item, "H-999")
        self.assertEqual(dialog._lines[1].alias, "")  # not carried on the line
        # the alias stays with its first owner, in the cache
        self.assertEqual(dialog._cache["H-999"].aliases, ())
        self.assertIn("foam", dialog._cache["S-4307"].aliases)
        self.assertEqual(resolve_key(dialog._cache, "foam")[0], "S-4307")
        # the red label says who owns it
        self.assertIn("foam", dialog._error.text())
        self.assertIn("S-4307", dialog._error.text())

    def test_cache_disk_failure_stays_on_the_error_after_add(self):
        from tp_deck import uline_dialog as dmod

        dialog = self.dialog()
        found = UlineItem(item="S-4307", name="Foam", price=12.0, units_per_set=10,
                          unit="Pack", min_sets=1, base_unit="Pack", tiers=((1, 12.0),),
                          aliases=(), url="", in_stock=True, note="chart",
                          added="", checked="")
        original = dmod.write_cache

        def boom(_cache):
            raise OSError("disk full")

        dmod.write_cache = boom
        try:
            dialog._key_edit.setText("S-4307")
            dialog._add()
            self.calls[0](True, found, "")
            self.assertEqual(len(dialog._lines), 1)  # the line was still added
            self.assertIn("disk full", dialog._error.text())  # disk message survives
        finally:
            dmod.write_cache = original

    def test_closing_the_builder_cancels_the_browser_task(self):
        from PySide6.QtGui import QCloseEvent

        dialog = self.dialog()
        task = _FakeTask()
        dialog._key_edit.setText("S-4307")
        dialog._add()
        # the stub runner returns None; the dialog stores whatever it is told to
        dialog._task = task
        dialog.closeEvent(QCloseEvent())

        self.assertTrue(task.canceled)  # closeEvent cancelled the running task
        self.calls[0](True, UlineItem(item="S-4307", name="Foam", price=12.0,
                                      units_per_set=10, unit="Pack", min_sets=1,
                                      base_unit="Pack", tiers=((1, 12.0),), aliases=(),
                                      url="", in_stock=True, note="chart", added="",
                                      checked=""), "")
        # the late success was dropped: no line, no cache write, no status change
        self.assertEqual(dialog._lines, [])
        self.assertEqual(uline_cache.load_cache(), {})

    def test_export_pdf_is_busy_once_and_the_first_path_is_kept(self):
        import tempfile
        from tp_deck import uline_dialog as dmod

        workdir = pathlib.Path(tempfile.mkdtemp())
        dialog = self.dialog({"uline_export_dir": str(workdir)})
        first = UlineItem(item="S-4307", name="Foam", price=12.0, units_per_set=10,
                          unit="Pack", min_sets=1, base_unit="Pack", tiers=((1, 12.0),),
                          aliases=(), url="", in_stock=True, note="chart",
                          added="", checked="")
        dialog._key_edit.setText("S-4307")
        dialog._add()
        self.calls[0](True, first, "")

        target = workdir / "chosen.pdf"
        with mock.patch.object(dmod.QFileDialog, "getSaveFileName",
                               return_value=(str(target), "PDF files (*.pdf)")):
            dialog._export_pdf()
            self.assertEqual(len(self.calls), 2)  # lookup, then one print
            self.assertTrue(dialog._busy)
            self.assertFalse(dialog._add_btn.isEnabled())  # Add is disabled for the print
            dialog._export_pdf()  # second click while the first is running
            self.assertIn("already running", dialog._error.text())
            self.assertEqual(len(self.calls), 2)  # no second print started
            # the first print's path is its own, not overwritten by the second
            self.calls[1](True, b"aA", "")
            self.assertTrue(target.exists())
            self.assertIn("Archived", dialog._status.text())
            self.assertFalse(dialog._busy)
            self.assertTrue(dialog._add_btn.isEnabled())

    def test_out_of_stock_is_shown_and_the_line_is_still_added(self):
        from tp_deck import uline_dialog as dmod

        dialog = self.dialog()
        found = UlineItem(item="S-4307", name="Foam", price=12.0, units_per_set=10,
                          unit="Pack", min_sets=1, base_unit="Pack", tiers=((1, 12.0),),
                          aliases=(), url="", in_stock=False, note="chart",
                          added="", checked="")
        dialog._key_edit.setText("S-4307")
        dialog._add()
        self.calls[0](True, found, "")

        self.assertEqual(len(dialog._lines), 1)  # the line is added anyway
        self.assertIn("out of stock", dialog._status.text())
        column = dmod.CACHE_COLUMNS.index("In Stock")
        cell = dialog._cache_table.item(0, column)
        self.assertEqual(cell.text(), "No")


class PdfPrintTab(unittest.TestCase):
    """PDF export closes the page it opened, on success and on a print failure."""

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_cache.CACHE_PATH = workdir / "uline_cache.json"
        uline_order.DRAFT_PATH = workdir / "uline_draft.json"
        self.events: list[str] = []

    def _print(self, session, settings=None):
        from tp_deck import uline_scraper as scraper

        page = _FakePage("", self.events)
        context = _FakeContext(page)
        browser = _FakeBrowser(context)
        playwright = _FakePlaywright(self.events, browser)

        async def fake_window_state(_page):
            return 1, "minimized", session

        async def fake_reassert(_window_id, _state, _session):
            self.events.append("re-minimized")

        def fake_playwright():
            return playwright

        with mock.patch.object(scraper, "_window_state", fake_window_state), \
             mock.patch.object(scraper, "_reassert_minimized", fake_reassert), \
             mock.patch("playwright.async_api.async_playwright", new=fake_playwright):
            return asyncio.run(
                uline_order.pdf_bytes("<html><body>x</body></html>",
                                      settings or {"uline_port": 9222})
            )

    def test_failed_print_closes_the_page_and_detaches_the_session(self):
        session = _PrintSession(self.events)
        with self.assertRaises(RuntimeError):
            self._print(session)
        self.assertIn("content", self.events)  # the page this print opened
        self.assertIn("closed", self.events)
        self.assertIn("re-minimized", self.events)  # minimized window stays minimized
        self.assertIn("detached", self.events)

    def test_successful_print_closes_the_page_too(self):
        session = _PrintSession(self.events, boom=False)
        data = self._print(session)
        self.assertEqual(data, b"a")  # the print payload is returned whole
        self.assertIn("closed", self.events)
        self.assertIn("detached", self.events)


class DraftLoading(unittest.TestCase):
    """A bad draft field must not stop the builder from opening."""

    def setUp(self):
        workdir = pathlib.Path(tempfile.mkdtemp())
        uline_order.DRAFT_PATH = workdir / "uline_draft.json"

    def test_one_bad_line_does_not_kill_the_draft(self):
        good = {"item": "S-4307", "name": "Foam", "unit": "Pack", "units_per_set": 10,
                "min_sets": 1, "base_unit": "Pack", "price": 12.0, "tiers": [[1, 12.0]],
                "needed_units": 10, "sets": 1, "price_override": None}
        bad = {"item": "H-999", "name": "Bad", "unit": "Each", "units_per_set": "nope",
              "min_sets": 1, "base_unit": "Each", "price": 1.0, "tiers": [],
              "needed_units": 1, "sets": 1, "price_override": None}
        uline_order.DRAFT_PATH.write_text(
            "[" + json.dumps(good) + "," + json.dumps(bad) + "]", encoding="utf-8"
        )
        lines = uline_order.load_draft()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].item, "S-4307")

    def test_unreadable_file_is_an_empty_order(self):
        uline_order.DRAFT_PATH.write_text("not json", encoding="utf-8")
        self.assertEqual(uline_order.load_draft(), [])


if __name__ == "__main__":
    unittest.main()
