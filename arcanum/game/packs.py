"""Booster packs — definitions and opening logic.

Each pack rolls 8 cards independently by rarity weight, then picks a random
card of that rarity from the full library (built-in + published). If a
rarity has no cards yet (young card pool), the roll falls to the nearest
populated rarity below, then above — the odds philosophy survives even
while the library is small.

Packs are FREE for now; the economy (earning/buying, collection persistence)
is a later milestone. Opening is currently presentational.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from arcanum.game import catalog
from arcanum.game.catalog import CardDef
from arcanum.game.keywords import Rarity

PACK_SIZE = 8

RARITY_ORDER = (Rarity.COMMON, Rarity.UNCOMMON, Rarity.RARE, Rarity.EPIC,
                Rarity.LEGENDARY)

# per-rarity presentation colors (VFX tint): legendary gold, epic purple,
# rare blue, uncommon green, common white/grey
RARITY_FX = {
    Rarity.COMMON: (214, 218, 228),
    Rarity.UNCOMMON: (96, 214, 140),
    Rarity.RARE: (86, 156, 255),
    Rarity.EPIC: (186, 106, 255),
    Rarity.LEGENDARY: (255, 205, 90),
}


@dataclass(frozen=True)
class PackDef:
    pack_id: str
    name: str
    image: str                      # assets/images/<image>
    cost: int                       # 0 for now — packs are free
    weights: dict                   # Rarity -> percent (sums to 100)
    tagline: str = ""


PACKS: tuple[PackDef, ...] = (
    PackDef("adventure", "Adventure Pack", "pack_adventure.png", 0,
            {Rarity.LEGENDARY: 0.5, Rarity.EPIC: 9.5, Rarity.RARE: 25.0,
             Rarity.UNCOMMON: 30.0, Rarity.COMMON: 35.0},
            "Every journey starts somewhere."),
    PackDef("wonder", "Wonder Pack", "pack_wonder.png", 0,
            {Rarity.LEGENDARY: 1.5, Rarity.EPIC: 13.5, Rarity.RARE: 33.0,
             Rarity.UNCOMMON: 30.0, Rarity.COMMON: 22.0},
            "For those who look up."),
    PackDef("cosmic", "Cosmic Pack", "pack_cosmic.png", 0,
            {Rarity.LEGENDARY: 10.0, Rarity.EPIC: 40.0, Rarity.RARE: 30.0,
             Rarity.UNCOMMON: 20.0, Rarity.COMMON: 0.0},
            "The stars owe you a debt."),
)

PACKS_BY_ID = {p.pack_id: p for p in PACKS}


def roll_rarity(pack: PackDef, rng: random.Random) -> Rarity:
    roll = rng.uniform(0.0, 100.0)
    cumulative = 0.0
    for rarity in (Rarity.LEGENDARY, Rarity.EPIC, Rarity.RARE,
                   Rarity.UNCOMMON, Rarity.COMMON):
        cumulative += pack.weights.get(rarity, 0.0)
        if roll < cumulative:
            return rarity
    return Rarity.COMMON


def _pool(rarity: Rarity) -> list[CardDef]:
    return [c for c in catalog.all_cards() if c.rarity is rarity]


def pick_card(rarity: Rarity, rng: random.Random) -> CardDef:
    """Random card of the rolled rarity; falls to the nearest populated
    rarity (below first, then above) while the library is young."""
    index = RARITY_ORDER.index(rarity)
    for i in list(range(index, -1, -1)) + list(range(index + 1,
                                                     len(RARITY_ORDER))):
        pool = _pool(RARITY_ORDER[i])
        if pool:
            return rng.choice(pool)
    raise RuntimeError("Card library is empty")


def open_pack(pack_id: str, rng: random.Random | None = None) -> list[CardDef]:
    """Roll the pack's 8 cards, sorted so the best reveal comes last."""
    pack = PACKS_BY_ID[pack_id]
    rng = rng or random.Random()
    cards = [pick_card(roll_rarity(pack, rng), rng) for _ in range(PACK_SIZE)]
    cards.sort(key=lambda c: RARITY_ORDER.index(c.rarity))
    return cards
