"""Global constants: app identity, filesystem paths, defaults.

Anything that is a *value the whole app agrees on* lives here so there is a
single source of truth when the project grows.
"""
from __future__ import annotations

import os
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
# Supabase project credentials. Set env vars ARCANUM_SUPABASE_URL and
# ARCANUM_SUPABASE_KEY, or paste the values here. The anon key is PUBLIC by
# design (safe to ship in the client); row-level security protects the data.
SUPABASE_URL = os.environ.get("ARCANUM_SUPABASE_URL",
                              "https://xiozpfainvmbcngcummm.supabase.co")
SUPABASE_ANON_KEY = os.environ.get(
    "ARCANUM_SUPABASE_KEY", "sb_publishable_I-nkNvBXXjAfg2wAJnblrw_USLbXJC1")
# Game server address. Set the ARCANUM_SERVER_URL environment variable, or
# paste your Render URL here (https://... becomes wss://...), e.g.
# "wss://arcanum-server.onrender.com"
GAME_SERVER_URL = os.environ.get("ARCANUM_SERVER_URL",
                                 "wss://arcanumtcg-v313.onrender.com")
PROTOCOL_VERSION = 1     # bump when the wire protocol changes
