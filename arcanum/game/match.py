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
    thorns_used: bool = False   # Thorns triggers once per turn
    undying_spent: bool = False # Undying triggers once ever

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

    def make_champion(self, name: str) -> CardInstance:
        return CardInstance(self._uid(), name, Kind.CHAMPION, 0,
                            attack=0, health=CHAMPION_HEALTH,
                            max_health=CHAMPION_HEALTH,
                            text="Defeat the enemy champion to win.")

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
    def start(self) -> None:
        if self.started:
            log.warning("MatchState.start() called twice; ignoring.")
            return
        self.started = True
        champion_names = ("Archmagus Lyra", "Umbral Sovereign")
        for i, player in enumerate(self.players):
            player.champion = self.make_champion(champion_names[i])
            hand = [self.make_creature(c) for c in (1, 2, 5, 7)]
            hand += [self.make_insight(), self.make_ley_crystal(),
                     self.make_falling_star()]
            self._rng.shuffle(hand)
            player.hand = hand
        self._begin_turn(self.first_player)

    def _begin_turn(self, index: int) -> None:
        self.active = index
        self.phase = Phase.DRAW
        self.turn_number += 1
        player = self.players[index]
        player.max_mana = min(MAX_MANA, player.max_mana + 1)
        player.mana = player.max_mana
        for creature in player.board:
            creature.exhausted = False
            creature.sick = False
            creature.thorns_used = False

    # ------------------------------------------------------------ phases
    def advance_phase(self) -> Phase:
        if not self.started:
            raise RuntimeError("Match has not started.")
        order = list(PHASE_ORDER)
        i = order.index(self.phase)
        if i + 1 < len(order):
            self.phase = order[i + 1]
        else:
            self._begin_turn(1 - self.active)
        return self.phase

    # ------------------------------------------------------------ drawing
    def _draw_one(self, index: int) -> Event:
        card = self.random_card()
        player = self.players[index]
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
        return DrawResult(card=event["card"], burned=event["burned"])

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
            source_owner = owner_of(source)
            # Soul Link: dealing damage heals your champion that much
            champ = self.players[source_owner].champion
            if source.has_kw("soul_link") and champ is not None:
                champ.health += amount
                events.append({"type": "heal", "player": source_owner,
                               "uid": champ.uid, "amount": amount,
                               "health": champ.health})
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
            strikes = [(attacker, target), (target, attacker)]
            if target_quick:
                strikes.reverse()
            first_src, first_victim = strikes[0]
            hit(first_src, first_victim, first_src.attack, combat=True)
            # Quick: if the quick side killed, the slow side never swings
            second_src, second_victim = strikes[1]
            quick_stopped = ((attacker_quick or target_quick)
                             and second_src.health <= 0)
            if second_src.attack > 0 and second_src.health > 0 \
                    and not quick_stopped:
                hit(second_src, second_victim, second_src.attack, combat=True)
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
            hit(attacker, target, attacker.attack, combat=True)

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
