"""Wire protocol for server-hosted matches (future).

Design decisions locked in now so gameplay code is written against a stable
contract from day one:

* Server-authoritative simulation — the client sends *intents* (play card,
  attack, pass priority) and renders *events* the server broadcasts back.
  This is the standard anti-cheat model for TCGs (MTG Arena works this way).
* Versioned JSON envelopes to start (easy to debug), with a `seq` number for
  ordering/acks. The envelope is transport-agnostic, so a later move to a
  binary codec (msgpack) or a different transport changes nothing above this
  layer.
* Every game-changing message carries `match_id` so one connection can, in
  principle, spectate or reconnect.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from arcanum.core.constants import PROTOCOL_VERSION


class MsgType(str, Enum):
    # -- connection / lobby
    HELLO = "hello"                    # client -> server: auth handshake
    WELCOME = "welcome"                # server -> client: accepted
    ERROR = "error"
    PING = "ping"
    PONG = "pong"

    # -- matchmaking
    QUEUE_JOIN = "queue.join"
    QUEUE_LEAVE = "queue.leave"
    MATCH_FOUND = "match.found"
    MATCH_READY = "match.ready"

    # -- in-match: client intents (requests, never authoritative)
    INTENT_PLAY_CARD = "intent.play_card"
    INTENT_ATTACK = "intent.attack"
    INTENT_BLOCK = "intent.block"
    INTENT_ACTIVATE = "intent.activate"
    INTENT_PASS_PRIORITY = "intent.pass_priority"
    INTENT_MULLIGAN = "intent.mulligan"
    INTENT_CONCEDE = "intent.concede"

    # -- in-match: server events (authoritative results)
    EVENT_GAME_STATE = "event.game_state"      # full snapshot (join/reconnect)
    EVENT_STATE_DELTA = "event.state_delta"    # incremental update
    EVENT_TURN_CHANGED = "event.turn_changed"
    EVENT_MATCH_ENDED = "event.match_ended"


@dataclass
class Envelope:
    """Everything on the wire is one of these."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0                       # sender-local monotonically increasing
    match_id: str = ""
    version: int = PROTOCOL_VERSION
    ts: float = field(default_factory=time.time)

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def decode(cls, raw: str | bytes) -> "Envelope":
        data = json.loads(raw)
        if int(data.get("version", -1)) != PROTOCOL_VERSION:
            raise ProtocolError(
                f"Protocol mismatch: got v{data.get('version')}, "
                f"client speaks v{PROTOCOL_VERSION}")
        return cls(
            type=str(data["type"]),
            payload=dict(data.get("payload", {})),
            seq=int(data.get("seq", 0)),
            match_id=str(data.get("match_id", "")),
            version=int(data["version"]),
            ts=float(data.get("ts", 0.0)),
        )


class ProtocolError(RuntimeError):
    pass
