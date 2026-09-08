"""Reusable UI widgets, themed navy & gold.

Interaction model (web-like):
* Hover is polled from the live mouse position every frame — never inferred
  from motion events, which can be missed around display-mode switches.
* The pointer changes contextually: hand over anything clickable, I-beam
  over text inputs (see `apply_cursor`, called by scenes each frame).
* User callbacks are wrapped so a faulty handler logs instead of crashing
  the render loop.

All widgets share the same interface (handle_event / update / draw) and are
positioned by `rect`, so scenes can rebuild layouts freely on resize.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional, Sequence

import pygame

from arcanum.ui import theme
from arcanum.ui.animation import approach

log = logging.getLogger(__name__)


def _safe(callback: Optional[Callable], *args) -> None:
    """Invoke a user callback; log instead of crashing on error."""
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:  # noqa: BLE001
        log.exception("Widget callback %r failed", getattr(callback, "__name__", callback))


class Widget:
    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.visible = True
        self.enabled = True

    @property
    def interactive(self) -> bool:
        return self.visible and self.enabled

    def mouse_over(self) -> bool:
        return self.interactive and self.rect.collidepoint(pygame.mouse.get_pos())

    def cursor_hint(self) -> Optional[int]:
        """System cursor to show while this widget is hovered, or None."""
        return None

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Return True if the event was consumed."""
        return False

    def update(self, dt: float) -> None: ...
    def draw(self, surface: pygame.Surface) -> None: ...


# ---------------------------------------------------------------------------
# Cursor management (call apply_cursor once per frame from the active scene)
# ---------------------------------------------------------------------------
_active_cursor: int | None = None
_cursor_overrides: dict[int, "pygame.cursors.Cursor"] = {}


def set_cursor_override(kind: int, cursor: "pygame.cursors.Cursor") -> None:
    """Replace a system cursor (e.g. SYSTEM_CURSOR_ARROW) with a custom one."""
    _cursor_overrides[kind] = cursor
    invalidate_cursor()


def invalidate_cursor() -> None:
    """Force the next apply_cursor to re-set the OS cursor (e.g. after the
    display is recreated, which resets SDL's cursor to the default)."""
    global _active_cursor
    _active_cursor = None


