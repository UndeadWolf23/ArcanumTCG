"""Matchmaking facade (future).

Thin wrapper over the network client: join/leave the ranked or casual queue
and surface MATCH_FOUND through the event bus. Kept separate from the client
so queue logic (deck validation before queueing, dodge penalties, wait-time
estimates) has a home that isn't transport code.
"""
from __future__ import annotations

import logging

from arcanum.core.events import EventBus
from arcanum.services.net.client import NetworkClient
from arcanum.services.net.protocol import MsgType

log = logging.getLogger(__name__)


class Matchmaker:
    def __init__(self, net: NetworkClient, bus: EventBus) -> None:
        self.net = net
        self.bus = bus
        self.in_queue = False

    def join_queue(self, mode: str = "casual", deck_id: str = "") -> None:
        log.info("Queue join requested (mode=%s) — server not available yet.", mode)
        self.in_queue = True
        self.net.send(MsgType.QUEUE_JOIN, {"mode": mode, "deck_id": deck_id})

    def leave_queue(self) -> None:
        self.in_queue = False
        self.net.send(MsgType.QUEUE_LEAVE)
