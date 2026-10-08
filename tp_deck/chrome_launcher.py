"""Start the portable Chromium debug browser when TP DECK opens.

Playwright still only attaches with connect_over_cdp. This module installs
Playwright's Chromium under browser/ and starts it as its own Windows process.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from playwright._impl._driver import compute_driver_executable, get_driver_env

from tp_deck.settings_manager import load_settings, update_settings

logger = logging.getLogger("tpdeck")

_PORT_READY_TIMEOUT_S = 45.0
_MINIMIZE_FOR_S = 4.0
_MINIMIZE_INTERVAL_S = 0.25
_INSTALL_TIMEOUT_S = 900.0

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_SW_SHOWMINNOACTIVE = 7
_TH32CS_SNAPPROCESS = 0x00000002

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PERCENT = re.compile(r"(\d{1,3})%")


def app_root() -> Path:
    """Directory that contains TP-DECK.bat and the tp_deck package."""
    return Path(__file__).resolve().parent.parent


def browser_dir() -> Path:
    """Portable Chromium install and profiles, next to the app."""
    return app_root() / "browser"


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


def _revision_of(exe: Path) -> str:
    name = exe.parent.parent.name
    prefix = "chromium-"
    if name.startswith(prefix):
        return name[len(prefix) :]
    return name


def _launch_targets(settings: dict[str, Any]) -> list[tuple[int, Path]]:
    root = browser_dir()
    ebay_port = int(settings.get("ebay_port", 9222))
    targets = [(ebay_port, root / "profile")]
    if str(settings.get("mode", "single")).lower() == "dual":
        erp_port = int(settings.get("erp_port", 9223))
        if erp_port != ebay_port:
            targets.append((erp_port, root / "profile-erp"))
    return targets


def _debug_port_ready(port: int) -> bool:
    url = f"http://127.0.0.1:{int(port)}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=0.4) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _clip(text: str, limit: int = 240) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


def _remember_revision(exe: Optional[Path]) -> None:
    if exe is None:
        return
    revision = _revision_of(exe)
    if not revision:
        return
    current = str(load_settings().get("chrome_version", ""))
    if current == revision:
        return
    update_settings(chrome_version=revision)
    logger.info("Recorded Chromium revision %s", revision)


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


def _note_install_line(
    piece: str,
    last_line: str,
    last_status: str,
    on_status: Callable[[str], None],
) -> tuple[str, str]:
    clean = " ".join(_ANSI.sub("", piece).split())
    if not clean:
        return last_line, last_status
    displayed = last_status
    match = _PERCENT.search(clean)
    if match:
        displayed = f"Downloading Chromium — {match.group(1)}%"
    elif "Download" in clean or "Chromium" in clean or "Chrome" in clean:
        displayed = "Downloading Chromium"
    if displayed != last_status:
        on_status(displayed)
    return clean, displayed


async def _stream_install_output(
    stream: asyncio.StreamReader,
    on_status: Callable[[str], None],
) -> str:
    last_line = ""
    last_status = ""
    pending = ""
    while True:
        chunk = await stream.read(1024)
        if not chunk:
            break
        pending += chunk.decode("utf-8", errors="replace")
        pieces = re.split(r"[\r\n]+", pending)
        pending = pieces.pop() if pieces else ""
        for piece in pieces:
            last_line, last_status = _note_install_line(
                piece, last_line, last_status, on_status
            )
        if "%" in pending:
            last_line, last_status = _note_install_line(
                pending, last_line, last_status, on_status
            )
    if pending.strip():
        last_line, _last_status = _note_install_line(
            pending, last_line, last_status, on_status
        )
    return last_line


async def _install_chromium(on_status: Callable[[str], None]) -> Path:
    on_status("Downloading Chromium")
    root = browser_dir()
    root.mkdir(parents=True, exist_ok=True)
    driver_executable, driver_cli = compute_driver_executable()
    env = get_driver_env()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(root)
    logger.info("Installing Playwright Chromium into %s", root)
    proc = await asyncio.create_subprocess_exec(
        driver_executable,
        driver_cli,
        "install",
        "chromium",
        "--no-shell",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if proc.stdout is None:
        raise RuntimeError("Could not install Chromium (no installer output).")

    try:
        tail = await asyncio.wait_for(
            _stream_install_output(proc.stdout, on_status),
            timeout=_INSTALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Chromium download timed out.") from None

    code = await proc.wait()
    exe = find_chromium_executable(root)
    if exe is None:
        detail = _clip(tail) if tail else f"exit code {code}"
        raise RuntimeError(f"Could not install Chromium. {detail}")
    if code != 0:
        logger.warning(
            "Chromium install exited %s after chrome.exe was written: %s",
            code,
            _clip(tail),
        )
    logger.info("Chromium installed at %s", exe)
    return exe


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

    exe = find_chromium_executable()
    if pending and exe is None:
        exe = await _install_chromium(on_status)
        on_status("Starting Chromium")
    _remember_revision(exe)

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
