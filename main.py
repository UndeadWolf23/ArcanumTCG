"""Arcanum — launch the game client.

Run:  python main.py
"""
import logging
import os
import sys
import traceback

# ---------------------------------------------------------------------------
# Windows DPI awareness — MUST happen before pygame/SDL creates a window.
# Without this, Windows display scaling (125%/150%) bitmap-stretches the
# window (blurry rendering) and can offset mouse coordinates.
# ---------------------------------------------------------------------------
os.environ.setdefault("SDL_WINDOWS_DPI_AWARENESS", "permonitorv2")  # SDL >= 2.24
if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2/v1
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()       # older Windows
    except Exception:  # noqa: BLE001 - cosmetic; never block launch on this
        pass

from arcanum.core.app import App
from arcanum.core.constants import user_data_dir

log = logging.getLogger("arcanum")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        App().run()
        return 0
    except Exception:  # noqa: BLE001
        log.exception("Fatal error — writing crash log.")
        try:
            crash_path = user_data_dir() / "crash.log"
            crash_path.write_text(traceback.format_exc(), encoding="utf-8")
            log.error("Crash details saved to %s", crash_path)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
