"""Backend facade — the single seam between the game and the outside world.

Scenes get one object (`app.backend`) and never construct services
themselves. Flipping the whole game from local/offline to Supabase + game
server is a change to `Backend.create()` only.
"""
from __future__ import annotations

import logging

from arcanum.core.constants import GAME_SERVER_URL, SUPABASE_URL
from arcanum.core.events import EventBus, Events
from arcanum.services.auth import AuthService, LocalAuthService, SupabaseAuthService
from arcanum.services.net.client import (NetworkClient, OfflineClient,
                                          WebSocketClient)
from arcanum.services.net.matchmaking import Matchmaker
from arcanum.services.decks import DeckStore, LocalDeckStore, SupabaseDeckStore
from arcanum.services.social import SupabaseSocial
from arcanum.services.session import Session

log = logging.getLogger(__name__)


class Backend:
    def __init__(self, auth: AuthService, net: NetworkClient, bus: EventBus) -> None:
        self.auth = auth
        self.net = net
        self.bus = bus
        self.session = Session()
        self.matchmaking = Matchmaker(net, bus)
        self.deck_store: DeckStore = LocalDeckStore()
        self.social: SupabaseSocial | None = None
        self.profile: dict | None = None          # {id, username, coins}
        self.challenges: list[dict] = []          # incoming friend challenges
        bus.subscribe(Events.NET_MESSAGE, self._on_social_message)

    def refresh_deck_store(self) -> None:
        """Called after login/logout: cloud decks for real accounts, local
        JSON for guests and offline mode."""
        token = self.session.access_token
        if SUPABASE_URL and token and self.session.user \
                and not self.session.user.is_guest:
            try:
                self.deck_store = SupabaseDeckStore(access_token=token)
                user = self.session.user
                if user is not None and getattr(user, "id", None):
                    try:
                        self.social = SupabaseSocial(
                            access_token=token, user_id=user.id)
                        self.social.ensure_profile(
                            user.username or "", self._apply_profile)
                    except RuntimeError:
                        self.social = None
                log.info("Deck store: Supabase (cloud saves).")
                return
            except RuntimeError as exc:
                log.warning("%s — using local decks.", exc)
        self.deck_store = LocalDeckStore()
        self.social = None
        self.profile = None
        log.info("Deck store: local file.")

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

    def _apply_profile(self, result) -> None:
        if result.ok and result.profile:
            self.profile = result.profile
            log.info("Profile ready: %s", result.profile.get("username"))
            self.bus.publish(Events.PROFILE_READY)
        elif result.error:
            log.warning("Profile setup failed: %s", result.error)

    @property
    def display_name(self) -> str:
        if self.profile and self.profile.get("username"):
            return str(self.profile["username"])
        user = self.session.user
        return user.username if user else "Adventurer"

    def _on_social_message(self, envelope=None, **_kw) -> None:
        from arcanum.services.net.protocol import MsgType
        if envelope is None:
            return
        if envelope.type == MsgType.CHALLENGE_INCOMING.value:
            name = str(envelope.payload.get("from", ""))
            from_id = str(envelope.payload.get("from_id", ""))
            if name and all(c.get("from_id") != from_id
                            for c in self.challenges):
                self.challenges.append({"from": name, "from_id": from_id})
        elif envelope.type == MsgType.CHALLENGE_RESULT.value:
            status = envelope.payload.get("status")
            who = str(envelope.payload.get("from", ""))
            if status == "cancelled" and who:
                self.challenges = [c for c in self.challenges
                                   if c.get("from") != who]
