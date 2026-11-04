"""Chrome profile migration, the update pulse, and launch update order."""

import asyncio
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tp_deck.chrome_launcher import (
    _download_file,
    _is_version_dir,
    chrome_update_needed,
    deletion_targets_are_safe,
    install_chrome_zip,
    migrate_one_profile,
    migrate_profiles,
    migrate_profiles_safely,
    old_version_dirs,
    profile_copy_matches,
    remove_old_browsers,
    resolve_profile_dir,
    stable_chrome_download,
)
from tp_deck.theme import NEUTRAL
from tp_deck.updater import _HELPER_PS1


def _write_profile(root: Path, cookies: bytes = b"cookies", lock: bytes = b"lock") -> None:
    network = root / "Default" / "Network"
    network.mkdir(parents=True, exist_ok=True)
    (root / "Local State").write_text("state", encoding="utf-8")
    (network / "Cookies").write_bytes(cookies)
    (network / "Cookies-journal").write_text("journal", encoding="utf-8")
    (root / "SingletonLock").write_bytes(lock)


class ProfileMigration(unittest.TestCase):
    def test_copy_waits_for_local_state_and_cookie_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "browser" / "profile"
            dest = root / "chrome-profiles" / "profile"
            _write_profile(src, cookies=b"abc", lock=b"source-lock")
            self.assertEqual(migrate_one_profile(src, dest), "copied")
            self.assertFalse(src.exists())
            self.assertEqual((dest / "Local State").read_text(encoding="utf-8"), "state")
            self.assertEqual((dest / "Default" / "Network" / "Cookies").read_bytes(), b"abc")

            again = root / "browser" / "profile"
            _write_profile(again, cookies=b"abc", lock=b"other-lock")
            (dest / "SingletonLock").write_bytes(b"dest-lock-is-longer")
            self.assertTrue(profile_copy_matches(again, dest))
            self.assertEqual(migrate_one_profile(again, dest), "kept")
            self.assertEqual(
                (dest / "Default" / "Network" / "Cookies").read_bytes(),
                b"abc",
            )
            self.assertFalse(again.exists())

            _write_profile(again, cookies=b"newer", lock=b"x")
            self.assertEqual(migrate_one_profile(again, dest), "kept")
            self.assertEqual(
                (dest / "Default" / "Network" / "Cookies").read_bytes(),
                b"abc",
            )
            self.assertTrue(again.exists())

    def test_a_bad_copy_keeps_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "browser" / "profile"
            dest = root / "chrome-profiles" / "profile"
            _write_profile(src, cookies=b"secret")
            with mock.patch(
                "tp_deck.chrome_launcher.profile_copy_matches",
                return_value=False,
            ):
                self.assertEqual(migrate_one_profile(src, dest), "failed")
            self.assertEqual((src / "Default" / "Network" / "Cookies").read_bytes(), b"secret")
            self.assertFalse(dest.exists())

    def test_failed_migration_does_not_download_or_forget_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            browser = root / "browser"
            profiles = root / "chrome-profiles"
            src = browser / "profile"
            src.mkdir(parents=True)
            (src / "Preferences").write_text("only", encoding="utf-8")
            with (
                mock.patch("tp_deck.chrome_launcher.browser_dir", return_value=browser),
                mock.patch("tp_deck.chrome_launcher.profiles_root", return_value=profiles),
                mock.patch("tp_deck.chrome_launcher.remember_profile_root") as remember,
            ):
                self.assertFalse(migrate_profiles())
            remember.assert_not_called()
            self.assertTrue((src / "Preferences").is_file())
            self.assertFalse((profiles / "profile").exists())

    def test_open_browser_skips_the_copy(self):
        async def body():
            with (
                mock.patch(
                    "tp_deck.chrome_launcher.browsers_still_open",
                    mock.AsyncMock(return_value=True),
                ),
                mock.patch(
                    "tp_deck.chrome_launcher.close_listening_browsers",
                    mock.AsyncMock(),
                ),
                mock.patch("tp_deck.chrome_launcher.migrate_profiles") as migrate,
            ):
                ready = await migrate_profiles_safely({"mode": "single", "ebay_port": 9222})
            self.assertFalse(ready)
            migrate.assert_not_called()

        asyncio.run(body())


