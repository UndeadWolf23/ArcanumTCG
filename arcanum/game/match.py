"""Match rules core — deliberately pygame-free.

Single source of truth for legality: mana, phases, zones, targeting. The
scene renders it and the dummy opponent reads it; neither mutates state
directly. Actions return (ok, reason, events) — `events` is a list of dicts
describing what happened (cards drawn, creatures destroyed, mana gained) so
the UI can animate results without re-deriving them. The same event stream
maps onto the network protocol's `event.*` messages when the server exists.

Rules for this milestone:
* Each player starts with a CHAMPION already in play (health 25). Defeating
  the enemy champion will be the win condition once combat lands.
* Opening hand: 7 cards — four creatures plus the three example specials
  (Astral Insight, Ley Crystal, Falling Star), shuffled.
* Mana (Hearthstone model): +1 max at the start of your turn, cap 12,
  refill to max. Ley Crystal raises the cap by 1 immediately.
* Phases: draw -> main -> combat -> end. Playing cards: your main phase only.
* Zones: creatures (max 6 per player), relics (max 3 per player).
* Targeted spells require a legal target (enemy creature) to be playable.
"""
from __future__ import annotations

import itertools
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

log = logging.getLogger(__name__)

MAX_MANA = 12
CREATURE_LIMIT = 6
RELIC_LIMIT = 3
HAND_LIMIT = 10
STARTING_HAND = 7
CHAMPION_HEALTH = 25


class Phase(str, Enum):
    DRAW = "draw"
    MAIN = "main"
    COMBAT = "combat"
    END = "end"


PHASE_ORDER = (Phase.DRAW, Phase.MAIN, Phase.COMBAT, Phase.END)


@dataclass
class StackItem:
    """One pending effect on the resolution stack (LIFO). Triggered abilities
    stack in APNAP order — the active player's triggers go on first, so the
    non-active player's resolve first, exactly like the MTG rule. The stack
    also hosts activated abilities; priority-window responses are the future
    hook and slot into this same structure."""
    owner: int
    source_uid: int
    effect: str                  # "trigger:<kw>" | "ability:<name>"
    value: int = 0
    target_uid: int = 0

STACK_SAFETY_LIMIT = 100


class Kind(str, Enum):
    CREATURE = "creature"
    SPELL = "spell"
    RELIC = "relic"
    CHAMPION = "champion"


class Effect(str, Enum):
    NONE = "none"
    DRAW_TWO = "draw_two"            # Astral Insight
    MANA_CRYSTAL = "mana_crystal"    # Ley Crystal
    DESTROY_TARGET = "destroy_target"  # Falling Star


@dataclass
class CardInstance:
    uid: int
    name: str
    kind: Kind
    cost: int
    attack: int = 0
    health: int = 0
    max_health: int = 0
    exhausted: bool = False     # has attacked this turn
    sick: bool = False          # summoned this turn; cannot attack yet
    haste: bool = False         # "unless otherwise specified": skips sickness
    keywords: dict = field(default_factory=dict)   # kw_id -> value (or None)
    charges: dict = field(default_factory=dict)    # charge kind -> count
    temp_attack: int = 0        # "+X/+0 until end of turn" buffs
    is_token: bool = False      # minions spawned by effects
    transformed: bool = False
    thorns_used: bool = False   # Thorns triggers once per turn
    undying_spent: bool = False # Undying triggers once ever

    def add_charge(self, kind: str, amount: int = 1) -> int:
        self.charges[kind] = self.charges.get(kind, 0) + amount
        return self.charges[kind]

    def has_kw(self, kw_id: str) -> bool:
        return kw_id in self.keywords

    def kw_value(self, kw_id: str, default: int = 0) -> int:
        value = self.keywords.get(kw_id)
        return default if value is None else int(value)
    text: str = ""
    effect: Effect = Effect.NONE
    needs_target: bool = False

    @property
    def damaged(self) -> bool:
        return 0 < self.health < self.max_health


@dataclass
class DrawResult:
    card: Optional[CardInstance] = None
    skipped: bool = False
    burned: bool = False


@dataclass
class PlayerState:
    name: str
    champion: Optional[CardInstance] = None
    hand: list[CardInstance] = field(default_factory=list)
    pile: list[CardInstance] | None = None      # None = endless random deck
    unspent_last_turn: int = 0
    board: list[CardInstance] = field(default_factory=list)    # creatures
    relics: list[CardInstance] = field(default_factory=list)
    max_mana: int = 0
    mana: int = 0


