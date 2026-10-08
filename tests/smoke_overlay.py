"""Windows smoke test for the crosshair overlay (run by CI on a Windows machine).

Opens the real app, shows a magenta dot crosshair, takes a screenshot and checks that the dot
is in the middle of the screen, that the overlay is click-through and that the hotkey registered.
Saves overlay-smoke.png so a person can look at it.
"""

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import ImageGrab  # noqa: E402

from toolkit import crosshair  # noqa: E402
from toolkit.ui import App  # noqa: E402

failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("PASS  " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


app = App(admin=True)
app.report_callback_exception = lambda exc, val, tb: failures.append(f"error: {val!r}")


def step_show() -> None:
    app.show("crosshair")
    app._xh_apply({**crosshair.PRESETS["Small dot"], "dot": 6, "outline": 0})
    app._xh_toggle()
    app.after(1500, step_check)


def step_check() -> None:
    check(app.overlay.visible, "overlay is visible")
    check(app.hotkey_listener is not None, "hotkey Ctrl+Shift+X registered")

    hwnd = ctypes.windll.user32.GetParent(app.overlay.win.winfo_id())
    exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
    check(bool(exstyle & 0x20), "overlay is click-through (WS_EX_TRANSPARENT)")
    check(bool(exstyle & 0x8), "overlay is always on top (WS_EX_TOPMOST)")

    app.after(800, step_screenshot)


def step_screenshot() -> None:
    shot = ImageGrab.grab(all_screens=False)
    shot.save("overlay-smoke.png")
    mon = crosshair.monitors()[0]
    cx, cy = mon["width"] // 2, mon["height"] // 2
    pixel = shot.getpixel((cx, cy))[:3]
    print(f"screen {shot.size}, center pixel {pixel}")
    target = (0xFF, 0x2B, 0xD6)
    check(all(abs(a - b) <= 8 for a, b in zip(pixel, target)), "magenta dot drawn at screen center")
    corner = shot.getpixel((cx + 40, cy + 40))[:3]
    check(corner != target, "area around the dot is see-through")
    app._xh_toggle()
    check(not app.overlay.visible, "overlay hides again")
    app.after(300, app.destroy)


def watchdog() -> None:
    failures.append("test did not finish within 40 s (see errors above)")
    app.destroy()


app.after(3000, step_show)
app.after(40000, watchdog)
app.mainloop()
if app.hotkey_listener:
    app.hotkey_listener.stop()
print("FAILED: " + ", ".join(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