class ProfileGuard(unittest.TestCase):
    def test_a_profile_path_is_not_a_deletable_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "browser"
            profile = root / "profile"
            _write_profile(profile)
            old = root / "chromium-1148"
            old.mkdir(parents=True)
            nested = root / "chrome-131.0.0.0" / "profile"
            _write_profile(nested)
            (root / "chrome-profiles").mkdir()
            self.assertFalse(_is_version_dir("chrome-profiles"))
            self.assertTrue(_is_version_dir("chrome-131.0.0.0"))
            self.assertTrue(_is_version_dir("chromium-1148"))
            with mock.patch("tp_deck.chrome_launcher.browser_dir", return_value=root):
                self.assertFalse(deletion_targets_are_safe([old], [profile]))
                self.assertFalse(deletion_targets_are_safe([profile], [profile]))
                self.assertFalse(
                    deletion_targets_are_safe(
                        [root / "chrome-131.0.0.0"],
                        [nested],
                    )
                )
            self.assertNotIn(root / "chrome-profiles", old_version_dirs(root=root))

    def test_helper_skips_browser_and_chrome_profiles(self):
        self.assertIn("/XD browser chrome-profiles", _HELPER_PS1)
        self.assertEqual(_HELPER_PS1.count("chrome-profiles"), 3)
        gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("chrome-profiles/", gitignore)

    def test_missing_saved_path_reuses_an_existing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = root / "gone"
            reused = root / "chrome-profiles" / "profile"
            _write_profile(reused)
            browser_profile = root / "browser" / "profile"
            _write_profile(browser_profile)
            with (
                mock.patch(
                    "tp_deck.chrome_launcher.profiles_root",
                    return_value=root / "chrome-profiles",
                ),
                mock.patch("tp_deck.chrome_launcher.browser_dir", return_value=root / "browser"),
            ):
                found = resolve_profile_dir({"chrome_profile_dir": str(saved)}, "profile")
                self.assertEqual(found, reused)
                _write_profile(browser_profile.parent / "profile-erp")
                only_browser = resolve_profile_dir({}, "profile-erp")
                self.assertEqual(only_browser, root / "browser" / "profile-erp")
                empty = resolve_profile_dir({}, "profile-missing")
                self.assertEqual(empty, root / "chrome-profiles" / "profile-missing")
                self.assertFalse(empty.exists())
                self.assertFalse((saved / "profile").exists())


class ChromeInstall(unittest.TestCase):
    def test_revision_numbers_do_not_count_as_chrome(self):
        self.assertTrue(chrome_update_needed("1148", "131.0.6778.85"))
        self.assertTrue(chrome_update_needed("", "131.0.6778.85"))
        self.assertFalse(chrome_update_needed("131.0.6778.85", "131.0.6778.85"))
        self.assertTrue(chrome_update_needed("120.0.0.0", "131.0.6778.85"))
        version, url = stable_chrome_download(
            {
                "channels": {
                    "Beta": {
                        "version": "132.0.0.0",
                        "downloads": {
                            "chrome": [{"platform": "win64", "url": "http://example.invalid/beta"}]
                        },
                    },
                    "Stable": {
                        "version": "131.0.6778.85",
                        "downloads": {
                            "chrome": [
                                {"platform": "linux64", "url": "http://example.invalid/linux"},
                                {"platform": "win64", "url": "http://example.invalid/win"},
                            ]
                        },
                    },
                }
            }
        )
        self.assertEqual(version, "131.0.6778.85")
        self.assertEqual(url, "http://example.invalid/win")

    def test_zip_install_leaves_profiles_until_the_delete_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "browser"
            profile = root / "profile"
            _write_profile(profile)
            old = root / "chromium-1148"
            old.mkdir(parents=True)
            (old / "chrome.exe").write_bytes(b"old")
            zip_path = Path(tmp) / "chrome.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("chrome-win64/chrome.exe", b"new")
            exe = install_chrome_zip(zip_path, "131.0.6778.85", root)
            self.assertEqual(exe.read_bytes(), b"new")
            self.assertTrue((profile / "Local State").is_file())
            safe = Path(tmp) / "chrome-profiles" / "profile"
            _write_profile(safe)
            with mock.patch("tp_deck.chrome_launcher.browser_dir", return_value=root):
                self.assertFalse(remove_old_browsers(exe.parent, [profile], root))
                self.assertTrue(old.is_dir())
                self.assertTrue(remove_old_browsers(exe.parent, [safe], root))
            self.assertFalse(old.exists())
            self.assertTrue(profile.is_dir())
            self.assertTrue(exe.is_file())
            self.assertTrue((safe / "Local State").is_file())


