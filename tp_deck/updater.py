"""Check the public GitHub Releases feed and stage an in-place upgrade.

Only a release install (python\\pythonw.exe beside TP-DECK.bat) updates itself.
A git checkout is left alone. The running process cannot overwrite its own
interpreter, so a hidden PowerShell helper copies the files after this process exits.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from tp_deck import __version__
from tp_deck.chrome_launcher import app_root

logger = logging.getLogger("tpdeck")

_API_TIMEOUT_S = 8.0
_CHUNK_TIMEOUT_S = 60.0
_DOWNLOAD_DEADLINE_S = 15 * 60
_USER_AGENT = "TP-DECK"
_ASSET_SUFFIX = "-windows-x64.zip"

# CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP.
# DETACHED_PROCESS is left off. With no console, child programs such as
# find.exe and timeout.exe open their own window and wait for keyboard input.
_HELPER_FLAGS = 0x08000000 | 0x00000200

_HELPER_PS1 = r"""
param(
  [Parameter(Mandatory = $true)][int]$WaitPid,
  [Parameter(Mandatory = $true)][string]$Source,
  [Parameter(Mandatory = $true)][string]$Dest,
  [Parameter(Mandatory = $true)][string]$Stage
)
$ErrorActionPreference = 'Continue'
$marker = Join-Path $env:TEMP 'tpdeck-update-last.txt'

function Note([string]$msg) {
  $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' [INFO] tpdeck: ' + $msg
  Add-Content -LiteralPath $marker -Value $line -ErrorAction SilentlyContinue
}

function Rel([string]$root, [string]$full) {
  $base = $root.TrimEnd('\') + '\'
  if ($full.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) {
    return $full.Substring($base.Length)
  }
  try {
    $rootUri = New-Object System.Uri ($base)
    $fullUri = New-Object System.Uri ($full)
    $rel = [System.Uri]::UnescapeDataString($rootUri.MakeRelativeUri($fullUri).ToString())
    return ($rel -replace '/', '\')
  } catch {
    return ''
  }
}

Note ("helper start wait=" + $WaitPid)
try { Wait-Process -Id $WaitPid -ErrorAction Stop } catch { Note ("wait ended: " + $_.Exception.Message) }
Start-Sleep -Seconds 2

$destRoot = [System.IO.Path]::GetFullPath($Dest).TrimEnd('\')
$keepNames = @('settings.json', 'sku_overrides.json', 'sku_location_cache.json', 'uline_cache.json', 'uline_draft.json')
$keep = Join-Path $env:TEMP ("tpdeck-keep-" + $WaitPid)
$code = 16
try {
  if (Test-Path -LiteralPath $keep) { Remove-Item -LiteralPath $keep -Recurse -Force -ErrorAction SilentlyContinue }
  New-Item -ItemType Directory -Path $keep -Force | Out-Null
  Get-ChildItem -LiteralPath $destRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
    ($keepNames -contains $_.Name) -and ($_.FullName -notmatch '\\browser\\')
  } | ForEach-Object {
    $rel = Rel $destRoot $_.FullName
    if (-not $rel) { return }
    $out = Join-Path $keep $rel
    $dir = Split-Path -Parent $out
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item -LiteralPath $_.FullName -Destination $out -Force
  }

  $attempt = 0
  do {
    $attempt++
    & robocopy $Source $destRoot /E /NFL /NDL /NJH /NJS /R:2 /W:1 /XD browser /XF settings.json sku_overrides.json sku_location_cache.json uline_cache.json uline_draft.json tpdeck.log tpdeck.log.1 tpdeck.log.2 tpdeck.log.3 tpdeck.log.4 tpdeck.log.5
    if ($null -ne $LASTEXITCODE) { $code = $LASTEXITCODE } else { $code = 0 }
    if ($code -lt 8) { break }
    Note ("robocopy attempt " + $attempt + " code " + $code)
    Start-Sleep -Seconds 2
  } while ($attempt -lt 5)

  Get-ChildItem -LiteralPath $keep -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
    $rel = Rel $keep $_.FullName
    if (-not $rel) { return }
    $out = Join-Path $destRoot $rel
    $dir = Split-Path -Parent $out
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item -LiteralPath $_.FullName -Destination $out -Force
  }
} catch {
  Note ("copy failed: " + $_.Exception.Message)
} finally {
  Remove-Item -LiteralPath $keep -Recurse -Force -ErrorAction SilentlyContinue
}

Get-ChildItem -LiteralPath $destRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
  $_.FullName -notmatch '\\browser\\'
} | Unblock-File -ErrorAction SilentlyContinue

