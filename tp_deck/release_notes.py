"""Show GitHub release notes after an update relaunches TP DECK."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tp_deck import app_version
from tp_deck.settings_manager import load_settings, update_settings
from tp_deck.theme import current_palette, notes_document_css, release_notes_qss
from tp_deck.updater import (
    describe_release,
    fetch_by_tag,
    is_release_layout,
    parse_version,
    release_notes_markdown,
)

logger = logging.getLogger("tpdeck")

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_URL = re.compile(r'(?<!href=")(https?://[^\s<]+)')
_NUMBERED = re.compile(r"\d+\.\s+(.*)")


def _dialog_qss() -> str:
    return release_notes_qss(current_palette())


def _notes_css() -> str:
    return notes_document_css(current_palette())


def _same_version(left: str, right: str) -> bool:
    parsed = parse_version(left)
    return parsed != (0,) and parsed == parse_version(right)


def _autolink(match: re.Match[str]) -> str:
    raw = match.group(1)
    url = raw.rstrip(".,);]")
    return f'<a href="{url}">{url}</a>{raw[len(url):]}'


def _inline(text: str) -> str:
    """Escape one line, then restore the inline markdown GitHub notes use."""
    safe = html.escape(text.strip())
    safe = _CODE.sub(r"<code>\1</code>", safe)
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    safe = _LINK.sub(r'<a href="\2">\1</a>', safe)
    safe = _URL.sub(_autolink, safe)
    return safe


def release_notes_html(markdown: str) -> str:
    """Turn a GitHub release body into the small HTML subset Qt can show."""
    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parts: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            parts.append("</ul>")
            list_open = False

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            close_list()
            continue
        heading = ""
        if stripped.startswith("### "):
            heading = stripped[4:]
            tag = "h3"
        elif stripped.startswith("## "):
            heading = stripped[3:]
            tag = "h2"
        elif stripped.startswith("# "):
            heading = stripped[2:]
            tag = "h1"
        if heading:
            close_list()
            parts.append(f"<{tag}>{_inline(heading)}</{tag}>")
            continue
        bullet = ""
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullet = stripped[2:]
        else:
            numbered = _NUMBERED.fullmatch(stripped)
            if numbered:
                bullet = numbered.group(1)
        if bullet:
            if not list_open:
                parts.append("<ul>")
                list_open = True
            parts.append(f"<li>{_inline(bullet)}</li>")
            continue
        close_list()
        parts.append(f"<p>{_inline(stripped)}</p>")
    close_list()
    body = "\n".join(parts) or "<p>This release has no notes.</p>"
    return (
        "<html><head><style>"
        + _notes_css()
        + "</style></head><body>"
        + body
        + "</body></html>"
    )


def load_release_notes(repo: str, version: str) -> tuple[str, str]:
    """Return (heading, markdown) for the installed version."""
    shown = version.strip()
    if shown[:1] not in {"v", "V"}:
        shown = f"v{shown}" if shown else "this version"
    release = fetch_by_tag(repo, shown) if "/" in repo else None
    if not release:
        return shown, "Release notes could not be loaded from GitHub."
    label, released = describe_release(release)
    heading = f"{label} — released {released}" if released else label
    notes = release_notes_markdown(release)
    if not notes:
        notes = "This release has no notes."
    return heading, notes


class ReleaseNotesDialog(QDialog):
    """What's-new window. Stays open until the reader closes it."""

    def __init__(
        self,
        heading: str,
        notes_html: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TP DECK — What's new")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(440, 360)
        self.resize(520, 440)
        self.setStyleSheet(_dialog_qss())

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(heading)
        title.setObjectName("notesTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(self._open_link)
        browser.document().setDefaultStyleSheet(_notes_css())
        browser.setHtml(notes_html)
        layout.addWidget(browser)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _open_link(self, url: QUrl) -> None:
        if url.isValid():
            QDesktopServices.openUrl(url)


async def present_release_notes_after_update(parent: QWidget) -> None:
    """Open the notes once when a release install comes back on a new version.

    A git checkout never updates itself, so it does not show this window.
    The version is recorded either way, including when the setting is off,
    so turning the setting back on does not replay an old update.
    """
    try:
        await _present_release_notes(parent)
    except Exception:
        logger.exception("Release notes popup failed")


async def _present_release_notes(parent: QWidget) -> None:
    if not is_release_layout():
        return
    settings = load_settings()
    current = app_version()
    seen = str(settings.get("last_seen_version") or "").strip()
    if _same_version(seen, current):
        return
    if not bool(settings.get("show_release_notes", True)):
        update_settings(last_seen_version=current)
        logger.info("Release notes popup is off; marked %s seen", current)
        return
    repo = str(settings.get("update_repo") or "").strip()
    heading, notes = await asyncio.to_thread(load_release_notes, repo, current)
    dialog = ReleaseNotesDialog(heading, release_notes_html(notes), parent=parent)
    setattr(parent, "_release_notes_dialog", dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    update_settings(last_seen_version=current)
    logger.info("Showed release notes for %s", current)
