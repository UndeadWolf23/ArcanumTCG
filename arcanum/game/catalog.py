"""Card catalog — the game's card database.

Every card that can exist is defined here (later: loaded from Supabase so
balance changes don't need a client patch). The deck builder browses this;
matches will instantiate from it once decks are wired into play.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from arcanum.game.match import Effect, Kind


class Rarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    MYTHIC = "mythic"


@dataclass(frozen=True)
class CardDef:
    card_id: str
    name: str
    kind: Kind
    cost: int
    rarity: Rarity = Rarity.COMMON
    attack: int = 0
    health: int = 0
    text: str = ""
    effect: Effect = Effect.NONE
    needs_target: bool = False
    haste: bool = False

    def matches(self, query: str) -> bool:
        """Search across name and rules text (case-insensitive)."""
        q = query.strip().lower()
        return not q or q in self.name.lower() or q in self.text.lower()


def _creature(cid, name, cost, atk, hp, rarity=Rarity.COMMON, text="",
              haste=False):
    if haste and "Haste" not in text:
        text = ("Haste. " + text).strip()
    return CardDef(cid, name, Kind.CREATURE, cost, rarity, atk, hp, text,
                   haste=haste)


CATALOG: tuple[CardDef, ...] = (
    # -- creatures: costs 1-7, a few statlines per cost --------------------
    _creature("c_wisp", "Astral Wisp", 1, 1, 1, text="A flicker of starlight."),
    _creature("c_moth", "Lunar Moth", 1, 1, 2,
              text="Drawn to the glow of ley lines."),
    _creature("c_sprite", "Meteor Sprite", 2, 2, 1, Rarity.UNCOMMON,
              text="It arrives before its own light.", haste=True),
    _creature("c_adept", "Ley Adept", 2, 2, 2,
              text="Student of the drifting constellations."),
    _creature("c_owl", "Umbral Owl", 2, 1, 3,
              text="Sees by the dark between stars."),
    _creature("c_sentinel", "Star Sentinel", 3, 2, 4,
              text="It has kept one watch for a thousand years."),
    _creature("c_prowler", "Void Prowler", 3, 4, 2, Rarity.UNCOMMON,
              text="Hunts in the silence beyond the veil."),
    _creature("c_golem", "Runestone Golem", 4, 3, 5,
              text="Carved with the map of a dead sky."),
    _creature("c_corsair", "Comet Corsair", 4, 4, 3, Rarity.UNCOMMON,
              text="Haste hits harder falling from orbit.", haste=True),
    _creature("c_oracle", "Nebula Oracle", 5, 4, 5, Rarity.RARE,
              text="Reads tomorrow in the drifting dust."),
    _creature("c_wyrm", "Aurora Wyrm", 5, 5, 4, Rarity.RARE,
              text="Its wake paints the night."),
    _creature("c_colossus", "Eclipse Colossus", 6, 6, 6, Rarity.RARE,
              text="Where it stands, noon becomes midnight."),
    _creature("c_seraph", "Zenith Seraph", 7, 7, 7, Rarity.MYTHIC,
              text="The high point of every sky."),
    _creature("c_devourer", "Night Devourer", 7, 8, 5, Rarity.MYTHIC,
              text="It swallowed a constellation whole."),

    # -- spells --------------------------------------------------------------
    CardDef("s_insight", "Astral Insight", Kind.SPELL, 3, Rarity.COMMON,
            text="Draw 2 cards.", effect=Effect.DRAW_TWO),
    CardDef("s_star", "Falling Star", Kind.SPELL, 3, Rarity.UNCOMMON,
            text="Destroy an enemy creature.",
            effect=Effect.DESTROY_TARGET, needs_target=True),

    # -- relics ----------------------------------------------------------
    CardDef("r_crystal", "Ley Crystal", Kind.RELIC, 2, Rarity.UNCOMMON,
            text="Gain +1 maximum mana.", effect=Effect.MANA_CRYSTAL),
)

BY_ID: dict[str, CardDef] = {c.card_id: c for c in CATALOG}

# deck construction rules
DECK_SIZE = 30
MAX_COPIES = 3
COPIES_BY_RARITY = {Rarity.COMMON: 3, Rarity.UNCOMMON: 3,
                    Rarity.RARE: 2, Rarity.MYTHIC: 1}


def starter_collection() -> dict[str, int]:
    """Copies of each card every account owns (economy comes later)."""
    return {c.card_id: COPIES_BY_RARITY[c.rarity] for c in CATALOG}


def max_copies(card_id: str) -> int:
    card = by_id(card_id)
    if card is None:
        return 0
    return min(MAX_COPIES, COPIES_BY_RARITY[card.rarity])


def validate_deck(cards: dict[str, int],
                  collection: dict[str, int]) -> tuple[bool, str]:
    """Deck legality: size, per-card limits, ownership, known cards."""
    total = 0
    for card_id, count in cards.items():
        card = by_id(card_id)
        if card is None:
            return False, f"Unknown card: {card_id}"
        if count < 1:
            return False, f"Bad count for {card.name}."
        if count > max_copies(card_id):
            return False, f"Too many copies of {card.name} (max {max_copies(card_id)})."
        if count > collection.get(card_id, 0):
            return False, f"You don't own {count}x {card.name}."
        total += count
    if total != DECK_SIZE:
        return False, f"Decks must be exactly {DECK_SIZE} cards ({total} now)."
    return True, ""


# ---------------------------------------------------------------------------
# Official (database) cards — merged into the browsable/playable pool
# ---------------------------------------------------------------------------
_TYPE_TO_KIND = {"hero": Kind.CREATURE, "spell": Kind.SPELL,
                 "relic": Kind.RELIC}


def _spec_to_def(spec) -> CardDef | None:
    """Adapt a database CardSpec into today's engine vocabulary. Champions,
    minions, and barriers return None until their zones land (engine v2)."""
    kind = _TYPE_TO_KIND.get(spec.card_type.value)
    if kind is None:
        return None
    return CardDef(
        card_id=spec.id, name=spec.name, kind=kind, cost=spec.cost,
        rarity=Rarity(spec.rarity.value), attack=spec.attack,
        health=spec.health, text=spec.composed_text(),
        haste=spec.has_keyword("rush"),
    )


def all_cards() -> tuple[CardDef, ...]:
    """Built-in starter set + playable official cards from the database."""
    from arcanum.services.cards import official_cards
    extra = []
    for spec in official_cards():
        if spec.id in BY_ID:
            continue
        card = _spec_to_def(spec)
        if card is not None:
            extra.append(card)
    return CATALOG + tuple(extra)


def by_id(card_id: str) -> CardDef | None:
    if card_id in BY_ID:
        return BY_ID[card_id]
    for card in all_cards():
        if card.card_id == card_id:
            return card
    return None


def official_keywords(card_id: str) -> dict:
    """Keyword dict for a database card (for the match engine)."""
    from arcanum.services.cards import official_cards
    for spec in official_cards():
        if spec.id == card_id:
            return {ref.id: ref.value for ref in spec.keywords}
    return {}


def full_collection() -> dict[str, int]:
    """Starter set + every collectible official card (economy later)."""
    owned = starter_collection()
    for card in all_cards():
        owned.setdefault(card.card_id, COPIES_BY_RARITY[card.rarity])
    return owned
