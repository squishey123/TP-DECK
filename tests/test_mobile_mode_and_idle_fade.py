"""Tests for Mobile Mode (the laptop panel) and the idle "Fade Out main UI".

Run from the repo root:  python -m unittest discover -s tests -v
Nothing here shows a window on screen or waits on a wall clock. The idle fade
is driven by moving _idle_touched into the past and calling _tick_idle_fade
directly, so the five-second arm and the five-second ramp are checked as
arithmetic rather than as stopwatch timing:

  - Mobile Mode never built the panel at all. Settings saved the flag and the
    theme had compact QSS for it, but Dashboard hardcoded one 300 x 372 column
    of spelled-out buttons, so switching the checkbox on changed nothing.
  - The fade had constants and no code: the only fade in the app was the Safe
    Shutdown grey ease, which changes colors and cannot make the panel
    see-through, so nothing behind the app was ever visible.
"""

import pathlib
import sys
import time
import unittest
from unittest import mock
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tp_deck.dashboard import Dashboard
from tp_deck.dashboard import (
    _DASHBOARD_HEIGHT,
    _DASHBOARD_HEIGHT_SLOW,
    _IDLE_ARM_SECONDS,
    _IDLE_FLOOR,
    _IDLE_RAMP_SECONDS,
    _MOBILE_ICON_EDGE,
    _MOBILE_HEIGHT,
    _MOBILE_HEIGHT_SLOW,
    _MOBILE_SYMBOLS,
    _MOBILE_WIDTH,
)
from tp_deck.theme import dashboard_qss
from tp_deck.settings_dialog import SettingsDialog

# PySide6 aborts the process when a widget is built with no application
# instance, and again at shutdown while widgets are still alive. These tests
# build real panels, so one application at module level keeps the run in one
# piece, like the other Qt test modules do.
SESSION_APP = QApplication.instance() or QApplication([])

# Qt keeps window opacity in 1/255 steps, so a computed floor of 0.35 reads
# back as 0.34901960784313724, which is 89 over 255. Every check on windowOpacity
# is that one step wide, while the panel's own model value is checked exactly.
_OPACITY_PLACES = 2


def _launch(**over):
    """Patch the settings TP DECK would have read at launch."""
    saved = {"mobile_mode": False, "fade_out_main_ui": False}
    saved.update(over)
    return mock.patch("tp_deck.dashboard.load_settings", return_value=saved)


def _left_alone_for(dashboard: Dashboard, seconds: float) -> None:
    """Put the last touch seconds into the past, the way an idle desk does."""
    dashboard._idle_touched = time.monotonic() - seconds


