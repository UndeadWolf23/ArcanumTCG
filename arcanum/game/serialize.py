"""Shared serialization for match state — used by BOTH client and server.

The server sends each player a *redacted* view: your own hand in full, the
opponent's hand as a count only. Snapshots are applied to the client's
mirror MatchState **in place**, keyed by card uid, so sprite references to
CardInstance objects stay valid across updates (health changes just appear).
"""
from __future__ import annotations

from typing import Any

from arcanum.game.match import CardInstance, Effect, Kind, MatchState, Phase


def card_to_dict(card: CardInstance) -> dict[str, Any]:
    return {
        "uid": card.uid, "name": card.name, "kind": card.kind.value,
        "cost": card.cost, "attack": card.attack, "health": card.health,
        "max_health": card.max_health, "exhausted": card.exhausted,
        "sick": card.sick, "haste": card.haste, "text": card.text,
        "effect": card.effect.value, "needs_target": card.needs_target,
        "keywords": dict(card.keywords),
    }


def card_from_dict(data: dict[str, Any]) -> CardInstance:
    return CardInstance(
        uid=int(data["uid"]), name=str(data["name"]),
        kind=Kind(data["kind"]), cost=int(data["cost"]),
        attack=int(data.get("attack", 0)), health=int(data.get("health", 0)),
        max_health=int(data.get("max_health", 0)),
        exhausted=bool(data.get("exhausted", False)),
        sick=bool(data.get("sick", False)), haste=bool(data.get("haste", False)),
        text=str(data.get("text", "")), effect=Effect(data.get("effect", "none")),
        needs_target=bool(data.get("needs_target", False)),
        keywords=dict(data.get("keywords", {})),
    )


def _update_card(card: CardInstance, data: dict[str, Any]) -> None:
    card.attack = int(data.get("attack", card.attack))
    card.health = int(data.get("health", card.health))
    card.max_health = int(data.get("max_health", card.max_health))
    card.exhausted = bool(data.get("exhausted", card.exhausted))
    card.sick = bool(data.get("sick", card.sick))


def snapshot_for(match: MatchState, viewer: int) -> dict[str, Any]:
    """Redacted view of the match from `viewer`'s seat (0 or 1)."""
    me = match.player(viewer)
    them = match.player(1 - viewer)

    def side(player, full_hand: bool) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": player.name,
            "mana": player.mana, "max_mana": player.max_mana,
            "champion": card_to_dict(player.champion) if player.champion else None,
            "board": [card_to_dict(c) for c in player.board],
            "relics": [card_to_dict(c) for c in player.relics],
        }
        if full_hand:
            data["hand"] = [card_to_dict(c) for c in player.hand]
        else:
            data["hand_count"] = len(player.hand)
        return data

    winner_rel = None
    if match.winner is not None:
        winner_rel = 0 if match.winner == viewer else 1
    return {
        "you": side(me, full_hand=True),
        "opp": side(them, full_hand=False),
        "your_turn": match.active == viewer,
        "phase": match.phase.value,
        "turn": match.turn_number,
        "winner": winner_rel,
        "started": match.started,
    }


def _sync_list(existing: list[CardInstance], incoming: list[dict],
               pool: dict[int, CardInstance]) -> None:
    """In-place zone sync. `pool` maps uid -> existing object across ALL of
    the player's zones, so a card moving hand -> board keeps its identity
    (sprites hold references; sick/health updates must reach them)."""
    new_list: list[CardInstance] = []
    for data in incoming:
        card = pool.get(int(data["uid"]))
        if card is None:
            card = card_from_dict(data)
            pool[card.uid] = card
        else:
            _update_card(card, data)
        new_list.append(card)
    existing[:] = new_list


def apply_snapshot(mirror: MatchState, snap: dict[str, Any]) -> None:
    """Apply a redacted snapshot to the client mirror. Viewer is seat 0."""
    you, opp = mirror.player(0), mirror.player(1)
    you_data, opp_data = snap["you"], snap["opp"]

    for player, data in ((you, you_data), (opp, opp_data)):
        player.name = data.get("name", player.name)
        player.mana = int(data["mana"])
        player.max_mana = int(data["max_mana"])
        pool: dict[int, CardInstance] = {
            c.uid: c for c in (*player.hand, *player.board, *player.relics)}
        if player.champion is not None:
            pool[player.champion.uid] = player.champion
        champ_data = data.get("champion")
        if champ_data:
            if player.champion is None or player.champion.uid != int(champ_data["uid"]):
                player.champion = card_from_dict(champ_data)
            else:
                _update_card(player.champion, champ_data)
        _sync_list(player.board, data.get("board", []), pool)
        _sync_list(player.relics, data.get("relics", []), pool)
        if player is you:
            _sync_list(player.hand, data.get("hand", []), pool)
    # opponent hand stays empty in the mirror; the count travels separately

    mirror.phase = Phase(snap["phase"])
    mirror.active = 0 if snap["your_turn"] else 1
    mirror.turn_number = int(snap["turn"])
    mirror.started = bool(snap.get("started", True))
    winner = snap.get("winner")
    mirror.winner = None if winner is None else int(winner)


def opp_hand_count(snap: dict[str, Any]) -> int:
    return int(snap["opp"].get("hand_count", 0))
