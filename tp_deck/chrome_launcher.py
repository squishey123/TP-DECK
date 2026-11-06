"""Start the portable Chrome debug browser when TP DECK opens.

Playwright still only attaches with connect_over_cdp. Chrome for Testing
lives under browser/chrome-<version>/. Logins live in chrome-profiles/,
outside that folder, so an update can replace the binary without them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from tp_deck.settings_manager import load_settings, update_settings

logger = logging.getLogger("tpdeck")

CFT_STABLE_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)

_PORT_READY_TIMEOUT_S = 45.0
_CHROME_DOWNLOAD_DEADLINE_S = 10 * 60.0
_MINIMIZE_FOR_S = 4.0
_MINIMIZE_INTERVAL_S = 0.25

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_SW_SHOWMINNOACTIVE = 7
_TH32CS_SNAPPROCESS = 0x00000002


def app_root() -> Path:
    """Directory that contains TP-DECK.bat and the tp_deck package."""
    return Path(__file__).resolve().parent.parent


def browser_dir() -> Path:
    """Portable Chrome binaries, next to the app. Profiles do not live here."""
    return app_root() / "browser"


def profiles_root() -> Path:
    """Permanent Chrome user-data directories, outside the binary install."""
    return app_root() / "chrome-profiles"


def _is_version_dir(name: str) -> bool:
    """True for chrome-131.0.x or chromium-1148, not for chrome-profiles."""
    if name.startswith("chromium-"):
        return True
    if name.startswith("chrome-") and len(name) > len("chrome-"):
        return name[len("chrome-")].isdigit()
    return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def profile_is_inside_browser_tree(path: Path) -> bool:
    """True when this folder would be replaced with a Chrome binary."""
    resolved = path.resolve()
    if _is_relative_to(resolved, browser_dir()):
        return True
    for parent in (resolved, *resolved.parents):
        if _is_version_dir(parent.name):
            return True
    return False


def profiles_block_chrome_update(profiles: list[Path], delete_dirs: list[Path]) -> bool:
    """Refuse a binary replace while a profile sits in the install or a delete target."""
    for profile in profiles:
        if not profile.exists():
            continue
        if profile_is_inside_browser_tree(profile):
            return True
        resolved = profile.resolve()
        for directory in delete_dirs:
            if _is_relative_to(resolved, directory) or resolved == directory.resolve():
                return True
    return False


def deletion_targets_are_safe(targets: list[Path], profiles: list[Path]) -> bool:
    """A Chrome update may delete only versioned binary folders."""
    if profiles_block_chrome_update(profiles, targets):
        return False
    for target in targets:
        if not target.is_dir() or not _is_version_dir(target.name):
            return False
    return True


def _cookies_file(root: Path) -> Optional[Path]:
    for relative in (
        Path("Default") / "Network" / "Cookies",
        Path("Default") / "Cookies",
    ):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def profile_copy_matches(src: Path, dest: Path) -> bool:
    """Local State is present, and Cookies matches in size when the source has it.

    A lock or journal file that differs does not fail the copy.
    """
    if not (src / "Local State").is_file() or not (dest / "Local State").is_file():
        return False
    src_cookies = _cookies_file(src)
    if src_cookies is None:
        return True
    dest_cookies = dest / src_cookies.relative_to(src)
    return dest_cookies.is_file() and dest_cookies.stat().st_size == src_cookies.stat().st_size


def migrate_one_profile(src: Path, dest: Path) -> str:
    """Copy one profile out of the browser install.

    Returns kept, copied, missing, or failed. An existing destination is not
    touched. A failed copy leaves the source in place and removes a partial dest.
    """
    if dest.exists():
        if src.exists() and profile_copy_matches(src, dest):
            try:
                shutil.rmtree(src)
            except OSError:
                logger.exception("Copied Chrome profile but could not remove %s", src)
        return "kept"
    if not src.exists():
        return "missing"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
    except OSError:
        logger.exception("Could not copy Chrome profile %s", src)
        shutil.rmtree(dest, ignore_errors=True)
        return "failed"
    if not profile_copy_matches(src, dest):
        shutil.rmtree(dest, ignore_errors=True)
        logger.error("Chrome profile copy %s did not match; the original was kept", src)
        return "failed"
    try:
        shutil.rmtree(src)
    except OSError:
        logger.exception("Copied Chrome profile but could not remove %s", src)
        return "failed"
    return "copied"


def migrate_profiles() -> bool:
    """Move browser/profile and browser/profile-erp into chrome-profiles.

    False means a copy failed, so a Chrome download must not run.
    """
    ready = True
    for name in ("profile", "profile-erp"):
        result = migrate_one_profile(browser_dir() / name, profiles_root() / name)
        if result == "failed":
            ready = False
    if ready:
        remember_profile_root()
    return ready


def remember_profile_root() -> None:
    """Store the absolute profile folder. Settings survive an app update."""
    root = str(profiles_root().resolve())
    if str(load_settings().get("chrome_profile_dir") or "") == root:
        return
    update_settings(chrome_profile_dir=root)
    logger.info("Chrome profiles are in %s", root)


def resolve_profile_dir(settings: dict[str, Any], name: str) -> Path:
    """The profile to launch. An empty folder is used only when none exists yet."""
    saved = str(settings.get("chrome_profile_dir") or "").strip()
    candidates: list[Path] = []
    if saved:
        candidates.append(Path(saved) / name)
    candidates.append(profiles_root() / name)
    candidates.append(browser_dir() / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    parent = Path(saved) if saved else profiles_root()
    return parent / name


def _chrome_version_tuple(text: str) -> Optional[tuple[int, ...]]:
    from tp_deck.updater import parse_version

    parts = parse_version(text)
    if len(parts) < 3:
        return None
    return parts


def chrome_update_needed(installed: str, stable: str) -> bool:
    """True when the installed Chrome is missing or older than stable.

    A single Playwright revision number is not a Chrome version, so it counts
    as not installed.
    """
    remote = _chrome_version_tuple(stable)
    if remote is None:
        return False
    current = _chrome_version_tuple(installed)
    if current is None:
        return True
    return remote > current


def stable_chrome_download(payload: dict[str, Any]) -> tuple[str, str]:
    """Stable Chrome for Testing, Windows 64-bit. Other channels are ignored."""
    channels = payload.get("channels") if isinstance(payload, dict) else None
    stable = channels.get("Stable") if isinstance(channels, dict) else None
    if not isinstance(stable, dict):
        raise RuntimeError("Chrome for Testing did not list a stable build.")
    version = str(stable.get("version") or "").strip()
    downloads = stable.get("downloads") if isinstance(stable.get("downloads"), dict) else {}
    chrome = downloads.get("chrome") if isinstance(downloads, dict) else None
    if not version or not isinstance(chrome, list):
        raise RuntimeError("Chrome for Testing did not list a stable build.")
    for item in chrome:
        if isinstance(item, dict) and item.get("platform") == "win64":
            url = str(item.get("url") or "").strip()
            if url:
                return version, url
    raise RuntimeError("Chrome for Testing stable has no Windows 64-bit build.")


def exe_product_version(path: Optional[Path]) -> str:
    """Product version from the executable, or blank when it cannot be read."""
    if path is None or not path.is_file() or sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buf):
            return ""
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(
            buf,
            "\\VarFileInfo\\Translation",
            ctypes.byref(ptr),
            ctypes.byref(length),
        ):
            return ""
        pair = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        key = f"\\StringFileInfo\\{pair[0]:04x}{pair[1]:04x}\\ProductVersion"
        if not version.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(length)):
            return ""
        raw = ctypes.wstring_at(ptr, length.value)
    except (OSError, AttributeError, ValueError):
        return ""
    match = re.search(r"\d+(?:\.\d+){2,}", raw or "")
    return match.group(0) if match else ""


def find_chromium_executable(root: Optional[Path] = None) -> Optional[Path]:
    """Return the newest installed Playwright Chromium chrome.exe, if present."""
    base = browser_dir() if root is None else root
    if not base.is_dir():
        return None
    candidates: list[Path] = []
    for marker in base.glob("chromium-*/INSTALLATION_COMPLETE"):
        exe = marker.parent / "chrome-win64" / "chrome.exe"
        if exe.is_file():
            candidates.append(exe)
    if not candidates:
        candidates = [
            path
            for path in base.glob("chromium-*/chrome-win64/chrome.exe")
            if path.is_file()
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def find_installed_chrome(root: Optional[Path] = None) -> Optional[Path]:
    """chrome-<version>/chrome.exe, or an older Playwright chromium build."""
    base = browser_dir() if root is None else root
    if not base.is_dir():
        return None
    candidates = [path for path in base.glob("chrome-*/chrome.exe") if path.is_file()]
    if candidates:
        return max(
            candidates,
            key=lambda path: _chrome_version_tuple(path.parent.name[len("chrome-") :])
            or (0,),
        )
    return find_chromium_executable(base)


def old_version_dirs(keep: Optional[Path] = None, root: Optional[Path] = None) -> list[Path]:
    """Versioned binary folders. The folder that holds the new chrome.exe stays."""
    base = browser_dir() if root is None else root
    if not base.is_dir():
        return []
    keep_resolved = keep.resolve() if keep is not None else None
    found: list[Path] = []
    for child in base.iterdir():
        if not child.is_dir() or not _is_version_dir(child.name):
            continue
        if keep_resolved is not None and child.resolve() == keep_resolved:
            continue
        found.append(child)
    return found


def remove_old_browsers(keep: Path, profiles: list[Path], root: Optional[Path] = None) -> bool:
    """Delete old version folders only. Returns False when that would be unsafe."""
    targets = old_version_dirs(keep, root)
    if not deletion_targets_are_safe(targets, profiles):
        logger.warning("Chrome update left old binaries in place to protect the profiles")
        return False
    for target in targets:
        shutil.rmtree(target)
        logger.info("Removed old Chrome build %s", target)
    return True


def _launch_targets(settings: dict[str, Any]) -> list[tuple[int, Path]]:
    ebay_port = int(settings.get("ebay_port", 9222))
    targets = [(ebay_port, resolve_profile_dir(settings, "profile"))]
    if str(settings.get("mode", "single")).lower() == "dual":
        erp_port = int(settings.get("erp_port", 9223))
        if erp_port != ebay_port:
            targets.append((erp_port, resolve_profile_dir(settings, "profile-erp")))
    return targets


def launch_ports(settings: dict[str, Any]) -> list[int]:
    return [port for port, _profile in _launch_targets(settings)]


def _debug_port_ready(port: int) -> bool:
    url = f"http://127.0.0.1:{int(port)}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=0.4) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _pid_from_tcp_table(port: int) -> Optional[int]:
    """Listener PID from the Windows TCP table. Independent of netstat's language."""
    if sys.platform != "win32":
        return None

    import ctypes
    import socket
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi")
    no_error = 0
    insufficient = 122
    af_inet = 2
    af_inet6 = 23
    listeners = 3

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    ]
    get_table.restype = wintypes.DWORD
    wanted = int(port)

    def _scan(family: int, row_type: type) -> Optional[int]:
        size = wintypes.DWORD(0)
        status = get_table(None, ctypes.byref(size), False, family, listeners, 0)
        if status not in (insufficient, no_error) or not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        status = get_table(buf, ctypes.byref(size), False, family, listeners, 0)
        if status != no_error:
            return None
        count = int(ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value)
        row_size = ctypes.sizeof(row_type)
        base = ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD)
        for index in range(count):
            row = row_type.from_address(base + index * row_size)
            local = socket.ntohs(int(row.dwLocalPort) & 0xFFFF)
            owner = int(row.dwOwningPid)
            if local == wanted and owner > 0:
                return owner
        return None

    return _scan(af_inet, MIB_TCPROW_OWNER_PID) or _scan(af_inet6, MIB_TCP6ROW_OWNER_PID)


