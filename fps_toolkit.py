"""Start FPS Toolkit. On Windows it asks for administrator rights (UAC) first."""

import sys
import traceback


def main() -> None:
    from toolkit.winutils import IS_WINDOWS, is_admin, relaunch_as_admin, write_log_file

    admin = is_admin()
    if IS_WINDOWS and not admin and "--no-elevate" not in sys.argv:
        if relaunch_as_admin():
            return  # the elevated copy takes over
        # UAC was declined: keep going without admin, the app shows a warning.

    try:
        from toolkit.ui import App
        App(admin=admin).mainloop()
    except Exception:
        write_log_file(traceback.format_exc())
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, traceback.format_exc(limit=3), "FPS Toolkit crashed", 0x10)
        raise


if __name__ == "__main__":
    main()