class MobilePanel(unittest.TestCase):
    def test_the_panel_becomes_a_wide_low_strip(self):
        with _launch(mobile_mode=True):
            dashboard = Dashboard()
            self.assertEqual(dashboard.width(), _MOBILE_WIDTH)
            self.assertEqual(dashboard.height(), _MOBILE_HEIGHT)
            self.assertGreater(dashboard.width(), dashboard.height())
            self.assertLess(dashboard.height(), _DASHBOARD_HEIGHT)
            dashboard.set_slow_razor(True)
            self.assertEqual(dashboard.height(), _MOBILE_HEIGHT_SLOW)
            dashboard.set_slow_razor(False)
            self.assertEqual(dashboard.height(), _MOBILE_HEIGHT)

    def test_the_body_is_one_row_of_square_symbol_buttons(self):
        with _launch(mobile_mode=True):
            dashboard = Dashboard()
            buttons = (
                dashboard.execute_btn,
                dashboard.pick_btn,
                dashboard.batch_btn,
                dashboard.cycle_btn,
                dashboard.stop_btn,
                dashboard.power_btn,
                dashboard.tools_btn,
                dashboard.settings_btn,
            )
            for button in buttons:
                self.assertEqual(button.width(), _MOBILE_ICON_EDGE)
                self.assertEqual(button.height(), _MOBILE_ICON_EDGE)
            self.assertEqual(dashboard.execute_btn.text(), _MOBILE_SYMBOLS["execute"])
            self.assertEqual(dashboard.pick_btn.text(), _MOBILE_SYMBOLS["pick"])
            self.assertEqual(dashboard.batch_btn.text(), _MOBILE_SYMBOLS["batch"])
            self.assertEqual(dashboard.stop_btn.text(), _MOBILE_SYMBOLS["stop"])
            self.assertEqual(dashboard.settings_btn.text(), _MOBILE_SYMBOLS["settings"])
            # the words are gone from the squares but not off the panel
            self.assertIn("Scrape eBay Orders", dashboard.execute_btn.toolTip())
            self.assertIn("Generate Pick List", dashboard.pick_btn.toolTip())
            self.assertIn("Batch Serial", dashboard.batch_btn.toolTip())
            self.assertIn("Emergency Stop", dashboard.stop_btn.toolTip())
            # the compact QSS squares the action and stop buttons; the full
            # sheet leaves them free-standing
            sheet = dashboard.styleSheet()
            self.assertIn(f"max-width: {_MOBILE_ICON_EDGE}px", sheet)
            self.assertNotIn("Scrape eBay Orders", sheet)

    def test_the_title_lamp_version_and_well_shrink_but_stay_readable(self):
        """Each piece shrinks against a full panel but is still sized to fit.

        Qt only lays a fixed-size panel out when it is shown, so a test that
        never shows one sees no geometry at all. What holds before any window
        appears is what every piece asks for: its own minimum and preferred
        size, and the column total, which has to stay inside the strip for
        nothing on the strip to be squeezed away."""
        with _launch(mobile_mode=True):
            strip = Dashboard()
        with _launch():
            tall = Dashboard()
        # a 9 pixel lamp against the full panel's 12, still a square dot
        self.assertEqual(strip._light.width(), 9)
        self.assertEqual(strip._light.height(), 9)
        self.assertLess(strip._light.minimumSize().width(), tall._light.minimumSize().width())
        # the title and the version line keep their words at a smaller type
        self.assertEqual(strip._title.text(), tall._title.text())
        self.assertEqual(strip._title.sizeHint().height(), 15)
        self.assertLess(strip._title.sizeHint().height(), tall._title.sizeHint().height())
        self.assertTrue(strip._version.text().startswith("//"))
        self.assertLess(strip._version.sizeHint().height(), tall._version.sizeHint().height())
        # the status readout keeps its words in a shorter well
        self.assertEqual(strip._well.minimumHeight(), 30)
        self.assertEqual(strip._well._text, "Idle")
        self.assertLess(strip._well.minimumHeight(), tall._well.minimumHeight())
        # the whole column of pieces still fits, at the heights they ask for
        column = strip.centralWidget().layout()
        self.assertLessEqual(column.totalSizeHint().height(), _MOBILE_HEIGHT)
        self.assertLessEqual(column.totalMinimumSize().height(), _MOBILE_HEIGHT)
        # with Slow Razor on the strip grows and the label line gets room too
        strip.set_slow_razor(True)
        self.assertEqual(strip.height(), _MOBILE_HEIGHT_SLOW)
        self.assertLessEqual(strip.centralWidget().layout().totalSizeHint().height(),
                             _MOBILE_HEIGHT_SLOW)

    def test_the_full_panel_keeps_its_words_and_size(self):
        with _launch():
            dashboard = Dashboard()
            self.assertEqual(dashboard.width(), 300)
            self.assertEqual(dashboard.height(), _DASHBOARD_HEIGHT)
            self.assertEqual(dashboard.execute_btn.text(), "Scrape eBay Orders")
            self.assertEqual(dashboard.pick_btn.text(), "Generate Pick List")
            self.assertEqual(dashboard.batch_btn.text(), "Batch Serial")
            self.assertIn("Emergency Stop", dashboard.stop_btn.text())
            # the compact square button must not leak onto the normal panel:
            # boxed to the glyph size there, Emergency Stop came up as a 10 px
            # sliver with its words cut, which a test that never shows a
            # window cannot see. The sheet is what draws it, so the sheet is
            # what is checked: the normal panel leaves the height open (the
            # sheet's own no-limit value) and carries no glyph-size box.
            _, _, after = dashboard.styleSheet().partition("QPushButton#stopBtn {")
            stop_rule = after.partition("}")[0]
            self.assertTrue(stop_rule.strip())
            self.assertIn("max-height: 16777215px", stop_rule)
            self.assertNotIn(f"max-height: {_MOBILE_ICON_EDGE}px", stop_rule)
            self.assertNotIn(f"min-height: {_MOBILE_ICON_EDGE}px", stop_rule)
            dashboard.set_slow_razor(True)
            self.assertEqual(dashboard.height(), _DASHBOARD_HEIGHT_SLOW)


