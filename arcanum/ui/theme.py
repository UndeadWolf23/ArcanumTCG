"""Visual identity: the navy & gold palette, typography, and draw helpers.

Every scene and widget pulls colors and fonts from here — never hard-coded —
so the whole game can be re-skinned from this one file.
"""
from __future__ import annotations

from functools import lru_cache

import pygame

# ---------------------------------------------------------------------------
# Palette — deep navy field, gold filigree
# ---------------------------------------------------------------------------
NAVY_ABYSS = (5, 9, 22)          # window clear color / vignette edges
NAVY_DEEP = (9, 15, 34)          # primary background
NAVY = (15, 24, 52)              # panels
NAVY_RAISED = (23, 35, 72)       # raised panels / hovered fields
NAVY_EDGE = (42, 58, 104)        # hairline panel borders

GOLD = (201, 165, 92)            # primary accent
GOLD_BRIGHT = (240, 208, 130)    # hover / highlights
GOLD_DIM = (128, 106, 62)        # inactive accent
GOLD_GLOW = (255, 224, 150)      # glow cores

TEXT = (230, 234, 245)
TEXT_DIM = (146, 156, 182)
TEXT_FAINT = (94, 104, 132)
TEXT_ON_GOLD = (24, 20, 8)

DANGER = (214, 92, 92)
SUCCESS = (110, 200, 140)

# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------
# Ship real .ttf files in assets/fonts later; for now we pick the best faces
# available on the OS so the framework runs anywhere with zero assets.
_DISPLAY_FACES = "georgia,palatinolinotype,timesnewroman,dejavuserif"
_BODY_FACES = "segoeui,verdana,dejavusans,arial"


@lru_cache(maxsize=64)
def display_font(size: int, bold: bool = False) -> pygame.font.Font:
    """Serif display face for titles — an old-world, arcane feel."""
    return pygame.font.SysFont(_DISPLAY_FACES, size, bold=bold)


@lru_cache(maxsize=64)
def body_font(size: int, bold: bool = False) -> pygame.font.Font:
    """Clean sans for UI labels, inputs, and body text."""
    return pygame.font.SysFont(_BODY_FACES, size, bold=bold)


# ---------------------------------------------------------------------------
# Draw helpers
# ---------------------------------------------------------------------------

def draw_text(
    surface: pygame.Surface,
    text: str,
    pos: tuple[int, int],
    font: pygame.font.Font,
    color: tuple[int, int, int] = TEXT,
    anchor: str = "topleft",
    alpha: int = 255,
) -> pygame.Rect:
    img = font.render(text, True, color)
    if alpha < 255:
        img.set_alpha(alpha)
    rect = img.get_rect(**{anchor: pos})
    surface.blit(img, rect)
    return rect


def draw_panel(
    surface: pygame.Surface,
    rect: pygame.Rect,
    fill: tuple[int, int, int] = NAVY,
    border: tuple[int, int, int] | None = NAVY_EDGE,
    radius: int = 10,
    border_width: int = 1,
) -> None:
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    if border:
        pygame.draw.rect(surface, border, rect, width=border_width, border_radius=radius)


_GLOW_CACHE: dict[tuple, pygame.Surface] = {}


def draw_glow_rect(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: tuple[int, int, int],
    strength: float,
    radius: int = 10,
    spread: int = 8,
) -> None:
    """Soft anti-aliased glow around a rect. `strength` in [0, 1].

    The glow is rendered once as a hard rounded rect at reduced size and
    smoothscaled up — the upscale feathers the edge cleanly. Surfaces are
    cached per shape; per-frame intensity is just a surface-alpha change.
    """
    if strength <= 0.01:
        return
    spread = max(4, int(spread))
    key = (rect.width, rect.height, color, radius, spread)
    glow = _GLOW_CACHE.get(key)
    if glow is None:
        down = max(3, spread // 2)          # downscale factor == feather size
        full = (rect.width + spread * 2, rect.height + spread * 2)
        small = pygame.Surface((max(2, full[0] // down), max(2, full[1] // down)),
                               pygame.SRCALPHA)
        pad = max(1, spread // down)
        inner = small.get_rect().inflate(-pad * 2, -pad * 2)
        pygame.draw.rect(small, (*color, 255), inner,
                         border_radius=max(2, radius // down))
        glow = pygame.transform.smoothscale(small, full)
        if len(_GLOW_CACHE) > 160:          # resolution changes repopulate this
            _GLOW_CACHE.clear()
        _GLOW_CACHE[key] = glow
    glow.set_alpha(int(60 * min(1.0, strength)))
    surface.blit(glow, (rect.x - spread, rect.y - spread))


def draw_hairline(surface: pygame.Surface, x1: int, x2: int, y: int,
                  color: tuple[int, int, int] = NAVY_EDGE) -> None:
    pygame.draw.line(surface, color, (x1, y), (x2, y))


def gold_gradient_rule(surface: pygame.Surface, center: tuple[int, int], width: int) -> None:
    """A thin ornamental rule that fades out at both ends — used under titles."""
    cx, cy = center
    half = width // 2
    for dx in range(-half, half + 1):
        t = 1.0 - abs(dx) / max(1, half)
        c = (
            int(GOLD[0] * t + NAVY_DEEP[0] * (1 - t)),
            int(GOLD[1] * t + NAVY_DEEP[1] * (1 - t)),
            int(GOLD[2] * t + NAVY_DEEP[2] * (1 - t)),
        )
        surface.set_at((cx + dx, cy), c)
        surface.set_at((cx + dx, cy + 1), c)
