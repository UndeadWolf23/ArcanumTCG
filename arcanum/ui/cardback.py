"""The universal card back — one image, used everywhere a card shows its
back (opponent hands, deck piles, pack reveals). Loaded once, auto-cropped
to its artwork bounds, and cached per requested size. Callers fall back to
their procedural back if the asset is missing.
"""
from __future__ import annotations

import logging
from typing import Optional

import pygame

from arcanum.core.constants import IMAGES_DIR, ROOT_DIR

log = logging.getLogger(__name__)

_source: Optional[pygame.Surface] = None
_load_failed = False
_cache: dict[tuple[int, int], pygame.Surface] = {}


def _load_source() -> Optional[pygame.Surface]:
    global _source, _load_failed
    if _source is not None or _load_failed:
        return _source
    for path in (IMAGES_DIR / "card_back.png", ROOT_DIR / "card_back.png"):
        if path.is_file():
            try:
                raw = pygame.image.load(str(path)).convert_alpha()
                bounds = raw.get_bounding_rect(min_alpha=10)
                _source = raw.subsurface(bounds).copy()
                log.info("Card back loaded (%dx%d after crop).",
                         *_source.get_size())
                return _source
            except pygame.error as exc:
                log.warning("Could not load card back %s: %s", path, exc)
    _load_failed = True
    return None


def get(size: tuple[int, int]) -> Optional[pygame.Surface]:
    """The card back scaled to exactly `size`, or None if unavailable."""
    size = (max(1, int(size[0])), max(1, int(size[1])))
    if size in _cache:
        return _cache[size]
    source = _load_source()
    if source is None:
        return None
    try:
        scaled = pygame.transform.smoothscale(source, size)
    except (pygame.error, ValueError):
        return None
    _cache[size] = scaled
    return scaled