class _FakeDownload:
    def __init__(self, payload: bytes, total: int | None = None):
        self._payload = payload
        self._offset = 0
        length = len(payload) if total is None else total
        self.headers = {} if length < 0 else {"Content-Length": str(length)}

    def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ChromeDownloadProgress(unittest.TestCase):
    def test_bytes_read_fill_a_known_download(self):
        seen: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chrome.zip"
            body = _FakeDownload(b"abcdefgh")
            with mock.patch(
                "tp_deck.chrome_launcher.urllib.request.urlopen",
                return_value=body,
            ):
                _download_file(
                    "http://example.invalid/chrome.zip",
                    dest,
                    lambda read, total: seen.append((read, total)),
                )
            self.assertEqual(dest.read_bytes(), b"abcdefgh")
        self.assertEqual(seen, [(0, 8), (8, 8)])

    def test_a_missing_length_reports_zero_total(self):
        seen: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "chrome.zip"
            body = _FakeDownload(b"abcdefgh", total=-1)
            with mock.patch(
                "tp_deck.chrome_launcher.urllib.request.urlopen",
                return_value=body,
            ):
                _download_file(
                    "http://example.invalid/chrome.zip",
                    dest,
                    lambda read, total: seen.append((read, total)),
                )
        self.assertEqual(seen, [(0, 0), (8, 0)])

    def test_the_status_bar_fills_with_the_chrome_download(self):
        from tp_deck.main import _chrome_download_progress

        class Dash:
            def __init__(self):
                self.progress: list[tuple] = []
                self.statuses: list[str] = []

            def set_status(self, text: str) -> None:
                self.statuses.append(text)

            def set_run_progress(self, locked: float, sheen) -> None:
                self.progress.append((locked, sheen))

        async def body():
            dash = Dash()
            report = _chrome_download_progress(dash, "131.0.6778.85")
            report(0, 1000)
            report(250, 1000)
            report(251, 1000)
            report(500, 1000)
            report(100, 0)
            for _ in range(5):
                await asyncio.sleep(0)
                if len(dash.progress) >= 4:
                    break
            return dash

        dash = asyncio.run(body())
        self.assertEqual(
            dash.statuses,
            ["Downloading Chrome 131.0.6778.85"] * 4,
        )
        self.assertEqual(
            dash.progress,
            [
                (0.0, None),
                (0.25, None),
                (0.5, None),
                (0.0, (0.0, 1.0)),
            ],
        )


class UpdateWarning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_app_chrome_and_both_use_different_pulses(self):
        from tp_deck.dashboard import Dashboard

        dashboard = Dashboard()
        dashboard._palette = NEUTRAL
        try:
            dashboard.begin_update_warning("app")
            dashboard._update_timer.stop()
            dashboard._paint_update_pulse(0.0)
            app_sheet = dashboard.execute_btn.styleSheet().lower()
            self.assertIn("#fbbf24", app_sheet)
            self.assertEqual(dashboard.pick_btn.styleSheet(), dashboard.execute_btn.styleSheet())
            self.assertEqual(dashboard.batch_btn.styleSheet(), dashboard.execute_btn.styleSheet())

            dashboard.begin_update_warning("chrome")
            dashboard._update_timer.stop()
            dashboard._paint_update_pulse(0.0)
            chrome_sheet = dashboard.execute_btn.styleSheet().lower()
            self.assertIn("#22c55e", chrome_sheet)
            self.assertNotIn("#fbbf24", chrome_sheet)

            dashboard.begin_update_warning("both")
            dashboard._update_timer.stop()
            now = time.monotonic()
            dashboard._update_started = now
            dashboard._paint_update_pulse(0.0)
            self.assertIn("#fbbf24", dashboard.execute_btn.styleSheet().lower())
            dashboard._update_started = now - 0.5
            dashboard._paint_update_pulse(0.0)
            self.assertIn("#22c55e", dashboard.execute_btn.styleSheet().lower())
        finally:
            dashboard.end_update_warning()
            dashboard.close()


