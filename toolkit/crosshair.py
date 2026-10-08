"""Crosshair overlay engine: a small transparent, click-through, always-on-top window in the
middle of the screen, plus the keyboard listener that switches crosshairs per weapon slot.

It only draws on the desktop, like any other window; it never reads or touches the game. Games
must run in borderless / "Windowed Fullscreen" mode for overlays to show on top.
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

from .winutils import IS_WINDOWS, require_windows

DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CrosshairOverlay"
SETTINGS_FILE = DATA_DIR / "settings.json"
OLD_TOOLKIT_FILE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FPSToolkit" / "crosshair.json"
KEY_COLOR = "#010203"  # painted pixels of this color become see-through
GAME_EXE = "FortniteClient-Win64-Shipping.exe"

STYLES = ["Cross", "Cross + dot", "Dot", "Circle", "Circle + dot", "T-shape"]

DEFAULT = {
    "style": "Cross", "color": "#00ff4c", "length": 7, "thickness": 2, "gap": 4, "dot": 2,
    "radius": 10, "outline": 1, "outline_color": "#000000", "opacity": 100,
    "offset_x": 0, "offset_y": 0, "monitor": 0,
}

# Built-in crosshairs. Sizes are in screen pixels; resize them to taste.
BUILTIN = {
    "SMG dot": {**DEFAULT, "style": "Dot", "color": "#00f0ff", "dot": 4},
    "Shotgun circle": {**DEFAULT, "style": "Circle + dot", "color": "#ffe600", "radius": 20,
                       "thickness": 2, "dot": 3},
    "Shotgun wide cross": {**DEFAULT, "style": "Cross + dot", "color": "#ff8a00", "length": 9,
                           "gap": 12, "thickness": 2, "dot": 3},
    "AR cross": {**DEFAULT, "style": "Cross + dot", "color": "#00ff4c", "length": 6, "gap": 4,
                 "thickness": 2, "dot": 2},
    "Sniper dot": {**DEFAULT, "style": "Dot", "color": "#ff2a2a", "dot": 2},
    "Classic cross": dict(DEFAULT),
}

NO_CHANGE = "No change"
HIDE = "Hide crosshair"

# Keys a weapon slot can be bound to. Name -> Windows virtual-key code.
SLOT_KEYS: dict[str, int | None] = {"—": None}
SLOT_KEYS.update({str(n): 0x30 + n for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)})
SLOT_KEYS.update({c: ord(c) for c in "QERTFGZXCV"})
SLOT_KEYS.update({f"F{n}": 0x6F + n for n in range(1, 7)})

DEFAULT_SLOTS = [
    {"key": "1", "crosshair": "SMG dot"},
    {"key": "2", "crosshair": "Shotgun circle"},
    {"key": "3", "crosshair": "AR cross"},
    {"key": "4", "crosshair": NO_CHANGE},
    {"key": "5", "crosshair": NO_CHANGE},
    {"key": "—", "crosshair": NO_CHANGE},
]

HOTKEYS: dict[str, tuple[int, int] | None] = {
    "Ctrl+Shift+X": (0x0002 | 0x0004, ord("X")),
    "F7": (0, 0x76), "F8": (0, 0x77), "F9": (0, 0x78),
    "F10": (0, 0x79), "F11": (0, 0x7A),
    "Insert": (0, 0x2D), "Home": (0, 0x24),
    "Off": None,
}


# --------------------------------------------------------------------------- settings

def load_settings() -> dict:
    data = {
        "crosshairs": {name: dict(cfg) for name, cfg in BUILTIN.items()},
        "slots": [dict(slot) for slot in DEFAULT_SLOTS],
        "active": "SMG dot",
        "hotkey": "Ctrl+Shift+X",
        "monitor": 0,
        "show_on_start": False,
        "fortnite_only": True,
    }
    try:
        saved = json.loads(SETTINGS_FILE.read_text("utf-8"))
        data.update({k: v for k, v in saved.items() if k in data})
    except (OSError, ValueError):
        # First run: bring over crosshairs saved in the FPS Toolkit's old Crosshair page.
        try:
            old = json.loads(OLD_TOOLKIT_FILE.read_text("utf-8"))
            for name, cfg in old.get("profiles", {}).items():
                data["crosshairs"].setdefault(name, cfg)
        except (OSError, ValueError, AttributeError):
            pass
    data["crosshairs"] = {name: {**DEFAULT, **cfg} for name, cfg in data["crosshairs"].items()}
    if not data["crosshairs"]:
        data["crosshairs"] = {name: dict(cfg) for name, cfg in BUILTIN.items()}
    valid = set(data["crosshairs"]) | {NO_CHANGE, HIDE}
    data["slots"] = (data["slots"] + [dict(s) for s in DEFAULT_SLOTS])[:len(DEFAULT_SLOTS)]
    for slot in data["slots"]:
        if slot.get("key") not in SLOT_KEYS:
            slot["key"] = "—"
        if slot.get("crosshair") not in valid:
            slot["crosshair"] = NO_CHANGE
    if data["active"] not in data["crosshairs"]:
        data["active"] = next(iter(data["crosshairs"]))
    if data["hotkey"] not in HOTKEYS:
        data["hotkey"] = "Ctrl+Shift+X"
    return data


def save_settings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    tmp.replace(SETTINGS_FILE)


# --------------------------------------------------------------------------- drawing

def canvas_size(cfg: dict) -> int:
    reach = max(cfg["gap"] + cfg["length"], cfg["radius"] + cfg["thickness"], cfg["dot"])
    return 2 * (reach + cfg["outline"]) + 9  # odd, so there's an exact center pixel


def draw(canvas: tk.Canvas, cfg: dict, cx: int, cy: int, scale: int = 1) -> None:
    """Draw the crosshair centered on pixel (cx, cy). Rectangles keep every edge pixel-sharp."""
    style, t, g, length, o = cfg["style"], cfg["thickness"], cfg["gap"], cfg["length"], cfg["outline"]
    shapes: list[tuple[str, tuple[int, int, int, int]]] = []  # in pixels relative to center

    if style in ("Cross", "Cross + dot", "T-shape") and length > 0:
        lo = -(t // 2)
        shapes += [("rect", (1 + g, lo, 1 + g + length, lo + t)),       # right
                   ("rect", (-g - length, lo, -g, lo + t)),             # left
                   ("rect", (lo, 1 + g, lo + t, 1 + g + length))]       # bottom
        if style != "T-shape":
            shapes.append(("rect", (lo, -g - length, lo + t, -g)))      # top
    if style in ("Circle", "Circle + dot"):
        r = cfg["radius"]
        shapes.append(("ring", (-r, -r, r + 1, r + 1)))
    if style in ("Dot", "Cross + dot", "Circle + dot") and cfg["dot"] > 0:
        d = cfg["dot"]
        lo = -(d // 2)
        shapes.append(("rect", (lo, lo, lo + d, lo + d)))

    def place(box, grow=0):
        x0, y0, x1, y1 = box
        return (cx + (x0 - grow) * scale, cy + (y0 - grow) * scale,
                cx + (x1 + grow) * scale, cy + (y1 + grow) * scale)

    for layer, color, grow in (("outline", cfg["outline_color"], o), ("fill", cfg["color"], 0)):
        if layer == "outline" and o <= 0:
            continue
        for kind, box in shapes:
            if kind == "rect":
                canvas.create_rectangle(*place(box, grow), fill=color, outline="", width=0)
            else:
                width = (t + 2 * grow) * scale
                canvas.create_oval(*place(box), outline=color, width=width)


# --------------------------------------------------------------------------- monitors

def monitors() -> list[dict]:
    """[{left, top, width, height, primary}], primary first."""
    if not IS_WINDOWS:
        return [{"left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True}]
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    found = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HANDLE, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def callback(hmon, _hdc, _rect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append({"left": r.left, "top": r.top, "width": r.right - r.left,
                          "height": r.bottom - r.top, "primary": bool(info.dwFlags & 1)})
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    found.sort(key=lambda m: not m["primary"])
    return found or [{"left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True}]


# --------------------------------------------------------------------------- overlay window

class Overlay:
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.win: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.visible = False
        self._keepalive = None

    def show(self, cfg: dict) -> None:
        require_windows()
        if self.win is None:
            self._create()
        self.visible = True
        self.win.deiconify()
        self.update(cfg)
        self._keep_on_top()

    def hide(self) -> None:
        self.visible = False
        if self._keepalive:
            self.root.after_cancel(self._keepalive)
            self._keepalive = None
        if self.win is not None:
            self.win.withdraw()

    def update(self, cfg: dict) -> None:
        if self.win is None or not self.visible:
            return
        size = canvas_size(cfg)
        self.canvas.configure(width=size, height=size)
        self.canvas.delete("all")
        draw(self.canvas, cfg, size // 2, size // 2)
        screens = monitors()
        mon = screens[cfg["monitor"]] if cfg["monitor"] < len(screens) else screens[0]
        x = mon["left"] + mon["width"] // 2 + cfg["offset_x"] - size // 2
        y = mon["top"] + mon["height"] // 2 + cfg["offset_y"] - size // 2
        self.win.geometry(f"{size}x{size}+{x}+{y}")
        self.win.attributes("-alpha", max(0.1, cfg["opacity"] / 100))
        self._click_through()

    def _create(self) -> None:
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=KEY_COLOR)
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", KEY_COLOR)
        self.canvas = tk.Canvas(win, bg=KEY_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.win = win

    def _click_through(self) -> None:
        """Let mouse clicks pass through to the game and keep it out of Alt-Tab."""
        self.win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
        GWL_EXSTYLE = -20
        LAYERED, TRANSPARENT, TOOLWINDOW, NOACTIVATE = 0x80000, 0x20, 0x80, 0x08000000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        wanted = style | LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
        if wanted != style:
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, wanted)

    def _keep_on_top(self) -> None:
        # Games in borderless mode can push other windows behind them; re-assert every 2 s.
        if not self.visible or self.win is None:
            return
        self.win.attributes("-topmost", True)
        self.win.lift()
        self._keepalive = self.root.after(2000, self._keep_on_top)


# --------------------------------------------------------------------------- global hotkey

class HotkeyListener(threading.Thread):
    """Calls `callback` (on this thread) whenever the hotkey is pressed, even inside a game."""

    WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000

    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        super().__init__(daemon=True)
        self.combo = combo
        self.callback = callback
        self.thread_id = 0
        self.ok = False
        self.ready = threading.Event()

    def run(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        mods, vk = HOTKEYS[self.combo]
        self.ok = bool(user32.RegisterHotKey(None, 1, mods | self.MOD_NOREPEAT, vk))
        self.ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                self.callback()
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


def start_hotkey(combo: str, callback: Callable[[], None]) -> HotkeyListener | None:
    """Start listening; None if the hotkey is off, not on Windows, or already taken."""
    if not IS_WINDOWS or HOTKEYS.get(combo) is None:
        return None
    listener = HotkeyListener(combo, callback)
    listener.start()
    listener.ready.wait(2)
    return listener if listener.ok else None


# --------------------------------------------------------------------------- weapon-slot keys

class KeyListener(threading.Thread):
    """Watches the keyboard for weapon-slot keys with a low-level hook.

    It only looks: every key press is passed on to the game unchanged, and nothing is ever
    pressed for you. The mouse is deliberately not hooked, so aiming input is never delayed.
    `callback(vk)` runs on this thread once per key press (held keys don't repeat).
    """

    WH_KEYBOARD_LL, WM_QUIT = 13, 0x0012
    WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105

    def __init__(self, callback: Callable[[int], None]) -> None:
        super().__init__(daemon=True)
        self.callback = callback
        self.watched: frozenset[int] = frozenset()
        self.thread_id = 0
        self.ok = False
        self.ready = threading.Event()
        self._down: set[int] = set()

    def run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def on_key(n_code, w_param, l_param):
            if n_code == 0:
                vk = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                if vk in self.watched:
                    if w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                        if vk not in self._down:
                            self._down.add(vk)
                            try:
                                self.callback(vk)
                            except Exception:  # noqa: BLE001 - never let a bug block the keyboard
                                pass
                    elif w_param in (self.WM_KEYUP, self.WM_SYSKEYUP):
                        self._down.discard(vk)
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(on_key)  # keep a reference so it isn't garbage-collected
        self.thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._proc,
                                        kernel32.GetModuleHandleW(None), 0)
        self.ok = bool(hook)
        self.ready.set()
        if not hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

    def stop(self) -> None:
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


def start_key_listener(callback: Callable[[int], None]) -> KeyListener | None:
    if not IS_WINDOWS:
        return None
    listener = KeyListener(callback)
    listener.start()
    listener.ready.wait(2)
    return listener if listener.ok else None


_process_names: dict[int, str] = {}


def foreground_process_name() -> str:
    """Name of the program whose window is in front, e.g. FortniteClient-Win64-Shipping.exe."""
    if not IS_WINDOWS:
        return ""
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value not in _process_names:
        try:
            import psutil
            _process_names[pid.value] = psutil.Process(pid.value).name()
        except Exception:  # noqa: BLE001 - process gone or access denied
            return ""
    return _process_names[pid.value]
