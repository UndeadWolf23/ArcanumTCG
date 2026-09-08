"""Early domain models for the TCG.

Deliberately minimal — these grow as mechanics are designed — but having
typed models from day one means the client, server, and database schema all
speak the same language. Card *definitions* (static data) will live in the
database; these classes model *instances* in a match plus player-owned
collections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CardType(str, Enum):
    CREATURE = "creature"
    SPELL = "spell"
    ARTIFACT = "artifact"
    LAND = "land"          # resource card; rename when the resource system is designed


class Rarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    MYTHIC = "mythic"


@dataclass
class CardDef:
    """Static definition, as stored in the database / balance files."""

    card_id: str
    name: str
    card_type: CardType
    cost: int
    rarity: Rarity = Rarity.COMMON
    attack: int = 0
    health: int = 0
    rules_text: str = ""
    art_ref: str = ""      # asset key / URL


@dataclass
class Deck:
    deck_id: str
    name: str
    card_ids: list[str] = field(default_factory=list)   # card_id, one entry per copy

    @property
    def size(self) -> int:
        return len(self.card_ids)


@dataclass
class PlayerProfile:
    """Account-level progression, synced with the database when online."""

    user_id: str
    display_name: str
    level: int = 1
    currency: int = 0
    collection: dict[str, int] = field(default_factory=dict)  # card_id -> copies owned
    decks: list[Deck] = field(default_factory=list)
