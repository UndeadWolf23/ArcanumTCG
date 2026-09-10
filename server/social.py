"""Server-side social: presence + friend challenges.

The game server is the realtime half of the friends system (the durable
graph lives in Supabase). It already knows every connected player by name,
so presence is a dictionary lookup and a challenge is a handshake:

    A: CHALLENGE_SEND {to, deck}        (deck stored on A's connection)
    B: CHALLENGE_INCOMING {from}        (pushed immediately if online)
    B: CHALLENGE_ACCEPT {from, deck} -> both seated in a MatchSession
       or CHALLENGE_DECLINE {from}   -> A hears CHALLENGE_RESULT declined

Challenges expire after CHALLENGE_TTL seconds or when either side
disconnects. Names are the usernames clients HELLO with (their profile
name), matched case-insensitively.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from arcanum.services.net.protocol import Envelope, MsgType
from server.sessions import MatchSession, Seat

log = logging.getLogger(__name__)

CHALLENGE_TTL = 90.0

# permanent account uid -> connection (names are display-only)
ONLINE: dict[str, Any] = {}
# (challenger_uid, target_uid) -> expiry timestamp
PENDING: dict[tuple[str, str], float] = {}


def _key(conn: Any) -> str:
    return conn.uid or ("name:" + conn.name.lower())   # guests fall back


def register(conn: Any) -> None:
    ONLINE[_key(conn)] = conn


def unregister(conn: Any) -> None:
    key = _key(conn)
    if ONLINE.get(key) is conn:
        ONLINE.pop(key, None)
    for pair in [p for p in PENDING if key in p]:
        PENDING.pop(pair, None)


def _prune() -> None:
    now = time.time()
    for pair in [p for p, expiry in PENDING.items() if expiry < now]:
        PENDING.pop(pair, None)


def _find(uid: str) -> Optional[Any]:
    conn = ONLINE.get(uid.strip())
    if conn is None:
        return None
    # never route to someone already fighting
    session = getattr(conn, "session", None)
    if session is not None and not session.closed:
        return None
    return conn


async def handle_presence(conn: Any, env: Envelope) -> None:
    ids = env.payload.get("ids", [])
    if not isinstance(ids, list):
        ids = []
    online = [i for i in ids[:100]
              if isinstance(i, str) and i.strip() in ONLINE]
    await conn.send(Envelope(type=MsgType.PRESENCE_STATE.value,
                             payload={"online": online}).encode())


async def handle_challenge_send(conn: Any, env: Envelope) -> None:
    _prune()
    target_uid = str(env.payload.get("to_id", "")).strip()
    display = str(env.payload.get("to", "them")).strip() or "them"
    target = _find(target_uid)
    deck = env.payload.get("deck")
    conn.deck = deck if isinstance(deck, dict) else None
    if target is None:
        await conn.send(Envelope(
            type=MsgType.CHALLENGE_RESULT.value,
            payload={"to": display, "status": "offline",
                     "message": f"{display} isn't available right "
                                "now."}).encode())
        return
    if target is conn:
        await conn.send(Envelope(
            type=MsgType.CHALLENGE_RESULT.value,
            payload={"to": display, "status": "invalid",
                     "message": "You can't challenge yourself."}).encode())
        return
    PENDING[(_key(conn), _key(target))] = time.time() + CHALLENGE_TTL
    log.info("%s challenges %s.", conn.name, target.name)
    await target.send(Envelope(
        type=MsgType.CHALLENGE_INCOMING.value,
        payload={"from": conn.name, "from_id": _key(conn)}).encode())
    await conn.send(Envelope(
        type=MsgType.CHALLENGE_RESULT.value,
        payload={"to": target.name, "status": "sent"}).encode())


async def handle_challenge_cancel(conn: Any, env: Envelope) -> None:
    target_uid = str(env.payload.get("to_id", "")).strip()
    PENDING.pop((_key(conn), target_uid), None)
    target = ONLINE.get(target_uid)
    if target is not None:
        await target.send(Envelope(
            type=MsgType.CHALLENGE_RESULT.value,
            payload={"from": conn.name, "status": "cancelled"}).encode())


async def handle_challenge_decline(conn: Any, env: Envelope) -> None:
    from_uid = str(env.payload.get("from_id", "")).strip()
    PENDING.pop((from_uid, _key(conn)), None)
    challenger = ONLINE.get(from_uid)
    if challenger is not None:
        await challenger.send(Envelope(
            type=MsgType.CHALLENGE_RESULT.value,
            payload={"to": conn.name, "status": "declined",
                     "message": f"{conn.name} declined the "
                                "challenge."}).encode())


async def handle_challenge_accept(conn: Any, env: Envelope) -> None:
    _prune()
    from_uid = str(env.payload.get("from_id", "")).strip()
    from_name = str(env.payload.get("from", "your friend")).strip()
    pair = (from_uid, _key(conn))
    if pair not in PENDING:
        await conn.send(Envelope(
            type=MsgType.CHALLENGE_RESULT.value,
            payload={"from": from_name, "status": "expired",
                     "message": "That challenge has expired."}).encode())
        return
    challenger = _find(from_uid)
    if challenger is None:
        PENDING.pop(pair, None)
        await conn.send(Envelope(
            type=MsgType.CHALLENGE_RESULT.value,
            payload={"from": from_name, "status": "offline",
                     "message": f"{from_name} left before the match "
                                "could start."}).encode())
        return
    PENDING.pop(pair, None)
    deck = env.payload.get("deck")
    conn.deck = deck if isinstance(deck, dict) else None

    seats = [Seat(challenger.name, challenger.send,
                  deck=getattr(challenger, "deck", None),
                  uid=getattr(challenger, "uid", "")),
             Seat(conn.name, conn.send, deck=getattr(conn, "deck", None),
                  uid=getattr(conn, "uid", ""))]
    session = MatchSession(seats)
    for index, player in enumerate((challenger, conn)):
        player.session, player.seat_index = session, index
        other = (challenger, conn)[1 - index]
        await player.send(Envelope(
            type=MsgType.MATCH_FOUND.value,
            payload={"opponent": other.name, "mode": "friendly"},
            match_id=session.match_id).encode())
    log.info("Friendly match: %s vs %s (%s).",
             challenger.name, conn.name, session.match_id)
    session.launch()
