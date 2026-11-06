"""Regression tests for the two windows that stopped appearing in v0.4.0.

Run from the repo root:  python -m unittest discover -s tests -v
Both crashes happened while the window was being built, so neither test opens
a window on screen, calls exec(), asks GitHub for anything, or clicks Save
(Save writes the real settings.json):
  - What's New died inside release_notes_html() with a NameError for a
    stylesheet name that does not exist. The caller logged the error and the
    dialog was never constructed, so the notes never showed.
  - the gear died in SettingsDialog.__init__ with AttributeError, because the
    old code connected theme_combo.currentData, which is a method and not a
    signal, so the settings window never opened and the theme preview never
    followed the picker.
"""

import pathlib
import sys
import unittest
from unittest import mock
from PySide6.QtWidgets import QApplication

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tp_deck.release_notes import release_notes_html
from tp_deck.settings_dialog import SettingsDialog
from tp_deck.theme import notes_document_css, palette_for

# PySide6 aborts the process when a widget is built with no application
# instance, and again at shutdown when that application is dropped while
# widgets are still alive. This test builds the real Settings dialog, so one
# application at module level keeps the whole run in one piece. The Settings
# dialog built below has no parent, so it uses this instance.
SESSION_APP = QApplication.instance() or QApplication([])

# Shaped like a GitHub release body: one heading and one bullet.
RELEASE_BODY = "## TP DECK 0.4.0\n- Batch Serial searches the PO column"

# The document font lives in notes_document_css, so it only reaches the page
# when the stylesheet name resolves.
DOCUMENT_FONT = "Segoe UI"
NO_NOTES = "This release has no notes."

SETTINGS_TITLE = "TP DECK \u2014 Settings"
# The preview title text is the palette's title color. "neutral" is the
# original slate palette, which reuses the cyan family; Red sits at the top
# of the wheel and neutral at the bottom.
NEUTRAL_TITLE_COLOR = "#0EA5E9"
RED_TITLE_COLOR = "#F87171"


class ReleaseNotesHtml(unittest.TestCase):
    def test_a_release_body_becomes_html_qt_can_show(self):
        """Building the notes page no longer dies on a missing stylesheet."""
        shown = release_notes_html(RELEASE_BODY)
        self.assertIn("TP DECK 0.4.0", shown)
        self.assertIn("Batch Serial searches the PO column", shown)
        self.assertIn("<style>", shown)
        self.assertIn(DOCUMENT_FONT, notes_document_css(palette_for("neutral")))
        self.assertIn(DOCUMENT_FONT, shown)

    def test_an_empty_body_still_shows_a_line(self):
        """The converter keeps its fallback for a release with no body."""
        shown = release_notes_html("")
        self.assertIn(NO_NOTES, shown)
        self.assertIn("<style>", shown)


class SettingsDialogWindow(unittest.TestCase):
    def test_the_gear_opens_and_a_picker_row_repaints_the_preview(self):
        saved = {"theme": "neutral"}
        with mock.patch("tp_deck.settings_dialog.load_settings", return_value=saved):
            # Constructing the dialog is the assertion: v0.4.0 raised
            # AttributeError here, so nothing below ever ran. The dialog reads
            # the patched settings twice, once for the window palette and once
            # to fill the form, so the real settings file stays untouched.
            dialog = SettingsDialog()
            try:
                self.assertEqual(dialog.windowTitle(), SETTINGS_TITLE)
                self.assertEqual(dialog.theme_combo.currentData(), "neutral")
                neutral_sheet = dialog.preview_title.styleSheet().lower()
                self.assertIn(NEUTRAL_TITLE_COLOR.lower(), neutral_sheet)
                self.assertNotIn(RED_TITLE_COLOR.lower(), neutral_sheet)

                # Moving the combo fires currentIndexChanged, which repaints
                # the preview. A handler wired to the row text instead of the
                # row number raises TypeError in this step.
                red_row = dialog.theme_combo.findData("red")
                self.assertNotEqual(red_row, -1)
                dialog.theme_combo.setCurrentIndex(red_row)

                red_sheet = dialog.preview_title.styleSheet().lower()
                self.assertIn(RED_TITLE_COLOR.lower(), red_sheet)
                self.assertNotIn(NEUTRAL_TITLE_COLOR.lower(), red_sheet)
            finally:
                dialog.close()


if __name__ == "__main__":
    unittest.main()
