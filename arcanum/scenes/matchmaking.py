"""Matchmaking — a dedicated full-screen scene.

Every road to a match runs through here, and it ALWAYS asks which deck first:

  PICK    choose a deck (saved decks + the Starter Deck)
  SEARCH  connecting / queued, with the golden wheel over the login artwork
          (practice mode skips SEARCH and starts the local match immediately)

The chosen deck rides along in the queue payload (`deck_name` + card counts),
ready for the server to deal from once decks-in-matches lands. Esc or Cancel
backs out cleanly at any point, leaving the queue if needed.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Optional

import pygame

from arcanum.core.constants import IMAGES_DIR, ROOT_DIR
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.game.catalog import starter_deck_cards
from arcanum.services.decks import DeckRecord, DeckResult
from arcanum.services.net.protocol import MsgType
from arcanum.ui import theme
from arcanum.ui.widgets import Button, apply_cursor

log = logging.getLogger(__name__)

PICK, SEARCH = range(2)
STARTER = DeckRecord(id="", name="Starter Deck",
                     cards=starter_deck_cards())
CONNECT_TIMEOUT = 25.0

MODE_TITLES = {"pvp": "Versus — find an opponent",
               "ai": "Duel the Umbral Adept",
               "practice": "Practice Match (offline)"}


def _load_art(name: str):
    for path in (IMAGES_DIR / name, ROOT_DIR / name):
        if path.is_file():
            try:
                return pygame.image.load(str(path)).convert()
            except pygame.error:
                pass
    return None


class MatchmakingScene(Scene):
    def on_enter(self, mode: str = "pvp", **kwargs) -> None:
        self.mode = mode
        self.phase = PICK
        self._time = 0.0
        self.status = ""
        self.error = ""
        self.decks: list[DeckRecord] = [STARTER]
        self.chosen: Optional[DeckRecord] = None
        self._awaiting = False
        self._pending_connect = False
        self._pending_timer = 0.0
        self._results: list[DeckResult] = []
        self._lock = threading.Lock()
        self._deck_rects: list[pygame.Rect] = []
        self._hover_deck: Optional[int] = None
        self.app.background.set_image(_load_art("background.jpg"))
        self.app.bus.subscribe(Events.NET_MESSAGE, self._on_net_message)
        self._build()
        self.app.backend.deck_store.list_decks(self._post_decks)

    def on_exit(self) -> None:
        if self._awaiting and self._net_state() == "CONNECTED":
            self.app.backend.net.send(MsgType.QUEUE_LEAVE, {})
        self.app.bus.unsubscribe(Events.NET_MESSAGE, self._on_net_message)

    def on_resize(self, size) -> None:
        if hasattr(self, "_time"):
            self._build()

    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.35, h / 1080))
        self.s = s
        self.btn_cancel = Button(pygame.Rect(0, 0, int(180 * s), int(48 * s)),
                                 "Cancel", self._cancel, primary=False,
                                 font_size=17,
                                 sound_cb=self.app.audio.ui_sound)
        self.btn_cancel.rect.midbottom = (w // 2, h - int(40 * s))

    # ------------------------------------------------------------ decks
    def _post_decks(self, result: DeckResult) -> None:
        with self._lock:
            self._results.append(result)

    def _apply_decks(self, result: DeckResult) -> None:
        if result.ok:
            self.decks = [STARTER] + result.decks
        else:
            self.decks = [STARTER]
            self.error = result.error or ""

    # ------------------------------------------------------------ flow
    def _net_state(self) -> str:
        return getattr(getattr(self.app.backend.net, "state", None), "name",
                       "DISCONNECTED")

    def _choose_deck(self, deck: DeckRecord) -> None:
        self.chosen = deck
        self.app.audio.ui_sound("play")
        if self.mode == "practice":
            self._start_practice()
            return
        state = self._net_state()
        if state == "CONNECTED":
            self._join_queue()
        elif state in ("CONNECTING", "RECONNECTING"):
            self.phase = SEARCH
            self._pending_connect = True
            self._pending_timer = 0.0
            self.status = "Connecting to the aether"
        else:
            self.error = "Server offline — try a Practice match instead."

    def _join_queue(self) -> None:
        self.phase = SEARCH
        self._awaiting = True
        self.status = ("Searching for an opponent" if self.mode == "pvp"
                       else "Summoning the Umbral Adept")
        payload = {"mode": self.mode,
                   "deck_name": self.chosen.name if self.chosen else "Starter",
                   "deck": dict(self.chosen.cards) if self.chosen else {}}
        self.app.backend.net.send(MsgType.QUEUE_JOIN, payload)

    def _start_practice(self) -> None:
        from arcanum.game.controller import LocalController
        from arcanum.scenes.match import MatchScene
        user = self.app.backend.session.user
        name = user.username if user else "You"
        log.info("Practice match with deck '%s'.",
                 self.chosen.name if self.chosen else "Starter")
        self.app.scenes.switch(MatchScene(self.app),
                               controller=LocalController(local_name=name))

    def _cancel(self) -> None:
        self.app.scenes.pop()

    # ------------------------------------------------------------ network
    def _on_net_message(self, envelope=None, **_kw) -> None:
        if envelope is None or self.phase != SEARCH:
            return
        if envelope.type == MsgType.MATCH_FOUND.value:
            opponent = envelope.payload.get("opponent", "an opponent")
            self.status = f"Match found — {opponent}!  Preparing the table"
        elif envelope.type == MsgType.QUEUE_JOIN.value:
            if envelope.payload.get("status") == "waiting":
                self.status = "Searching for an opponent"
        elif envelope.type == MsgType.EVENT_GAME_STATE.value and self._awaiting:
            self._awaiting = False
            from arcanum.game.controller import RemoteController
            from arcanum.scenes.match import MatchScene
            controller = RemoteController(self.app.bus, self.app.backend.net,
                                          envelope.match_id)
            controller._on_net_message(envelope)
            self.app.scenes.switch(MatchScene(self.app), controller=controller)
        elif envelope.type == MsgType.ERROR.value:
            self._awaiting = False
            self.phase = PICK
            self.error = envelope.payload.get("message", "Matchmaking failed.")

    # ------------------------------------------------------------ frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._cancel()
            return
        if self.btn_cancel.handle_event(event):
            return
        if self.phase == PICK and event.type == pygame.MOUSEBUTTONUP \
                and event.button == 1:
            for i, rect in enumerate(self._deck_rects):
                if rect.collidepoint(event.pos) and i < len(self.decks):
                    self._choose_deck(self.decks[i])
                    return

    def update(self, dt: float) -> None:
        self._time += dt
        with self._lock:
            pending, self._results = self._results, []
        for result in pending:
            self._apply_decks(result)
        if self._pending_connect:
            self._pending_timer += dt
            state = self._net_state()
            if state == "CONNECTED":
                self._pending_connect = False
                self._join_queue()
            elif state == "DISCONNECTED" or self._pending_timer > CONNECT_TIMEOUT:
                self._pending_connect = False
                self.phase = PICK
                self.error = ("Couldn't reach the server — it may be waking "
                              "up. Try again in a moment.")
        self.btn_cancel.update(dt)
        mouse = pygame.mouse.get_pos()
        self._hover_deck = None
        if self.phase == PICK:
            for i, rect in enumerate(self._deck_rects):
                if rect.collidepoint(mouse):
                    self._hover_deck = i
                    break
        apply_cursor([self.btn_cancel], force_hand=self._hover_deck is not None)

    # ------------------------------------------------------------ drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, h = surface.get_size()
        s = self.s
        veil = pygame.Surface((w, h), pygame.SRCALPHA)
        veil.fill((*theme.NAVY_ABYSS, 165))
        surface.blit(veil, (0, 0))

        theme.draw_text(surface, MODE_TITLES.get(self.mode, "Matchmaking"),
                        (w // 2, int(h * 0.12)),
                        theme.display_font(int(30 * s)), theme.GOLD_BRIGHT,
                        anchor="center")

        if self.phase == PICK:
            self._draw_pick(surface)
        else:
            self._draw_search(surface)

        if self.error:
            theme.draw_text(surface, self.error,
                            (w // 2, self.btn_cancel.rect.y - int(28 * s)),
                            theme.body_font(int(14 * s)), theme.DANGER,
                            anchor="center")
        self.btn_cancel.draw(surface)

    def _draw_pick(self, surface) -> None:
        w, h = surface.get_size()
        s = self.s
        theme.draw_text(surface, "Choose your deck",
                        (w // 2, int(h * 0.20)),
                        theme.body_font(int(18 * s)), theme.TEXT,
                        anchor="center")
        self._deck_rects = []
        card_w, card_h = int(300 * s), int(64 * s)
        gap = int(14 * s)
        top = int(h * 0.27)
        for i, deck in enumerate(self.decks[:8]):
            rect = pygame.Rect(0, 0, card_w, card_h)
            rect.midtop = (w // 2, top + i * (card_h + gap))
            self._deck_rects.append(rect)
            hover = i == self._hover_deck
            if hover:
                theme.draw_glow_rect(surface, rect, theme.GOLD_GLOW, 0.45,
                                     radius=12, spread=8)
            theme.draw_panel(surface, rect, fill=theme.NAVY,
                             border=theme.GOLD if hover else theme.NAVY_EDGE,
                             radius=12)
            theme.draw_text(surface, deck.name,
                            (rect.x + int(18 * s), rect.centery),
                            theme.body_font(int(16 * s), bold=True),
                            theme.TEXT, anchor="midleft")
            note = "Balanced starter" if deck is STARTER \
                else f"{deck.size} cards"
            theme.draw_text(surface, note,
                            (rect.right - int(18 * s), rect.centery),
                            theme.body_font(int(13 * s)), theme.TEXT_DIM,
                            anchor="midright")
        if len(self.decks) == 1:
            theme.draw_text(surface,
                            "Build your own in the Deck Builder (Decks tab).",
                            (w // 2,
                             self._deck_rects[-1].bottom + int(30 * s)),
                            theme.body_font(int(13 * s)), theme.TEXT_FAINT,
                            anchor="center")

    def _draw_search(self, surface) -> None:
        w, h = surface.get_size()
        s = self.s
        cx, cy = w // 2, int(h * 0.44)
        radius = int(58 * s)
        box = pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2)
        for i in range(3):
            start = self._time * (2.2 if i % 2 == 0 else -1.7) + i * 2.1
            span = 2.0 - i * 0.35
            arc_box = box.inflate(int(-16 * s) * i, int(-16 * s) * i)
            pygame.draw.arc(surface, theme.GOLD if i != 1 else theme.GOLD_DIM,
                            arc_box, start, start + span,
                            max(2, int(4 * s) - i))
        for k in range(8):
            angle = -self._time * 2.6 + k * math.tau / 8
            mote_r = radius + int(14 * s)
            pos = (int(cx + mote_r * math.cos(angle)),
                   int(cy + mote_r * math.sin(angle)))
            theme.aa_circle(surface, theme.GOLD_BRIGHT, pos,
                            int(3 * s) + (1 if k % 2 == 0 else 0))
        theme.aa_circle(surface, theme.GOLD, (cx, cy), int(6 * s))

        dots = "." * (1 + int(self._time * 2.5) % 3)
        theme.draw_text(surface, f"{self.status}{dots}",
                        (cx, cy + radius + int(52 * s)),
                        theme.display_font(int(22 * s)), theme.GOLD_BRIGHT,
                        anchor="center")
        deck_name = self.chosen.name if self.chosen else "Starter Deck"
        theme.draw_text(surface, f"Deck:  {deck_name}",
                        (cx, cy + radius + int(88 * s)),
                        theme.body_font(int(14 * s)), theme.TEXT_DIM,
                        anchor="center")
