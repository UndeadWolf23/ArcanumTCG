"""Matchmaking lobby — pairs players into MatchSessions. Transport-free.

The transport (app.py) hands in connection-like objects exposing `.name`,
an async `.send(raw)`, and mutable `.session` / `.seat_index` / `.in_queue`
attributes. The lobby owns the waiting line and session creation:

* mode "pvp": wait in line; when a second player arrives, both are seated
  (seat order randomized so neither always goes first) and MATCH_FOUND +
  the opening snapshots go out.
* mode "ai": instant match against the server-side AI.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Optional

from arcanum.services.net.protocol import Envelope, MsgType
from server.sessions import MatchSession, Seat

log = logging.getLogger(__name__)


def _ack(status: str) -> Envelope:
    return Envelope(type=MsgType.QUEUE_JOIN.value, payload={"status": status})


def _error(code: str, message: str) -> Envelope:
    return Envelope(type=MsgType.ERROR.value,
                    payload={"code": code, "message": message})


class Lobby:
    def __init__(self, rng: random.Random | None = None) -> None:
        self.waiting: list[Any] = []      # connections queued for pvp
        self.rng = rng or random.Random()

    # ------------------------------------------------------------ queue
    async def join(self, conn: Any, mode: str) -> None:
        if conn.session is not None and not conn.session.closed:
            await conn.send(_error("already_in_match",
                                   "You're already in a match.").encode())
            return
        if mode == "ai":
            await self._remove(conn)          # can't queue and fight AI at once
            await self._start_ai(conn)
            return
        # default: pvp
        if conn in self.waiting:
            await conn.send(_error("already_in_queue",
                                   "Already searching for an opponent.").encode())
            return
        opponent = self._pop_waiting(exclude=conn)
        if opponent is None:
            self.waiting.append(conn)
            conn.in_queue = True
            await conn.send(_ack("waiting").encode())
            log.info("%s queued for versus (%d waiting).",
                     conn.name, len(self.waiting))
            return
        await self._start_pvp(opponent, conn)

    async def leave(self, conn: Any) -> None:
        removed = await self._remove(conn)
        await conn.send(Envelope(type=MsgType.QUEUE_LEAVE.value,
                                 payload={"status": "left" if removed
                                          else "not_queued"}).encode())

    async def on_disconnect(self, conn: Any) -> None:
        await self._remove(conn)

    def _pop_waiting(self, exclude: Any) -> Optional[Any]:
        while self.waiting:
            candidate = self.waiting.pop(0)
            candidate.in_queue = False
            if candidate is not exclude:
                return candidate
        return None

    async def _remove(self, conn: Any) -> bool:
        if conn in self.waiting:
            self.waiting.remove(conn)
            conn.in_queue = False
            return True
        return False

    # ------------------------------------------------------------ sessions
    async def _start_ai(self, conn: Any) -> None:
        seats = [Seat(conn.name, conn.send,
                      deck=getattr(conn, "deck", None),
                      uid=getattr(conn, "uid", "")),
                 Seat("Umbral Adept", None)]
        session = MatchSession(seats)
        conn.session, conn.seat_index = session, 0
        log.info("%s started an AI match (%s).", conn.name, session.match_id)
        await conn.send(Envelope(type=MsgType.MATCH_FOUND.value,
                                 payload={"opponent": "Umbral Adept",
                                          "mode": "ai"},
                                 match_id=session.match_id).encode())
        session.launch()

    async def _start_pvp(self, a: Any, b: Any) -> None:
        pair = [a, b]
        self.rng.shuffle(pair)               # random first player
        seats = [Seat(pair[0].name, pair[0].send,
                      deck=getattr(pair[0], "deck", None),
                      uid=getattr(pair[0], "uid", "")),
                 Seat(pair[1].name, pair[1].send,
                      deck=getattr(pair[1], "deck", None),
                      uid=getattr(pair[1], "uid", ""))]
        session = MatchSession(seats)
        for index, conn in enumerate(pair):
            conn.session, conn.seat_index = session, index
            other = pair[1 - index]
            await conn.send(Envelope(type=MsgType.MATCH_FOUND.value,
                                     payload={"opponent": other.name,
                                              "mode": "pvp"},
                                     match_id=session.match_id).encode())
        log.info("Versus match %s: %s vs %s.",
                 session.match_id, seats[0].name, seats[1].name)
        session.launch()
