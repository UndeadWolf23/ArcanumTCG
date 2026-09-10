"""Arcanum game server — websocket transport for Render (or any host).

Run locally:   python -m server.app        (ws://localhost:10000)
On Render:     same command; Render injects PORT and terminates TLS, so
               clients connect via wss://<service>.onrender.com

Flow: client sends HELLO -> WELCOME. Client sends queue.join -> the server
seats them against the built-in AI and launches a MatchSession (PvP pairing
is a later step). Intents are routed to that session; the session broadcasts
authoritative deltas. /healthz answers Render's health checker.
"""
from __future__ import annotations

import asyncio
import http
import logging
import os
import signal

import websockets

from arcanum.services.net.protocol import Envelope, MsgType, ProtocolError
from server import logic
from server import social
from server.lobby import Lobby
from server.sessions import MatchSession

log = logging.getLogger("arcanum.server")

CONNECTED: set = set()
LOBBY = Lobby()


async def health_check(path, request_headers):
    """Answer plain HTTP (Render health checks, browsers) with 200; let
    websocket UPGRADE requests pass through to the real handshake."""
    if logic.wants_websocket(request_headers.get):
        return None                      # proceed with the websocket handshake
    if path in ("/", "/healthz"):
        body = f"arcanum-server {logic.SERVER_VERSION} ok\n".encode()
        return http.HTTPStatus.OK, [("Content-Type", "text/plain")], body
    return http.HTTPStatus.NOT_FOUND, [("Content-Type", "text/plain")], b"not found\n"


class Connection:
    """One client socket: identity, queue state, and their match seat."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.name = "Adventurer"
        self.uid = ""
        self.session: MatchSession | None = None
        self.seat_index = 0
        self.in_queue = False

    async def send(self, raw: str) -> None:
        await self.ws.send(raw)

    async def route(self, env: Envelope) -> None:
        mtype = env.type
        if mtype == MsgType.PING.value:
            reply = logic.handle_envelope(env, len(CONNECTED))
            if reply is not None:
                await self.send(reply.encode())
            return
        if mtype == MsgType.PRESENCE_QUERY.value:
            await social.handle_presence(self, env)
            return
        if mtype == MsgType.CHALLENGE_SEND.value:
            await social.handle_challenge_send(self, env)
            return
        if mtype == MsgType.CHALLENGE_ACCEPT.value:
            await social.handle_challenge_accept(self, env)
            return
        if mtype == MsgType.CHALLENGE_DECLINE.value:
            await social.handle_challenge_decline(self, env)
            return
        if mtype == MsgType.CHALLENGE_CANCEL.value:
            await social.handle_challenge_cancel(self, env)
            return
        if mtype == MsgType.QUEUE_JOIN.value:
            mode = str(env.payload.get("mode", "pvp")).lower()
            deck = env.payload.get("deck")
            self.deck = deck if isinstance(deck, dict) else None
            await LOBBY.join(self, mode)
            return
        if mtype == MsgType.QUEUE_LEAVE.value:
            await LOBBY.leave(self)
            return
        # in-match intents
        if mtype in (MsgType.INTENT_PLAY_CARD.value, MsgType.INTENT_ATTACK.value,
                     MsgType.INTENT_PASS_PRIORITY.value,
                     MsgType.INTENT_ACTIVATE.value,
                     MsgType.INTENT_CONCEDE.value):
            if self.session is None or self.session.closed:
                await self.send(logic.make_error(
                    "no_match", "You're not in a match.").encode())
                return
            await self.session.handle_intent(self.seat_index, env)
            return
        reply = logic.handle_envelope(env, len(CONNECTED))
        if reply is not None:
            await self.send(reply.encode())


async def _card_library_loop() -> None:
    """Keep the official card library loaded so player decks that contain
    published cards validate and build into real piles."""
    from arcanum.services import cards as card_library
    while True:
        try:
            done = asyncio.Event()
            result = {}
            card_library.refresh(lambda n, err:
                                 (result.update(n=n, err=err), done.set()))
            try:
                await asyncio.wait_for(done.wait(), timeout=20)
                log.info("Card library: %s cards (%s)",
                         result.get("n"), result.get("err") or "ok")
            except asyncio.TimeoutError:
                log.warning("Card library refresh timed out.")
        except Exception:  # noqa: BLE001
            log.exception("Card library refresh crashed")
        await asyncio.sleep(3600)


async def handler(websocket):
    peer = getattr(websocket, "remote_address", ("?",))[0]
    conn = Connection(websocket)
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=logic.HELLO_TIMEOUT)
        conn.name, _token, conn.uid = logic.parse_hello(raw)
        CONNECTED.add(websocket)
        social.register(conn)
        log.info("HELLO from %s (%s) — %d online", conn.name, peer, len(CONNECTED))
        await websocket.send(logic.make_welcome(conn.name, len(CONNECTED)).encode())

        async for raw in websocket:
            try:
                env = Envelope.decode(raw)
            except (ProtocolError, ValueError) as exc:
                await websocket.send(logic.make_error("bad_envelope", str(exc)).encode())
                continue
            await conn.route(env)
    except asyncio.TimeoutError:
        log.info("Peer %s never said hello; closing.", peer)
    except ProtocolError as exc:
        log.info("Protocol error from %s: %s", peer, exc)
    except websockets.ConnectionClosed:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Unexpected error for %s", peer)
    finally:
        CONNECTED.discard(websocket)
        social.unregister(conn)
        try:
            await LOBBY.on_disconnect(conn)
            if conn.session is not None and not conn.session.closed:
                await conn.session.on_disconnect(conn.seat_index)
        except Exception:  # noqa: BLE001
            log.exception("Cleanup failed for %s", conn.name)
        log.info("%s disconnected — %d online", conn.name, len(CONNECTED))


async def main() -> None:
    port = int(os.environ.get("PORT", "10000"))
    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
        except NotImplementedError:
            pass
    asyncio.get_running_loop().create_task(_card_library_loop())
    async with websockets.serve(handler, "0.0.0.0", port,
                                process_request=health_check,
                                ping_interval=20, ping_timeout=20,
                                max_size=2 ** 20):
        log.info("Arcanum server v%s listening on :%d", logic.SERVER_VERSION, port)
        await stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    asyncio.run(main())
