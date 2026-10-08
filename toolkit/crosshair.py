"""Crosshair overlay: a small transparent, click-through, always-on-top window in the middle of
the screen. It only draws on the desktop, like any other window; it never reads or touches the
game. Games must run in borderless / "Windowed Fullscreen" mode for overlays to show on top.
"""

from __future__ import annotations

import ctypes
import json
import threading
import tkinter as tk
from typing import Callable

from .winutils import APP_DIR, IS_WINDOWS, require_windows

SETTINGS_FILE = APP_DIR / "crosshair.json"
KEY_COLOR = "#010203"  # painted pixels of this color become see-through

STYLES = ["Cross", "Cross + dot", "Dot", "Circle", "Circle + dot", "T-shape"]

DEFAULT = {
    "style": "Cross", "color": "#00ff4c", "length": 7, "thickness": 2, "gap": 4, "dot": 2,
    "radius": 10, "outline": 1, "outline_color": "#000000", "opacity": 100,
    "offset_x": 0, "offset_y": 0, "monitor": 0,
}

PRESETS = {
    "Classic green": DEFAULT,
    "Small dot": {**DEFAULT, "style": "Dot", "color": "#ff2bd6", "dot": 4},
    "Cyan cross + dot": {**DEFAULT, "style": "Cross + dot", "color": "#00f0ff", "length": 6,
                         "gap": 3, "dot": 2},
    "Circle + dot": {**DEFAULT, "style": "Circle + dot", "color": "#ffe600", "radius": 9,
                     "thickness": 2, "dot": 2},
    "T-shape": {**DEFAULT, "style": "T-shape", "color": "#ffffff", "length": 8, "gap": 3},
    "Big red": {**DEFAULT, "color": "#ff2a2a", "length": 14, "thickness": 3, "gap": 6},
}

HOTKEYS: dict[str, tuple[int, int] | None] = {
    "Ctrl+Shift+X": (0x0002 | 0x0004, ord("X")),
    "F6": (0, 0x75), "F7": (0, 0x76), "F8": (0, 0x77), "F9": (0, 0x78),
    "F10": (0, 0x79), "F11": (0, 0x7A),
    "Insert": (0, 0x2D), "Home": (0, 0x24),
    "Off": None,
}


# --------------------------------------------------------------------------- settings

def load_settings() -> dict:
    data = {"current": dict(DEFAULT), "profiles": {}, "hotkey": "Ctrl+Shift+X",
            "show_on_start": False}
    try:
        saved = json.loads(SETTINGS_FILE.read_text("utf-8"))
        data.update({k: v for k, v in saved.items() if k in data})
        data["current"] = {**DEFAULT, **data["current"]}
    except (OSError, ValueError):
        pass
    if data["hotkey"] not in HOTKEYS:
        data["hotkey"] = "Ctrl+Shift+X"
    return data


def save_settings(data: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), "utf-8")


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