def _pid_from_netstat(port: int) -> Optional[int]:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    wanted = str(int(port))
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[3].upper() != "LISTENING":
            continue
        _host, _sep, local_port = parts[1].rpartition(":")
        if local_port != wanted:
            continue
        try:
            return int(parts[4])
        except ValueError:
            continue
    return None


def pid_listening_on(port: int) -> Optional[int]:
    """PID of the process listening on this TCP port."""
    if sys.platform != "win32":
        return None
    return _pid_from_tcp_table(port) or _pid_from_netstat(port)


def request_process_close(pid: int) -> int:
    """Post WM_CLOSE to that process's visible windows. Returns how many."""
    if sys.platform != "win32" or pid <= 0:
        return 0

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    posted = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal posted
        if not user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) != pid:
            return True
        if user32.PostMessageW(hwnd, 0x0010, 0, 0):
            posted += 1
        return True

    user32.EnumWindows.argtypes = [type(_callback), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(_callback, 0)
    return posted


def _taskkill_gentle(pid: int) -> None:
    """Ask Windows to close the process. No /F, so Chrome can write its session."""
    if sys.platform != "win32" or pid <= 0:
        return
    subprocess.run(
        ["taskkill", "/PID", str(int(pid))],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def wait_until_ports_closed(ports: list[int], timeout_s: float = 20.0) -> bool:
    """True once none of the debug ports are still answering."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready = [port for port in ports if await asyncio.to_thread(_debug_port_ready, port)]
        if not ready:
            return True
        await asyncio.sleep(0.25)
    return False


async def close_listening_browsers(ports: list[int]) -> None:
    """Close Chromium the way the window X does, after DevTools has disconnected.

    CDP Browser.close while a debugger is attached leaves the profile marked
    crashed, so the next launch asks the user to restore tabs by hand.
    """
    pids: list[int] = []
    for port in ports:
        pid = await asyncio.to_thread(pid_listening_on, port)
        if pid is None:
            logger.warning("No process is listening on debug port %s", port)
            continue
        if pid not in pids:
            pids.append(pid)
            logger.info("Chromium debug port %s is pid %s", port, pid)
    for pid in pids:
        count = await asyncio.to_thread(request_process_close, pid)
        logger.info("Posted close to %s window(s) for pid %s", count, pid)
    if not ports:
        return
    if await wait_until_ports_closed(ports, timeout_s=8):
        logger.info("Chromium exited cleanly")
        return
    for pid in pids:
        logger.info("Chromium pid %s is still up; asking Windows to close it", pid)
        try:
            await asyncio.to_thread(_taskkill_gentle, pid)
        except subprocess.TimeoutExpired:
            logger.warning("Close request for pid %s timed out", pid)
    if await wait_until_ports_closed(ports, timeout_s=12):
        logger.info("Chromium exited cleanly")
        return
    logger.warning(
        "Chromium is still listening after the close request. "
        "It was left running so the session is not force-killed."
    )


def _spawn_chromium(exe: Path, port: int, profile: Path) -> subprocess.Popen[bytes]:
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(exe),
        f"--remote-debugging-port={int(port)}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
        "--start-minimized",
    ]
    flags = 0
    if sys.platform == "win32":
        flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    logger.info("Starting Chromium on port %s (profile %s)", port, profile)
    return subprocess.Popen(
        args,
        cwd=str(exe.parent),
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    proc.kill()


async def _wait_for_port(port: int, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + _PORT_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if await asyncio.to_thread(_debug_port_ready, port):
            logger.info("Chromium debug port %s is ready", port)
            return
        if proc.poll() is not None:
            raise RuntimeError(
                f"Chromium exited before debug port {port} opened "
                f"(code {proc.returncode})."
            )
        await asyncio.sleep(0.3)
    _stop_process(proc)
    raise RuntimeError(
        f"Chromium did not open debug port {port}. Close anything already using that port and open TP DECK again."
    )


def _process_tree(root_pid: int) -> set[int]:
    """Root pid plus descendants. Falls back to the root pid if the snapshot fails."""
    if root_pid <= 0 or sys.platform != "win32":
        return {root_pid} if root_pid > 0 else set()

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid:
        return {root_pid}

    children: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parent = int(entry.th32ParentProcessID)
            pid = int(entry.th32ProcessID)
            children.setdefault(parent, []).append(pid)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    found = {root_pid}
    stack = [root_pid]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _minimize_windows(pids: set[int]) -> int:
    """Minimize visible top-level windows owned by these pids. Returns how many."""
    if sys.platform != "win32" or not pids:
        return 0

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    minimized = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal minimized
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) in pids:
            user32.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)
            minimized += 1
        return True

    user32.EnumWindows.argtypes = [type(_callback), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(_callback, 0)
    return minimized


def _minimize_process_tree(root_pid: int) -> int:
    return _minimize_windows(_process_tree(root_pid))


def capture_foreground() -> tuple[int, dict[int, bool]]:
    """Foreground window, plus whether each top-level window is minimized."""
    if sys.platform != "win32":
        return 0, {}

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    previous = int(user32.GetForegroundWindow() or 0)
    iconic: dict[int, bool] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd: int, _lparam: int) -> bool:
        iconic[int(hwnd)] = bool(user32.IsIconic(hwnd))
        return True

    user32.EnumWindows.argtypes = [type(_callback), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(_callback, 0)
    return previous, iconic


def restore_foreground(previous: int, iconic: dict[int, bool]) -> None:
    """Return focus to the window that was in front, and re-minimize a Chromium that popped up."""
    if sys.platform != "win32" or not previous:
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    current = int(user32.GetForegroundWindow() or 0)
    if current and current != previous and iconic.get(current, False):
        user32.ShowWindow(current, _SW_SHOWMINNOACTIVE)
    now = int(user32.GetForegroundWindow() or 0)
    if now == previous:
        return
    pid = wintypes.DWORD()
    fg_thread = int(user32.GetWindowThreadProcessId(now or current, ctypes.byref(pid)) or 0)
    our_thread = int(kernel32.GetCurrentThreadId() or 0)
    attached = False
    if fg_thread and our_thread and fg_thread != our_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, our_thread, True))
    user32.SetForegroundWindow(previous)
    if attached:
        user32.AttachThreadInput(fg_thread, our_thread, False)


async def _keep_minimized(root_pid: int) -> None:
    """Keep a Chromium we just started in the taskbar.

    --start-minimized is not always enough: session restore can show the last
    window a moment later. Retry only for the process this open spawned.
    """
    if sys.platform != "win32" or root_pid <= 0:
        return
    deadline = time.monotonic() + _MINIMIZE_FOR_S
    while time.monotonic() < deadline:
        count = await asyncio.to_thread(_minimize_process_tree, root_pid)
        if count:
            logger.debug("Minimized %s Chromium window(s)", count)
        await asyncio.sleep(_MINIMIZE_INTERVAL_S)


def _profile_dirs(settings: dict[str, Any]) -> list[Path]:
    return [profile for _port, profile in _launch_targets(settings)]


async def browsers_still_open(ports: list[int]) -> bool:
    ready = [port for port in ports if await asyncio.to_thread(_debug_port_ready, port)]
    return bool(ready)


async def migrate_profiles_safely(settings: dict[str, Any]) -> bool:
    """Close Chrome, then copy profiles. A browser that stays open skips both."""
    ports = launch_ports(settings)
    if await browsers_still_open(ports):
        await close_listening_browsers(ports)
        if await browsers_still_open(ports):
            logger.warning(
                "Chrome stayed open. Profile copy and the Chrome download were skipped."
            )
            return False
    return migrate_profiles()


async def clear_leftover_browsers(keep: Path, profiles: list[Path]) -> None:
    """Delete chrome-*/chromium-* folders that an update left behind.

    The new build lands before the old folders are gone, so an app closed
    mid-delete leaves them there. The next launch sees Chrome as current and
    never offers the update, so this cleanup runs by itself, off the UI thread
    and without the prompt. The folder holding the live chrome.exe stays.
    """
    try:
        await asyncio.to_thread(remove_old_browsers, keep, profiles)
    except Exception:
        logger.exception("Leftover Chrome build folders could not be removed")


async def peek_chrome_update(settings: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Stable version and download URL when Chrome should be replaced.

    None means the installed build is current, or the profiles are not safe
    to leave behind a binary replace. A check that cannot be read raises.
    """
    version, url = stable_chrome_download(
        await asyncio.to_thread(_read_json, CFT_STABLE_URL)
    )
    profiles = _profile_dirs(settings)
    exe = find_installed_chrome()
    installed = exe_product_version(exe)
    if not chrome_update_needed(installed, version):
        if installed:
            current = str(settings.get("chrome_version") or "")
            if current != installed:
                update_settings(chrome_version=installed)
        if exe is not None:
            await clear_leftover_browsers(exe.parent, profiles)
        return None
    delete_dirs = old_version_dirs(exe.parent if exe is not None else None)
    if profiles_block_chrome_update(profiles, delete_dirs):
        logger.warning("Chrome update skipped; a profile is still inside the browser install")
        return None
    return version, url


def _read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TP-DECK"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Chrome for Testing returned an unexpected response.")
    return payload


def _download_file(
    url: str,
    dest: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Save a Chrome zip. on_progress receives bytes read and the total size.

    The total is 0 when the server does not send a length. The bar then sweeps
    instead of filling a known fraction. A stalled connection gives up after
    _CHROME_DOWNLOAD_DEADLINE_S so the open cannot sit there forever.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "TP-DECK"})
    started = time.monotonic()
    deadline = started + _CHROME_DOWNLOAD_DEADLINE_S
    with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as handle:
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError, AttributeError):
            total = 0
        read = 0
        if on_progress is not None:
            on_progress(0, total)
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            read += len(chunk)
            if on_progress is not None:
                on_progress(read, total)
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Chrome download gave up after {int(_CHROME_DOWNLOAD_DEADLINE_S // 60)} minutes."
                )
    logger.info("Chrome zip read in %.1fs", time.monotonic() - started)


def install_chrome_zip(zip_path: Path, version: str, root: Optional[Path] = None) -> Path:
    """Unpack a Chrome for Testing zip into browser/chrome-<version>/chrome.exe.

    The zip is unpacked straight into the hidden staging folder beside the
    destination, so the move onto its final name is a rename and never a second
    copy of a 150 MB build. Chrome keeps running from the old folder while this
    one fills.
    """
    base = browser_dir() if root is None else root
    base.mkdir(parents=True, exist_ok=True)
    dest = base / f"chrome-{version}"
    exe = dest / "chrome.exe"
    if exe.is_file():
        return exe
    partial = base / f".chrome-{version}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(partial)
        found = next((path for path in partial.rglob("chrome.exe") if path.is_file()), None)
        if found is None:
            raise RuntimeError("Chrome download did not contain chrome.exe.")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(found.parent), str(dest))
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    if not (dest / "chrome.exe").is_file():
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("Chrome download did not contain chrome.exe.")
    return exe


