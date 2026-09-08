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
import threading
import time
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
    def connect(self, auth_token: str, name: str = "") -> None: ...

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

    def connect(self, auth_token: str, name: str = "") -> None:
        log.info("OfflineClient: connect() called — running in offline mode.")
        self.state = ConnectionState.DISCONNECTED
        self.bus.publish_threadsafe(Events.NET_DISCONNECTED, reason="offline_mode")

    def disconnect(self) -> None:
        self.state = ConnectionState.DISCONNECTED

    def send(self, msg_type: MsgType, payload: dict | None = None, match_id: str = "") -> None:
        log.debug("OfflineClient: dropped %s (offline)", msg_type.value)


class WebSocketClient(NetworkClient):
    """Production transport: `websocket-client` on a daemon thread.

    Threading model (as designed): the network thread owns the socket; every
    inbound envelope is handed to the main thread via the event bus queue.
    Reconnects use exponential backoff (1s -> 2s -> 4s ... cap 30s).
    """

    def __init__(self, bus: EventBus, url: str) -> None:
        super().__init__(bus)
        try:
            import websocket  # noqa: F401  (websocket-client package)
        except ImportError as exc:
            raise RuntimeError(
                "Online play needs the 'websocket-client' package: "
                "pip install websocket-client") from exc
        self.url = url.rstrip("/")
        self.latency_ms: int | None = None
        self.online_count: int | None = None
        self._name = ""
        self._token = ""
        self._stop = threading.Event()
        self._ws = None
        self._thread: threading.Thread | None = None
        self._pinger: threading.Thread | None = None

    # -- interface ---------------------------------------------------------
    def connect(self, auth_token: str, name: str = "") -> None:
        if self._thread and self._thread.is_alive():
            return
        self._token, self._name = auth_token, name or "Adventurer"
        self._stop.clear()
        self.state = ConnectionState.CONNECTING
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="arcanum-net")
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self.state = ConnectionState.DISCONNECTED

    def send(self, msg_type: MsgType, payload: dict | None = None,
             match_id: str = "") -> None:
        ws = self._ws
        if self.state is not ConnectionState.CONNECTED or ws is None:
            log.debug("Not connected; dropped %s", msg_type.value)
            return
        try:
            ws.send(self._make(msg_type, payload, match_id).encode())
        except Exception as exc:  # noqa: BLE001
            log.warning("Send failed (%s); connection will retry.", exc)

    # -- network thread ------------------------------------------------------
    def _run(self) -> None:
        import websocket
        backoff = 1.0
        while not self._stop.is_set():
            try:
                log.info("Connecting to %s ...", self.url)
                ws = websocket.create_connection(self.url, timeout=10)
                ws.settimeout(30)
                self._ws = ws
                hello = self._make(MsgType.HELLO,
                                   {"token": self._token, "name": self._name}, "")
                ws.send(hello.encode())
                backoff = 1.0
                while not self._stop.is_set():
                    raw = ws.recv()
                    if raw:
                        self._handle_raw(raw)
            except Exception as exc:  # noqa: BLE001 - any transport failure
                if self._stop.is_set():
                    break
                log.info("Connection lost (%s); retrying in %.0fs", exc, backoff)
                self._on_dropped(str(exc))
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self._ws = None
        self.state = ConnectionState.DISCONNECTED

    def _on_dropped(self, reason: str) -> None:
        was_connected = self.state is ConnectionState.CONNECTED
        self.state = ConnectionState.RECONNECTING
        self.latency_ms = None
        if was_connected:
            self.bus.publish_threadsafe(Events.NET_DISCONNECTED, reason=reason)

    # -- inbound (network thread; publish, never touch game state) ---------
    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            env = Envelope.decode(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("Bad envelope from server: %s", exc)
            return
        if env.type == MsgType.WELCOME.value:
            self.state = ConnectionState.CONNECTED
            self.online_count = int(env.payload.get("online", 0)) or None
            log.info("Welcome from server v%s (%s online)",
                     env.payload.get("server_version", "?"),
                     env.payload.get("online", "?"))
            self.bus.publish_threadsafe(Events.NET_CONNECTED,
                                        payload=env.payload)
            self._start_pinger()
        elif env.type == MsgType.PONG.value:
            sent = float(env.payload.get("echo_ts", 0.0))
            if sent:
                self.latency_ms = max(0, int((time.time() - sent) * 1000))
            online = env.payload.get("online")
            if online is not None:
                self.online_count = int(online)
        else:
            self.bus.publish_threadsafe(Events.NET_MESSAGE, envelope=env)

    def _start_pinger(self) -> None:
        if self._pinger and self._pinger.is_alive():
            return

        def loop() -> None:
            while not self._stop.wait(5.0):
                if self.state is ConnectionState.CONNECTED:
                    self.send(MsgType.PING, {"ts": time.time()})
        self._pinger = threading.Thread(target=loop, daemon=True,
                                        name="arcanum-ping")
        self._pinger.start()
