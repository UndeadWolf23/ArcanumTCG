"""Server-side match sessions — transport-free, asyncio-driven.

A MatchSession owns the ONLY authoritative MatchState for a match. Seats are
humans (a `send` coroutine from the transport) or the built-in AI. Every
accepted intent broadcasts a delta: viewer-relative redacted events plus a
fresh redacted snapshot, so clients can never desync or peek at hidden info.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from arcanum.game.dummy_opponent import DummyOpponent
from arcanum.game.match import MatchState, Phase
from arcanum.game.serialize import card_to_dict, snapshot_for
from arcanum.services.net.protocol import Envelope, MsgType

log = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]

DRAW_DELAY = 0.7
END_DELAY = 0.4
AI_PLAY_DELAY = 0.85
AI_ATTACK_DELAY = 1.05


@dataclass
class Seat:
    name: str
    send: Optional[SendFn] = None       # None => AI seat
    connected: bool = True
    deck: Optional[dict] = None         # submitted with QUEUE_JOIN
    uid: str = ""                       # permanent account id (rewards)

    @property
    def is_ai(self) -> bool:
        return self.send is None


def redact_events(events: list[dict[str, Any]], viewer: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        event = dict(event)
        if "player" in event:
            event["player"] = 0 if event["player"] == viewer else 1
        if event.get("type") == "phase" and "active" in event:
            event["your_turn"] = event.pop("active") == viewer
        card = event.pop("card", None)
        if event.get("type") in ("played", "spawn") and card is not None:
            event["card"] = card_to_dict(card) if not isinstance(card, dict) \
                else card
        elif event.get("type") == "draw":
            if event["player"] == 0 and card is not None:
                event["card_uid"] = card.uid
            elif event["player"] == 1:
                event["hidden"] = True
        out.append(event)
    return out


def _checked_deck(deck) -> dict:
    """A player's submitted deck, or the Starter if absent/invalid."""
    from arcanum.game import catalog as cat
    starter = cat.starter_deck_cards()
    if not isinstance(deck, dict) or not deck:
        return starter
    try:
        ok, why = cat.validate_deck(deck, cat.full_collection())
    except Exception:  # noqa: BLE001
        return starter
    if not ok:
        log.info("Submitted deck rejected (%s); starter assigned.", why)
        return starter
    return {str(k): int(v) for k, v in deck.items()}


