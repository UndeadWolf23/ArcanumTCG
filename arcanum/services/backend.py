"""Backend facade — the single seam between the game and the outside world.

Scenes get one object (`app.backend`) and never construct services
themselves. Flipping the whole game from local/offline to Supabase + game
server is a change to `Backend.create()` only.
"""
from __future__ import annotations

import logging

from arcanum.core.constants import SUPABASE_URL
from arcanum.core.events import EventBus
from arcanum.services.auth import AuthService, LocalAuthService, SupabaseAuthService
from arcanum.services.net.client import NetworkClient, OfflineClient
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
        """Service resolution. Local today; Supabase + WebSocket later."""
        if SUPABASE_URL:
            log.info("Backend: Supabase configured — using online services.")
            auth: AuthService = SupabaseAuthService()
            # net = WebSocketClient(bus)   # enabled with the game server
            net: NetworkClient = OfflineClient(bus)
        else:
            log.info("Backend: no Supabase config — using local offline services.")
            auth = LocalAuthService()
            net = OfflineClient(bus)
        return cls(auth=auth, net=net, bus=bus)
