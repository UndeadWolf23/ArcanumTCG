"""Backend facade — the single seam between the game and the outside world.

Scenes get one object (`app.backend`) and never construct services
themselves. Flipping the whole game from local/offline to Supabase + game
server is a change to `Backend.create()` only.
"""
from __future__ import annotations

import logging

from arcanum.core.constants import GAME_SERVER_URL, SUPABASE_URL
from arcanum.core.events import EventBus
from arcanum.services.auth import AuthService, LocalAuthService, SupabaseAuthService
from arcanum.services.net.client import (NetworkClient, OfflineClient,
                                          WebSocketClient)
from arcanum.services.net.matchmaking import Matchmaker
from arcanum.services.session import Session

log = logging.getLogger(__name__)


class Backend:
    def __init__(self, auth: AuthService, net: NetworkClient, bus: EventBus) -> None:
        self.auth = auth
        self.net = net
        self.bus = bus
        self.session = Session()
        self.matchmaking = Matchmaker(net, bus)

    @classmethod
    def create(cls, bus: EventBus) -> "Backend":
        """Service resolution: pick real services when they're configured."""
        if SUPABASE_URL:
            try:
                auth: AuthService = SupabaseAuthService()
                log.info("Backend: Supabase configured — real accounts enabled.")
            except RuntimeError as exc:
                log.warning("%s — falling back to local auth.", exc)
                auth = LocalAuthService()
        else:
            log.info("Backend: no Supabase config — using local auth.")
            auth = LocalAuthService()

        net: NetworkClient
        if GAME_SERVER_URL:
            try:
                net = WebSocketClient(bus, GAME_SERVER_URL)
                log.info("Backend: game server configured (%s).", GAME_SERVER_URL)
            except RuntimeError as exc:
                log.warning("%s — running offline.", exc)
                net = OfflineClient(bus)
        else:
            log.info("Backend: no game server configured — offline mode.")
            net = OfflineClient(bus)
        return cls(auth=auth, net=net, bus=bus)