class IdleFade(unittest.TestCase):
    def test_nothing_fades_until_the_setting_is_on(self):
        with _launch(fade_out_main_ui=False):
            dashboard = Dashboard()
            # off at launch means no watch ticking: the timer only runs while
            # the setting is on, so an idle panel costs nothing
            self.assertFalse(dashboard._idle_timer.isActive())
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            # turning it on mid-session starts the countdown and fades
            dashboard.set_idle_fade_enabled(True)
            self.assertTrue(dashboard._idle_timer.isActive())
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), _IDLE_FLOOR, places=_OPACITY_PLACES)
            dashboard.set_idle_fade_enabled(False)
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            # turning it off drops the watch too, not just the glass
            self.assertFalse(dashboard._idle_timer.isActive())

    def test_five_idle_seconds_start_a_five_second_ramp_to_the_floor(self):
        with _launch(fade_out_main_ui=True):
            dashboard = Dashboard()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS / 2)
            dashboard._tick_idle_fade()
            half = 1.0 - 0.5 * (1.0 - _IDLE_FLOOR)
            self.assertAlmostEqual(dashboard.windowOpacity(), half, places=_OPACITY_PLACES)
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS * 2)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), _IDLE_FLOOR, places=_OPACITY_PLACES)
            # never fully see-through, and the lamp gains its findable ring
            self.assertGreater(dashboard.windowOpacity(), 0.0)
            self.assertGreater(dashboard._light._boost, 0.0)

    def test_a_running_job_and_auto_cycle_do_not_hold_the_panel_solid(self):
        with _launch(fade_out_main_ui=True):
            dashboard = Dashboard()
            dashboard.set_autocycle(True)
            dashboard.set_processing(True, job="orders")
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), _IDLE_FLOOR, places=_OPACITY_PLACES)

    def test_the_pointer_hovering_the_panel_brings_it_back_solid(self):
        with _launch(fade_out_main_ui=True):
            dashboard = Dashboard()
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), _IDLE_FLOOR, places=_OPACITY_PLACES)
            inside = dashboard.pos() + QPoint(20, 20)
            with mock.patch("tp_deck.dashboard.QCursor.pos", return_value=inside):
                dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            self.assertEqual(dashboard._light._boost, 0.0)

    def test_a_click_on_the_panel_brings_it_back_solid(self):
        with _launch(fade_out_main_ui=True):
            dashboard = Dashboard()
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), _IDLE_FLOOR, places=_OPACITY_PLACES)
            dashboard._handle_stop()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)

    def test_shutdown_and_an_update_warning_keep_the_panel_solid(self):
        with _launch(fade_out_main_ui=True):
            dashboard = Dashboard()
            dashboard.begin_shutdown_arm()
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            dashboard.cancel_shutdown_arm()
            dashboard.begin_update_warning()
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            dashboard.end_update_warning()

    def test_the_fade_leaves_the_shutdown_grey_alone(self):
        """The two fades are separate: window alpha vs the grey color ease."""
        with _launch(fade_out_main_ui=True):
            dashboard = Dashboard()
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            faded = dashboard.windowOpacity()
            # The grey ease starts at solid, so it takes a tick of the arm to
            # leave any grey at all: two seconds in it is 2/5 of the way to
            # grey, and the panel has not shut down, which needs five. The four
            # places cover the microseconds the backdating of the arm drifts.
            dashboard.begin_shutdown_arm()
            dashboard._arm_started = time.monotonic() - 2.0
            dashboard._tick_shutdown_arm()
            self.assertAlmostEqual(dashboard.windowOpacity(), 1.0, places=_OPACITY_PLACES)
            self.assertAlmostEqual(dashboard._fade, 2.0 / 5.0, places=4)
            self.assertFalse(dashboard._shutting_down)
            self.assertEqual(dashboard._light._boost, 0.0)
            self.assertAlmostEqual(faded, _IDLE_FLOOR, places=_OPACITY_PLACES)


