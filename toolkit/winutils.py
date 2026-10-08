"""Low-level Windows helpers: admin elevation, registry access, commands and saved state."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import winreg

# Same numbers as winreg.REG_SZ / winreg.REG_DWORD, defined here so the module imports anywhere.
REG_SZ = 1
REG_DWORD = 4

CREATE_NO_WINDOW = 0x08000000

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FPSToolkit"
STATE_FILE = APP_DIR / "state.json"
LOG_FILE = APP_DIR / "toolkit.log"


class WindowsOnlyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("this only works on Windows")


def require_windows() -> None:
    if not IS_WINDOWS:
        raise WindowsOnlyError()


# --------------------------------------------------------------------------- admin

def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """Show the UAC prompt and restart this program elevated. True if the new process started."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.exists():
            exe = str(pythonw)  # no console window behind the app
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return result > 32


# --------------------------------------------------------------------------- state

class State:
    """Small JSON store for backups of everything we change, so it can be reverted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        try:
            self._data = json.loads(STATE_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str, default=None):
        with self._lock:
            value = self._data.get(key, default)
            return json.loads(json.dumps(value))  # hand out a copy

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def pop(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._save()

    def _save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
        tmp.replace(STATE_FILE)


STATE = State()


def write_log_file(message: str) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- registry

def _hive(name: str):
    return {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[name]


def _view() -> int:
    return winreg.KEY_WOW64_64KEY


def reg_read(hive: str, path: str, name: str):
    """Return (value, type) or None when the value does not exist."""
    require_windows()
    try:
        with winreg.OpenKey(_hive(hive), path, 0, winreg.KEY_READ | _view()) as key:
            return winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None


def reg_write(hive: str, path: str, name: str, value, kind: int) -> None:
    require_windows()
    with winreg.CreateKeyEx(_hive(hive), path, 0, winreg.KEY_SET_VALUE | _view()) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def reg_delete(hive: str, path: str, name: str) -> None:
    require_windows()
    try:
        with winreg.OpenKey(_hive(hive), path, 0, winreg.KEY_SET_VALUE | _view()) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- commands

def decode_output(raw: bytes) -> str:
    if raw and raw.count(b"\x00") > len(raw) // 4:
        text = raw.decode("utf-16-le", errors="replace")  # sfc and dism write UTF-16
    else:
        encoding = "utf-8"
        if IS_WINDOWS:
            encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        text = raw.decode(encoding, errors="replace")
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def run(args: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run a command without a console window. Returns (exit code, combined output)."""
    require_windows()
    try:
        proc = subprocess.run(
            args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW
        )
    except FileNotFoundError:
        return 1, f"{args[0]} was not found"
    except subprocess.TimeoutExpired:
        return 1, f"{args[0]} timed out after {timeout}s"
    return proc.returncode, decode_output(proc.stdout + proc.stderr)


def powershell(script: str, timeout: int = 300) -> tuple[int, str]:
    return run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        timeout=timeout,
    )


def open_target(target: str) -> None:
    """Open a file, folder, ms-settings: page or program the way Explorer would."""
    require_windows()
    os.startfile(target)  # noqa: S606 - targets are fixed strings in this app
