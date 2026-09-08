"""Network client layer for server-hosted matches.

`NetworkClient` is the interface gameplay code depends on. Today it resolves
to `OfflineClient` (a no-op that reports 'offline'); when the game server
exists, `WebSocketClient` is completed and swapped in via the Backend facade.

Threading model (locked in now):
* A dedicated network thread owns the socket and does all blocking I/O.
* Inbound messages are handed to the main thread via
  `EventBus.publish_threadsafe(Events.NET_MESSAGE, envelope=...)` — game and
  UI code never touches the socket and never needs locks.
* Outbound sends are queued; the network thread drains the queue.
"""
from __future__ import annotations

import itertools
import logging
from abc import ABC, abstractmethod
from enum import Enum, auto

from arcanum.core.events import EventBus, Events
from arcanum.services.net.protocol import Envelope, MsgType

log = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()


class NetworkClient(ABC):
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.state = ConnectionState.DISCONNECTED
        self._seq = itertools.count(1)

    # -- interface -------------------------------------------------------
    @abstractmethod
    def connect(self, auth_token: str) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def send(self, msg_type: MsgType, payload: dict | None = None, match_id: str = "") -> None: ...

    # -- helpers -----------------------------------------------------------
    def _make(self, msg_type: MsgType, payload: dict | None, match_id: str) -> Envelope:
        return Envelope(type=msg_type.value, payload=payload or {},
                        seq=next(self._seq), match_id=match_id)


class OfflineClient(NetworkClient):
    """Stand-in used until the game server exists. Never connects."""

    def connect(self, auth_token: str) -> None:
        log.info("OfflineClient: connect() called — running in offline mode.")
        self.state = ConnectionState.DISCONNECTED
        self.bus.publish_threadsafe(Events.NET_DISCONNECTED, reason="offline_mode")

    def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED

    def send(self, msg_type: MsgType, payload: dict | None = None, match_id: str = "") -> None:
        log.debug("OfflineClient: dropped %s (offline)", msg_type.value)


class WebSocketClient(NetworkClient):
    """Future production client (implementation plan, not yet wired):

    * `websockets` (or `websocket-client`) on a daemon thread.
    * connect(): open GAME_SERVER_URL, send HELLO with the Supabase JWT,
      await WELCOME, then set CONNECTED and publish NET_CONNECTED.
    * Heartbeat PING every 10s; missing 2 PONGs => RECONNECTING with
      exponential backoff (1s, 2s, 4s... cap 30s), resume via match_id.
    * Every inbound Envelope is published thread-safely as NET_MESSAGE.
    """

    def connect(self, auth_token: str) -> None:
        raise NotImplementedError("Game server transport not implemented yet.")

    def disconnect(self) -> None: ...

    def send(self, msg_type: MsgType, payload: dict | None = None, match_id: str = "") -> None:
        raise NotImplementedError("Game server transport not implemented yet.")
