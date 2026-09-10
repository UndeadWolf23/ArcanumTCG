"""Card catalog — the game's card database.

Every card that can exist is defined here (later: loaded from Supabase so
balance changes don't need a client patch). The deck builder browses this;
matches will instantiate from it once decks are wired into play.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from arcanum.game.cardspec import CardSpec, KeywordRef
from arcanum.game.keywords import CardType, Rarity
from arcanum.game.match import Effect, Kind


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
    _creature("c_wyrm", "Aurora Wyrm", 5, 5, 4, Rarity.EPIC,
              text="Its wake paints the night."),
    _creature("c_colossus", "Eclipse Colossus", 6, 6, 6, Rarity.EPIC,
              text="Where it stands, noon becomes midnight."),
    _creature("c_seraph", "Zenith Seraph", 7, 7, 7, Rarity.LEGENDARY,
              text="The high point of every sky."),
    _creature("c_devourer", "Night Devourer", 7, 8, 5, Rarity.LEGENDARY,
              text="It swallowed a constellation whole."),

    # -- spells --------------------------------------------------------------
    CardDef("s_insight", "Astral Insight", Kind.SPELL, 3, Rarity.COMMON,
            text="Draw 2 cards.", effect=Effect.DRAW_TWO),
    CardDef("s_star", "Falling Star", Kind.SPELL, 3, Rarity.UNCOMMON,
            text="Destroy an enemy hero.",
            effect=Effect.DESTROY_TARGET, needs_target=True),

    # -- relics ----------------------------------------------------------
    CardDef("r_crystal", "Ley Crystal", Kind.RELIC, 2, Rarity.UNCOMMON,
            text="Gain +1 maximum mana.", effect=Effect.MANA_CRYSTAL),
)

BY_ID: dict[str, CardDef] = {c.card_id: c for c in CATALOG}

# deck construction rules
# ---------------------------------------------------------------------------
# Built-in champions, barriers, and keyword test heroes (engine v2 preview:
# champions are deck-required now; barriers/keyword triggers script up next)
# ---------------------------------------------------------------------------
def _spec(id_, name, ct, rarity, cost=0, attack=0, health=0, durability=0,
          hero_types=(), keywords=(), text="", flavor=""):
    return CardSpec(
        id=id_, name=name, card_type=ct, rarity=rarity, cost=cost,
        attack=attack, health=health, durability=durability,
        hero_types=list(hero_types),
        keywords=[KeywordRef(k, v) for k, v in keywords],
        rules_text=text, flavor=flavor)


BUILTIN_SPECS: tuple[CardSpec, ...] = (
    # ------------------------------------------------- starter champions (3)
    _spec("ch_luna", "Luna, Star Regent", CardType.CHAMPION, Rarity.RARE,
          health=25, keywords=(("inspire", None),),
          text="Passive — Inspire: whenever a hero you control attacks, "
               "another hero you control gets +1/+0 until end of turn.",
          flavor="The stars answer her before she asks."),
    _spec("ch_grimmar", "Grimmar, Oathbreaker", CardType.CHAMPION,
          Rarity.RARE, health=28, keywords=(("blood_oath", None),),
          text="Passive — Blood Oath: whenever Grimmar loses life, put a "
               "Blood charge on him. At 5 charges: deal 3 damage to the "
               "enemy champion and heal 3.",
          flavor="Every scar is a promise he intends to keep."),
    _spec("ch_seren", "Seren, Tidebound Oracle", CardType.CHAMPION,
          Rarity.RARE, health=22, keywords=(("treasury", None),),
          text="Passive — Treasury: at the beginning of your turn, if you "
               "control no relics, create an Energy Potion relic.",
          flavor="The tide always pays its debts."),

    # ---------------------------------------------------------- barriers (3)
    _spec("b_starwall", "Wall of Falling Stars", CardType.BARRIER,
          Rarity.COMMON, cost=2, durability=4,
          keywords=(("reflect", 1),),
          flavor="Touch it and the sky touches back."),
    _spec("b_sanctum", "Sanctum of the Veiled", CardType.BARRIER,
          Rarity.UNCOMMON, cost=3, durability=5,
          keywords=(("ward", None), ("sanctuary", None)),
          flavor="Behind it, even fate must knock."),
    _spec("b_lastlight", "Bulwark of Last Light", CardType.BARRIER,
          Rarity.RARE, cost=4, durability=6,
          keywords=(("regenerate", 1), ("last_wall", None)),
          flavor="It has never fallen twice."),

    # ----------------------------------- keyword test heroes (engine v2 lab)
    _spec("t_umbral_stalker", "Umbral Stalker", CardType.HERO,
          Rarity.UNCOMMON, cost=3, attack=3, health=2,
          hero_types=["Assassin", "Ghost"], keywords=(("umbral", None),),
          flavor="You won't see the plane it walks."),
    _spec("t_veilwarden", "Veilwarden of Dusk", CardType.HERO,
          Rarity.UNCOMMON, cost=3, attack=2, health=4,
          hero_types=["Knight", "Ghost"], keywords=(("veil_pierce", None),),
          flavor="Both worlds fear his patrol."),
    _spec("t_broodmother", "Gravehatch Broodmother", CardType.HERO,
          Rarity.RARE, cost=4, attack=3, health=3,
          hero_types=["Summoner", "Zombie"], keywords=(("last_stand", None),),
          text="When Gravehatch Broodmother enters play, create two 1/1 "
               "Hatchling minions.",
          flavor="She is never outnumbered."),
    _spec("t_bannerlord", "Bannerlord of the Dawn", CardType.HERO,
          Rarity.RARE, cost=4, attack=2, health=4,
          hero_types=["General", "Human"],
          keywords=(("banner", None), ("rage", None)),
          text="When Bannerlord of the Dawn enters play, create a 1/1 "
               "Squire minion.",
          flavor="Minions fight harder beneath his colors."),
    _spec("t_stormvessel", "Vessel of Storms", CardType.HERO, Rarity.RARE,
          cost=3, attack=1, health=4, hero_types=["Wizard", "Oracle"],
          keywords=(("reservoir", None), ("discharge", None)),
          flavor="Lightning waits inside her like a held breath."),
    _spec("t_ascendant", "Pilgrim of the Astral Path", CardType.HERO,
          Rarity.EPIC, cost=5, attack=3, health=5,
          hero_types=["Monk", "Oracle"],
          keywords=(("ascension", None), ("astral", 3)),
          text="Transform: becomes Star-Crowned Pilgrim, a 6/8 with "
               "Starlight.",
          flavor="Three nights of stillness, then the stars."),
    _spec("t_blooddrinker", "Crimson Duelist", CardType.HERO,
          Rarity.UNCOMMON, cost=2, attack=2, health=3,
          hero_types=["Duelist", "Vampire"],
          keywords=(("bloodthirst", None), ("lifebound", None)),
          flavor="She fights best when losing."),
    _spec("t_devoted", "Sun-Sworn Devotee", CardType.HERO, Rarity.UNCOMMON,
          cost=3, attack=2, health=4, hero_types=["Priest", "Human"],
          keywords=(("devotion", None), ("purify", None)),
          flavor="His faith is arithmetic."),
    _spec("t_surgecaster", "Surgecaster Adept", CardType.HERO,
          Rarity.COMMON, cost=2, attack=2, health=2,
          hero_types=["Wizard", "Human"],
          keywords=(("surge", None), ("channel", None)),
          flavor="Every spell is a running start."),
    _spec("t_greedling", "Vault Greedling", CardType.HERO, Rarity.COMMON,
          cost=1, attack=1, health=2, hero_types=["Thief", "Golem"],
          keywords=(("greed", None), ("harvest", None)),
          flavor="It hoards what you forget to spend."),
)

BUILTIN_SPECS_BY_ID = {s.id: s for s in BUILTIN_SPECS}


def builtin_specs() -> tuple[CardSpec, ...]:
    return BUILTIN_SPECS


DECK_SIZE = 30
MAX_COPIES = 3
COPIES_BY_RARITY = {Rarity.COMMON: 3, Rarity.UNCOMMON: 3, Rarity.RARE: 2,
                    Rarity.EPIC: 2, Rarity.LEGENDARY: 1}


def starter_collection() -> dict[str, int]:
    """Copies of each card every account owns (economy comes later)."""
    return {c.card_id: COPIES_BY_RARITY[c.rarity] for c in CATALOG}


def max_copies(card_id: str) -> int:
    if card_type_of(card_id) == "champion":
        return 1
    card = by_id(card_id)
    if card is not None:
        return min(MAX_COPIES, COPIES_BY_RARITY[card.rarity])
    spec = spec_by_id(card_id)
    if spec is not None and spec.card_type.value != "minion":
        return min(MAX_COPIES, COPIES_BY_RARITY[Rarity.parse(spec.rarity.value)])
    return 0


def validate_deck(cards: dict[str, int],
                  collection: dict[str, int]) -> tuple[bool, str]:
    """Deck legality: size, per-card limits, ownership, known cards."""
    total = 0
    champions = 0
    for card_id, count in cards.items():
        kind = card_type_of(card_id)
        card = by_id(card_id)
        spec = spec_by_id(card_id)
        name = card.name if card else (spec.name if spec else card_id)
        if not kind:
            return False, f"Unknown card: {card_id}"
        if kind == "minion":
            return False, f"{name} is a minion token, not a deck card."
        if count < 1:
            return False, f"Bad count for {name}."
        if count > max_copies(card_id):
            return False, f"Too many copies of {name} (max {max_copies(card_id)})."
        if count > collection.get(card_id, 0):
            return False, f"You don't own {count}x {name}."
        if kind == "champion":
            champions += count
        total += count
    if champions == 0:
        return False, "Every deck must include a champion."
    if champions > 1:
        return False, "A deck can only have one champion."
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
        rarity=Rarity.parse(spec.rarity.value), attack=spec.attack,
        health=spec.health, text=spec.composed_text(),
        haste=spec.has_keyword("rush"),
    )


def spec_by_id(card_id: str):
    """Any known CardSpec: built-in first, then the official database."""
    spec = BUILTIN_SPECS_BY_ID.get(card_id)
    if spec is not None:
        return spec
    from arcanum.services.cards import get_spec
    return get_spec(card_id)


def all_specs() -> list:
    from arcanum.services.cards import official_cards
    out = list(BUILTIN_SPECS)
    seen = {s.id for s in out} | set(BY_ID)
    for spec in official_cards():
        if spec.id not in seen:
            out.append(spec)
    return out


def all_cards() -> tuple[CardDef, ...]:
    """Engine-playable pool: built-ins + heroes/spells/relics from specs."""
    extra = []
    for spec in all_specs():
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
    """Keyword dict for a spec-backed card (for the match engine)."""
    spec = spec_by_id(card_id)
    if spec is not None:
        return {ref.id: ref.value for ref in spec.keywords}
    return {}


def card_type_of(card_id: str) -> str:
    spec = spec_by_id(card_id)
    if spec is not None:
        return spec.card_type.value
    card = BY_ID.get(card_id)
    if card is None:
        return ""
    return {Kind.CREATURE: "hero", Kind.SPELL: "spell",
            Kind.RELIC: "relic"}[card.kind]


def full_collection() -> dict[str, int]:
    """Starter set + every collectible card (economy later)."""
    owned = starter_collection()
    for card in all_cards():
        owned.setdefault(card.card_id, COPIES_BY_RARITY[card.rarity])
    for spec in all_specs():
        if spec.card_type.value == "minion":
            continue
        owned.setdefault(spec.id, max_copies(spec.id))
    return owned


def starter_deck_cards() -> dict[str, int]:
    """The Starter Deck: 1 champion + a spread of testable cards (30 total)."""
    deck = {
        "ch_luna": 1,
        # early heroes
        "c_wisp": 2, "c_moth": 2, "t_greedling": 2, "t_surgecaster": 2,
        "t_blooddrinker": 2,
        # mid heroes incl. minion spawners + planes
        "t_umbral_stalker": 2, "t_veilwarden": 2, "t_devoted": 2,
        "t_broodmother": 2, "t_bannerlord": 1, "t_stormvessel": 2,
        # top end
        "t_ascendant": 1, "c_wyrm": 1,
        # spells + relics + barriers
        "s_star": 2, "s_insight": 1, "r_crystal": 1,
        "b_starwall": 1, "b_sanctum": 1,
    }
    assert sum(deck.values()) == DECK_SIZE, sum(deck.values())
    return deck
