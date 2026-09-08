"""Login scene.

Modes: SIGN_IN, SIGN_UP, FORGOT. Auth calls run on worker threads; results
land in `self._pending_result` and are applied on the main thread in update().
On launch the App tries the saved remember-me token before showing this scene.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import pygame

from arcanum.core.constants import APP_NAME, APP_VERSION, IMAGES_DIR, ROOT_DIR
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.services.auth import AuthResult, AuthService
from arcanum.ui import theme
from arcanum.ui.animation import Tween
from arcanum.ui.effects import TextGleam
from arcanum.ui.widgets import Button, Checkbox, LinkButton, TextInput, apply_cursor

log = logging.getLogger(__name__)

SIGN_IN, SIGN_UP, FORGOT = "sign_in", "sign_up", "forgot"

# Placeholder key art. Searched next to main.py first, then assets/images/.
BACKGROUND_CANDIDATES = (ROOT_DIR / "background.jpg", IMAGES_DIR / "background.jpg")


def _load_background() -> pygame.Surface | None:
    for path in BACKGROUND_CANDIDATES:
        if path.is_file():
            try:
                return pygame.image.load(str(path)).convert()
            except pygame.error as exc:
                log.warning("Could not load %s: %s", path, exc)
    log.info("No background.jpg found; using procedural backdrop.")
    return None


class LoginScene(Scene):
    PANEL_W, FIELD_H = 420, 48

    def on_enter(self, **kwargs) -> None:
        self.app.background.set_image(_load_background())
        self.mode = SIGN_IN
        self.busy = False
        self.error = ""
        self.notice = kwargs.get("notice", "")
        self._pending_result: Optional[AuthResult] = None
        self._result_lock = threading.Lock()
        self._remember = False
        self._time = 0.0
        self._intro = Tween(0.0, 1.0, 0.6, delay=0.1)
        self._gleam = TextGleam(APP_NAME.upper(), theme.display_font(64, bold=True))
        self._build()

    def on_exit(self) -> None:
        self.app.background.set_image(None)

    def on_resize(self, size: tuple[int, int]) -> None:
        if hasattr(self, "mode"):
            self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        cx = w // 2
        panel_h = {SIGN_IN: 470, SIGN_UP: 520, FORGOT: 330}[self.mode]
        self.panel = pygame.Rect(0, 0, self.PANEL_W, panel_h)
        self.panel.center = (cx, int(h * 0.56))
        px, py = self.panel.x + 40, self.panel.y + 36
        fw = self.PANEL_W - 80
        ui = self.app.audio.ui_sound

        self.inputs: list[TextInput] = []
        self.widgets: list = []

        # exit button — top-right corner of the screen, closes the app
        self.widgets.append(Button(pygame.Rect(w - 68, 24, 44, 44), "×",
                                   self.app.quit, primary=False, font_size=24,
                                   sound_cb=ui))

        def add_input(y: int, placeholder: str, password: bool = False) -> TextInput:
            box = TextInput(pygame.Rect(px, y, fw, self.FIELD_H), placeholder,
                            password=password, on_submit=lambda _t: self._primary_action())
            self.inputs.append(box)
            self.widgets.append(box)
            return box

        if self.mode == SIGN_IN:
            self.in_identifier = add_input(py + 40, "Username or email")
            self.in_password = add_input(py + 104, "Password", password=True)
            self.chk_remember = Checkbox((px, py + 170), "Remember me", self._remember,
                                         on_change=self._set_remember)
            self.widgets.append(self.chk_remember)
            self.widgets.append(LinkButton((px + fw, py + 181), "Forgot password?",
                                           lambda: self._set_mode(FORGOT), anchor="midright"))
            self.btn_primary = Button(pygame.Rect(px, py + 214, fw, 50), "Sign In",
                                      self._primary_action, sound_cb=ui)
            self.widgets.append(self.btn_primary)
            self.widgets.append(LinkButton((cx, py + 296), "New here?  Create an account",
                                           lambda: self._set_mode(SIGN_UP), font_size=16))
            self.widgets.append(Button(pygame.Rect(px, py + 330, fw, 44),
                                       "Bypass Login  (dev)", self._bypass,
                                       primary=False, font_size=16, sound_cb=ui))
        elif self.mode == SIGN_UP:
            self.in_username = add_input(py + 40, "Username")
            self.in_email = add_input(py + 104, "Email")
            self.in_password = add_input(py + 168, "Password  (8+ characters)", password=True)
            self.in_confirm = add_input(py + 232, "Confirm password", password=True)
            self.chk_remember = Checkbox((px, py + 298), "Remember me", self._remember,
                                         on_change=self._set_remember)
            self.widgets.append(self.chk_remember)
            self.btn_primary = Button(pygame.Rect(px, py + 340, fw, 50), "Create Account",
                                      self._primary_action, sound_cb=ui)
            self.widgets.append(self.btn_primary)
            self.widgets.append(LinkButton((cx, py + 420), "Already have an account?  Sign in",
                                           lambda: self._set_mode(SIGN_IN), font_size=16))
        else:  # FORGOT
            self.in_email = add_input(py + 64, "Account email")
            self.btn_primary = Button(pygame.Rect(px, py + 130, fw, 50), "Send Reset Link",
                                      self._primary_action, sound_cb=ui)
            self.widgets.append(self.btn_primary)
            self.widgets.append(LinkButton((cx, py + 210), "Back to sign in",
                                           lambda: self._set_mode(SIGN_IN), font_size=16))

        if self.inputs:
            self.inputs[0].focused = True

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.error = ""
        self.notice = ""
        self._build()

    def _set_remember(self, value: bool) -> None:
        self._remember = value

    # ------------------------------------------------------------ actions
    def _primary_action(self) -> None:
        if self.busy:
            return
        self.error, self.notice = "", ""
        auth = self.app.backend.auth
        if self.mode == SIGN_IN:
            identifier = self.in_identifier.text.strip()
            password = self.in_password.text
            if not identifier or not password:
                self.error = "Enter your username and password."
                return
            self._start_busy()
            auth.sign_in(identifier, password, self._on_result)
        elif self.mode == SIGN_UP:
            if self.in_password.text != self.in_confirm.text:
                self.error = "Passwords do not match."
                return
            self._start_busy()
            auth.sign_up(self.in_username.text, self.in_email.text,
                         self.in_password.text, self._on_result)
        else:
            self._start_busy()
            auth.request_password_reset(self.in_email.text, self._on_result)

    def _bypass(self) -> None:
        """DEV ONLY — skip auth with a temp account. Remove before release."""
        user = AuthService.guest_user()
        self.app.backend.session.begin(user)
        self.app.bus.publish(Events.AUTH_LOGIN_SUCCESS, user=user)
        self.app.goto_home()

    def _start_busy(self) -> None:
        self.busy = True
        self.btn_primary.enabled = False

    def _on_result(self, result: AuthResult) -> None:
        """Called from the auth worker thread — just stash the result."""
        with self._result_lock:
            self._pending_result = result

    def _apply_result(self, result: AuthResult) -> None:
        self.busy = False
        self.btn_primary.enabled = True
        if self.mode == FORGOT:
            if result.ok:
                self.notice = "If that email has an account, a reset link is on its way."
            else:
                self.error = result.error
            return
        if result.ok and result.user:
            self.app.backend.session.begin(result.user, result.remember_token, self._remember)
            self.app.bus.publish(Events.AUTH_LOGIN_SUCCESS, user=result.user)
            self.app.goto_home()
        else:
            self.error = result.error or "Something went wrong. Try again."
            self.app.bus.publish(Events.AUTH_LOGIN_FAILED, error=self.error)

    # ------------------------------------------------------------- frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB and self.inputs:
            focused = next((i for i, box in enumerate(self.inputs) if box.focused), -1)
            step = -1 if (event.mod & pygame.KMOD_SHIFT) else 1
            for box in self.inputs:
                box.focused = False
            self.inputs[(focused + step) % len(self.inputs)].focused = True
            return
        for widget in self.widgets:
            if widget.handle_event(event):
                return

    def update(self, dt: float) -> None:
        self._time += dt
        self._intro.update(dt)
        self._gleam.update(dt)
        with self._result_lock:
            result, self._pending_result = self._pending_result, None
        if result is not None:
            self._apply_result(result)
        for widget in self.widgets:
            widget.update(dt)
        apply_cursor(self.widgets)

    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w = surface.get_width()
        cx = w // 2
        alpha = int(255 * self._intro.value)
        rise = int((1.0 - self._intro.value) * 24)

        # Title block — gleam sweeps across the letters every few seconds
        title_y = self.panel.y - 96 + rise
        self._gleam.draw(
            surface, (cx, title_y), alpha=alpha,
            animate=not self.app.settings.accessibility.reduced_motion)
        theme.gold_gradient_rule(surface, (cx, title_y + 46), 320)
        theme.draw_text(surface, "A collectible card battler", (cx, title_y + 66),
                        theme.body_font(16), theme.TEXT_DIM, anchor="center", alpha=alpha)

        # Panel
        theme.draw_glow_rect(surface, self.panel, theme.GOLD, 0.3 * self._intro.value,
                             radius=14, spread=14)
        theme.draw_panel(surface, self.panel, fill=theme.NAVY, border=theme.GOLD_DIM,
                         radius=14)

        heading = {SIGN_IN: "Welcome back", SIGN_UP: "Create your account",
                   FORGOT: "Reset password"}[self.mode]
        theme.draw_text(surface, heading, (cx, self.panel.y + 30),
                        theme.display_font(26), theme.TEXT, anchor="center")

        for widget in self.widgets:
            widget.draw(surface)

        # status line
        status_y = self.panel.bottom - 24
        if self.busy:
            dots = "." * (int(self._time * 3) % 4)
            theme.draw_text(surface, f"Working{dots}", (cx, status_y),
                            theme.body_font(15), theme.TEXT_DIM, anchor="center")
        elif self.error:
            theme.draw_text(surface, self.error, (cx, status_y),
                            theme.body_font(15), theme.DANGER, anchor="center")
        elif self.notice:
            theme.draw_text(surface, self.notice, (cx, status_y),
                            theme.body_font(15), theme.SUCCESS, anchor="center")

        theme.draw_text(surface, f"v{APP_VERSION}  ·  offline development build",
                        (w - 16, surface.get_height() - 14),
                        theme.body_font(13), theme.TEXT_FAINT, anchor="bottomright")
