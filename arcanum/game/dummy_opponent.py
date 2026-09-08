"""Scripted opponent for dev-test matches.

Reads the match state and expresses intents through the same validated
methods the player uses. Priority order: remove the player's best creature
if it can, otherwise play the most expensive thing it can afford (relics and
draw spells included). Returns (card, target_uid | None).
"""
from __future__ import annotations

from typing import Optional

from arcanum.game.match import CardInstance, MatchState

Play = tuple[CardInstance, Optional[int]]


class DummyOpponent:
    def __init__(self, player_index: int = 1) -> None:
        self.index = player_index

    def choose_play(self, match: MatchState) -> Optional[Play]:
        hand = match.player(self.index).hand
        playable = [c for c in hand if match.can_play(self.index, c.uid)[0]]
        if not playable:
            return None
        # prefer removal when a target exists
        targeted = [c for c in playable if c.needs_target]
        for card in targeted:
            targets = match.valid_targets(self.index, card)
            if targets:
                best = max(targets, key=lambda t: t.attack)
                return card, best.uid
        rest = [c for c in playable if not c.needs_target]
        if not rest:
            return None
        return max(rest, key=lambda c: c.cost), None

    def choose_attack(self, match: MatchState) -> Optional[Play]:
        """One attack at a time: (attacker, target_uid) or None when done.

        Prefers a trade it survives, then any kill, then the weakest enemy;
        goes face once the row is clear.
        """
        ready = [c for c in match.player(self.index).board
                 if match.can_attack(self.index, c.uid)[0]]
        if not ready:
            return None
        attacker = max(ready, key=lambda c: c.attack)
        targets = match.valid_attack_targets(self.index)
        if not targets:
            return None
        champion = match.player(1 - self.index).champion
        enemy_creatures = [t for t in targets if t is not champion]
        if enemy_creatures:
            kills = [t for t in enemy_creatures if t.health <= attacker.attack]
            survivable = [t for t in kills if t.attack < attacker.health]
            if survivable:
                target = max(survivable, key=lambda t: t.attack)
            elif kills:
                target = max(kills, key=lambda t: t.attack)
            else:
                target = min(enemy_creatures, key=lambda t: t.health)
        else:
            target = targets[0]      # champion
        return attacker, target.uid