def apply_cursor(widgets: Iterable[Widget], force_hand: bool = False) -> None:
    global _active_cursor
    cursor = pygame.SYSTEM_CURSOR_ARROW
    if force_hand:
        cursor = pygame.SYSTEM_CURSOR_HAND
    else:
        for widget in widgets:
            hint = widget.cursor_hint()
            if hint is not None:
                cursor = hint
                break
    if cursor != _active_cursor:
        try:
            pygame.mouse.set_cursor(_cursor_overrides.get(cursor, cursor))
            _active_cursor = cursor
        except pygame.error:
            pass  # platform without cursor support — cosmetic only


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------
class Button(Widget):
    """Primary (gold fill) or ghost (outlined) button."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        on_click: Callable[[], None],
        primary: bool = True,
        font_size: int = 20,
        sound_cb: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.on_click = on_click
        self.primary = primary
        self.font_size = font_size
        self.sound_cb = sound_cb
        self._hover = 0.0
        self._hovered = False
        self._pressed = False
        self._press = 0.0

    def cursor_hint(self) -> Optional[int]:
        return pygame.SYSTEM_CURSOR_HAND if self._hovered else None

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self._pressed = True
                self._press = 1.0
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            was_pressed = self._pressed
            self._pressed = False
            if was_pressed and self.rect.collidepoint(event.pos):
                _safe(self.sound_cb, "click")
                _safe(self.on_click)
                return True
        return False

    def update(self, dt: float) -> None:
        hovered = self.mouse_over()
        if hovered and not self._hovered:
            _safe(self.sound_cb, "hover")
        self._hovered = hovered
        self._hover = approach(self._hover, 1.0 if hovered else 0.0, dt, speed=14.0)
        if not self._pressed:
            self._press = approach(self._press, 0.0, dt, speed=10.0)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        r = self.rect.copy()
        if self._press > 0.05:
            r = r.inflate(-int(2 * self._press) * 2, -int(2 * self._press) * 2)

        if self.enabled:
            theme.draw_glow_rect(surface, r, theme.GOLD_GLOW, self._hover * 0.55,
                                 radius=8, spread=8)

        if self.primary:
            base = theme.GOLD if self.enabled else theme.GOLD_DIM
            fill = tuple(
                min(255, int(base[i] + (theme.GOLD_BRIGHT[i] - base[i]) * self._hover))
                for i in range(3)
            )
            pygame.draw.rect(surface, fill, r, border_radius=8)
            color = theme.TEXT_ON_GOLD
        else:
            fill = tuple(
                int(theme.NAVY[i] + (theme.NAVY_RAISED[i] - theme.NAVY[i]) * self._hover)
                for i in range(3)
            )
            pygame.draw.rect(surface, fill, r, border_radius=8)
            edge = theme.GOLD if (self._hover > 0.35 and self.enabled) else theme.NAVY_EDGE
            pygame.draw.rect(surface, edge, r, width=1, border_radius=8)
            color = theme.TEXT if self.enabled else theme.TEXT_FAINT

        font = theme.body_font(self.font_size, bold=self.primary)
        theme.draw_text(surface, self.label, r.center, font, color, anchor="center")


class LinkButton(Widget):
    """Small text-only button (e.g. 'Forgot password?')."""

    def __init__(self, pos: tuple[int, int], label: str, on_click: Callable[[], None],
                 font_size: int = 15, anchor: str = "center") -> None:
        font = theme.body_font(font_size)
        img_rect = font.render(label, True, theme.TEXT).get_rect(**{anchor: pos})
        super().__init__(img_rect.inflate(8, 6))
        self.label = label
        self.on_click = on_click
        self.font_size = font_size
        self._hovered = False
        self._hover = 0.0

    def cursor_hint(self) -> Optional[int]:
        return pygame.SYSTEM_CURSOR_HAND if self._hovered else None

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            return False
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.rect.collidepoint(event.pos):
                _safe(self.on_click)
                return True
        return False

    def update(self, dt: float) -> None:
        self._hovered = self.mouse_over()
        self._hover = approach(self._hover, 1.0 if self._hovered else 0.0, dt, speed=14.0)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        color = tuple(
            int(theme.TEXT_DIM[i] + (theme.GOLD_BRIGHT[i] - theme.TEXT_DIM[i]) * self._hover)
            for i in range(3)
        )
        font = theme.body_font(self.font_size)
        rect = theme.draw_text(surface, self.label, self.rect.center, font, color,
                               anchor="center")
        if self._hover > 0.4:
            pygame.draw.line(surface, color, rect.bottomleft, rect.bottomright)


# ---------------------------------------------------------------------------
# Text input
# ---------------------------------------------------------------------------
class TextInput(Widget):
    def __init__(
        self,
        rect: pygame.Rect,
        placeholder: str = "",
        password: bool = False,
        max_length: int = 64,
        on_submit: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(rect)
        self.placeholder = placeholder
        self.password = password
        self.max_length = max_length
        self.on_submit = on_submit
        self.text = ""
        self.focused = False
        self._focus = 0.0
        self._hovered = False
        self._caret_time = 0.0

    def cursor_hint(self) -> Optional[int]:
        return pygame.SYSTEM_CURSOR_IBEAM if self._hovered else None

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            was = self.focused
            self.focused = self.rect.collidepoint(event.pos)
            if self.focused and not was:
                self._caret_time = 0.0
            return self.focused
        if not self.focused:
            return False
        if event.type == pygame.KEYDOWN:
            self._caret_time = 0.0
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                _safe(self.on_submit, self.text)
            elif event.key == pygame.K_ESCAPE:
                self.focused = False
            elif event.key == pygame.K_TAB:
                return False  # let the scene move focus
            return True
        if event.type == pygame.TEXTINPUT and len(self.text) < self.max_length:
            self.text += event.text
            return True
        return False

    def update(self, dt: float) -> None:
        self._hovered = self.mouse_over()
        self._focus = approach(self._focus, 1.0 if self.focused else 0.0, dt, speed=14.0)
        self._caret_time += dt

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        theme.draw_glow_rect(surface, self.rect, theme.GOLD_GLOW, self._focus * 0.4,
                             radius=8, spread=7)
        fill = theme.NAVY_RAISED if (self.focused or self._hovered) else theme.NAVY
        if self.focused:
            edge = theme.GOLD
        elif self._hovered:
            edge = theme.GOLD_DIM
        else:
            edge = theme.NAVY_EDGE
        theme.draw_panel(surface, self.rect, fill=fill, border=edge, radius=8)

        font = theme.body_font(18)
        pad = 14
        shown = ("•" * len(self.text)) if self.password else self.text
        if shown:
            img = font.render(shown, True, theme.TEXT)
            # clip long text to the field, keeping the caret end visible
            clip_w = self.rect.width - pad * 2 - 4
            src = pygame.Rect(max(0, img.get_width() - clip_w), 0,
                              min(clip_w, img.get_width()), img.get_height())
            dest = (self.rect.x + pad, self.rect.centery - img.get_height() // 2)
            surface.blit(img, dest, src)
            caret_x = dest[0] + src.width + 2
        else:
            theme.draw_text(surface, self.placeholder,
                            (self.rect.x + pad, self.rect.centery),
                            font, theme.TEXT_FAINT, anchor="midleft")
            caret_x = self.rect.x + pad

        if self.focused and (self._caret_time % 1.0) < 0.55:
            pygame.draw.line(surface, theme.GOLD_BRIGHT,
                             (caret_x, self.rect.y + 11),
                             (caret_x, self.rect.bottom - 11), 1)


# ---------------------------------------------------------------------------
# Checkbox
# ---------------------------------------------------------------------------
class Checkbox(Widget):
    BOX = 20

    def __init__(self, pos: tuple[int, int], label: str, checked: bool = False,
                 on_change: Optional[Callable[[bool], None]] = None) -> None:
        font = theme.body_font(16)
        label_w = font.size(label)[0]
        super().__init__(pygame.Rect(pos[0], pos[1], self.BOX + 10 + label_w, self.BOX))
        self.label = label
        self.checked = checked
        self.on_change = on_change
        self._hover = 0.0
        self._hovered = False
        self._check = 1.0 if checked else 0.0

    def cursor_hint(self) -> Optional[int]:
        return pygame.SYSTEM_CURSOR_HAND if self._hovered else None

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            return False
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.checked = not self.checked
                _safe(self.on_change, self.checked)
                return True
        return False

    def update(self, dt: float) -> None:
        self._hovered = self.mouse_over()
        self._hover = approach(self._hover, 1.0 if self._hovered else 0.0, dt, speed=14.0)
        self._check = approach(self._check, 1.0 if self.checked else 0.0, dt, speed=18.0)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        box = pygame.Rect(self.rect.x, self.rect.y, self.BOX, self.BOX)
        edge = theme.GOLD if (self.checked or self._hover > 0.35) else theme.NAVY_EDGE
        theme.draw_panel(surface, box, fill=theme.NAVY, border=edge, radius=5)
        if self._check > 0.05:
            size = int(10 * min(1.0, self._check))
            if size > 1:
                inner = pygame.Rect(0, 0, size, size)
                inner.center = box.center
                pygame.draw.rect(surface, theme.GOLD_BRIGHT, inner, border_radius=2)
        color = theme.TEXT if self._hovered else theme.TEXT_DIM
        theme.draw_text(surface, self.label, (box.right + 10, box.centery),
                        theme.body_font(16), color, anchor="midleft")


# ---------------------------------------------------------------------------
# Slider (audio volumes)
# ---------------------------------------------------------------------------
class Slider(Widget):
    def __init__(self, rect: pygame.Rect, value: float = 0.5,
                 on_change: Optional[Callable[[float], None]] = None) -> None:
        super().__init__(rect)
        self.value = min(1.0, max(0.0, value))
        self.on_change = on_change
        self._dragging = False
        self._hover = 0.0
        self._hovered = False

    @property
    def _hit_rect(self) -> pygame.Rect:
        return self.rect.inflate(0, 18)

    def cursor_hint(self) -> Optional[int]:
        return pygame.SYSTEM_CURSOR_HAND if (self._hovered or self._dragging) else None

    def _set_from_x(self, x: int) -> None:
        t = (x - self.rect.x) / max(1, self.rect.width)
        value = min(1.0, max(0.0, t))
        if value != self.value:
            self.value = value
            _safe(self.on_change, self.value)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._hit_rect.collidepoint(event.pos):
                self._dragging = True
                self._set_from_x(event.pos[0])
                return True
        elif event.type == pygame.MOUSEMOTION and self._dragging:
            self._set_from_x(event.pos[0])
            return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._dragging = False
        return False

    def update(self, dt: float) -> None:
        self._hovered = self.interactive and self._hit_rect.collidepoint(pygame.mouse.get_pos())
        self._hover = approach(self._hover,
                               1.0 if (self._hovered or self._dragging) else 0.0,
                               dt, speed=14.0)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        track = pygame.Rect(self.rect.x, self.rect.centery - 2, self.rect.width, 4)
        pygame.draw.rect(surface, theme.NAVY_RAISED, track, border_radius=2)
        fill = track.copy()
        fill.width = max(0, int(track.width * self.value))
        pygame.draw.rect(surface, theme.GOLD, fill, border_radius=2)
        knob_x = track.x + int(track.width * self.value)
        radius = 7 + int(2 * self._hover)
        center = (knob_x, track.centery)
        theme.draw_glow_rect(
            surface,
            pygame.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2),
            theme.GOLD_GLOW, self._hover * 0.5, radius=radius, spread=6)
        pygame.draw.circle(surface, theme.GOLD_BRIGHT, center, radius)
        pygame.draw.circle(surface, theme.NAVY_DEEP, center, radius, width=1)
        # readout, web-style
        theme.draw_text(surface, f"{int(round(self.value * 100))}",
                        (self.rect.right + 14, track.centery),
                        theme.body_font(14), theme.TEXT_DIM, anchor="midleft")


# ---------------------------------------------------------------------------
# Segmented control (screen mode picker)
# ---------------------------------------------------------------------------
class SegmentedControl(Widget):
    def __init__(self, rect: pygame.Rect, options: Sequence[str], selected: int = 0,
                 on_change: Optional[Callable[[int], None]] = None) -> None:
        super().__init__(rect)
        self.options = list(options)
        self.selected = selected
        self.on_change = on_change
        self._hover_index: int | None = None
        self._marker_x = float(self._segment(selected).x)

    def _segment(self, i: int) -> pygame.Rect:
        w = self.rect.width // len(self.options)
        return pygame.Rect(self.rect.x + i * w, self.rect.y, w, self.rect.height)

    def cursor_hint(self) -> Optional[int]:
        if self._hover_index is not None and self._hover_index != self.selected:
            return pygame.SYSTEM_CURSOR_HAND
        return None

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            return False
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.rect.collidepoint(event.pos):
                for i in range(len(self.options)):
                    if self._segment(i).collidepoint(event.pos):
                        if i != self.selected:
                            self.selected = i
                            _safe(self.on_change, i)
                        return True
        return False

    def update(self, dt: float) -> None:
        self._hover_index = None
        if self.interactive:
            pos = pygame.mouse.get_pos()
            if self.rect.collidepoint(pos):
                for i in range(len(self.options)):
                    if self._segment(i).collidepoint(pos):
                        self._hover_index = i
                        break
        self._marker_x = approach(self._marker_x, float(self._segment(self.selected).x),
                                  dt, speed=18.0)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        theme.draw_panel(surface, self.rect, fill=theme.NAVY, radius=8)
        marker = self._segment(self.selected).copy()
        marker.x = int(self._marker_x)
        marker = marker.inflate(-6, -6)
        pygame.draw.rect(surface, theme.NAVY_RAISED, marker, border_radius=6)
        pygame.draw.rect(surface, theme.GOLD, marker, width=1, border_radius=6)
        for i, option in enumerate(self.options):
            seg = self._segment(i)
            selected = i == self.selected
            color = theme.GOLD_BRIGHT if selected else (
                theme.TEXT if i == self._hover_index else theme.TEXT_DIM)
            theme.draw_text(surface, option, seg.center,
                            theme.body_font(16, bold=selected), color, anchor="center")


# ---------------------------------------------------------------------------
# Dropdown (resolution / colorblind pickers)
# ---------------------------------------------------------------------------
class Dropdown(Widget):
    ITEM_H = 38

    def __init__(self, rect: pygame.Rect, options: Sequence[str], selected: int = 0,
                 on_change: Optional[Callable[[int], None]] = None) -> None:
        super().__init__(rect)
        self.options = list(options)
        self.selected = selected
        self.on_change = on_change
        self.open = False
        self._hover_item: int | None = None
        self._hovered = False
        self._hover = 0.0

    @property
    def _list_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.x, self.rect.bottom + 4,
                           self.rect.width, self.ITEM_H * len(self.options))

    def cursor_hint(self) -> Optional[int]:
        if self._hovered or (self.open and self._hover_item is not None):
            return pygame.SYSTEM_CURSOR_HAND
        return None

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.interactive:
            if self.open:
                self.open = False
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.open = not self.open
                return True
            if self.open:
                if self._list_rect.collidepoint(event.pos):
                    index = (event.pos[1] - self._list_rect.y) // self.ITEM_H
                    if 0 <= index < len(self.options):
                        changed = index != self.selected
                        self.selected = index
                        self.open = False
                        if changed:
                            _safe(self.on_change, index)
                    return True
                self.open = False   # click-away closes, like a web select
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and self.open:
            self.open = False
            return True
        return False

    def update(self, dt: float) -> None:
        pos = pygame.mouse.get_pos()
        self._hovered = self.interactive and self.rect.collidepoint(pos)
        self._hover_item = None
        if self.open and self._list_rect.collidepoint(pos):
            index = (pos[1] - self._list_rect.y) // self.ITEM_H
            if 0 <= index < len(self.options):
                self._hover_item = index
        self._hover = approach(self._hover, 1.0 if (self._hovered or self.open) else 0.0,
                               dt, speed=14.0)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        if self.enabled:
            edge = theme.GOLD if (self.open or self._hover > 0.35) else theme.NAVY_EDGE
            text_color = theme.TEXT
        else:
            edge, text_color = theme.NAVY_EDGE, theme.TEXT_FAINT
        theme.draw_panel(surface, self.rect, fill=theme.NAVY, border=edge, radius=8)
        theme.draw_text(surface, self.options[self.selected],
                        (self.rect.x + 14, self.rect.centery),
                        theme.body_font(17), text_color, anchor="midleft")
        # chevron
        cx, cy = self.rect.right - 22, self.rect.centery
        direction = -1 if self.open else 1
        chevron = theme.GOLD if self.enabled else theme.GOLD_DIM
        pygame.draw.polygon(surface, chevron, [
            (cx - 6, cy - 3 * direction), (cx + 6, cy - 3 * direction),
            (cx, cy + 4 * direction)])

    def draw_overlay(self, surface: pygame.Surface) -> None:
        """Open list is drawn last by the scene so it sits above other widgets."""
        if not (self.visible and self.open):
            return
        lr = self._list_rect
        theme.draw_glow_rect(surface, lr, (0, 0, 0), 0.7, radius=8, spread=10)
        theme.draw_panel(surface, lr, fill=theme.NAVY_RAISED, border=theme.GOLD_DIM, radius=8)
        for i, option in enumerate(self.options):
            item = pygame.Rect(lr.x, lr.y + i * self.ITEM_H, lr.width, self.ITEM_H)
            if i == self._hover_item:
                pygame.draw.rect(surface, theme.NAVY_EDGE, item.inflate(-6, -4),
                                 border_radius=6)
            color = theme.GOLD_BRIGHT if i == self.selected else theme.TEXT
            theme.draw_text(surface, option, (item.x + 14, item.centery),
                            theme.body_font(16), color, anchor="midleft")