async def download_chrome(
    version: str,
    url: str,
    settings: dict[str, Any],
    on_status: Callable[[str], None],
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_sweep: Optional[Callable[[], None]] = None,
) -> Optional[Path]:
    """Download stable Chrome. None leaves the current browser in place.

    The new chrome.exe is the finished update. The old build folders are
    deleted after it lands, off the UI thread, and a delete that fails is
    logged and left alone: the new browser still opens, and the next launch
    clears whatever is left over. Steps with no byte count to fill, the
    unpack and that delete, hand the bar to on_sweep so it sweeps instead
    of sitting full.
    """
    ports = launch_ports(settings)
    if await browsers_still_open(ports):
        await close_listening_browsers(ports)
        if await browsers_still_open(ports):
            on_status("Chrome is still open — leaving the current browser")
            return None
    profiles = _profile_dirs(settings)
    current = find_installed_chrome()
    keep = current.parent if current is not None else None
    if profiles_block_chrome_update(profiles, old_version_dirs(keep)):
        on_status("Chrome update skipped — logins are still inside the browser folder")
        return None
    on_status(f"Downloading Chrome {version}")
    try:
        with tempfile.TemporaryDirectory(prefix="tpdeck-chrome-zip-") as tmp:
            zip_path = Path(tmp) / f"chrome-{version}.zip"
            await asyncio.to_thread(_download_file, url, zip_path, on_progress)
            on_status(f"Unpacking Chrome {version}")
            if on_sweep is not None:
                on_sweep()
            unpack_start = time.monotonic()
            exe = await asyncio.to_thread(install_chrome_zip, zip_path, version)
            logger.info("Chrome unpacked and installed in %.1fs", time.monotonic() - unpack_start)
    except Exception:
        logger.exception("Chrome download failed")
        on_status("Chrome download failed — opening the current browser")
        return None
    if profiles_block_chrome_update(profiles, old_version_dirs(exe.parent)):
        on_status("Chrome update skipped — logins are still inside the browser folder")
        return None
    on_status("Removing old Chrome")
    if on_sweep is not None:
        on_sweep()
    try:
        await asyncio.to_thread(remove_old_browsers, exe.parent, profiles)
    except Exception:
        logger.exception(
            "Old Chrome folders could not be removed; the new build is in place"
        )
    update_settings(chrome_version=version)
    on_status("Updating Chrome")
    return exe