$log = Join-Path $destRoot 'tp_deck\tpdeck.log'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Add-Content -LiteralPath $log -Value "$stamp [INFO] tpdeck: Update copy finished with robocopy code $code" -ErrorAction SilentlyContinue
Note ("robocopy code " + $code)

$bat = Join-Path $destRoot 'TP-DECK.bat'
if (Test-Path -LiteralPath $bat) {
  Start-Process -FilePath $bat -WorkingDirectory $destRoot
  Note 'relaunched'
} else {
  Note 'TP-DECK.bat missing; did not relaunch'
}
if ($code -ge 8) { Note 'update copy incomplete; previous files were left in place where the copy failed' }
if ($Stage -like '*tpdeck-update-*') { Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue }
exit 0
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


def fetch_by_tag(repo: str, tag: str) -> Optional[dict[str, Any]]:
    """Release payload for one tag, such as v0.3.4. None when the lookup fails."""
    slug = repo.strip().strip("/")
    wanted = tag.strip()
    if wanted[:1] not in {"v", "V"}:
        wanted = "v" + wanted
    if not slug or wanted == "v":
        return None
    safe = urllib.parse.quote(wanted, safe="")
    url = f"https://api.github.com/repos/{slug}/releases/tags/{safe}"
    try:
        with _open(url, _API_TIMEOUT_S) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.info("Release notes lookup failed: %s", exc)
        return None
    if not isinstance(payload, dict):
        logger.info("Release notes lookup returned an unexpected response")
        return None
    return payload


def release_notes_markdown(release: dict[str, Any]) -> str:
    """The release body with GitHub's HTML comments removed."""
    body = str(release.get("body") or "").replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return body.strip()


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


def describe_release(release: dict[str, Any]) -> tuple[str, str]:
    """Return (v1.2.3, Oct 14, 2026). The date is empty when the feed omits it."""
    tag = str(release.get("tag_name") or "").strip()
    if not tag:
        shown = "the new release"
    elif tag[0] in {"v", "V"}:
        shown = "v" + tag[1:]
    else:
        shown = "v" + tag
    published = str(release.get("published_at") or "").strip()
    if not published:
        return shown, ""
    try:
        when = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        return shown, ""
    return shown, f"{when.strftime('%b')} {when.day}, {when.year}"


def asset_size(release: dict[str, Any], name: str) -> int:
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and str(asset.get("name") or "") == name:
            try:
                return max(0, int(asset.get("size") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _download(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


def download_zip(
    url: str,
    dest: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    total_hint: int = 0,
) -> None:
    deadline = time.monotonic() + _DOWNLOAD_DEADLINE_S
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _download(url, _CHUNK_TIMEOUT_S) as response, dest.open("wb") as handle:
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            total = max(0, int(total_hint or 0))
        read = 0
        if on_progress is not None:
            on_progress(0, total)
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("Update download timed out.")
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            read += len(chunk)
            if on_progress is not None:
                on_progress(read, total)
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
    """Copy the payload after this process exits, without a visible console."""
    helper = Path(tempfile.gettempdir()) / "tpdeck-update.ps1"
    helper.write_text(_HELPER_PS1, encoding="ascii")
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-WaitPid",
            str(os.getpid()),
            "-Source",
            str(source),
            "-Dest",
            str(dest),
            "-Stage",
            str(stage),
        ],
        creationflags=_HELPER_FLAGS,
        cwd=str(helper.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def download_and_spawn(
    name: str,
    url: str,
    root: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    total_hint: int = 0,
) -> None:
    """Download beside the install, in temp, so the copy does not recurse into itself."""
    stage = Path(tempfile.mkdtemp(prefix="tpdeck-update-"))
    zip_path = stage / name
    try:
        download_zip(url, zip_path, on_progress=on_progress, total_hint=total_hint)
        payload = extract_payload(zip_path, stage / "unpack")
        spawn_helper(payload, root, stage)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
