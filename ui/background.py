"""Shared animated backdrop: deep navy field, drifting gold motes, vignette.

One instance is owned by the App and reused by every scene, so the motion is
continuous across screen changes. All heavy surfaces (vignette, mote sprites)
are cached and rebuilt only on resolution change.
"""
from __future__ import annotations

import math
import random

import pygame

from arcanum.ui import theme


class _Mote:
    __slots__ = ("x", "y", "radius", "speed", "phase", "depth")

    def __init__(self, w: int, h: int) -> None:
        self.x = random.uniform(0, w)
        self.y = random.uniform(0, h)
        self.depth = random.uniform(0.3, 1.0)          # parallax-ish brightness/size
        self.radius = random.uniform(1.0, 2.6) * self.depth
        self.speed = random.uniform(6, 16) * self.depth
        self.phase = random.uniform(0, math.tau)


class Background:
    MOTE_DENSITY = 1 / 22000  # motes per pixel

    def __init__(self, size: tuple[int, int]) -> None:
        self.reduced_motion = False    # set from accessibility settings
        self._time = random.uniform(0, 100)
        self._image_raw: pygame.Surface | None = None
        self._image_scaled: pygame.Surface | None = None
        self._vignette: pygame.Surface | None = None
        self._mote_sprites: list[pygame.Surface] = []
        self.resize(size)

    # -- image layer ---------------------------------------------------------
    def set_image(self, image: pygame.Surface | None) -> None:
        """Use `image` as the backdrop (aspect-filled) instead of the
        procedural gradient. Pass None to return to the procedural look.
        Motes and vignette still draw on top either way."""
        self._image_raw = image
        self._image_scaled = None  # rebuild lazily at current size

    def _scaled_image(self) -> pygame.Surface | None:
        if self._image_raw is None:
            return None
        if self._image_scaled is None or self._image_scaled.get_size() != self._cover_size():
            self._image_scaled = pygame.transform.smoothscale(
                self._image_raw, self._cover_size())
        return self._image_scaled

    def _cover_size(self) -> tuple[int, int]:
        """Aspect-fill: smallest scale that covers the screen (center-crop)."""
        assert self._image_raw is not None
        w, h = self.size
        iw, ih = self._image_raw.get_size()
        scale = max(w / iw, h / ih)
        return (max(w, int(iw * scale + 0.5)), max(h, int(ih * scale + 0.5)))

    # -- construction ----------------------------------------------------
    def resize(self, size: tuple[int, int]) -> None:
        self.size = size
        self._image_scaled = None
        w, h = size
        count = max(30, int(w * h * self.MOTE_DENSITY))
        self._motes = [_Mote(w, h) for _ in range(count)]
        self._vignette = self._build_vignette(size)
        if not self._mote_sprites:
            self._mote_sprites = [self._build_mote_sprite(r) for r in (2, 3, 4, 6)]

    @staticmethod
    def _build_mote_sprite(radius: int) -> pygame.Surface:
        size = radius * 6
        sprite = pygame.Surface((size, size), pygame.SRCALPHA)
        center = size // 2
        for r in range(radius * 3, 0, -1):
            t = r / (radius * 3)
            alpha = int(90 * (1 - t) ** 2)
            pygame.draw.circle(sprite, (*theme.GOLD_GLOW, alpha), (center, center), r)
        return sprite

    @staticmethod
    def _build_vignette(size: tuple[int, int]) -> pygame.Surface:
        """Radial darkening toward the edges; built small then smoothscaled."""
        small = pygame.Surface((160, 90), pygame.SRCALPHA)
        cx, cy = 80, 45
        max_d = math.hypot(cx, cy)
        for y in range(90):
            for x in range(160):
                d = math.hypot(x - cx, y - cy) / max_d
                alpha = int(190 * max(0.0, d - 0.45) ** 1.6)
                small.set_at((x, y), (*theme.NAVY_ABYSS, min(200, alpha)))
        return pygame.transform.smoothscale(small, size)

    # -- frame -------------------------------------------------------------
    def update(self, dt: float) -> None:
        self._time += dt
        w, h = self.size
        drift = 0.2 if self.reduced_motion else 1.0
        for mote in self._motes:
            mote.y -= mote.speed * dt * drift
            if not self.reduced_motion:
                mote.x += math.sin(self._time * 0.5 + mote.phase) * 6 * dt
            if mote.y < -10:
                mote.y = h + 10
                mote.x = random.uniform(0, w)

    def draw(self, surface: pygame.Surface) -> None:
        w, h = self.size
        image = self._scaled_image()
        if image is not None:
            # centered crop of the aspect-filled image
            iw, ih = image.get_size()
            surface.blit(image, ((w - iw) // 2, (h - ih) // 2))
            # faint navy scrim keeps gold text and panels readable on busy art
            scrim = pygame.Surface((w, h), pygame.SRCALPHA)
            scrim.fill((*theme.NAVY_ABYSS, 70))
            surface.blit(scrim, (0, 0))
        else:
            # vertical navy gradient in coarse bands (cheap, invisible seams)
            bands = 24
            band_h = h // bands + 1
            for i in range(bands):
                t = i / (bands - 1)
                color = tuple(
                    int(theme.NAVY_ABYSS[c] + (theme.NAVY_DEEP[c] - theme.NAVY_ABYSS[c])
                        * (0.35 + 0.65 * math.sin(t * math.pi)))
                    for c in range(3)
                )
                surface.fill(color, (0, i * band_h, w, band_h))

        # slow aurora sweep — a wide, faint gold band drifting across
        # (skipped over image backdrops: the art brings its own light)
        if not self.reduced_motion and image is None:
            sweep_x = (math.sin(self._time * 0.08) * 0.5 + 0.5) * w
            band = pygame.Surface((int(w * 0.5), h), pygame.SRCALPHA)
            for x in range(0, band.get_width(), 4):
                t = 1 - abs(x - band.get_width() / 2) / (band.get_width() / 2)
                alpha = int(10 * t)
                if alpha:
                    pygame.draw.rect(band, (*theme.GOLD, alpha), (x, 0, 4, h))
            surface.blit(band, (int(sweep_x - band.get_width() / 2), 0))

        # motes
        for mote in self._motes:
            twinkle = (0.8 if self.reduced_motion
                       else 0.6 + 0.4 * math.sin(self._time * 1.4 + mote.phase))
            index = min(len(self._mote_sprites) - 1, int(mote.radius))
            sprite = self._mote_sprites[index]
            sprite.set_alpha(int(150 * mote.depth * twinkle))
            surface.blit(sprite, (mote.x - sprite.get_width() / 2,
                                  mote.y - sprite.get_height() / 2))

        if self._vignette is not None:
            surface.blit(self._vignette, (0, 0))
