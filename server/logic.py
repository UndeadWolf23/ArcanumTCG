"""Server-side protocol logic — transport-free so it's unit-testable.

app.py owns the actual websocket; everything about *what to say* lives here.
The server reuses the client's protocol module so both sides can never drift.
"""
from __future__ import annotations

import logging
import time

from arcanum.services.net.protocol import Envelope, MsgType, ProtocolError

log = logging.getLogger(__name__)

SERVER_VERSION = "0.2.1"
HELLO_TIMEOUT = 10.0     # seconds to identify yourself before we hang up


def wants_websocket(get_header) -> bool:
    """True if the request is a websocket upgrade (not plain HTTP).

    `get_header` is any callable like headers.get. Checks the Upgrade header,
    which proxies (Cloudflare/Render) pass through untouched.
    """
    upgrade = get_header("Upgrade") or get_header("upgrade") or ""
    return "websocket" in str(upgrade).lower()


def parse_hello(raw: str | bytes) -> tuple[str, str, str]:
    """Validate the first message. Returns (name, token) or raises ProtocolError."""
    env = Envelope.decode(raw)          # also enforces protocol version
    if env.type != MsgType.HELLO.value:
        raise ProtocolError(f"Expected hello, got {env.type!r}")
    name = str(env.payload.get("name", "")).strip()[:24] or "Adventurer"
    uid = str(env.payload.get("uid", "")).strip()[:64]
    token = str(env.payload.get("token", ""))
    # Dev mode: any token is accepted. Supabase JWT verification lands here
    # later: decode the JWT, check signature + expiry, extract the user id.
    return name, token, uid


def make_welcome(name: str, online: int) -> Envelope:
    return Envelope(type=MsgType.WELCOME.value, payload={
        "name": name,
        "online": online,
        "server_version": SERVER_VERSION,
        "motd": "Arcanum dev server — connection online.",
    })


def make_error(code: str, message: str) -> Envelope:
    return Envelope(type=MsgType.ERROR.value,
                    payload={"code": code, "message": message})


def handle_envelope(env: Envelope, online: int) -> Envelope | None:
    """Route one post-handshake message; return the reply (or None)."""
    if env.type == MsgType.PING.value:
        # echo the client's timestamp back so it can measure round-trip time
        return Envelope(type=MsgType.PONG.value,
                        payload={"echo_ts": env.payload.get("ts", env.ts),
                                 "online": online,
                                 "server_time": time.time()})
    log.info("Unhandled message type %r", env.type)
    return make_error("unknown_type", f"Server can't handle {env.type!r} yet.")