async def ensure_debug_chromium(
    settings: dict[str, Any],
    on_status: Callable[[str], None],
) -> None:
    """Open minimized Chromium on the debug port, or keep one that is already up."""
    on_status("Starting Chromium")
    pending: list[tuple[int, Path]] = []
    for port, profile in _launch_targets(settings):
        if await asyncio.to_thread(_debug_port_ready, port):
            logger.info(
                "Debug port %s already open; leaving that window as it is",
                port,
            )
            continue
        pending.append((port, profile))

    exe = find_installed_chrome()
    if pending and exe is None:
        on_status("Chrome is not installed — opening TP DECK without it")
        return
    version = exe_product_version(exe)
    if version and str(settings.get("chrome_version") or "") != version:
        update_settings(chrome_version=version)

    if not pending or exe is None:
        return

    minimize_tasks: list[asyncio.Task[None]] = []
    try:
        for port, profile in pending:
            proc = _spawn_chromium(exe, port, profile)
            try:
                await _wait_for_port(port, proc)
            except Exception:
                _stop_process(proc)
                raise
            minimize_tasks.append(
                asyncio.create_task(
                    _keep_minimized(proc.pid),
                    name=f"tpdeck-minimize-{port}",
                )
            )
    finally:
        if minimize_tasks:
            await asyncio.gather(*minimize_tasks)
