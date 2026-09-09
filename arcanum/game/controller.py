"""Match controllers — the seam between the match scene and reality.

The scene talks to a controller and cannot tell what's behind it:

* LocalController — offline practice. Owns the authoritative MatchState and
  the DummyOpponent, drives phases with its own timers, and emits the same
  event dicts the server would send.
* RemoteController — online play. Sends intents over the websocket, mirrors
  the server's redacted snapshots into a local MatchState (used only for
  instant UI hints — glows, snap-backs, reasons), and relays the server's
  events for animation. The server remains the sole authority.

Event vocabulary (scene-facing), player index is always viewer-relative
(0 = you): match_start, phase, draw, played, mana, destroy, attack, damage,
death, victory, rejected.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from arcanum.core.events import EventBus, Events
from arcanum.game.dummy_opponent import DummyOpponent
from arcanum.game.match import MatchState, Phase
from arcanum.game.serialize import (apply_snapshot, card_to_dict,
                                    opp_hand_count)
from arcanum.services.net.client import NetworkClient
from arcanum.services.net.protocol import Envelope, MsgType

log = logging.getLogger(__name__)

Event = dict[str, Any]


class MatchController:
    def activate(self, uid: int, ability: str, target_uid: int = 0) -> None:
        raise NotImplementedError

    """Interface. `state` is a MatchState used for reads/UI hints only."""

    def __init__(self) -> None:
        self.state = MatchState()
        self.opp_hand_count = 0
        self._queue: list[Event] = []

    # -- intents (fire and forget; results arrive as events) ---------------
    def start(self) -> None: ...
    def play_card(self, uid: int, target_uid: int | None = None) -> None: ...
    def attack(self, attacker_uid: int, target_uid: int) -> None: ...
    def pass_phase(self) -> None: ...
    def concede(self) -> None: ...
    def close(self) -> None: ...

    # -- frame hooks ----------------------------------------------------------
    def update(self, dt: float) -> None: ...

    def poll_events(self) -> list[Event]:
        events, self._queue = self._queue, []
        return events

    def _emit(self, event: Event) -> None:
        self._queue.append(event)


# ---------------------------------------------------------------------------
# Local (offline practice)
# ---------------------------------------------------------------------------
class LocalController(MatchController):
    def activate(self, uid: int, ability: str, target_uid: int = 0) -> None:
        ok, why, events = self.state.activate(0, uid, ability, target_uid)
        if not ok:
            self._emit({"type": "rejected", "reason": why})
            return
        for event in self._normalize(events):
            self._emit(event)

    def __init__(self, local_name: str = "You", seed: int | None = None,
                 deck: dict | None = None) -> None:
        super().__init__()
        self.state = MatchState(local_name=local_name, seed=seed)
        self.deck = self._checked(deck)
        self.ai = DummyOpponent(player_index=1)
        self._timers: list[list] = []
        self._closed = False

    @staticmethod
    def _checked(deck: dict | None) -> dict:
        """The player's chosen deck, or the Starter if it doesn't validate."""
        from arcanum.game import catalog as cat
        starter = cat.starter_deck_cards()
        if not deck:
            return starter
        ok, why = cat.validate_deck(deck, cat.full_collection())
        if not ok:
            log.warning("Chosen deck invalid (%s); using the Starter Deck.",
                        why)
            return starter
        return dict(deck)

    # -- helpers -------------------------------------------------------------
    def _schedule(self, delay: float, fn: Callable[[], None]) -> None:
        self._timers.append([max(0.0, delay), fn])

    def update(self, dt: float) -> None:
        due = [t for t in self._timers if (t.__setitem__(0, t[0] - dt) or t[0] <= 0)]
        for timer in due:
            self._timers.remove(timer)
            try:
                timer[1]()
            except Exception:  # noqa: BLE001
                log.exception("Local match flow step failed")

    def _flush_turn_events(self) -> list[Event]:
        events = list(getattr(self.state, "pending_turn_events", []))
        self.state.pending_turn_events = []
        return events

    def _phase_event(self) -> Event:
        return {"type": "phase", "phase": self.state.phase.value,
                "your_turn": self.state.is_local_turn(),
                "turn": self.state.turn_number}

    def _normalize(self, events: list[Event]) -> list[Event]:
        """Match the server's wire shape exactly: cards become dicts/uids,
        opponent draws are hidden, opponent hand count stays in step."""
        out: list[Event] = []
        for event in events:
            event = dict(event)
            etype = event.get("type")
            card = event.pop("card", None)
            if etype == "played" and card is not None:
                event["card"] = card_to_dict(card)
            elif etype == "draw":
                if event.get("player") == 1:
                    event["hidden"] = True
                    if card is not None and not event.get("burned"):
                        self.opp_hand_count += 1
                elif card is not None:
                    event["card_uid"] = card.uid
            out.append(event)
        return out

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        from arcanum.game import catalog as cat
        self.state.start(decks=[self.deck, cat.starter_deck_cards()])
        self.opp_hand_count = len(self.state.player(1).hand)
        self._emit({"type": "match_start"})
        self._emit(self._phase_event())
        self._enter_phase()

    def close(self) -> None:
        self._closed = True
        self._timers.clear()

    # -- phase driver (ported from the old scene orchestration) ------------
    def _enter_phase(self) -> None:
        if self._closed or self.state.winner is not None:
            return
        phase, local = self.state.phase, self.state.is_local_turn()
        if phase is Phase.DRAW:
            def do_draw() -> None:
                result = self.state.draw_step(self.state.active)
                if result.card is not None or result.skipped:
                    event: Event = {"type": "draw",
                                    "player": 0 if local else 1,
                                    "burned": result.burned,
                                    "skipped": result.skipped}
                    if local and result.card is not None:
                        event["card_uid"] = result.card.uid
                    elif result.card and not result.burned:
                        self.opp_hand_count += 1
                        event["hidden"] = True
                    self._emit(event)
                self._advance()
            self._schedule(0.7, do_draw)
        elif phase is Phase.MAIN:
            if not local:
                self._schedule(0.8, self._ai_play_step)
        elif phase is Phase.COMBAT:
            if not local:
                self._schedule(0.7, self._ai_attack_step)
        elif phase is Phase.END:
            self._schedule(0.4, self._advance)

    def _advance(self) -> None:
        if self._closed or self.state.winner is not None:
            return
        self.state.advance_phase()
        for _ev in self._normalize(self._flush_turn_events()):
            self._emit(_ev)
        self._emit(self._phase_event())
        self._enter_phase()

    def _ai_play_step(self) -> None:
        choice = self.ai.choose_play(self.state)
        if choice is None:
            self._advance()
            return
        card, target = choice
        ok, reason, events = self.state.play_card(1, card.uid, target)
        if not ok:
            log.warning("Local AI play rejected: %s", reason)
            self._advance()
            return
        self.opp_hand_count = max(0, self.opp_hand_count - 1)
        for event in self._normalize(events):
            self._emit(event)
        self._schedule(0.85, self._ai_play_step)

    def _ai_attack_step(self) -> None:
        choice = self.ai.choose_attack(self.state)
        if choice is None:
            self._advance()
            return
        attacker, target_uid = choice
        ok, reason, events = self.state.attack(1, attacker.uid, target_uid)
        if not ok:
            log.warning("Local AI attack rejected: %s", reason)
            self._advance()
            return
        for event in self._normalize(events):
            self._emit(event)
        self._schedule(1.05, self._ai_attack_step)

    # -- intents -------------------------------------------------------------
    def play_card(self, uid: int, target_uid: int | None = None) -> None:
        ok, reason, events = self.state.play_card(0, uid, target_uid)
        if not ok:
            self._emit({"type": "rejected", "reason": reason})
            return
        for event in self._normalize(events):
            self._emit(event)

    def attack(self, attacker_uid: int, target_uid: int) -> None:
        ok, reason, events = self.state.attack(0, attacker_uid, target_uid)
        if not ok:
            self._emit({"type": "rejected", "reason": reason})
            return
        for event in self._normalize(events):
            self._emit(event)

    def pass_phase(self) -> None:
        if (self.state.is_local_turn()
                and self.state.phase in (Phase.MAIN, Phase.COMBAT)
                and self.state.winner is None):
            self._advance()

    def concede(self) -> None:
        if self.state.winner is None and self.state.started:
            self.state.winner = 1
            self._emit({"type": "victory", "player": 1, "forfeit": True})


# ---------------------------------------------------------------------------
# Remote (server-authoritative)
# ---------------------------------------------------------------------------
class RemoteController(MatchController):
    def activate(self, uid: int, ability: str, target_uid: int = 0) -> None:
        self.net.send(MsgType.INTENT_ACTIVATE,
                      {"uid": uid, "ability": ability,
                       "target": target_uid}, match_id=self.match_id)

    def __init__(self, bus: EventBus, net: NetworkClient, match_id: str) -> None:
        super().__init__()
        self.bus = bus
        self.net = net
        self.match_id = match_id
        self._started_emitted = False
        bus.subscribe(Events.NET_MESSAGE, self._on_net_message)
        bus.subscribe(Events.NET_DISCONNECTED, self._on_net_down)

    def close(self) -> None:
        self.bus.unsubscribe(Events.NET_MESSAGE, self._on_net_message)
        self.bus.unsubscribe(Events.NET_DISCONNECTED, self._on_net_down)

    # -- inbound (main thread; bus already marshalled it) --------------------
    def _on_net_message(self, envelope: Envelope, **_kw) -> None:
        if envelope.match_id and envelope.match_id != self.match_id:
            return
        if envelope.type == MsgType.EVENT_GAME_STATE.value:
            self._apply_state(envelope.payload.get("state"))
            if not self._started_emitted:
                self._started_emitted = True
                self._emit({"type": "match_start"})
                self._emit({"type": "phase",
                            "phase": self.state.phase.value,
                            "your_turn": self.state.is_local_turn(),
                            "turn": self.state.turn_number})
        elif envelope.type == MsgType.EVENT_STATE_DELTA.value:
            self._apply_state(envelope.payload.get("state"))
            for event in envelope.payload.get("events", []):
                self._emit(dict(event))
        elif envelope.type == MsgType.ERROR.value:
            reason = envelope.payload.get("message", "The server refused that.")
            self._emit({"type": "rejected", "reason": reason})

    def _apply_state(self, snap: Optional[dict]) -> None:
        if not snap:
            return
        try:
            apply_snapshot(self.state, snap)
            self.opp_hand_count = opp_hand_count(snap)
        except (KeyError, ValueError, TypeError):
            log.exception("Bad snapshot from server; keeping previous state")

    def _on_net_down(self, **_kw) -> None:
        if self.state.winner is None:
            self._emit({"type": "rejected",
                        "reason": "Connection lost — trying to reconnect..."})

    # -- intents -------------------------------------------------------------
    def _send(self, msg_type: MsgType, payload: dict) -> None:
        self.net.send(msg_type, payload, match_id=self.match_id)

    def start(self) -> None:
        pass  # the server started the match; the snapshot is on its way

    def play_card(self, uid: int, target_uid: int | None = None) -> None:
        self._send(MsgType.INTENT_PLAY_CARD,
                   {"uid": uid, "target_uid": target_uid})

    def attack(self, attacker_uid: int, target_uid: int) -> None:
        self._send(MsgType.INTENT_ATTACK,
                   {"attacker_uid": attacker_uid, "target_uid": target_uid})

    def pass_phase(self) -> None:
        self._send(MsgType.INTENT_PASS_PRIORITY, {})

    def concede(self) -> None:
        self._send(MsgType.INTENT_CONCEDE, {})
