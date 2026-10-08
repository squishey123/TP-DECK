"""Check the public GitHub Releases feed and stage an in-place upgrade.

Only a release install (python\\pythonw.exe beside TP-DECK.bat) updates itself.
A git checkout is left alone. The running process cannot overwrite its own
interpreter, so a detached cmd helper copies the files after this process exits.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

from tp_deck import __version__
from tp_deck.chrome_launcher import app_root

logger = logging.getLogger("tpdeck")

_API_TIMEOUT_S = 8.0
_CHUNK_TIMEOUT_S = 60.0
_DOWNLOAD_DEADLINE_S = 15 * 60
_USER_AGENT = "TP-DECK"
_ASSET_SUFFIX = "-windows-x64.zip"

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
_HELPER_FLAGS = 0x00000008 | 0x00000200 | 0x08000000

_HELPER_BAT = """\
@echo off
setlocal
set "PID=%~1"
set "SRC=%~2"
set "DEST=%~3"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find " %PID% " >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
robocopy "%SRC%" "%DEST%" /E /NFL /NDL /NJH /NJS /XD browser /XF settings.json sku_overrides.json sku_location_cache.json tpdeck.log
>> "%DEST%\\tp_deck\\tpdeck.log" echo Update copy finished with robocopy code %ERRORLEVEL%
powershell -NoProfile -Command "Get-ChildItem -LiteralPath $env:DEST -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\\\browser\\\\' } | Unblock-File"
if exist "%DEST%\\TP-DECK.bat" start "" "%DEST%\\TP-DECK.bat"
echo %~4 | find /I "tpdeck-update-" >nul
if not errorlevel 1 rmdir /s /q "%~4"
exit /b 0
"""


def parse_version(text: str) -> tuple[int, ...]:
    """Turn 'v0.2.10' into (0, 2, 10). Non-numeric tails are ignored."""
    raw = str(text or "").strip()
    if raw[:1] in {"v", "V"}:
        raw = raw[1:]
    parts: list[int] = []
    for piece in raw.split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def is_release_layout(root: Optional[Path] = None) -> bool:
    base = app_root() if root is None else root
    return (base / "python" / "pythonw.exe").is_file()


def _open(url: str, timeout: float):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_latest(repo: str) -> Optional[dict[str, Any]]:
    slug = repo.strip().strip("/")
    url = f"https://api.github.com/repos/{slug}/releases/latest"
    try:
        with _open(url, _API_TIMEOUT_S) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.info("Update check failed: %s", exc)
        return None
    if not isinstance(payload, dict):
        logger.info("Update check returned an unexpected response")
        return None
    return payload


def find_windows_asset(release: dict[str, Any]) -> Optional[tuple[str, str]]:
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        download = str(asset.get("browser_download_url") or "")
        if name.startswith("TP-DECK-") and name.endswith(_ASSET_SUFFIX) and download:
            return name, download
    return None


def _download(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def download_zip(url: str, dest: Path) -> None:
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_S
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _download(url, _CHUNK_TIMEOUT_S) as response, dest.open("wb") as handle:
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("Update download timed out.")
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    if dest.stat().st_size < 1024 * 1024:
        raise RuntimeError("Update download was incomplete.")


def extract_payload(zip_path: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    nested = dest / "TP-DECK"
    if (nested / "TP-DECK.bat").is_file():
        return nested
    if (dest / "TP-DECK.bat").is_file():
        return dest
    for child in dest.iterdir():
        if child.is_dir() and (child / "TP-DECK.bat").is_file():
            return child
    raise RuntimeError("Update zip did not contain TP-DECK.bat.")


def spawn_helper(source: Path, dest: Path, stage: Path) -> None:
    helper = Path(tempfile.gettempdir()) / "tpdeck-update.bat"
    helper.write_text(_HELPER_BAT, encoding="ascii")
    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            str(helper),
            str(os.getpid()),
            str(source),
            str(dest),
            str(stage),
        ],
        creationflags=_HELPER_FLAGS,
        cwd=str(helper.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def download_and_spawn(name: str, url: str, root: Path) -> None:
    """Download beside the install, in temp, so the copy does not recurse into itself."""
    stage = Path(tempfile.mkdtemp(prefix="tpdeck-update-"))
    zip_path = stage / name
    try:
        download_zip(url, zip_path)
        payload = extract_payload(zip_path, stage / "unpack")
        spawn_helper(payload, root, stage)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
