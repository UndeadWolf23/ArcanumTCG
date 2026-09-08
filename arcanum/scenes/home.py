"""Home scene — the main menu hub after login.

Play / Collection / Store are placeholders that will become their own scenes;
the layout, transitions, and session plumbing they need already work.
"""
from __future__ import annotations

import pygame

from arcanum.core.constants import APP_NAME
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.ui import theme
from arcanum.ui.animation import Tween
from arcanum.ui.widgets import Button, apply_cursor


class HomeScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self._intro = Tween(0.0, 1.0, 0.5)
        self.toast = ""
        self._toast_timer = 0.0
        self._build()

    def on_resize(self, size: tuple[int, int]) -> None:
        if hasattr(self, "_time"):
            self._build()

    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        ui = self.app.audio.ui_sound
        btn_w, btn_h, gap = 340, 62, 18
        x = int(w * 0.5) - btn_w // 2
        y = int(h * 0.42)
        entries = [
            ("Play", self._play, True),
            ("Collection", lambda: self._todo("Collection"), False),
            ("Store", lambda: self._todo("Store"), False),
            ("Settings", self._settings, False),
            ("Log Out", self._logout, False),
        ]
        self.widgets = []
        for i, (label, action, primary) in enumerate(entries):
            rect = pygame.Rect(x, y + i * (btn_h + gap), btn_w, btn_h)
            self.widgets.append(Button(rect, label, action, primary=primary, sound_cb=ui))

    # -- actions ---------------------------------------------------------
    def _play(self) -> None:
        # Dev-test match against the scripted opponent. Real matchmaking
        # replaces this via backend.matchmaking once the server exists.
        from arcanum.scenes.match import MatchScene
        self.app.scenes.switch(MatchScene(self.app))

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

        theme.draw_text(surface, APP_NAME.upper(), (cx, int(h * 0.18)),
                        theme.display_font(56, bold=True), theme.GOLD_BRIGHT,
                        anchor="center", alpha=alpha)
        theme.gold_gradient_rule(surface, (cx, int(h * 0.18) + 42), 280)

        user = self.app.backend.session.user
        if user:
            tag = f"Signed in as  {user.username}" + ("  ·  temp account" if user.is_guest else "")
            theme.draw_text(surface, tag, (cx, int(h * 0.18) + 66),
                            theme.body_font(16), theme.TEXT_DIM, anchor="center", alpha=alpha)

        for widget in self.widgets:
            widget.draw(surface)

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
