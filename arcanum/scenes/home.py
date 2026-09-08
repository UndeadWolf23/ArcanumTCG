"""Home scene — the main menu hub after login.

Play: when connected to the game server, requests a server-hosted match
(the scene opens when the opening snapshot arrives). Offline, it starts a
local practice match instead. Practice vs AI is always local.
"""
from __future__ import annotations

import pygame

from arcanum.core.constants import APP_NAME
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.services.net.protocol import MsgType
from arcanum.ui import theme
from arcanum.ui.animation import Tween
from arcanum.ui.widgets import Button, apply_cursor


class HomeScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self._intro = Tween(0.0, 1.0, 0.5)
        self.toast = ""
        self._toast_timer = 0.0
        self._awaiting_match = False
        self.app.bus.subscribe(Events.NET_MESSAGE, self._on_net_message)
        self._build()

    def on_exit(self) -> None:
        self.app.bus.unsubscribe(Events.NET_MESSAGE, self._on_net_message)

    def on_resize(self, size: tuple[int, int]) -> None:
        if hasattr(self, "_time"):
            self._build()

    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        ui = self.app.audio.ui_sound
        btn_w, btn_h, gap = 340, 58, 16
        x = int(w * 0.5) - btn_w // 2
        y = int(h * 0.40)
        entries = [
            ("Play", self._play, True),
            ("Practice vs AI", self._practice, False),
            ("Collection", lambda: self._todo("Collection"), False),
            ("Store", lambda: self._todo("Store"), False),
            ("Settings", self._settings, False),
            ("Log Out", self._logout, False),
        ]
        self.widgets = []
        for i, (label, action, primary) in enumerate(entries):
            rect = pygame.Rect(x, y + i * (btn_h + gap), btn_w, btn_h)
            self.widgets.append(Button(rect, label, action, primary=primary,
                                       sound_cb=ui))

    # -- helpers -----------------------------------------------------------
    def _net_connected(self) -> bool:
        state = getattr(self.app.backend.net, "state", None)
        return getattr(state, "name", "") == "CONNECTED"

    def _local_name(self) -> str:
        user = self.app.backend.session.user
        return user.username if user else "You"

    def _start_local_match(self) -> None:
        from arcanum.game.controller import LocalController
        from arcanum.scenes.match import MatchScene
        self.app.scenes.switch(
            MatchScene(self.app),
            controller=LocalController(local_name=self._local_name()))

    # -- actions ---------------------------------------------------------
    def _play(self) -> None:
        """Server-hosted match when online; local practice otherwise."""
        if self._net_connected():
            if self._awaiting_match:
                self._show_toast("Already finding a match...")
                return
            self._awaiting_match = True
            self._show_toast("Summoning an opponent...")
            self.app.backend.net.send(MsgType.QUEUE_JOIN, {"mode": "casual"})
        else:
            self._show_toast("Offline — starting a practice match.")
            self._start_local_match()

    def _practice(self) -> None:
        self._start_local_match()

    def _on_net_message(self, envelope=None, **_kw) -> None:
        if envelope is None or not self._awaiting_match:
            return
        if envelope.type == MsgType.EVENT_GAME_STATE.value:
            self._awaiting_match = False
            from arcanum.game.controller import RemoteController
            from arcanum.scenes.match import MatchScene
            controller = RemoteController(self.app.bus, self.app.backend.net,
                                          envelope.match_id)
            controller._on_net_message(envelope)   # seed the first snapshot
            self.app.scenes.switch(MatchScene(self.app), controller=controller)
        elif envelope.type == MsgType.ERROR.value:
            self._awaiting_match = False
            self._show_toast(envelope.payload.get("message",
                                                  "Matchmaking failed."))

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

    # -- frame -------------------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        for widget in self.widgets:
            if widget.handle_event(event):
                return

    def update(self, dt: float) -> None:
        self._time += dt
        self._intro.update(dt)
        self._toast_timer = max(0.0, self._toast_timer - dt)
        for widget in self.widgets:
            widget.update(dt)
        apply_cursor(self.widgets)

    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, h = surface.get_size()
        cx = w // 2
        alpha = int(255 * self._intro.value)

        theme.draw_text(surface, APP_NAME.upper(), (cx, int(h * 0.16)),
                        theme.display_font(56, bold=True), theme.GOLD_BRIGHT,
                        anchor="center", alpha=alpha)
        theme.gold_gradient_rule(surface, (cx, int(h * 0.16) + 42), 280)

        user = self.app.backend.session.user
        if user:
            tag = f"Signed in as  {user.username}"
            if user.is_guest:
                tag += "  ·  temp account"
            theme.draw_text(surface, tag, (cx, int(h * 0.16) + 66),
                            theme.body_font(16), theme.TEXT_DIM,
                            anchor="center", alpha=alpha)

        for widget in self.widgets:
            widget.draw(surface)

        # server connection status (bottom-left)
        net = self.app.backend.net
        state_name = getattr(getattr(net, "state", None), "name", "DISCONNECTED")
        if state_name == "CONNECTED":
            bits = ["Online"]
            online = getattr(net, "online_count", None)
            if online:
                bits.append(f"{online} in the aether")
            latency = getattr(net, "latency_ms", None)
            if latency is not None:
                bits.append(f"{latency} ms")
            status, color = "  ·  ".join(bits), theme.SUCCESS
        elif state_name in ("CONNECTING", "RECONNECTING"):
            dots = "." * (1 + int(self._time * 2) % 3)
            status, color = f"Connecting to server{dots}", theme.TEXT_DIM
        else:
            status, color = "Offline — practice only", theme.TEXT_FAINT
        theme.draw_text(surface, status, (24, h - 18), theme.body_font(14),
                        color, anchor="bottomleft")

        if self._toast_timer > 0 and self.toast:
            fade = min(1.0, self._toast_timer / 0.4)
            box = pygame.Rect(0, 0, min(w - 80, 560), 44)
            box.midbottom = (cx, h - 40)
            veil = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(230 * fade)),
                             veil.get_rect(), border_radius=10)
            pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                             veil.get_rect(), width=1, border_radius=10)
            surface.blit(veil, box.topleft)
            theme.draw_text(surface, self.toast, box.center, theme.body_font(15),
                            theme.TEXT, anchor="center", alpha=int(255 * fade))