class MatchSession:
    def __init__(self, seats: list[Seat], seed: int | None = None) -> None:
        assert len(seats) == 2
        self.match_id = uuid.uuid4().hex[:12]
        self.seats = seats
        self.decks = [_checked_deck(seat.deck) for seat in seats]
        self.match = MatchState(local_name=seats[0].name,
                                opponent_name=seats[1].name, seed=seed)
        self.ai = DummyOpponent(player_index=1) if seats[1].is_ai else None
        self._pass_event = asyncio.Event()
        self.closed = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ outbound
    async def _send_to(self, seat_index: int, envelope: Envelope) -> None:
        seat = self.seats[seat_index]
        if seat.is_ai or not seat.connected or seat.send is None:
            return
        try:
            await seat.send(envelope.encode())
        except Exception:  # noqa: BLE001
            log.info("Send to seat %d failed; marking disconnected.", seat_index)
            seat.connected = False

    def _count_progress(self, events: list[dict[str, Any]]) -> None:
        stats = getattr(self, "stats", None)
        if stats is None:
            stats = self.stats = [
                {k: 0 for k in ("summon_heroes", "sacrifice_permanents",
                                "cast_spells", "gain_life", "attack_heroes",
                                "destroy_heroes", "draw_cards")}
                for _ in (0, 1)]
        for event in events:
            etype = event.get("type")
            who = event.get("player")
            if who not in (0, 1):
                continue
            if etype == "played":
                card = event.get("card")
                if isinstance(card, dict):
                    kind = str(card.get("kind", ""))
                else:
                    kind = str(getattr(getattr(card, "kind", None),
                                       "value", ""))
                if kind == "creature":
                    stats[who]["summon_heroes"] += 1
                elif kind == "spell":
                    stats[who]["cast_spells"] += 1
            elif etype == "spawn":
                stats[who]["summon_heroes"] += 1
            elif etype == "attack":
                stats[who]["attack_heroes"] += 1
            elif etype == "death":
                # credit the OTHER side with a destroy
                stats[1 - who]["destroy_heroes"] += 1
            elif etype == "draw" and not event.get("skipped"):
                stats[who]["draw_cards"] += 1
            elif etype == "heal":
                stats[who]["gain_life"] += int(event.get("amount", 0) or 0)
            elif etype == "keyword" and event.get("keyword") in (
                    "consume", "tribute", "offering"):
                stats[who]["sacrifice_permanents"] += 1

    async def broadcast_delta(self, events: list[dict[str, Any]]) -> None:
        self._count_progress(events)
        for viewer in (0, 1):
            payload = {"events": redact_events(events, viewer),
                       "state": snapshot_for(self.match, viewer)}
            await self._send_to(viewer, Envelope(
                type=MsgType.EVENT_STATE_DELTA.value, payload=payload,
                match_id=self.match_id))

    async def broadcast_start(self) -> None:
        for viewer in (0, 1):
            await self._send_to(viewer, Envelope(
                type=MsgType.EVENT_GAME_STATE.value,
                payload={"state": snapshot_for(self.match, viewer)},
                match_id=self.match_id))

    def _phase_event(self) -> dict[str, Any]:
        return {"type": "phase", "phase": self.match.phase.value,
                "active": self.match.active, "turn": self.match.turn_number}

    # ------------------------------------------------------------ lifecycle
    def launch(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        try:
            self.match.start(decks=self.decks)
            await self.broadcast_start()
            while not self.closed and self.match.winner is None:
                await self._pump_phase()
        except Exception:  # noqa: BLE001
            log.exception("Match %s crashed; closing.", self.match_id)
        finally:
            self.closed = True
            log.info("Match %s ended (winner=%s).", self.match_id, self.match.winner)
            await self._settle_rewards()

    async def _settle_rewards(self) -> None:
        from server import economy
        if not economy.enabled() or self.match.winner is None:
            return
        stats = getattr(self, "stats", [{}, {}])
        for index, seat in enumerate(self.seats):
            if seat.is_ai or not seat.uid:
                continue
            try:
                await economy.add_progress(seat.uid, stats[index])
                delta: dict[str, Any] = {}
                if index == self.match.winner and not                         self.seats[1 - index].is_ai:
                    win = await economy.record_win(seat.uid)
                    if win.get("awarded"):
                        delta["gold_awarded"] = win["awarded"]
                        delta["win_number"] = win.get("wins")
                wallet = await economy.wallet(seat.uid) or {}
                completed = [d for d in wallet.get("dailies", [])
                             if d.get("progress", 0) >= d.get("goal", 1)
                             and not d.get("claimed")]
                if completed:
                    delta["challenges_completed"] = completed
                if delta:
                    delta["type"] = "rewards"
                    await self._send_to(index, Envelope(
                        type=MsgType.ECONOMY_DELTA.value, payload=delta,
                        match_id=self.match_id))
            except Exception:  # noqa: BLE001
                log.exception("Reward settlement failed for seat %d", index)

    async def _advance(self) -> None:
        self.match.advance_phase()
        events = list(getattr(self.match, "pending_turn_events", []))
        self.match.pending_turn_events = []
        events.append(self._phase_event())
        await self.broadcast_delta(events)

    async def _pump_phase(self) -> None:
        phase = self.match.phase
        seat = self.seats[self.match.active]
        if phase is Phase.DRAW:
            await asyncio.sleep(DRAW_DELAY)
            active = self.match.active
            result = self.match.draw_step(active)
            events: list[dict[str, Any]] = []
            if result.card is not None or result.skipped:
                events.append({"type": "draw", "player": active,
                               "card": result.card, "burned": result.burned,
                               "skipped": result.skipped})
            self.match.advance_phase()
            events.extend(getattr(self.match, "pending_turn_events", []))
            self.match.pending_turn_events = []
            events.append(self._phase_event())
            await self.broadcast_delta(events)
        elif phase in (Phase.MAIN, Phase.COMBAT):
            if seat.is_ai:
                await self._ai_phase(phase)
            else:
                # the pass intent itself performs the advance (handler below),
                # then wakes this pump to evaluate the next phase
                self._pass_event.clear()
                await self._pass_event.wait()
        elif phase is Phase.END:
            await asyncio.sleep(END_DELAY)
            await self._advance()

    async def _ai_phase(self, phase: Phase) -> None:
        assert self.ai is not None
        while not self.closed and self.match.winner is None:
            if phase is Phase.MAIN:
                choice = self.ai.choose_play(self.match)
                if choice is None:
                    break
                await asyncio.sleep(AI_PLAY_DELAY)
                card, target = choice
                ok, reason, events = self.match.play_card(1, card.uid, target)
                if not ok:
                    log.warning("AI play rejected (%s) — ending main.", reason)
                    break
            else:
                choice = self.ai.choose_attack(self.match)
                if choice is None:
                    break
                await asyncio.sleep(AI_ATTACK_DELAY)
                attacker, target_uid = choice
                ok, reason, events = self.match.attack(1, attacker.uid, target_uid)
                if not ok:
                    log.warning("AI attack rejected (%s) — ending combat.",
                                reason)
                    break
            if not ok:
                log.warning("Server AI intent rejected: %s", reason)
                break
            await self.broadcast_delta(events)
        if not self.closed and self.match.winner is None:
            await self._advance()

    # ------------------------------------------------------------ intents
    async def handle_intent(self, seat_index: int, env: Envelope) -> None:
        if self.closed:
            return
        mtype = env.type
        if mtype == MsgType.INTENT_PASS_PRIORITY.value:
            if (self.match.active == seat_index
                    and self.match.phase in (Phase.MAIN, Phase.COMBAT)
                    and self.match.winner is None):
                await self._advance()
                self._pass_event.set()
            return
        if mtype == MsgType.INTENT_CONCEDE.value:
            await self._end_by_forfeit(loser=seat_index)
            return
        if mtype == MsgType.INTENT_PLAY_CARD.value:
            uid = int(env.payload.get("uid", -1))
            target = env.payload.get("target_uid")
            target = int(target) if target is not None else None
            ok, reason, events = self.match.play_card(seat_index, uid, target)
        elif mtype == MsgType.INTENT_ATTACK.value:
            ok, reason, events = self.match.attack(
                seat_index,
                int(env.payload.get("attacker_uid", -1)),
                int(env.payload.get("target_uid", -1)))
        elif mtype == MsgType.INTENT_ACTIVATE.value:
            ok, reason, events = self.match.activate(
                seat_index,
                int(env.payload.get("uid", -1)),
                str(env.payload.get("ability", "")),
                int(env.payload.get("target", 0)))
        else:
            ok, reason, events = False, f"Unknown intent {mtype!r}.", []

        if not ok:
            await self._send_to(seat_index, Envelope(
                type=MsgType.ERROR.value,
                payload={"code": "rejected", "message": reason},
                match_id=self.match_id))
            return
        await self.broadcast_delta(events)
        if self.match.winner is not None:
            self._pass_event.set()

    async def _end_by_forfeit(self, loser: int) -> None:
        if self.match.winner is not None or self.closed:
            return
        winner = 1 - loser
        self.match.winner = winner
        await self.broadcast_delta([{"type": "victory", "player": winner,
                                     "forfeit": True}])
        self._pass_event.set()
        self.closed = True

    async def on_disconnect(self, seat_index: int) -> None:
        self.seats[seat_index].connected = False
        if self.seats[1 - seat_index].is_ai:
            self.closed = True
            self._pass_event.set()
        else:
            await self._end_by_forfeit(loser=seat_index)
