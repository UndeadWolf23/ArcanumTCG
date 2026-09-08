"""Global constants: app identity, filesystem paths, defaults.

Anything that is a *value the whole app agrees on* lives here so there is a
single source of truth when the project grows.
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Arcanum"
APP_VERSION = "0.1.0"
ORG_NAME = "ArcanumStudio"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Project root = two levels up from this file (arcanum/core/constants.py)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
IMAGES_DIR = ASSETS_DIR / "images"
SOUNDS_DIR = ASSETS_DIR / "sounds"
FONTS_DIR = ASSETS_DIR / "fonts"

# Per-user writable data (settings, cached session). For a shipped build this
# should move to an OS-appropriate location (e.g. %APPDATA%/Arcanum); a single
# helper keeps that future change one-line.


def user_data_dir() -> Path:
    if getattr(sys, "frozen", False):  # packaged build (PyInstaller etc.)
        base = Path.home() / f".{APP_NAME.lower()}"
    else:  # running from source: keep data next to the project for easy dev
        base = ROOT_DIR / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


SETTINGS_FILE = user_data_dir() / "settings.json"
SESSION_FILE = user_data_dir() / "session.json"
LOCAL_USERS_FILE = user_data_dir() / "local_users.json"  # dev-only fake user DB

# ---------------------------------------------------------------------------
# Display defaults
# ---------------------------------------------------------------------------
TARGET_FPS = 60

# Common 16:9 resolutions offered in the settings menu. The user's native
# desktop resolution is always injected into this list at runtime.
RESOLUTION_PRESETS: list[tuple[int, int]] = [
    (1280, 720),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
    (2560, 1440),
    (3440, 1440),
    (3840, 2160),
]

SCREEN_MODES = ("borderless", "windowed", "fullscreen")

# ---------------------------------------------------------------------------
# Networking placeholders (wired up when the backend goes live)
# ---------------------------------------------------------------------------
SUPABASE_URL = ""        # e.g. "https://xyzcompany.supabase.co"
SUPABASE_ANON_KEY = ""   # public anon key (safe for client distribution)
GAME_SERVER_URL = ""     # e.g. "wss://play.arcanum.gg/match"
PROTOCOL_VERSION = 1     # bump when the wire protocol changes
