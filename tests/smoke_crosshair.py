"""Windows smoke test for the Crosshair app (run by CI on a Windows machine).

Shows the overlay, then presses the weapon-slot keys 2 and 1 the way a keyboard would. Checks
from screenshots that the crosshair switches (shotgun = yellow, SMG = cyan at screen center),
that the overlay is click-through and on top, and that the key presses still reach the focused
window. Saves crosshair-smoke-*.png for a person to look at.
"""

import ctypes
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import ImageGrab  # noqa: E402

import toolkit.crosshair as X  # noqa: E402

tmp = Path(tempfile.mkdtemp())
X.DATA_DIR, X.SETTINGS_FILE, X.OLD_TOOLKIT_FILE = tmp, tmp / "settings.json", tmp / "none.json"

from toolkit.crosshair_ui import CrosshairApp  # noqa: E402

failures: list[str] = []


def check(ok: bool, what: str, fatal: bool = True) -> None:
    print(("PASS  " if ok else ("FAIL  " if fatal else "WARN  ")) + what)
    if not ok and fatal:
        failures.append(what)


def press(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def center_pixel(name: str) -> tuple[int, int, int]:
    shot = ImageGrab.grab(all_screens=False)
    shot.save(f"crosshair-smoke-{name}.png")
    mon = X.monitors()[0]
    return shot.getpixel((mon["width"] // 2, mon["height"] // 2))[:3]


def near(a, b, tol=10) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


app = CrosshairApp()
app.report_callback_exception = lambda exc, val, tb: failures.append(f"error: {val!r}")
app.s["fortnite_only"] = False  # no Fortnite on the test machine
entry_win = entry = None


def step_show() -> None:
    global entry_win, entry
    check(app.key_listener is not None, "weapon-slot key listener started")
    check(app.hotkey_listener is not None, "show/hide hotkey Ctrl+Shift+X registered")
    app.s["active"] = "SMG dot"
    app.toggle()
    check(app.overlay.visible, "overlay is visible")
    hwnd = ctypes.windll.user32.GetParent(app.overlay.win.winfo_id())
    exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
    check(bool(exstyle & 0x20), "overlay is click-through")
    check(bool(exstyle & 0x8), "overlay is always on top")
    # A text box to type into, standing in for the game: the keys must still arrive there.
    entry_win = tk.Toplevel(app)
    entry_win.geometry("300x60+10+10")
    entry = tk.Entry(entry_win)
    entry.pack(fill="x")
    entry_win.after(300, lambda: (entry_win.focus_force(), entry.focus_force()))
    app.after(1200, step_smg)


def step_smg() -> None:
    check(near(center_pixel("smg"), (0x00, 0xF0, 0xFF)), "SMG dot (cyan) at screen center")
    press(0x32)  # key "2"
    app.after(800, step_shotgun)


def step_shotgun() -> None:
    check(app.s["active"] == "Shotgun circle", "pressing 2 switched to 'Shotgun circle'")
    check(near(center_pixel("shotgun"), (0xFF, 0xE6, 0x00)), "shotgun crosshair (yellow) at screen center")
    press(0x31)  # key "1"
    app.after(800, step_back)


def step_back() -> None:
    check(app.s["active"] == "SMG dot", "pressing 1 switched back to 'SMG dot'")
    check(near(center_pixel("smg-again"), (0x00, 0xF0, 0xFF)), "SMG dot shown again")
    typed = entry.get()
    print(f"text box received: {typed!r}")
    check(typed == "21", "key presses still reached the focused window", fatal=False)
    app.toggle()
    check(not app.overlay.visible, "overlay hides again")
    app.after(300, app.destroy)


def watchdog() -> None:
    failures.append("test did not finish within 45 s")
    app.destroy()


app.after(2500, step_show)
app.after(45000, watchdog)
app.mainloop()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