Event = dict[str, Any]


class MatchState:
    """Authoritative state for one match. Index 0 = local player, 1 = opponent."""

    def __init__(self, local_name: str = "You", opponent_name: str = "Opponent",
                 seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._uids = itertools.count(1)
        self.players = [PlayerState(local_name), PlayerState(opponent_name)]
        self.active = 0
        self.phase: Phase = Phase.DRAW
        self.turn_number = 0
        self.started = False
        self.winner: Optional[int] = None
        self.stack: list[StackItem] = []
        self.pending_turn_events: list[Event] = []
        self.first_player = 0
        self._first_draw_pending = True

    # ------------------------------------------------------------ card factory
    def _uid(self) -> int:
        return next(self._uids)

    def make_creature(self, cost: int | None = None) -> CardInstance:
        cost = self._rng.randint(1, 7) if cost is None else cost
        power = max(1, cost)
        return CardInstance(self._uid(), "Astral Familiar", Kind.CREATURE, cost,
                            attack=power, health=power, max_health=power,
                            text="A loyal spark of starlight.")

    def make_insight(self) -> CardInstance:
        return CardInstance(self._uid(), "Astral Insight", Kind.SPELL, 3,
                            text="Draw 2 cards.", effect=Effect.DRAW_TWO)

    def make_ley_crystal(self) -> CardInstance:
        return CardInstance(self._uid(), "Ley Crystal", Kind.RELIC, 2,
                            text="Gain +1 maximum mana.",
                            effect=Effect.MANA_CRYSTAL)

    def make_falling_star(self) -> CardInstance:
        return CardInstance(self._uid(), "Falling Star", Kind.SPELL, 3,
                            text="Destroy an enemy creature.",
                            effect=Effect.DESTROY_TARGET, needs_target=True)

    def make_champion(self, name: str,
                      health: int = CHAMPION_HEALTH,
                      text: str = "Defeat the enemy champion to win.") -> CardInstance:
        return CardInstance(self._uid(), name, Kind.CHAMPION, 0,
                            attack=0, health=health, max_health=health,
                            text=text)

    def _instance_from_def(self, card_def) -> CardInstance:
        from arcanum.game import catalog as cat
        inst = CardInstance(
            self._uid(), card_def.name, card_def.kind, card_def.cost,
            attack=card_def.attack, health=card_def.health,
            max_health=card_def.health, text=card_def.text,
            effect=card_def.effect, needs_target=card_def.needs_target,
            haste=card_def.haste)
        inst.keywords = dict(cat.official_keywords(card_def.card_id))
        return inst

    def _build_pile(self, index: int, deck: dict) -> None:
        """Turn a validated deck list into this player's champion + pile."""
        from arcanum.game import catalog as cat
        player = self.players[index]
        pile: list[CardInstance] = []
        skipped_barriers = 0
        for card_id, count in deck.items():
            kind = cat.card_type_of(card_id)
            if kind == "champion":
                spec = cat.spec_by_id(card_id)
                if spec is not None:
                    player.champion = self.make_champion(
                        spec.name, health=max(1, spec.health),
                        text=spec.composed_text() or
                        "Defeat the enemy champion to win.")
                continue
            if kind == "barrier":
                skipped_barriers += count      # zone lands in engine v2
                continue
            card_def = cat.by_id(card_id)
            if card_def is None:
                log.warning("Deck card %s unknown to the engine; skipped.",
                            card_id)
                continue
            for _ in range(count):
                pile.append(self._instance_from_def(card_def))
        if skipped_barriers:
            log.info("%d barrier(s) held out of the pile (engine v2).",
                     skipped_barriers)
        self._rng.shuffle(pile)
        player.pile = pile

    def random_card(self) -> CardInstance:
        roll = self._rng.random()
        if roll < 0.70:
            return self.make_creature()
        if roll < 0.82:
            return self.make_insight()
        if roll < 0.91:
            return self.make_ley_crystal()
        return self.make_falling_star()

    # ------------------------------------------------------------ helpers
    def player(self, index: int) -> PlayerState:
        return self.players[index]

    def find_in_hand(self, index: int, uid: int) -> Optional[CardInstance]:
        return next((c for c in self.players[index].hand if c.uid == uid), None)

    def is_local_turn(self) -> bool:
        return self.active == 0

    def valid_targets(self, index: int, card: CardInstance) -> list[CardInstance]:
        """Legal targets for a targeted card (currently: enemy creatures)."""
        if not card.needs_target:
            return []
        return list(self.players[1 - index].board)

    # ------------------------------------------------------------ lifecycle
    def start(self, decks: list | None = None) -> None:
        if self.started:
            log.warning("MatchState.start() called twice; ignoring.")
            return
        self.started = True
        champion_names = ("Archmagus Lyra", "Umbral Sovereign")
        for i, player in enumerate(self.players):
            deck = decks[i] if decks and i < len(decks) and decks[i] else None
            if deck:
                self._build_pile(i, deck)
                if player.champion is None:
                    player.champion = self.make_champion(champion_names[i])
                player.hand = [player.pile.pop()
                               for _ in range(min(7, len(player.pile)))]
            else:
                player.champion = self.make_champion(champion_names[i])
                hand = [self.make_creature(c) for c in (1, 2, 5, 7)]
                hand += [self.make_insight(), self.make_ley_crystal(),
                         self.make_falling_star()]
                self._rng.shuffle(hand)
                player.hand = hand
        self._begin_turn(self.first_player)

    def _begin_turn(self, index: int) -> None:
        # remember unspent energy for Greed, then rotate the turn
        self.players[self.active].unspent_last_turn = self.players[self.active].mana
        self.active = index
        self.phase = Phase.DRAW
        self.turn_number += 1
        player = self.players[index]
        player.max_mana = min(MAX_MANA, player.max_mana + 1)
        player.mana = player.max_mana
        for side in self.players:            # "until end of turn" expires
            for creature in side.board:
                creature.temp_attack = 0
        for creature in player.board:
            creature.exhausted = False
            creature.sick = False
            creature.thorns_used = False
        self.pending_turn_events: list[Event] = []
        self.upkeep_triggers(self.pending_turn_events)

    # ------------------------------------------------------------ phases
    def advance_phase(self) -> Phase:
        if not self.started:
            raise RuntimeError("Match has not started.")
        order = list(PHASE_ORDER)
        i = order.index(self.phase)
        if i + 1 < len(order):
            self.phase = order[i + 1]
            if self.phase is Phase.END:
                self.pending_turn_events = []
                self.end_step_triggers(self.pending_turn_events)
        else:
            self._begin_turn(1 - self.active)
        return self.phase

    # ------------------------------------------------------------ drawing
    def _draw_one(self, index: int) -> Event:
        player = self.players[index]
        if player.pile is not None:
            if not player.pile:
                log.info("%s's deck is empty; no card drawn.", player.name)
                return {"type": "draw", "player": index, "empty": True}
            card = player.pile.pop()
        else:
            card = self.random_card()
        if len(player.hand) >= HAND_LIMIT:
            log.info("%s over hand limit; %s burned.", player.name, card.name)
            return {"type": "draw", "player": index, "card": card, "burned": True}
        player.hand.append(card)
        return {"type": "draw", "player": index, "card": card, "burned": False}

    def draw_step(self, index: int) -> DrawResult:
        if index != self.active or self.phase is not Phase.DRAW:
            log.warning("draw_step out of order (player=%s phase=%s)", index, self.phase)
            return DrawResult()
        if self._first_draw_pending and index == self.first_player:
            self._first_draw_pending = False
            return DrawResult(skipped=True)
        event = self._draw_one(index)
        return DrawResult(card=event.get("card"),
                          burned=bool(event.get("burned", False)))

    # ------------------------------------------------------------ playing
    def can_play(self, index: int, uid: int) -> tuple[bool, str]:
        if not self.started:
            return False, "The match hasn't started."
        if self.winner is not None:
            return False, "The match is over."
        if index != self.active:
            return False, "It isn't your turn."
        if self.phase is not Phase.MAIN:
            return False, "Cards can only be played in your main phase."
        card = self.find_in_hand(index, uid)
        if card is None:
            return False, "That card isn't in your hand."
        player = self.players[index]
        if player.mana < card.cost:
            return False, f"Not enough mana ({player.mana}/{card.cost})."
        if card.kind is Kind.CREATURE and len(player.board) >= CREATURE_LIMIT:
            return False, f"Creature row is full ({CREATURE_LIMIT} max)."
        if card.kind is Kind.RELIC and len(player.relics) >= RELIC_LIMIT:
            return False, f"Relic slots are full ({RELIC_LIMIT} max)."
        if card.kind is Kind.CHAMPION:
            return False, "Champions cannot be played from hand."
        if card.needs_target and not self.valid_targets(index, card):
            return False, "No valid targets."
        return True, ""

    def play_card(self, index: int, uid: int,
                  target_uid: int | None = None) -> tuple[bool, str, list[Event]]:
        ok, reason = self.can_play(index, uid)
        if not ok:
            return False, reason, []
        card = self.find_in_hand(index, uid)
        assert card is not None
        player = self.players[index]

        # targeted cards must name a legal target at play time
        target: Optional[CardInstance] = None
        if card.needs_target:
            target = next((c for c in self.valid_targets(index, card)
                           if c.uid == target_uid), None)
            if target is None:
                return False, "Choose an enemy creature to target.", []

        player.hand.remove(card)
        player.mana -= card.cost
        events: list[Event] = [{"type": "played", "player": index, "card": card}]

        if card.kind is Kind.CREATURE:
            card.sick = not (card.haste or card.has_kw("rush"))
            player.board.append(card)
            # Enter-the-battlefield effects (e.g. Charged) resolve before
            # state-based actions check for lethal (<=0) toughness — a
            # 0/0 creature only survives if something pumps it first.
            self._enter_battlefield(index, card, events)
            self._resolve_state_based_death(index, card, events)
        elif card.kind is Kind.RELIC:
            player.relics.append(card)
            if card.effect is Effect.MANA_CRYSTAL:
                before = player.max_mana
                player.max_mana = min(MAX_MANA, player.max_mana + 1)
                gained = player.max_mana - before
                player.mana = min(player.max_mana, player.mana + gained)
                events.append({"type": "mana", "player": index, "amount": gained})
        elif card.kind is Kind.SPELL:
            if card.effect is Effect.DRAW_TWO:
                events.append(self._draw_one(index))
                events.append(self._draw_one(index))
            elif card.effect is Effect.DESTROY_TARGET:
                assert target is not None
                enemy = self.players[1 - index]
                enemy.board.remove(target)
                events.append({"type": "destroy", "player": 1 - index,
                               "uid": target.uid})

        log.info("%s plays %s (cost %d, %d mana left).",
                 player.name, card.name, card.cost, player.mana)
        if card.kind is Kind.SPELL:
            self.on_spell_cast(index, events)
        return True, "", events

    # ------------------------------------------------------- entering play
    def _enter_battlefield(self, index: int, card: CardInstance,
                            events: list[Event]) -> None:
        """Trigger a creature's own enter-the-battlefield keywords.

        Called once, right after the card is placed on the board and
        before any state-based death check, so a permanent stat boost
        here (Charged) can save a creature that would otherwise die for
        having 0 toughness.
        """
        if card.has_kw("charged"):
            bump = card.kw_value("charged", 1)
            card.attack += bump
            card.health += bump
            card.max_health += bump
            events.append({"type": "keyword", "keyword": "charged",
                           "player": index, "uid": card.uid,
                           "attack": card.attack, "health": card.health})

    def _resolve_state_based_death(self, index: int, card: CardInstance,
                                    events: list[Event]) -> bool:
        """Check one permanent for lethal (<=0) toughness and, if so, kill
        it — honoring Undying. Returns True if the card actually died
        (Undying saves it, so that case returns False)."""
        side = self.players[index]
        if card not in side.board or card.health > 0:
            return False
        if card.has_kw("undying") and not card.undying_spent:
            card.undying_spent = True
            card.health = 1
            events.append({"type": "keyword", "keyword": "undying",
                           "player": index, "uid": card.uid, "health": 1})
            return False
        side.board.remove(card)
        events.append({"type": "death", "player": index, "uid": card.uid})
        self.on_hero_died(card, index, events)
        return True

    # ------------------------------------------------------------ combat
    def can_attack(self, index: int, attacker_uid: int) -> tuple[bool, str]:
        if not self.started:
            return False, "The match hasn't started."
        if self.winner is not None:
            return False, "The match is over."
        if index != self.active:
            return False, "It isn't your turn."
        if self.phase is not Phase.COMBAT:
            return False, "Creatures attack during your combat phase."
        attacker = next((c for c in self.players[index].board
                         if c.uid == attacker_uid), None)
        if attacker is None:
            return False, "That creature isn't on your board."
        if attacker.exhausted:
            return False, f"{attacker.name} has already attacked this turn."
        if attacker.sick:
            return False, (f"{attacker.name} is still being summoned — "
                           "it can attack next turn.")
        return True, ""

    def valid_attack_targets(self, index: int) -> list[CardInstance]:
        """Enemy creatures; the enemy champion only once their board is empty."""
        enemy = self.players[1 - index]
        if enemy.board:
            return list(enemy.board)
        return [enemy.champion] if enemy.champion else []

    # ------------------------------------------------------------ the stack
    def _find_card(self, uid: int):
        """(owner, card, zone) anywhere on the table."""
        for i, player in enumerate(self.players):
            for zone_name, zone in (("board", player.board),
                                    ("relics", player.relics)):
                for card in zone:
                    if card.uid == uid:
                        return i, card, zone_name
            if player.champion is not None and player.champion.uid == uid:
                return i, player.champion, "champion"
        return None, None, None

    def _heroes_with(self, index: int, kw: str) -> list[CardInstance]:
        return [c for c in self.players[index].board if c.has_kw(kw)]

    def _queue_triggers(self, items: list[StackItem]) -> None:
        """APNAP: active player's triggers are pushed FIRST (resolve last)."""
        items.sort(key=lambda it: 0 if it.owner == self.active else 1)
        self.stack.extend(items)

    def _resolve_stack(self, events: list) -> None:
        guard = 0
        while self.stack:
            guard += 1
            if guard > STACK_SAFETY_LIMIT:
                log.error("Stack safety limit hit; clearing.")
                self.stack.clear()
                break
            item = self.stack.pop()
            try:
                self._resolve_item(item, events)
            except Exception:  # noqa: BLE001 - one bad effect can't end a match
                log.exception("Stack item %s failed to resolve", item.effect)

    def _buff(self, card: CardInstance, owner: int, atk: int, hp: int,
              events: list, temp: bool = False) -> None:
        if temp:
            card.temp_attack += atk
        else:
            card.attack += atk
            card.health += hp
            card.max_health += hp
        events.append({"type": "buff", "player": owner, "uid": card.uid,
                       "attack": atk, "health": 0 if temp else hp,
                       "temp": temp})

    def _spawn_token(self, owner: int, name: str, atk: int, hp: int,
                     events: list) -> None:
        side = self.players[owner]
        if len(side.board) >= CREATURE_LIMIT or hp < 1:
            return
        token = CardInstance(self._uid(), name, Kind.CREATURE, 0,
                             attack=max(0, atk), health=hp, max_health=hp,
                             text="A summoned minion.", is_token=True,
                             sick=True)
        side.board.append(token)
        events.append({"type": "spawn", "player": owner, "card": token})

    def _grant_potion(self, owner: int, events: list) -> None:
        potion = CardInstance(self._uid(), "Energy Potion", Kind.RELIC, 0,
                              text="Sacrifice this relic: gain 1 energy.")
        potion.keywords = {"tribute": None}
        self.players[owner].relics.append(potion)
        events.append({"type": "spawn", "player": owner, "card": potion,
                       "zone": "relics"})

    def _resolve_item(self, item: StackItem, events: list) -> None:
        owner, card, _zone = self._find_card(item.source_uid)
        side = self.players[item.owner]
        effect = item.effect
        if effect == "trigger:ascension" and card is not None:
            count = card.add_charge("astral")
            events.append({"type": "charge", "player": item.owner,
                           "uid": card.uid, "kind": "astral",
                           "count": count})
        elif effect == "trigger:astral" and card is not None:
            need = card.kw_value("astral", 3)
            if not card.transformed and card.charges.get("astral", 0) >= need:
                card.transformed = True
                card.attack += 3
                card.health += 3
                card.max_health += 3
                events.append({"type": "keyword", "keyword": "transform",
                               "player": item.owner, "uid": card.uid,
                               "attack": card.attack, "health": card.health})
        elif effect == "trigger:greed" and card is not None:
            self._buff(card, item.owner, 1, 1, events)
            events.append({"type": "keyword", "keyword": "greed",
                           "player": item.owner, "uid": card.uid})
        elif effect == "trigger:surge" and card is not None:
            self._buff(card, item.owner, 2, 0, events, temp=True)
            events.append({"type": "keyword", "keyword": "surge",
                           "player": item.owner, "uid": card.uid})
        elif effect == "trigger:reservoir" and card is not None:
            count = card.add_charge("lightning")
            events.append({"type": "charge", "player": item.owner,
                           "uid": card.uid, "kind": "lightning",
                           "count": count})
        elif effect == "trigger:rage" and card is not None:
            self._buff(card, item.owner, 1, 1, events)
            events.append({"type": "keyword", "keyword": "rage",
                           "player": item.owner, "uid": card.uid})
        elif effect == "trigger:lifebound" and card is not None:
            self._buff(card, item.owner, 1, 0, events, temp=True)
            events.append({"type": "keyword", "keyword": "lifebound",
                           "player": item.owner, "uid": card.uid})
        elif effect == "trigger:harvest":
            self._grant_potion(item.owner, events)
            events.append({"type": "keyword", "keyword": "harvest",
                           "player": item.owner, "uid": item.source_uid})
        elif effect == "trigger:last_stand":
            self._spawn_token(item.owner, "Last Stand Minion",
                              item.value // 1000, item.value % 1000, events)
            events.append({"type": "keyword", "keyword": "last_stand",
                           "player": item.owner, "uid": item.source_uid})

    # -------------------------------------------------- trigger entry points
    def on_spell_cast(self, index: int, events: list) -> None:
        items = []
        for kw in ("surge", "reservoir"):
            for hero in self._heroes_with(index, kw):
                items.append(StackItem(index, hero.uid, f"trigger:{kw}"))
        self._queue_triggers(items)
        self._resolve_stack(events)

    def on_champion_damaged(self, index: int, events: list) -> None:
        items = [StackItem(index, h.uid, "trigger:rage")
                 for h in self._heroes_with(index, "rage")]
        self._queue_triggers(items)
        self._resolve_stack(events)

    def on_champion_life_gain(self, index: int, events: list) -> None:
        items = [StackItem(index, h.uid, "trigger:lifebound")
                 for h in self._heroes_with(index, "lifebound")]
        self._queue_triggers(items)
        self._resolve_stack(events)

    def on_hero_hit_champion(self, hero: CardInstance, owner: int,
                             events: list) -> None:
        if hero.has_kw("harvest"):
            self._queue_triggers([StackItem(owner, hero.uid,
                                            "trigger:harvest")])
            self._resolve_stack(events)

    def on_hero_died(self, card: CardInstance, owner: int,
                     events: list) -> None:
        if card.has_kw("last_stand"):
            atk, hp = card.attack // 2, card.max_health // 2
            self._queue_triggers([StackItem(owner, card.uid,
                                            "trigger:last_stand",
                                            value=atk * 1000 + hp)])
            self._resolve_stack(events)

    def upkeep_triggers(self, events: list) -> None:
        """Start-of-turn segment for the active player (Greed, Astral)."""
        index = self.active
        items = []
        for hero in self._heroes_with(index, "greed"):
            if self.players[index].unspent_last_turn > 0:
                items.append(StackItem(index, hero.uid, "trigger:greed"))
        for hero in self._heroes_with(index, "astral"):
            items.append(StackItem(index, hero.uid, "trigger:astral"))
        self._queue_triggers(items)
        self._resolve_stack(events)

    def end_step_triggers(self, events: list) -> None:
        """End-of-turn segment for the active player (Ascension)."""
        index = self.active
        items = [StackItem(index, h.uid, "trigger:ascension")
                 for h in self._heroes_with(index, "ascension")]
        self._queue_triggers(items)
        self._resolve_stack(events)

    # ----------------------------------------------- effective (shown) stats
    def effective_attack(self, card: CardInstance, owner: int) -> int:
        value = card.attack + card.temp_attack
        me = self.players[owner].champion
        them = self.players[1 - owner].champion
        if card.has_kw("bloodthirst") and me is not None and them is not None \
                and me.health < them.health:
            value += 2
        if card.has_kw("devotion") and me is not None:
            value += max(0, me.health - me.max_health)
        return max(0, value)

    # ------------------------------------------------- activated abilities
    ABILITY_KEYWORDS = ("channel", "discharge", "tribute")

    def available_abilities(self, index: int, uid: int) -> list[str]:
        owner, card, zone = self._find_card(uid)
        if owner != index or card is None:
            return []
        if self.active != index or self.phase is not Phase.MAIN:
            return []
        out = []
        if card.has_kw("channel") and not card.exhausted and not card.sick:
            out.append("channel")
        if card.has_kw("discharge") and card.charges.get("lightning", 0) > 0:
            out.append("discharge")
        if card.has_kw("tribute") and zone == "relics":
            out.append("tribute")
        return out

    def activate(self, index: int, uid: int, ability: str,
                 target_uid: int = 0):
        events: list[Event] = []
        if ability not in self.available_abilities(index, uid):
            return False, "That ability can't be used right now.", events
        owner, card, zone = self._find_card(uid)
        player = self.players[index]
        if ability == "channel":
            card.exhausted = True
            if player.mana < MAX_MANA:
                player.mana += 1
            events.append({"type": "keyword", "keyword": "channel",
                           "player": index, "uid": uid, "mana": player.mana})
            events.append({"type": "mana", "player": index,
                           "mana": player.mana, "max_mana": player.max_mana})
        elif ability == "discharge":
            t_owner, target, t_zone = self._find_card(target_uid)
            if target is None or t_zone != "board" or t_owner == index:
                return False, "Discharge needs an enemy hero target.", events
            amount = card.charges.pop("lightning", 0)
            if amount <= 0:
                return False, "No Lightning charges stored.", events
            events.append({"type": "keyword", "keyword": "discharge",
                           "player": index, "uid": uid, "amount": amount})
            target.health -= amount
            events.append({"type": "damage", "player": t_owner,
                           "uid": target.uid, "amount": amount,
                           "health": target.health})
            events.append({"type": "charge", "player": index, "uid": uid,
                           "kind": "lightning", "count": 0})
            if target.health <= 0:
                if target.has_kw("undying") and not target.undying_spent:
                    target.undying_spent = True
                    target.health = 1
                    events.append({"type": "keyword", "keyword": "undying",
                                   "player": t_owner, "uid": target.uid,
                                   "health": 1})
                else:
                    self.players[t_owner].board.remove(target)
                    events.append({"type": "death", "player": t_owner,
                                   "uid": target.uid})
                    self.on_hero_died(target, t_owner, events)
        elif ability == "tribute":
            player.relics.remove(card)
            if player.mana < MAX_MANA:
                player.mana += 1
            events.append({"type": "destroy", "player": index, "uid": uid})
            events.append({"type": "keyword", "keyword": "tribute",
                           "player": index, "uid": uid, "mana": player.mana})
            events.append({"type": "mana", "player": index,
                           "mana": player.mana, "max_mana": player.max_mana})
        return True, "", events

    def attack(self, index: int, attacker_uid: int,
               target_uid: int) -> tuple[bool, str, list[Event]]:
        ok, reason = self.can_attack(index, attacker_uid)
        if not ok:
            return False, reason, []
        attacker = next(c for c in self.players[index].board
                        if c.uid == attacker_uid)
        enemy = self.players[1 - index]
        targets = self.valid_attack_targets(index)
        target = next((t for t in targets if t.uid == target_uid), None)
        if target is None:
            if enemy.board and enemy.champion and target_uid == enemy.champion.uid:
                return False, "Enemy creatures must be dealt with first.", []
            return False, "That isn't a legal attack target.", []

        attacker.exhausted = True
        events: list[Event] = [{"type": "attack", "player": index,
                                "attacker": attacker_uid, "target": target_uid}]
        me = self.players[index]

        def owner_of(card) -> int:
            return index if (card in me.board or card is me.champion) else 1 - index

        def hit(source, victim, amount: int, combat: bool) -> None:
            """One packet of damage, with on-damage keyword triggers."""
            if amount <= 0 or victim.health <= 0:
                return
            victim_owner = owner_of(victim)
            victim.health -= amount
            events.append({"type": "damage", "player": victim_owner,
                           "uid": victim.uid, "amount": amount,
                           "health": victim.health})
            if victim.kind is Kind.CHAMPION:
                self.on_champion_damaged(victim_owner, events)
                if source.kind is Kind.CREATURE:
                    self.on_hero_hit_champion(source, owner_of(source),
                                              events)
            source_owner = owner_of(source)
            # Soul Link: dealing damage heals your champion that much
            champ = self.players[source_owner].champion
            if source.has_kw("soul_link") and champ is not None:
                champ.health += amount
                events.append({"type": "heal", "player": source_owner,
                               "uid": champ.uid, "amount": amount,
                               "health": champ.health})
                self.on_champion_life_gain(source_owner, events)
            # Intelligent: damaging the opposing champion draws a card
            if (victim.kind is Kind.CHAMPION and victim is not champ
                    and source.has_kw("intelligent")):
                events.append(self._draw_one(source_owner))
            if victim.kind is Kind.CREATURE:
                # Lethal: any damage to a hero destroys it
                if source.has_kw("lethal") and source.kind is Kind.CREATURE:
                    victim.health = min(victim.health, 0)
                    events.append({"type": "keyword", "keyword": "lethal",
                                   "player": source_owner, "uid": source.uid})
                # Execute: damaging a hero left at 3 or less destroys it
                elif source.has_kw("execute") and 0 < victim.health <= 3:
                    victim.health = 0
                    events.append({"type": "keyword", "keyword": "execute",
                                   "player": source_owner, "uid": source.uid})
                # Thorns: first damage each turn bites the source back
                if (victim.has_kw("thorns") and not victim.thorns_used
                        and combat and source.kind is Kind.CREATURE
                        and source.health > 0):
                    victim.thorns_used = True
                    events.append({"type": "keyword", "keyword": "thorns",
                                   "player": victim_owner, "uid": victim.uid})
                    hit(victim, source, victim.kw_value("thorns", 1),
                        combat=False)

        enemy_champ = enemy.champion
        if target.kind is Kind.CREATURE:
            pre_health = target.health
            attacker_quick = attacker.has_kw("quick") and not target.has_kw("quick")
            target_quick = target.has_kw("quick") and not attacker.has_kw("quick")
            attacker_power = self.effective_attack(attacker, index)
            target_power = self.effective_attack(target, 1 - index)
            if attacker_quick or target_quick:
                # Quick strikes first; the slow side only swings back alive
                first, second = ((attacker, target) if attacker_quick
                                 else (target, attacker))
                first_power = attacker_power if first is attacker else target_power
                second_power = target_power if first is attacker else attacker_power
                hit(first, second, first_power, combat=True)
                if second.health > 0 and second_power > 0:
                    hit(second, first, second_power, combat=True)
            else:
                # normal combat: damage lands simultaneously — two 3/3s trade
                hit(attacker, target, attacker_power, combat=True)
                if target_power > 0:
                    hit(target, attacker, target_power, combat=True)
            # Crush: excess damage spills onto the defending champion
            if (attacker.has_kw("crush") and target.health < 0
                    and enemy_champ is not None):
                events.append({"type": "keyword", "keyword": "crush",
                               "player": index, "uid": attacker.uid})
                hit(attacker, enemy_champ, -target.health, combat=False)
            # Pierce: combat damage to a hero pokes their champion for 1
            if (attacker.has_kw("pierce") and target.health < pre_health
                    and enemy_champ is not None):
                events.append({"type": "keyword", "keyword": "pierce",
                               "player": index, "uid": attacker.uid})
                hit(attacker, enemy_champ, 1, combat=False)
        else:
            hit(attacker, target, self.effective_attack(attacker, index),
                combat=True)

        # deaths — state-based, with Undying and Feast woven in
        def resolve_death(card) -> None:
            owner = owner_of(card)
            died = self._resolve_state_based_death(owner, card, events)
            if not died:
                return
            killer = attacker if card is target else target
            if (killer.kind is Kind.CREATURE and killer.has_kw("feast")
                    and killer.health > 0):
                killer_owner = owner_of(killer)
                who = self.players[killer_owner]
                if who.mana < MAX_MANA:
                    who.mana += 1
                events.append({"type": "keyword", "keyword": "feast",
                               "player": killer_owner, "uid": killer.uid,
                               "mana": who.mana})

        if target.kind is Kind.CREATURE:
            resolve_death(target)
        resolve_death(attacker)

        # champion defeat ends the match (Crush/Pierce can finish one too)
        for champ_owner in (1 - index, index):
            champ = self.players[champ_owner].champion
            if champ is not None and champ.health <= 0 and self.winner is None:
                self.winner = 1 - champ_owner
                events.append({"type": "victory", "player": 1 - champ_owner})
                log.info("%s wins the match!",
                         self.players[1 - champ_owner].name)
        return True, "", events


    def move_in_hand(self, index: int, uid: int, new_index: int) -> bool:
        hand = self.players[index].hand
        card = self.find_in_hand(index, uid)
        if card is None:
            return False
        hand.remove(card)
        hand.insert(max(0, min(new_index, len(hand))), card)
        return True
