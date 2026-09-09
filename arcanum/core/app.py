"""Application core: owns the window, main loop, services, and scene stack.

Boot flow:
    1. Read settings; open the display (default: borderless at native res).
    2. Build the backend (auth/net/session) and audio.
    3. If a remember-me token is saved, try silent sign-in -> Home.
       Otherwise -> Login.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import pygame

from arcanum.core.config import Settings
from arcanum.core.constants import APP_NAME, IMAGES_DIR, ROOT_DIR, TARGET_FPS
from arcanum.core.events import EventBus, Events
from arcanum.core.scene import SceneManager
from arcanum.services.auth import AuthResult
from arcanum.services.backend import Backend
from arcanum.audio.audio import AudioManager
from arcanum.ui import widgets as ui_widgets
from arcanum.ui.background import Background

log = logging.getLogger(__name__)

# Custom pointer art. Searched next to main.py first, then assets/images/.
CURSOR_CANDIDATES = (IMAGES_DIR / "cursor.png", ROOT_DIR / "cursor.png")
CURSOR_HEIGHT = 36  # px; source art is scaled down to this


class App:
    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption(APP_NAME)

        self.settings = Settings.load()
        self.native_resolution = self._detect_native_resolution()
        self.screen = self._open_display(
            self.settings.graphics.screen_mode,
            self.settings.graphics.resolution or self.native_resolution,
        )

        self.clock = pygame.time.Clock()
        self.bus = EventBus()
        self.backend = Backend.create(self.bus)
        self.audio = AudioManager(self.settings.audio)
        self.background = Background(self.screen.get_size())
        self.background.reduced_motion = self.settings.accessibility.reduced_motion
        self._install_custom_cursor()
        self.scenes = SceneManager(self)
        self.running = False

        # silent sign-in state (worker thread -> main thread)
        self._silent_result: Optional[AuthResult] = None
        self._silent_lock = threading.Lock()

        self.bus.subscribe(Events.APP_QUIT, self.quit)
        self.bus.subscribe(Events.AUTH_LOGIN_SUCCESS, self._on_login)
        self.bus.subscribe(Events.AUTH_LOGOUT, self._on_logout)

    # ------------------------------------------------------------ display
    @staticmethod
    def _detect_native_resolution() -> tuple[int, int]:
        info = pygame.display.Info()
        return (info.current_w, info.current_h)

    def _open_display(self, mode: str, resolution: tuple[int, int]) -> pygame.Surface:
        """Open the display, walking a fallback chain until something works.

        Requested mode -> (fullscreen at nearest supported mode) ->
        (fullscreen at native) -> borderless native -> windowed 1280x720.
        """
        vsync = 1 if self.settings.graphics.vsync else 0
        for attempt_mode, size, flags in self._display_attempts(mode, resolution):
            try:
                screen = self._set_mode(size, flags, vsync)
            except pygame.error as exc:
                log.warning("Display open failed for %s %s (%s); trying next fallback.",
                            attempt_mode, size, exc)
                continue
            self.settings.graphics.screen_mode = attempt_mode
            self.settings.graphics.resolution = screen.get_size()
            return screen
        # Last resort: let pygame pick anything at all.
        screen = pygame.display.set_mode((1280, 720))
        self.settings.graphics.screen_mode = "windowed"
        self.settings.graphics.resolution = screen.get_size()
        return screen

    def _display_attempts(
        self, mode: str, resolution: tuple[int, int]
    ) -> list[tuple[str, tuple[int, int], int]]:
        native = self.native_resolution
        attempts: list[tuple[str, tuple[int, int], int]] = []
        if mode == "fullscreen":
            snapped = self._closest_fullscreen_mode(resolution)
            attempts.append(("fullscreen", snapped, pygame.FULLSCREEN))
            if snapped != native:
                attempts.append(("fullscreen", native, pygame.FULLSCREEN))
            attempts.append(("borderless", native, pygame.NOFRAME))
        elif mode == "borderless":
            attempts.append(("borderless", native, pygame.NOFRAME))
        else:  # windowed
            size = (min(resolution[0], native[0]), min(resolution[1], native[1]))
            attempts.append(("windowed", size, 0))
        attempts.append(("windowed", (1280, 720), 0))
        return attempts

    @staticmethod
    def _closest_fullscreen_mode(resolution: tuple[int, int]) -> tuple[int, int]:
        """Snap a requested resolution to one the display actually supports."""
        try:
            modes = pygame.display.list_modes()
        except pygame.error:
            return resolution
        if modes == -1 or not modes:  # -1 means "anything goes"
            return resolution
        if resolution in modes:
            return resolution
        return min(modes, key=lambda m: abs(m[0] - resolution[0]) + abs(m[1] - resolution[1]))

    def _set_mode(self, size: tuple[int, int], flags: int, vsync: int) -> pygame.Surface:
        # pygame/SDL cannot reliably change FULLSCREEN/NOFRAME flags on a live
        # window — tearing the display down and recreating it is the dependable
        # path, and is what the mode switch in most SDL games actually does.
        if pygame.display.get_surface() is not None:
            pygame.display.quit()
            pygame.display.init()
            pygame.display.set_caption(APP_NAME)
        try:
            return pygame.display.set_mode(size, flags, vsync=vsync)
        except (pygame.error, TypeError) as exc:
            if vsync:  # some drivers/builds reject vsync for this mode; retry without
                log.debug("Retrying %s without vsync (%s)", size, exc)
                return pygame.display.set_mode(size, flags)
            raise

    def apply_display(self, mode: str, resolution: tuple[int, int]) -> None:
        """Called by the settings scene's Apply button."""
        self.screen = self._open_display(mode, resolution)
        pygame.event.clear()  # drop stale input queued across the switch
        ui_widgets.invalidate_cursor()  # display recreation resets the cursor
        size = self.screen.get_size()
        self.background.resize(size)
        self.scenes.notify_resize(size)
        self.bus.publish(Events.DISPLAY_CHANGED, size=size, mode=self.settings.graphics.screen_mode)
        log.info("Display: %s @ %dx%d", self.settings.graphics.screen_mode, *size)

    def current_resolution(self) -> tuple[int, int]:
        return self.screen.get_size()

    # ------------------------------------------------------------ cursor
    def _install_custom_cursor(self) -> None:
        """Use cursor.png as the pointer (arrow + hand states), if present.

        The hand (clickable) state gets a slightly brightened copy so hover
        still reads. The system I-beam is kept for text fields. Missing or
        unreadable art falls back to system cursors with a log line.
        """
        for path in CURSOR_CANDIDATES:
            if not path.is_file():
                continue
            try:
                image = pygame.image.load(str(path)).convert_alpha()
                scale = CURSOR_HEIGHT / image.get_height()
                size = (max(8, round(image.get_width() * scale)), CURSOR_HEIGHT)
                arrow = pygame.transform.smoothscale(image, size)
                hand = arrow.copy()
                hand.fill((30, 26, 12), special_flags=pygame.BLEND_RGB_ADD)
                # hotspot at the arrow tip (top-left of the art)
                ui_widgets.set_cursor_override(
                    pygame.SYSTEM_CURSOR_ARROW, pygame.cursors.Cursor((1, 1), arrow))
                ui_widgets.set_cursor_override(
                    pygame.SYSTEM_CURSOR_HAND, pygame.cursors.Cursor((1, 1), hand))
                ui_widgets.apply_cursor(())  # show it immediately
                log.info("Custom cursor installed from %s", path)
                return
            except (pygame.error, ValueError, ZeroDivisionError) as exc:
                log.warning("Could not load cursor %s: %s", path, exc)
        log.info("No cursor.png found; using system cursors.")

    # ------------------------------------------------------------ session/net
    def _on_login(self, user, **_kw) -> None:
        from arcanum.services import cards as card_library
        # Load whatever's on disk first (instant, works offline), then
        # block (bounded) on a real download so the library is actually in
        # sync with the database before home/deckbuilder render — otherwise
        # goto_home() below fires before the old fire-and-forget refresh()
        # thread finished, and players briefly (or, on a slow/failed
        # network check, indefinitely) see cards already removed from the
        # database because they were never dropped from the local cache.
        card_library.load_cache()
        card_library.refresh_sync()
        self.backend.refresh_deck_store()
        token = self.backend.session.saved_token() or "dev"
        try:
            self.backend.net.connect(token, name=user.username)
        except Exception:  # noqa: BLE001 - going online must never block play
            log.exception("Could not start network client")

    def _on_logout(self, **_kw) -> None:
        self.backend.refresh_deck_store()
        try:
            self.backend.net.disconnect()
        except Exception:  # noqa: BLE001
            log.exception("Network disconnect failed")

    # ------------------------------------------------------------ navigation
    def goto_login(self, notice: str = "") -> None:
        from arcanum.scenes.login import LoginScene
        self.scenes.switch(LoginScene(self), notice=notice)

    def goto_home(self) -> None:
        from arcanum.scenes.home import HomeScene
        self.scenes.switch(HomeScene(self))

    # ------------------------------------------------------------ boot
    def _boot(self) -> None:
        token = self.backend.session.saved_token()
        if token:
            log.info("Found saved session; attempting silent sign-in.")
            self.backend.auth.sign_in_with_token(token, self._on_silent_result)
            from arcanum.scenes.login import LoginScene
            self.scenes.switch(LoginScene(self), transition=False)
        else:
            self.goto_login()

    def _on_silent_result(self, result: AuthResult) -> None:
        with self._silent_lock:
            self._silent_result = result

    def _pump_silent_signin(self) -> None:
        with self._silent_lock:
            result, self._silent_result = self._silent_result, None
        if result is None:
            return
        if result.ok and result.user:
            self.backend.session.begin(result.user, result.remember_token, remember=True,
                                       access_token=result.access_token)
            self.bus.publish(Events.AUTH_LOGIN_SUCCESS, user=result.user)
            self.goto_home()
        else:
            self.backend.session.clear_saved_token()

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        self.running = True
        self._boot()
        try:
            while self.running:
                dt = min(self.clock.tick(self.settings.graphics.max_fps or TARGET_FPS)
                         / 1000.0, 0.1)

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.quit()
                    else:
                        self.scenes.handle_event(event)

                self.bus.pump()
                self._pump_silent_signin()
                self.background.update(dt)
                self.scenes.update(dt)
                self.scenes.draw(self.screen)
                pygame.display.flip()
        finally:
            # settings persist and the window closes even on a crash
            self.settings.save()
            try:
                self.backend.net.disconnect()
            except Exception:  # noqa: BLE001
                pass
            pygame.quit()

    def quit(self) -> None:
        self.running = False
