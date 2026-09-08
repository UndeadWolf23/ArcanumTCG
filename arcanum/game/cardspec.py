"""CardSpec — the universal card model.

This is what the Card Designer produces, what the `cards` database table
stores (as JSON), and what the client library downloads. It describes any of
the six card types; validation enforces the rules of card construction
(heroes get up to 2 hero types, barriers get durability, only category-legal
keywords, values where required, and so on).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from arcanum.game.keywords import (HERO_TYPES, KEYWORDS_BY_ID, CardType,
                                   Rarity, format_keyword)

SPEC_VERSION = 1
MAX_HERO_TYPES = 2
STAT_LIMIT = 99
COST_LIMIT = 12


@dataclass
class KeywordRef:
    id: str
    value: int | None = None


@dataclass
class CardSpec:
    id: str
    name: str
    card_type: CardType
    rarity: Rarity = Rarity.COMMON
    cost: int = 0
    attack: int = 0
    health: int = 0                 # heroes/minions: life. champions: start life
    durability: int = 0             # barriers only
    hero_types: list[str] = field(default_factory=list)
    keywords: list[KeywordRef] = field(default_factory=list)
    rules_text: str = ""            # custom ability text (beyond keywords)
    flavor: str = ""
    image: str = ""                 # storage object name, e.g. "h_batman.png"
    set_code: str = "BASE"
    collectible: bool = True

    # ------------------------------------------------------------ helpers
    def composed_text(self) -> str:
        """Full rules box: keyword lines then custom text."""
        lines = [format_keyword(ref.id, ref.value) for ref in self.keywords]
        if self.rules_text.strip():
            lines.append(self.rules_text.strip())
        return "\n".join(lines)

    def has_keyword(self, kw_id: str) -> bool:
        return any(ref.id == kw_id for ref in self.keywords)

    def keyword_value(self, kw_id: str, default: int = 0) -> int:
        for ref in self.keywords:
            if ref.id == kw_id:
                return ref.value if ref.value is not None else default
        return default

    # ------------------------------------------------------------ storage
    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_version": SPEC_VERSION,
            "id": self.id, "name": self.name,
            "card_type": self.card_type.value, "rarity": self.rarity.value,
            "cost": self.cost, "attack": self.attack, "health": self.health,
            "durability": self.durability, "hero_types": list(self.hero_types),
            "keywords": [{"id": r.id, "value": r.value} for r in self.keywords],
            "rules_text": self.rules_text, "flavor": self.flavor,
            "image": self.image, "set_code": self.set_code,
            "collectible": self.collectible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CardSpec":
        return cls(
            id=str(data["id"]), name=str(data["name"]),
            card_type=CardType(data["card_type"]),
            rarity=Rarity(data.get("rarity", "common")),
            cost=int(data.get("cost", 0)), attack=int(data.get("attack", 0)),
            health=int(data.get("health", 0)),
            durability=int(data.get("durability", 0)),
            hero_types=[str(t) for t in data.get("hero_types", [])],
            keywords=[KeywordRef(str(k["id"]),
                                 None if k.get("value") is None
                                 else int(k["value"]))
                      for k in data.get("keywords", [])],
            rules_text=str(data.get("rules_text", "")),
            flavor=str(data.get("flavor", "")),
            image=str(data.get("image", "")),
            set_code=str(data.get("set_code", "BASE")),
            collectible=bool(data.get("collectible", True)),
        )

    # ------------------------------------------------------------ validation
    def validate(self) -> tuple[bool, str]:
        if not self.id or not re.fullmatch(r"[a-z0-9_\-]{3,60}", self.id):
            return False, ("Card id must be 3-60 chars of lowercase letters, "
                           "digits, _ or -.")
        if not (1 <= len(self.name.strip()) <= 40):
            return False, "Name must be 1-40 characters."
        if not (0 <= self.cost <= COST_LIMIT):
            return False, f"Cost must be 0-{COST_LIMIT}."

        ct = self.card_type
        if ct in (CardType.HERO, CardType.MINION):
            if not (0 <= self.attack <= STAT_LIMIT
                    and 1 <= self.health <= STAT_LIMIT):
                return False, "Heroes/minions need attack 0+ and health 1+."
        if ct is CardType.CHAMPION:
            if not (10 <= self.health <= STAT_LIMIT):
                return False, "Champions need starting life of at least 10."
            if self.attack:
                return False, "Champions cannot have attack (they don't attack)."
        if ct is CardType.BARRIER:
            if not (1 <= self.durability <= STAT_LIMIT):
                return False, "Barriers need durability of at least 1."
        if ct is not CardType.BARRIER and self.durability:
            return False, "Only barriers have durability."
        if ct is CardType.SPELL and (self.attack or self.health):
            return False, "Spells don't have attack or health."

        if self.hero_types:
            if ct is not CardType.HERO:
                return False, "Only heroes have hero types."
            if len(self.hero_types) > MAX_HERO_TYPES:
                return False, f"Heroes have at most {MAX_HERO_TYPES} types."
            if len(set(self.hero_types)) != len(self.hero_types):
                return False, "Duplicate hero type."
            for hero_type in self.hero_types:
                if hero_type not in HERO_TYPES:
                    return False, f"Unknown hero type: {hero_type}"

        seen: set[str] = set()
        for ref in self.keywords:
            kw = KEYWORDS_BY_ID.get(ref.id)
            if kw is None:
                return False, f"Unknown keyword: {ref.id}"
            if kw.category is not ct:
                return False, (f"{kw.name.split(' ')[0]} is a "
                               f"{kw.category.value} keyword; this card is a "
                               f"{ct.value}.")
            if kw.has_value and (ref.value is None or ref.value < 1):
                return False, f"{kw.name} needs a value of 1 or more."
            if not kw.has_value and ref.value is not None:
                return False, f"{kw.name} doesn't take a value."
            if ref.id in seen:
                return False, f"Duplicate keyword: {kw.name}"
            seen.add(ref.id)
        return True, ""


def make_card_id(name: str) -> str:
    """Readable, collision-safe id: 'Batman, World's...' -> 'batman-worlds-a1b2c3'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "card"
    return f"{slug}-{uuid.uuid4().hex[:6]}"