class CompactProcessing(unittest.TestCase):
    def test_a_run_greys_the_square_instead_of_renaming_it(self):
        with _launch(mobile_mode=True):
            dashboard = Dashboard()
            dashboard.set_processing(True, job="orders")
            self.assertFalse(dashboard.execute_btn.isEnabled())
            self.assertEqual(dashboard.execute_btn.text(), _MOBILE_SYMBOLS["execute"])
            self.assertEqual(dashboard._well._text, "Processing")
            dashboard.set_processing(False)
            self.assertEqual(dashboard.execute_btn.text(), _MOBILE_SYMBOLS["execute"])

    def test_a_queued_order_keeps_the_serial_symbol(self):
        with _launch(mobile_mode=True):
            dashboard = Dashboard()
            dashboard.set_serial_queued(True)
            self.assertEqual(dashboard.batch_btn.text(), _MOBILE_SYMBOLS["batch"])
            dashboard.set_serial_queued(False)
            self.assertEqual(dashboard.batch_btn.text(), _MOBILE_SYMBOLS["batch"])

    def test_the_full_panel_still_says_processing_and_queued(self):
        with _launch():
            dashboard = Dashboard()
            dashboard.set_processing(True, job="orders")
            self.assertEqual(dashboard.execute_btn.text(), "\u23f3 Processing...")
            dashboard.set_processing(False)
            dashboard.set_serial_queued(True)
            self.assertEqual(dashboard.batch_btn.text(), "Queued")


class SettingsWiring(unittest.TestCase):
    """Settings is what carries the two features to a panel that is open.

    The page reads the flags when it opens, writes both back the moment Save
    runs, and the fade is applied straight away. The panel's shape is not, so
    the strip waits for the next launch, which is what the help text says.
    load_settings and update_settings are patched, so the real settings file
    is never touched and nothing is ever shown on screen.
    """

    def test_the_page_opens_with_the_flags_as_they_are_stored(self):
        saved = {"mobile_mode": True, "fade_out_main_ui": False, "cache_ttl": "12h"}
        with mock.patch("tp_deck.settings_dialog.load_settings", return_value=saved):
            dialog = SettingsDialog()
            try:
                self.assertTrue(dialog.mobile_mode_check.isChecked())
                self.assertFalse(dialog.fade_out_check.isChecked())
            finally:
                dialog.close()

    def test_save_writes_both_flags_and_the_fade_starts_right_away(self):
        saved = {"mobile_mode": True, "fade_out_main_ui": True, "cache_ttl": "12h"}
        written: dict[str, object] = {}
        record = lambda **kwargs: written.update(kwargs)
        with _launch(mobile_mode=True, fade_out_main_ui=True), \
             mock.patch("tp_deck.settings_dialog.load_settings", return_value=saved), \
             mock.patch("tp_deck.settings_dialog.update_settings", side_effect=record), \
             mock.patch("tp_deck.theme.update_settings", side_effect=record):
            # the strip came up compact on this launch and went transparent
            dashboard = Dashboard()
            self.assertEqual(dashboard.height(), _MOBILE_HEIGHT)
            _left_alone_for(dashboard, _IDLE_ARM_SECONDS + _IDLE_RAMP_SECONDS)
            dashboard._tick_idle_fade()
            self.assertAlmostEqual(dashboard.windowOpacity(), _IDLE_FLOOR,
                                   places=_OPACITY_PLACES)
            dialog = SettingsDialog(parent=dashboard)
            try:
                # the operator turns the fade off and presses Save
                dialog.fade_out_check.setChecked(False)
                dialog._save()
                self.assertIs(written["mobile_mode"], True)
                self.assertIs(written["fade_out_main_ui"], False)
                # Save reaches the open panel: the glass goes back solid, and
                # the watch that was ticking every 250 ms is switched off with
                # it, so the rest of the session costs nothing
                self.assertFalse(dashboard._idle_fade_enabled)
                self.assertFalse(dashboard._idle_timer.isActive())
                self.assertAlmostEqual(dashboard.windowOpacity(), 1.0,
                                       places=_OPACITY_PLACES)
                # the strip keeps the shape it had at launch, so the shape is
                # the half that waits for the next launch, like the help says
                self.assertEqual(dashboard.height(), _MOBILE_HEIGHT)
                self.assertTrue(dashboard._compact)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
