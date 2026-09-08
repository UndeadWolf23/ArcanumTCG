"""Scene system: every screen (login, home, settings, match...) is a Scene.

The SceneManager owns a stack of scenes so overlays (settings over the home
screen, pause over a match) are pushes rather than replacements, and handles
smooth fade transitions between scenes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import pygame

if TYPE_CHECKING:
    from arcanum.core.app import App


class Scene:
    """Base class for all screens."""

    def __init__(self, app: "App") -> None:
        self.app = app

    # -- lifecycle ----------------------------------------------------------
    def on_enter(self, **kwargs) -> None:
        """Called when the scene becomes the active (top) scene."""

    def on_exit(self) -> None:
        """Called when the scene is removed or covered."""

    def on_resize(self, size: tuple[int, int]) -> None:
        """Called when the window size changes; rebuild layout here."""

    # -- frame hooks ---------------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None: ...
    def update(self, dt: float) -> None: ...
    def draw(self, surface: pygame.Surface) -> None: ...


class SceneManager:
    FADE_TIME = 0.25  # seconds for each half of a cross-fade

    def __init__(self, app: "App") -> None:
        self.app = app
        self._stack: list[Scene] = []
        # transition state
        self._pending: Optional[tuple[str, Scene | None, dict]] = None
        self._fade = 0.0          # 0 = clear, 1 = fully black
        self._fading_out = False

    # -- public api ----------------------------------------------------------
    @property
    def current(self) -> Optional[Scene]:
        return self._stack[-1] if self._stack else None

    def switch(self, scene: Scene, transition: bool = True, **kwargs) -> None:
        """Replace the whole stack with `scene`."""
        self._request("switch", scene, transition, kwargs)

    def push(self, scene: Scene, transition: bool = True, **kwargs) -> None:
        """Overlay `scene` on top of the current one."""
        self._request("push", scene, transition, kwargs)

    def pop(self, transition: bool = True, **kwargs) -> None:
        """Remove the top scene, revealing the one beneath."""
        self._request("pop", None, transition, kwargs)

    def _request(self, op: str, scene: Scene | None, transition: bool, kwargs: dict) -> None:
        if transition and self._stack:
            self._pending = (op, scene, kwargs)
            self._fading_out = True
        else:
            self._apply(op, scene, kwargs)

    def _apply(self, op: str, scene: Scene | None, kwargs: dict) -> None:
        if op == "switch":
            while self._stack:
                self._stack.pop().on_exit()
            assert scene is not None
            self._stack.append(scene)
            scene.on_resize(self.app.screen.get_size())
            scene.on_enter(**kwargs)
        elif op == "push":
            assert scene is not None
            self._stack.append(scene)
            scene.on_resize(self.app.screen.get_size())
            scene.on_enter(**kwargs)
        elif op == "pop" and self._stack:
            self._stack.pop().on_exit()
            if self.current:
                self.current.on_resize(self.app.screen.get_size())
                self.current.on_enter(**kwargs)

    # -- frame hooks ----------------------------------------------------------
    def handle_event(self, event: pygame.event.Event) -> None:
        if self._pending is None and self.current:
            self.current.handle_event(event)

    def update(self, dt: float) -> None:
        # drive transition fade
        if self._fading_out:
            self._fade = min(1.0, self._fade + dt / self.FADE_TIME)
            if self._fade >= 1.0:
                op, scene, kwargs = self._pending  # type: ignore[misc]
                self._pending = None
                self._fading_out = False
                self._apply(op, scene, kwargs)
        elif self._fade > 0.0:
            self._fade = max(0.0, self._fade - dt / self.FADE_TIME)

        if self.current:
            self.current.update(dt)

    def notify_resize(self, size: tuple[int, int]) -> None:
        for scene in self._stack:
            scene.on_resize(size)

    def draw(self, surface: pygame.Surface) -> None:
        if self.current:
            self.current.draw(surface)
        if self._fade > 0.0:
            veil = pygame.Surface(surface.get_size())
            veil.fill((4, 7, 18))
            veil.set_alpha(int(self._fade * 255))
            surface.blit(veil, (0, 0))
