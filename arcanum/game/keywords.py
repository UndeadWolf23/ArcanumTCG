"""Keyword & type registry — the rules vocabulary of Arcanum, as data.

Every keyword a card can carry is declared here with its category, reminder
text, whether it takes a numeric value (X), what charge kind it uses (if
any), and whether the match engine has scripted it yet. The Card Designer
reads this registry to offer keywords; the engine reads it to wire behavior;
validation reads it to reject nonsense (a Barrier with Lethal, etc.).

Adding a keyword = adding a row here + (eventually) scripting its trigger.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CardType(str, Enum):
    CHAMPION = "champion"
    HERO = "hero"
    MINION = "minion"
    SPELL = "spell"
    RELIC = "relic"
    BARRIER = "barrier"


class Rarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"

    @classmethod
    def parse(cls, value: str) -> "Rarity":
        value = str(value).lower()
        if value == "mythic":          # legacy cards published pre-rework
            return cls.LEGENDARY
        return cls(value)


@dataclass(frozen=True)
class KeywordDef:
    id: str
    name: str
    category: CardType          # which card type may carry it
    text: str                   # reminder text ({x} formats the value)
    has_value: bool = False     # "Thorns 2", "Astral 3", ...
    charge: str = ""            # charge kind this keyword creates/consumes
    implemented: bool = False   # scripted in the match engine yet?


def _hero(id_, name, text, has_value=False, charge="", implemented=False):
    return KeywordDef(id_, name, CardType.HERO, text, has_value, charge,
                      implemented)


def _champ(id_, name, text, has_value=False, charge="", implemented=False):
    return KeywordDef(id_, name, CardType.CHAMPION, text, has_value, charge,
                      implemented)


def _relic(id_, name, text, has_value=False, charge="", implemented=False):
    return KeywordDef(id_, name, CardType.RELIC, text, has_value, charge,
                      implemented)


def _barrier(id_, name, text, has_value=False, charge="", implemented=False):
    return KeywordDef(id_, name, CardType.BARRIER, text, has_value, charge,
                      implemented)


KEYWORDS: tuple[KeywordDef, ...] = (
    # ------------------------------------------------ hero: combat pack (LIVE)
    _hero("rush", "Rush", "Can attack the same turn it was played.",
          implemented=True),
    _hero("quick", "Quick",
          "When fighting another hero, this hero's damage is dealt first.",
          implemented=True),
    _hero("lethal", "Lethal",
          "Any damage this hero deals to another hero destroys that hero.",
          implemented=True),
    _hero("pierce", "Pierce",
          "When this hero deals combat damage to another hero, it also deals "
          "1 damage to their champion.", implemented=True),
    _hero("crush", "Crush",
          "Excess damage dealt by this hero to a hero is dealt to that "
          "hero's champion.", implemented=True),
    _hero("execute", "Execute",
          "When this hero damages a hero with 3 or less life, destroy that "
          "hero.", implemented=True),
    _hero("thorns", "Thorns {x}",
          "The first time this hero takes damage each turn, it deals {x} "
          "damage to the source.", has_value=True, implemented=True),
    _hero("soul_link", "Soul Link",
          "When this hero deals damage, your champion gains that much life.",
          implemented=True),
    _hero("feast", "Feast",
          "Whenever this hero destroys another hero, gain 1 energy.",
          implemented=True),
    _hero("undying", "Undying",
          "The first time this hero dies, return it to play with 1 health.",
          implemented=True),
    _hero("intelligent", "Intelligent",
          "When this hero deals damage to an opponent's champion, draw 1 "
          "card.", implemented=True),
    _hero("charged", "Charged {x}",
          "When this hero enters the battlefield, place a +{x}/+{x} "
          "charge on it.", has_value=True, charge="growth",
          implemented=True),

    # ------------------------------------------- hero: registered (engine v2)
    _hero("harvest", "Harvest",
          "When this hero deals damage to a champion, create an Energy "
          "Potion relic."),
    _hero("ascension", "Ascension",
          "At the beginning of your end step, this hero gains an Astral "
          "charge.", charge="astral"),
    _hero("astral", "Astral {x}",
          "At the beginning of your turn, if this hero has {x} or more "
          "Astral charges, transform it.", has_value=True, charge="astral"),
    _hero("starlight", "Starlight",
          "This hero gets +1/+1 for each permanent you control with an "
          "Astral counter.", implemented=True),
    _hero("devotion", "Devotion",
          "This hero gets +1/+1 for each point of life your champion has "
          "above their starting life."),
    _hero("lifebound", "Lifebound",
          "Whenever your champion gains life, this hero gains +1/+1 until "
          "end of turn."),
    _hero("bloodthirst", "Bloodthirst",
          "This hero gets +2/+0 while your champion has less life than the "
          "opposing champion."),
    _hero("channel", "Channel", "Exhaust this hero: gain 1 energy."),
    _hero("greed", "Greed",
          "At the beginning of your turn, if you have unused energy from the "
          "previous turn, this hero gains +1/+1."),
    _hero("last_stand", "Last Stand",
          "When this hero dies, create a minion with power and health equal "
          "to half this hero's, rounded down."),
    _hero("reanimate", "Reanimate {x}",
          "You may play this hero from your void by paying {x}.",
          has_value=True),
    _hero("reservoir", "Reservoir",
          "When you cast a spell, this hero gains a Lightning charge.",
          charge="lightning"),
    _hero("discharge", "Discharge",
          "Remove any number of Lightning charges from this hero: deal that "
          "much damage to a target hero.", charge="lightning"),
    _hero("surge", "Surge",
          "Whenever you cast a spell, this hero gains +2/+0 until end of "
          "turn."),
    _hero("resonate", "Resonate",
          "Whenever this hero gains any charge, another target hero you "
          "control gains a charge of a kind it already has.", implemented=True),
    _hero("banner", "Banner", "Minions you control have +1/+0.", implemented=True),
    _hero("pack", "Pack {x}",
          "All other heroes you control that share a type with this hero "
          "get +{x}/+{x}.", has_value=True),
    _hero("consume", "Consume",
          "Destroy a friendly minion: this hero gains +2/+2 until end of "
          "turn.", implemented=True),
    _hero("purify", "Purify",
          "Remove a charge from a friendly hero: heal that hero for 2.", implemented=True),
    _hero("rage", "Rage",
          "Whenever your champion takes damage, this hero gains +1/+1."),
    _hero("umbral", "Umbral",
          "This hero can only be blocked by heroes with Umbral or Veil "
          "Pierce.", implemented=True),
    _hero("veil_pierce", "Veil Pierce",
          "This hero can block Umbral heroes.", implemented=True),
    _hero("relicbound_hero", "Relicbound",
          "While you control this hero, your relics with Relicbound cannot "
          "be destroyed."),

    # ------------------------------------------------------------ champion
    _champ("rebirth", "Rebirth",
           "The first time your champion would be defeated, restore it to "
           "10 life instead.", implemented=True),
    _champ("sacrifice", "Sacrifice",
           "Once per turn, you may sacrifice a minion to activate this "
           "champion's Sacrifice ability."),
    _champ("command", "Command",
           "Your minions get +1/+1 while this champion is in play.", implemented=True),
    _champ("inspire", "Inspire",
           "Whenever a hero you control attacks, another hero you control "
           "gets +1/+0 until end of turn.", implemented=True),
    _champ("rally", "Rally",
           "Whenever you create your second minion each turn, draw a card.", implemented=True),
    _champ("ascendant", "Ascendant",
           "Whenever one of your heroes transforms, your champion gains 2 "
           "life and 1 energy.", implemented=True),
    _champ("blood_oath", "Blood Oath",
           "Whenever your champion loses life, put a Blood charge on it. At "
           "5 Blood charges, trigger its Blood Oath ability.",
           charge="blood", implemented=True),
    _champ("treasury", "Treasury",
           "At the beginning of your turn, if you have no relics, create an "
           "Energy Potion relic.", implemented=True),

    # ------------------------------------------------------------ relic
    _relic("empower", "Empower",
           "Whenever you gain energy, put an Empower charge on this relic. "
           "Remove 3: trigger its Empower ability.", charge="empower"),
    _relic("tribute", "Tribute",
           "Sacrifice this relic: trigger its Tribute ability."),
    _relic("salvage", "Salvage",
           "When this relic is destroyed, return another target relic from "
           "your void to your hand."),
    _relic("fortune", "Fortune",
           "At the beginning of your turn, if you control no other relics, "
           "trigger this relic's Fortune ability."),
    _relic("attune", "Attune",
           "When this relic enters play, choose a hero. Whenever that hero "
           "gains a charge, trigger this relic's Attune ability."),
    _relic("hoard", "Hoard",
           "Whenever you gain extra energy, put a Hoard charge on this "
           "relic. Remove 5: gain 3 energy.", charge="hoard", implemented=True),
    _relic("ritual", "Ritual {x}",
           "At the beginning of your end step, if you met this relic's "
           "condition this turn, put a Ritual charge on it. At {x} charges, "
           "trigger its Ritual ability.", has_value=True, charge="ritual"),
    _relic("relicbound_relic", "Relicbound",
           "This relic cannot be destroyed while you control a hero with "
           "Relicbound."),
    _relic("conduit", "Conduit",
           "The first time each turn one of your heroes gains a charge, you "
           "may move that charge to this relic."),
    _relic("offering", "Offering",
           "Whenever you sacrifice a hero or minion, you may put an Offering "
           "charge on this relic.", charge="offering"),
    _relic("countdown", "Countdown {x}",
           "Enters with {x} Countdown charges. At the beginning of your "
           "turn, remove one. When the last is removed, trigger its "
           "Countdown ability.", has_value=True, charge="countdown"),
    _relic("legacy", "Legacy",
           "When this relic is destroyed, its Legacy ability remains active "
           "until the end of your next turn."),

    # ------------------------------------------------------------ barrier
    _barrier("reflect", "Reflect {x}",
             "Whenever this Barrier takes damage, deal {x} damage to the "
             "source.", has_value=True),
    _barrier("fortify", "Fortify {x}",
             "Your champion and heroes take {x} less damage from attacks "
             "while this Barrier is active.", has_value=True),
    _barrier("ward", "Ward",
             "The first time each turn this Barrier would be damaged, "
             "prevent that damage."),
    _barrier("regenerate", "Regenerate {x}",
             "At the beginning of your turn, restore {x} durability to this "
             "Barrier.", has_value=True),
    _barrier("sanctuary", "Sanctuary",
             "Heroes protected by this Barrier cannot be targeted by "
             "opposing abilities.", implemented=True),
    _barrier("aegis", "Aegis",
             "The first time each turn a protected hero would be destroyed, "
             "prevent it and destroy this Barrier instead.", implemented=True),
    _barrier("barrierlink", "Barrierlink",
             "When another Barrier you control is destroyed, move all of its "
             "remaining charges onto this Barrier.", implemented=True),
    _barrier("last_wall", "Last Wall",
             "When this Barrier is destroyed, your champion cannot take "
             "damage until the beginning of your next turn."),
)

KEYWORDS_BY_ID: dict[str, KeywordDef] = {k.id: k for k in KEYWORDS}


def keywords_for(card_type: CardType) -> list[KeywordDef]:
    return [k for k in KEYWORDS if k.category is card_type]


def format_keyword(kw_id: str, value: int | None = None) -> str:
    """'thorns', 2 -> 'Thorns 2 — The first time ... deals 2 damage ...'"""
    kw = KEYWORDS_BY_ID[kw_id]
    name = kw.name.format(x=value) if kw.has_value else kw.name
    text = kw.text.format(x=value) if kw.has_value else kw.text
    return f"{name} — {text}"


# --------------------------------------------------------------- hero types
# (the source list contained "Detective" twice; deduplicated to 60)
HERO_TYPES: tuple[str, ...] = (
    "Fighter", "Knight", "Berserker", "Duelist", "Monk", "Ninja", "Archer",
    "Gunslinger", "Wrestler", "Samurai",
    "Wizard", "Witch", "Warlock", "Druid", "Necromancer", "Alchemist",
    "Summoner", "Illusionist", "Psychic", "Oracle",
    "Detective", "Inventor", "Scientist", "Explorer", "Scholar", "Strategist",
    "Treasure Hunter", "Monster Hunter",
    "Thief", "Assassin", "Spy", "Pirate", "Bounty Hunter", "Mercenary",
    "Outlaw", "Con Artist",
    "King", "Queen", "General", "Captain", "Merchant", "Blacksmith",
    "Healer", "Priest", "Cultist", "Rebel",
    "Human", "Dog", "Cat", "Wolf", "Dragon", "Demon", "Angel", "Vampire",
    "Werewolf", "Zombie", "Ghost", "Golem", "Robot", "Alien",
)


# ---------------------------------------------------------------------------
# Single source of truth for engine support. Set at import so inline flags
# can never drift from what the match engine actually executes.
# ---------------------------------------------------------------------------
ENGINE_IMPLEMENTED = {
    # combat core
    "rush", "quick", "lethal", "pierce", "crush", "execute", "thorns",
    "soul_link", "feast", "undying", "umbral", "veil_pierce",
    # triggers, charges, statics
    "ascension", "astral", "greed", "surge", "reservoir", "rage",
    "lifebound", "harvest", "last_stand", "bloodthirst", "devotion",
    "starlight", "banner", "resonate", "intelligent", "charged",
    "reanimate",
    # activated abilities
    "channel", "discharge", "tribute", "consume", "purify",
    # champion passives
    "inspire", "treasury", "blood_oath", "rebirth", "command", "rally",
    "ascendant",
    # relics
    "hoard", "fortune", "countdown", "empower", "salvage", "conduit",
    "offering", "relicbound_relic", "relicbound_hero",
    # barriers
    "ward", "reflect", "regenerate", "last_wall", "sanctuary", "aegis",
    "barrierlink",
}

for _def in KEYWORDS:
    try:
        _def.implemented = _def.id in ENGINE_IMPLEMENTED
    except AttributeError:                       # frozen dataclass
        object.__setattr__(_def, "implemented",
                           _def.id in ENGINE_IMPLEMENTED)
