"""Start the Crosshair overlay app. No administrator rights needed."""

import traceback


def main() -> None:
    from toolkit.winutils import IS_WINDOWS

    if IS_WINDOWS:
        import ctypes
        # Only one copy at a time, otherwise two overlays and two key listeners would run.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW(None, False, "CrosshairOverlay.SingleInstance")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(None, "Crosshair is already running.", "Crosshair", 0x40)
            return
    try:
        from toolkit.crosshair_ui import CrosshairApp
        CrosshairApp().mainloop()
    except Exception:
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(limit=3),
                                             "Crosshair crashed", 0x10)
        raise


if __name__ == "__main__":
    main()
