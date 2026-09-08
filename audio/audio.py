"""Audio manager: bus-based mixing (master / music / sfx / ui).

Sounds are registered by name and played through a bus; effective volume is
`bus_volume * master_volume` (0 if muted). The settings scene drives this
live, so sliders are heard immediately. No audio assets ship yet — playing an
unknown sound logs at debug level and is otherwise a silent no-op, so the
framework runs clean without files.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pygame

from arcanum.core.config import AudioSettings
from arcanum.core.constants import SOUNDS_DIR

log = logging.getLogger(__name__)

BUSES = ("music", "sfx", "ui")


class AudioManager:
    def __init__(self, settings: AudioSettings) -> None:
        self.settings = settings
        self._sounds: dict[str, tuple[pygame.mixer.Sound, str]] = {}  # name -> (sound, bus)
        self._ready = False
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(32)
            self._ready = True
        except pygame.error as exc:
            log.warning("Audio device unavailable (%s); running silent.", exc)
        self._autoload()

    # -- library -----------------------------------------------------------
    def _autoload(self) -> None:
        """Load assets/sounds/<bus>/<name>.(wav|ogg) automatically."""
        if not self._ready:
            return
        for bus in BUSES:
            folder = SOUNDS_DIR / bus
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if path.suffix.lower() in (".wav", ".ogg"):
                    self.load(path.stem, path, bus)

    def load(self, name: str, path: Path, bus: str = "sfx") -> None:
        if not self._ready:
            return
        try:
            self._sounds[name] = (pygame.mixer.Sound(str(path)), bus)
        except pygame.error as exc:
            log.warning("Could not load sound %s: %s", path, exc)

    # -- playback ------------------------------------------------------------
    def _bus_volume(self, bus: str) -> float:
        s = self.settings
        if s.muted:
            return 0.0
        bus_vol = {"music": s.music_volume, "sfx": s.sfx_volume, "ui": s.ui_volume}.get(bus, 1.0)
        return s.master_volume * bus_vol

    def play(self, name: str, bus: str | None = None) -> None:
        entry = self._sounds.get(name)
        if entry is None:
            log.debug("Sound %r not loaded; skipping.", name)
            return
        sound, default_bus = entry
        sound.set_volume(self._bus_volume(bus or default_bus))
        sound.play()

    def play_music(self, path: Path, loop: bool = True) -> None:
        if not self._ready:
            return
        try:
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.set_volume(self._bus_volume("music"))
            pygame.mixer.music.play(-1 if loop else 0)
        except pygame.error as exc:
            log.warning("Could not play music %s: %s", path, exc)

    def apply_settings(self) -> None:
        """Call after any audio setting changes; updates live music volume."""
        if self._ready and pygame.mixer.music.get_busy():
            pygame.mixer.music.set_volume(self._bus_volume("music"))

    # convenience hook for widgets (hover/click ticks)
    def ui_sound(self, name: str) -> None:
        self.play(name, bus="ui")
