"""Home hub — MTGA-style main menu.

Layout: top navigation bar (Home / Profile / Decks / Packs / Store / Mastery)
with currency chips and a settings gear on the right; a featured-news
carousel center-left; game-mode panels on the right column; a quest-medallion
row along the bottom; the player identity bottom-left; and the big Play
button (versus matchmaking, click again to cancel) bottom-right.
"""
from __future__ import annotations

import math

import pygame

from arcanum.core.constants import APP_NAME
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.services.net.protocol import MsgType
from arcanum.ui import theme
from arcanum.ui.animation import approach
from arcanum.ui.widgets import Button, LinkButton, apply_cursor

NAV_ITEMS = ("Home", "Profile", "Decks", "Packs", "Store", "Mastery")

SLIDES = (
    ("Welcome to Arcanum", "A collectible card battler of stars and gold. "
     "Champions clash across the astral table."),
    ("Server-hosted matches are live", "Every card you play is validated by "
     "the Arcanum server. Queue up and duel across the aether."),
    ("The Deck Builder is open", "Browse the starter collection, shape a "
     "30-card deck, and save it to your account."),
)


class HomeScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self.toast = ""
        self._toast_timer = 0.0
        self._awaiting_match = False
        self._slide = 0
        self._slide_timer = 0.0
        self._nav_hover: int | None = None
        self._panel_hover: int | None = None
        self._nav_glow = [0.0] * len(NAV_ITEMS)
        self.app.bus.subscribe(Events.NET_MESSAGE, self._on_net_message)
        self._build()

    def on_exit(self) -> None:
        if self._awaiting_match and self._net_connected():
            self.app.backend.net.send(MsgType.QUEUE_LEAVE, {})
            self._awaiting_match = False
        self.app.bus.unsubscribe(Events.NET_MESSAGE, self._on_net_message)

    def on_resize(self, size) -> None:
        if hasattr(self, "_time"):
            self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.3, h / 1080))
        self.s = s
        ui = self.app.audio.ui_sound

        # top nav bar
        self.nav_rects: list[pygame.Rect] = []
        x = int(28 * s)
        for label in NAV_ITEMS:
            width = theme.body_font(int(17 * s), bold=True).size(label)[0]
            self.nav_rects.append(pygame.Rect(x, int(14 * s),
                                              width + int(28 * s), int(40 * s)))
            x += width + int(46 * s)
        self.btn_gear = Button(pygame.Rect(w - int(64 * s), int(14 * s),
                                           int(44 * s), int(40 * s)), "⚙",
                               self._settings, primary=False, font_size=20,
                               sound_cb=ui)

        # featured carousel (center-left)
        self.carousel = pygame.Rect(int(60 * s), int(120 * s),
                                    int(w * 0.52), int(h * 0.50))
        arrow_y = self.carousel.centery
        self.btn_prev = Button(pygame.Rect(self.carousel.x + int(10 * s),
                                           arrow_y - int(22 * s), int(40 * s),
                                           int(44 * s)), "‹",
                               lambda: self._change_slide(-1), primary=False,
                               font_size=24, sound_cb=ui)
        self.btn_next = Button(pygame.Rect(self.carousel.right - int(50 * s),
                                           arrow_y - int(22 * s), int(40 * s),
                                           int(44 * s)), "›",
                               lambda: self._change_slide(1), primary=False,
                               font_size=24, sound_cb=ui)

        # mode panels (right column)
        panel_x = self.carousel.right + int(28 * s)
        panel_w = w - panel_x - int(40 * s)
        self.mode_panels = []
        modes = [
            ("Duel the Umbral Adept", "Server-hosted match vs AI",
             self._play_ai_online),
            ("Practice Match", "Offline, on your machine", self._practice),
            ("Ranked Season", "Coming soon", lambda: self._todo("Ranked")),
        ]
        py = self.carousel.y
        ph = (self.carousel.height - int(24 * s)) // 3
        for title, sub, action in modes:
            rect = pygame.Rect(panel_x, py, panel_w, ph)
            self.mode_panels.append((rect, title, sub, action))
            py += ph + int(12 * s)

        # big Play (versus queue toggle), bottom-right
        self.btn_play = Button(pygame.Rect(w - int(300 * s), h - int(110 * s),
                                           int(250 * s), int(66 * s)), "Play",
                               self._play, sound_cb=ui, font_size=24)
        # identity + logout, bottom-left
        self.lnk_logout = LinkButton((int(150 * s), h - int(36 * s)),
                                     "Log out", self._logout, font_size=14,
                                     anchor="midleft")
        self.widgets = [self.btn_gear, self.btn_prev, self.btn_next,
                        self.btn_play, self.lnk_logout]

        # quest medallions (placeholder progression row)
        self.medallions = []
        mx = w // 2 - int(150 * s)
        for i in range(4):
            self.medallions.append((mx + i * int(90 * s), h - int(76 * s)))

    # ------------------------------------------------------------ helpers
    def _net_connected(self) -> bool:
        state = getattr(self.app.backend.net, "state", None)
        return getattr(state, "name", "") == "CONNECTED"

    def _local_name(self) -> str:
        user = self.app.backend.session.user
        return user.username if user else "You"

    def _change_slide(self, step: int) -> None:
        self._slide = (self._slide + step) % len(SLIDES)
        self._slide_timer = 0.0

    def _start_local_match(self) -> None:
        from arcanum.game.controller import LocalController
        from arcanum.scenes.match import MatchScene
        self.app.scenes.switch(
            MatchScene(self.app),
            controller=LocalController(local_name=self._local_name()))

    # ------------------------------------------------------------ actions
    def _play(self) -> None:
        if not self._net_connected():
            self._show_toast("Offline — starting a practice match.")
            self._start_local_match()
            return
        if self._awaiting_match:
            self._awaiting_match = False
            self.app.backend.net.send(MsgType.QUEUE_LEAVE, {})
            self._show_toast("Left the queue.")
            return
        self._awaiting_match = True
        self._show_toast("Searching for an opponent...")
        self.app.backend.net.send(MsgType.QUEUE_JOIN, {"mode": "pvp"})

    def _play_ai_online(self) -> None:
        if not self._net_connected():
            self._show_toast("Server offline — try Practice instead.")
            return
        if self._awaiting_match:
            self._show_toast("Cancel your search first.")
            return
        self._awaiting_match = True
        self._show_toast("Summoning the Umbral Adept...")
        self.app.backend.net.send(MsgType.QUEUE_JOIN, {"mode": "ai"})

    def _practice(self) -> None:
        self._start_local_match()

    def _open_decks(self) -> None:
        from arcanum.scenes.deckbuilder import DeckBuilderScene
        self.app.scenes.push(DeckBuilderScene(self.app))

    def _nav_action(self, index: int) -> None:
        label = NAV_ITEMS[index]
        if label == "Decks":
            self._open_decks()
        elif label != "Home":
            self._todo(label)

    def _settings(self) -> None:
        from arcanum.scenes.settings import SettingsScene
        self.app.scenes.push(SettingsScene(self.app))

    def _logout(self) -> None:
        self.app.backend.session.end()
        self.app.bus.publish(Events.AUTH_LOGOUT)
        self.app.goto_login()

    def _todo(self, name: str) -> None:
        self._show_toast(f"{name} is on the roadmap — not built yet.")

    def _show_toast(self, message: str) -> None:
        self.toast = message
        self._toast_timer = 3.0

    def _on_net_message(self, envelope=None, **_kw) -> None:
        if envelope is None or not self._awaiting_match:
            return
        if envelope.type == MsgType.MATCH_FOUND.value:
            opponent = envelope.payload.get("opponent", "an opponent")
            self._show_toast(f"Match found — {opponent}!")
            return
        if envelope.type == MsgType.QUEUE_JOIN.value:
            if envelope.payload.get("status") == "waiting":
                self._show_toast("In queue — waiting for a challenger...")
            return
        if envelope.type == MsgType.EVENT_GAME_STATE.value:
            self._awaiting_match = False
            from arcanum.game.controller import RemoteController
            from arcanum.scenes.match import MatchScene
            controller = RemoteController(self.app.bus, self.app.backend.net,
                                          envelope.match_id)
            controller._on_net_message(envelope)
            self.app.scenes.switch(MatchScene(self.app), controller=controller)
        elif envelope.type == MsgType.ERROR.value:
            self._awaiting_match = False
            self._show_toast(envelope.payload.get("message",
                                                  "Matchmaking failed."))

    # ------------------------------------------------------------ frame
    def handle_event(self, event: pygame.event.Event) -> None:
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for i, rect in enumerate(self.nav_rects):
                if rect.collidepoint(event.pos):
                    self._nav_action(i)
                    return
            for i, (rect, _t, _sub, action) in enumerate(self.mode_panels):
                if rect.collidepoint(event.pos):
                    action()
                    return

    def update(self, dt: float) -> None:
        self._time += dt
        self._toast_timer = max(0.0, self._toast_timer - dt)
        self._slide_timer += dt
        if self._slide_timer > 7.0:
            self._change_slide(1)
        mouse = pygame.mouse.get_pos()
        self._nav_hover = next((i for i, r in enumerate(self.nav_rects)
                                if r.collidepoint(mouse)), None)
        self._panel_hover = next((i for i, (r, *_rest) in
                                  enumerate(self.mode_panels)
                                  if r.collidepoint(mouse)), None)
        for i in range(len(NAV_ITEMS)):
            target = 1.0 if i == self._nav_hover else 0.0
            self._nav_glow[i] = approach(self._nav_glow[i], target, dt)
        dots = "." * (1 + int(self._time * 2) % 3)
        self.btn_play.label = (f"Searching{dots}"
                               if self._awaiting_match else "Play")
        for widget in self.widgets:
            widget.update(dt)
        apply_cursor(self.widgets,
                     force_hand=(self._nav_hover is not None
                                 or self._panel_hover is not None))

    # ------------------------------------------------------------ drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, h = surface.get_size()
        s = self.s

        # nav bar
        bar = pygame.Rect(0, 0, w, int(66 * s))
        veil = pygame.Surface(bar.size, pygame.SRCALPHA)
        veil.fill((*theme.NAVY_ABYSS, 200))
        surface.blit(veil, (0, 0))
        pygame.draw.line(surface, theme.GOLD_DIM, (0, bar.bottom),
                         (w, bar.bottom))
        for i, label in enumerate(NAV_ITEMS):
            rect = self.nav_rects[i]
            active = label == "Home"
            glow = self._nav_glow[i]
            color = theme.GOLD_BRIGHT if active else tuple(
                int(theme.TEXT_DIM[c] + (theme.TEXT[c] - theme.TEXT_DIM[c]) * glow)
                for c in range(3))
            theme.draw_text(surface, label, rect.center,
                            theme.body_font(int(17 * s), bold=active), color,
                            anchor="center")
            if active:
                pygame.draw.line(surface, theme.GOLD,
                                 (rect.x + 6, bar.bottom - 2),
                                 (rect.right - 6, bar.bottom - 2), 2)
        # currency chips (economy placeholder)
        chip_x = w - int(300 * s)
        for icon_color, amount in ((theme.GOLD, "1,000"),
                                   ((86, 156, 255), "50")):
            chip = pygame.Rect(chip_x, int(18 * s), int(96 * s), int(32 * s))
            theme.draw_panel(surface, chip, fill=theme.NAVY,
                             border=theme.NAVY_EDGE, radius=16)
            pygame.draw.circle(surface, icon_color,
                               (chip.x + int(16 * s), chip.centery), int(9 * s))
            theme.draw_text(surface, amount,
                            (chip.x + int(32 * s), chip.centery),
                            theme.body_font(int(14 * s)), theme.TEXT,
                            anchor="midleft")
            chip_x += int(108 * s)

        # carousel
        theme.draw_glow_rect(surface, self.carousel, theme.GOLD, 0.2,
                             radius=14, spread=12)
        theme.draw_panel(surface, self.carousel, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=14)
        title, body = SLIDES[self._slide]
        theme.draw_text(surface, title,
                        (self.carousel.x + int(70 * s),
                         self.carousel.bottom - int(96 * s)),
                        theme.display_font(int(26 * s)), theme.GOLD_BRIGHT,
                        anchor="topleft")
        font = theme.body_font(int(15 * s))
        words, line, ty = body.split(), "", self.carousel.bottom - int(58 * s)
        max_w = self.carousel.width - int(140 * s)
        for word in words + ["\\n"]:
            test = f"{line} {word}".strip()
            if word == "\\n" or font.size(test)[0] > max_w:
                theme.draw_text(surface, line,
                                (self.carousel.x + int(70 * s), ty), font,
                                theme.TEXT_DIM, anchor="topleft")
                ty += font.get_linesize()
                line = word if word != "\\n" else ""
            else:
                line = test
        # emblem art placeholder: rotating star sigil
        cx = self.carousel.centerx
        cy = self.carousel.y + int(self.carousel.height * 0.36)
        for k in range(8):
            angle = self._time * 0.3 + k * math.tau / 8
            r_out = int(70 * s)
            end = (cx + r_out * math.cos(angle), cy + r_out * math.sin(angle))
            pygame.draw.line(surface, theme.GOLD_DIM, (cx, cy), end)
        pygame.draw.circle(surface, theme.GOLD, (cx, cy), int(10 * s))
        theme.draw_text(surface, APP_NAME.upper(), (cx, cy + int(96 * s)),
                        theme.display_font(int(34 * s), bold=True),
                        theme.GOLD_BRIGHT, anchor="center")
        for i in range(len(SLIDES)):
            dot = (self.carousel.centerx + (i - 1) * int(20 * s),
                   self.carousel.bottom - int(16 * s))
            color = theme.GOLD if i == self._slide else theme.NAVY_EDGE
            pygame.draw.circle(surface, color, dot, int(4 * s))

        # mode panels
        for i, (rect, title, sub, _a) in enumerate(self.mode_panels):
            hover = i == self._panel_hover
            if hover:
                theme.draw_glow_rect(surface, rect, theme.GOLD_GLOW, 0.4,
                                     radius=12, spread=8)
            theme.draw_panel(surface, rect, fill=theme.NAVY,
                             border=theme.GOLD if hover else theme.NAVY_EDGE,
                             radius=12)
            theme.draw_text(surface, title,
                            (rect.x + int(18 * s), rect.y + int(16 * s)),
                            theme.body_font(int(17 * s), bold=True),
                            theme.TEXT, anchor="topleft")
            theme.draw_text(surface, sub,
                            (rect.x + int(18 * s), rect.y + int(42 * s)),
                            theme.body_font(int(13 * s)), theme.TEXT_DIM,
                            anchor="topleft")

        # quest medallions (placeholders)
        for cx, cy in self.medallions:
            pygame.draw.circle(surface, theme.NAVY_RAISED, (cx, cy), int(26 * s))
            pygame.draw.circle(surface, theme.NAVY_EDGE, (cx, cy), int(26 * s),
                               width=1)
            theme.draw_text(surface, "?", (cx, cy),
                            theme.body_font(int(16 * s)), theme.TEXT_FAINT,
                            anchor="center")
        theme.draw_text(surface, "Quests — coming soon",
                        (self.medallions[0][0] - int(40 * s),
                         self.medallions[0][1] + int(40 * s)),
                        theme.body_font(int(12 * s)), theme.TEXT_FAINT,
                        anchor="topleft")

        # identity + connection status, bottom-left
        avatar = (int(60 * s), h - int(70 * s))
        pygame.draw.circle(surface, theme.NAVY_RAISED, avatar, int(30 * s))
        pygame.draw.circle(surface, theme.GOLD_DIM, avatar, int(30 * s), width=2)
        user = self.app.backend.session.user
        name = (user.username if user else "You") + \
            ("  ·  temp" if user and user.is_guest else "")
        theme.draw_text(surface, name, (int(104 * s), h - int(84 * s)),
                        theme.body_font(int(16 * s), bold=True), theme.TEXT,
                        anchor="topleft")
        net = self.app.backend.net
        state_name = getattr(getattr(net, "state", None), "name", "DISCONNECTED")
        if state_name == "CONNECTED":
            bits = ["Online"]
            if getattr(net, "online_count", None):
                bits.append(f"{net.online_count} in the aether")
            if getattr(net, "latency_ms", None) is not None:
                bits.append(f"{net.latency_ms} ms")
            status, color = "  ·  ".join(bits), theme.SUCCESS
        elif state_name in ("CONNECTING", "RECONNECTING"):
            status, color = "Connecting...", theme.TEXT_DIM
        else:
            status, color = "Offline — practice only", theme.TEXT_FAINT
        theme.draw_text(surface, status, (int(104 * s), h - int(62 * s)),
                        theme.body_font(int(13 * s)), color, anchor="topleft")

        for widget in self.widgets:
            widget.draw(surface)

        if self._toast_timer > 0 and self.toast:
            fade = min(1.0, self._toast_timer / 0.4)
            box = pygame.Rect(0, 0, min(w - 80, 560), 44)
            box.midbottom = (w // 2, h - int(120 * s))
            veil = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(230 * fade)),
                             veil.get_rect(), border_radius=10)
            pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                             veil.get_rect(), width=1, border_radius=10)
            surface.blit(veil, box.topleft)
            theme.draw_text(surface, self.toast, box.center,
                            theme.body_font(15), theme.TEXT, anchor="center",
                            alpha=int(255 * fade))
