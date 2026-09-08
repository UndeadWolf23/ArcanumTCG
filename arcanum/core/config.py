"""Persistent user settings (graphics, audio, preferences).

Settings are stored as JSON in the user data directory. Access is through a
small typed wrapper so scenes never touch raw dicts, and unknown/corrupt
values degrade gracefully to defaults.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any

from arcanum.core.constants import SETTINGS_FILE, SCREEN_MODES

log = logging.getLogger(__name__)


@dataclass
class GraphicsSettings:
    resolution: tuple[int, int] | None = None  # None => use native desktop res
    screen_mode: str = "borderless"            # borderless | windowed | fullscreen
    vsync: bool = True
    max_fps: int = 60

    def sanitized(self) -> "GraphicsSettings":
        if self.screen_mode not in SCREEN_MODES:
            self.screen_mode = "borderless"
        if self.resolution is not None:
            try:
                w, h = self.resolution
                self.resolution = (max(640, int(w)), max(360, int(h)))
            except (TypeError, ValueError):
                self.resolution = None
        self.max_fps = max(30, min(int(self.max_fps), 480))
        return self


@dataclass
class AudioSettings:
    master_volume: float = 0.8
    music_volume: float = 0.7
    sfx_volume: float = 0.8
    ui_volume: float = 0.8
    muted: bool = False

    def sanitized(self) -> "AudioSettings":
        for name in ("master_volume", "music_volume", "sfx_volume", "ui_volume"):
            value = getattr(self, name)
            try:
                setattr(self, name, min(1.0, max(0.0, float(value))))
            except (TypeError, ValueError):
                setattr(self, name, 0.8)
        self.muted = bool(self.muted)
        return self


COLORBLIND_MODES = ("off", "deuteranopia", "protanopia", "tritanopia")


@dataclass
class AccessibilitySettings:
    reduced_motion: bool = False       # calms background/idle animation
    screen_shake: bool = True          # future match VFX honor this
    colorblind_mode: str = "off"       # future card/board palettes honor this

    def sanitized(self) -> "AccessibilitySettings":
        self.reduced_motion = bool(self.reduced_motion)
        self.screen_shake = bool(self.screen_shake)
        if self.colorblind_mode not in COLORBLIND_MODES:
            self.colorblind_mode = "off"
        return self


@dataclass
class Settings:
    graphics: GraphicsSettings = field(default_factory=GraphicsSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    accessibility: AccessibilitySettings = field(default_factory=AccessibilitySettings)

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls) -> "Settings":
        if not SETTINGS_FILE.exists():
            return cls()
        try:
            raw: dict[str, Any] = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            gfx_raw = dict(raw.get("graphics", {}))
            if gfx_raw.get("resolution") is not None:
                gfx_raw["resolution"] = tuple(gfx_raw["resolution"])
            gfx = GraphicsSettings(**{
                k: v for k, v in gfx_raw.items()
                if k in GraphicsSettings.__dataclass_fields__
            }).sanitized()
            aud = AudioSettings(**{
                k: v for k, v in dict(raw.get("audio", {})).items()
                if k in AudioSettings.__dataclass_fields__
            }).sanitized()
            acc = AccessibilitySettings(**{
                k: v for k, v in dict(raw.get("accessibility", {})).items()
                if k in AccessibilitySettings.__dataclass_fields__
            }).sanitized()
            return cls(graphics=gfx, audio=aud, accessibility=acc)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            log.warning("Failed to load settings (%s); using defaults.", exc)
            return cls()

    def save(self) -> None:
        try:
            SETTINGS_FILE.write_text(
                json.dumps(asdict(self), indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.error("Could not save settings: %s", exc)
