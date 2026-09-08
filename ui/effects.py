"""Decorative text effects.

TextGleam renders a piece of text and, every few seconds, sweeps a soft
diagonal highlight across it. The highlight is masked by the glyph alpha, so
only the letterforms light up — nothing bleeds onto the background.
"""
from __future__ import annotations

import pygame

from arcanum.ui import theme


class TextGleam:
    def __init__(
        self,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int] = theme.GOLD_BRIGHT,
        shine_color: tuple[int, int, int] = (255, 246, 218),
        shadow_color: tuple[int, int, int] = theme.NAVY_ABYSS,
        period: float = 7.0,   # seconds between sweeps
        sweep: float = 1.2,    # duration of one sweep
        angle: float = 18.0,   # band slant in degrees
    ) -> None:
        self.base = font.render(text, True, color)
        self.shine = font.render(text, True, shine_color)
        self.shadow = font.render(text, True, shadow_color)
        self.period = max(period, sweep + 0.5)
        self.sweep = sweep

        # A slanted gradient band, prebuilt once; per-frame work is two blits.
        w, h = self.base.get_size()
        band_w = max(48, h)
        band = pygame.Surface((band_w, h * 3), pygame.SRCALPHA)
        for x in range(band_w):
            t = 1.0 - abs(x - band_w / 2) / (band_w / 2)
            alpha = int(215 * t * t)
            if alpha:
                pygame.draw.line(band, (255, 255, 255, alpha), (x, 0), (x, h * 3 - 1))
        self.band = pygame.transform.rotate(band, angle)
        self._t = self.period - 1.6  # first sweep lands shortly after appearing

    # ------------------------------------------------------------------
    def update(self, dt: float) -> None:
        self._t += dt

    def _sweep_progress(self) -> float | None:
        """0..1 while a sweep is in flight, else None."""
        phase = self._t % self.period
        return phase / self.sweep if phase < self.sweep else None

    def draw(
        self,
        surface: pygame.Surface,
        center: tuple[int, int],
        alpha: int = 255,
        animate: bool = True,
        shadow_offset: tuple[int, int] | None = (3, 3),
    ) -> pygame.Rect:
        rect = self.base.get_rect(center=center)
        if shadow_offset:
            self.shadow.set_alpha(int(alpha * 0.7))
            surface.blit(self.shadow,
                         (rect.x + shadow_offset[0], rect.y + shadow_offset[1]))
        self.base.set_alpha(alpha)
        surface.blit(self.base, rect)

        progress = self._sweep_progress() if animate else None
        if progress is None:
            return rect

        w, h = self.base.get_size()
        bw, bh = self.band.get_size()
        x = int(-bw + progress * (w + 2 * bw))
        # mask: gradient band clipped to the text's bounding box...
        mask = pygame.Surface((w, h), pygame.SRCALPHA)
        mask.blit(self.band, (x, (h - bh) // 2))
        # ...then multiplied into the bright text so only glyphs catch light
        overlay = self.shine.copy()
        overlay.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        overlay.set_alpha(alpha)
        surface.blit(overlay, rect)
        return rect