class StartupOrder(unittest.TestCase):
    def test_app_update_returns_before_chrome_download(self):
        from tp_deck.main import _startup

        calls: list = []

        class Dash:
            def __init__(self):
                self.closed = False
                self._error = False
                self._status_text = ""
                self.statuses: list[str] = []

            def set_active(self, _active):
                return None

            def set_status(self, text):
                self._status_text = text
                self.statuses.append(text)
                self._error = str(text).startswith("Error")

            def clear_progress(self):
                return None

            def close(self):
                self.closed = True

        async def migrate(_settings):
            calls.append("migrate")
            return True

        def peek_app():
            calls.append("app")
            return ("TP DECK 9 — released today", "a.zip", "http://example.invalid/a.zip", {})

        async def peek_chrome(_settings):
            calls.append("chrome")
            return ("131.0.6778.85", "http://example.invalid/chrome.zip")

        async def confirm(_dashboard, label, kind="app"):
            calls.append(("confirm", kind, label))
            return False

        async def download_app(_dashboard, _pending):
            calls.append("download-app")

        async def download_browser(*_args, **_kwargs):
            calls.append("download-chrome")
            return None

        async def prepare(_dashboard):
            calls.append("prepare")

        async def notes(_dashboard):
            calls.append("notes")

        dash = Dash()

        async def body():
            with (
                mock.patch("tp_deck.main.migrate_profiles_safely", migrate),
                mock.patch("tp_deck.main.peek_app_release", peek_app),
                mock.patch("tp_deck.main.peek_chrome_update", peek_chrome),
                mock.patch("tp_deck.main._confirm_update", confirm),
                mock.patch("tp_deck.main._download_app_release", download_app),
                mock.patch("tp_deck.main.download_chrome", download_browser),
                mock.patch("tp_deck.main._prepare_chromium", prepare),
                mock.patch("tp_deck.main.present_release_notes_after_update", notes),
            ):
                await _startup(dash)

        asyncio.run(body())
        self.assertEqual(
            calls,
            [
                "migrate",
                "app",
                "chrome",
                (
                    "confirm",
                    "both",
                    "TP DECK 9 — released today and Chrome 131.0.6778.85",
                ),
                "download-app",
            ],
        )
        self.assertTrue(dash.closed)
        self.assertNotIn("download-chrome", calls)
        self.assertNotIn("prepare", calls)

    def test_failed_chrome_check_still_opens(self):
        from tp_deck.main import _startup

        calls: list[str] = []

        class Dash:
            def __init__(self):
                self.closed = False
                self._error = False
                self._status_text = ""
                self.statuses: list[str] = []

            def set_active(self, _active):
                return None

            def set_status(self, text):
                self._status_text = text
                self.statuses.append(text)
                self._error = str(text).startswith("Error")

            def clear_progress(self):
                return None

            def set_actions_enabled(self, _enabled):
                return None

            def close(self):
                self.closed = True

        async def migrate(_settings):
            calls.append("migrate")
            return True

        def peek_app():
            calls.append("app")
            return None

        async def peek_chrome(_settings):
            calls.append("chrome")
            raise RuntimeError("offline")

        async def download_browser(*_args, **_kwargs):
            calls.append("download-chrome")
            return None

        async def prepare(dashboard):
            calls.append("prepare")
            dashboard.set_status("Idle")

        async def notes(_dashboard):
            calls.append("notes")

        dash = Dash()

        async def body():
            with (
                mock.patch("tp_deck.main.migrate_profiles_safely", migrate),
                mock.patch("tp_deck.main.peek_app_release", peek_app),
                mock.patch("tp_deck.main.peek_chrome_update", peek_chrome),
                mock.patch("tp_deck.main.download_chrome", download_browser),
                mock.patch("tp_deck.main._prepare_chromium", prepare),
                mock.patch("tp_deck.main.present_release_notes_after_update", notes),
            ):
                await _startup(dash)

        asyncio.run(body())
        self.assertEqual(calls, ["migrate", "app", "chrome", "prepare", "notes"])
        self.assertFalse(dash.closed)
        self.assertEqual(
            dash.statuses[-1],
            "Chrome update check failed — opening the current browser",
        )
        self.assertNotIn("download-chrome", calls)

    def test_unmigrated_profiles_skip_the_chrome_download(self):
        from tp_deck.main import _startup

        calls: list[str] = []

        class Dash:
            def __init__(self):
                self._error = False
                self._status_text = ""

            def set_active(self, _active):
                return None

            def set_status(self, text):
                self._status_text = text
                self._error = str(text).startswith("Error")

            def clear_progress(self):
                return None

            def close(self):
                calls.append("close")

        async def migrate(_settings):
            calls.append("migrate")
            return False

        def peek_app():
            calls.append("app")
            return None

        async def peek_chrome(_settings):
            calls.append("chrome")
            return ("131.0.6778.85", "http://example.invalid/chrome.zip")

        async def download_browser(*_args, **_kwargs):
            calls.append("download-chrome")
            return None

        async def prepare(_dashboard):
            calls.append("prepare")

        async def notes(_dashboard):
            return None

        async def body():
            with (
                mock.patch("tp_deck.main.migrate_profiles_safely", migrate),
                mock.patch("tp_deck.main.peek_app_release", peek_app),
                mock.patch("tp_deck.main.peek_chrome_update", peek_chrome),
                mock.patch("tp_deck.main.download_chrome", download_browser),
                mock.patch("tp_deck.main._prepare_chromium", prepare),
                mock.patch("tp_deck.main.present_release_notes_after_update", notes),
            ):
                await _startup(Dash())

        asyncio.run(body())
        self.assertEqual(calls, ["migrate", "app", "prepare"])
