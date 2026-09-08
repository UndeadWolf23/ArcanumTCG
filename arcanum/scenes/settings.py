"""Settings scene — sidebar submenus: Graphics, Audio, Accessibility.

Graphics changes are staged and take effect on Apply (so a bad resolution
never strands the player mid-change); audio and accessibility changes are
live and everything saves on exit. Pushed as an overlay, so Back returns to
whatever was underneath. Exit Game closes the app cleanly.
"""
from __future__ import annotations

import pygame

from arcanum.core.config import COLORBLIND_MODES
from arcanum.core.constants import RESOLUTION_PRESETS, SCREEN_MODES
from arcanum.core.scene import Scene
from arcanum.ui import theme
from arcanum.ui.animation import approach
from arcanum.ui.widgets import (Button, Checkbox, Dropdown, SegmentedControl,
                                 Slider, apply_cursor)

GRAPHICS, AUDIO, ACCESSIBILITY = "Graphics", "Audio", "Accessibility"
SUBMENUS = (GRAPHICS, AUDIO, ACCESSIBILITY)
MODE_LABELS = {"windowed": "Windowed", "borderless": "Borderless", "fullscreen": "Fullscreen"}
COLORBLIND_LABELS = {"off": "Off", "deuteranopia": "Deuteranopia",
                     "protanopia": "Protanopia", "tritanopia": "Tritanopia"}


class SettingsScene(Scene):
    PANEL_W, PANEL_H = 780, 560
    NAV_W = 190

    def on_enter(self, **kwargs) -> None:
        self.submenu = GRAPHICS
        gfx = self.app.settings.graphics
        self._staged_mode = gfx.screen_mode
        self._staged_resolution = self.app.current_resolution()
        self._nav_hover: int | None = None
        self._nav_glow = [0.0] * len(SUBMENUS)
        self._build()

    def on_exit(self) -> None:
        self.app.settings.save()

    def on_resize(self, size: tuple[int, int]) -> None:
        if hasattr(self, "submenu"):
            self._staged_resolution = self.app.current_resolution()
            self._build()

    # ------------------------------------------------------------------ UI
    def _resolutions(self) -> list[tuple[int, int]]:
        native = self.app.native_resolution
        options = [r for r in RESOLUTION_PRESETS if r[0] <= native[0] and r[1] <= native[1]]
        if native not in options:
            options.append(native)
        if self._staged_resolution not in options:
            options.append(self._staged_resolution)
        return sorted(set(options))

    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        self.panel = pygame.Rect(0, 0, self.PANEL_W, self.PANEL_H)
        self.panel.center = (w // 2, h // 2)
        ui = self.app.audio.ui_sound

        # sidebar nav rects
        self.nav_rects = []
        nav_x = self.panel.x + 20
        nav_y = self.panel.y + 92
        for i in range(len(SUBMENUS)):
            self.nav_rects.append(pygame.Rect(nav_x, nav_y + i * 56, self.NAV_W - 32, 46))

        # content area
        cx = self.panel.x + self.NAV_W + 28
        cw = self.PANEL_W - self.NAV_W - 76
        body_y = self.panel.y + 96
        control_x, control_w = cx + 170, cw - 170

        self.rows: list[tuple[str, object, str]] = []  # (label, widget, submenu)

        # ---- Graphics ----------------------------------------------------
        native = self.app.native_resolution
        self.mode_picker = SegmentedControl(
            pygame.Rect(control_x, body_y, control_w, 42),
            [MODE_LABELS[m] for m in SCREEN_MODES],
            selected=list(SCREEN_MODES).index(self._staged_mode),
            on_change=self._on_mode,
        )
        self.rows.append(("Screen mode", self.mode_picker, GRAPHICS))

        resolutions = self._resolutions()
        labels = [f"{rw} × {rh}" + ("   (native)" if (rw, rh) == native else "")
                  for rw, rh in resolutions]
        self._resolution_options = resolutions
        self.res_dropdown = Dropdown(
            pygame.Rect(control_x, body_y + 64, control_w, 42),
            labels, selected=resolutions.index(self._staged_resolution),
            on_change=self._on_resolution)
        self.rows.append(("Resolution", self.res_dropdown, GRAPHICS))

        self.btn_apply = Button(pygame.Rect(cx + cw - 170, body_y + 150, 170, 46),
                                "Apply", self._apply_graphics, sound_cb=ui)
        self.rows.append(("", self.btn_apply, GRAPHICS))

        # ---- Audio ---------------------------------------------------------
        audio = self.app.settings.audio
        specs = [("Master", "master_volume"), ("Music", "music_volume"),
                 ("Effects", "sfx_volume"), ("Interface", "ui_volume")]
        for i, (label, attr) in enumerate(specs):
            slider = Slider(pygame.Rect(control_x, body_y + i * 58 + 12, control_w, 12),
                            value=getattr(audio, attr),
                            on_change=lambda v, a=attr: self._on_volume(a, v))
            self.rows.append((label, slider, AUDIO))
        self.chk_mute = Checkbox((cx, body_y + len(specs) * 58 + 16), "Mute all audio",
                                 checked=audio.muted, on_change=self._on_mute)
        self.rows.append(("", self.chk_mute, AUDIO))

        # ---- Accessibility -------------------------------------------------
        acc = self.app.settings.accessibility
        self.chk_reduced = Checkbox((cx, body_y + 8), "Reduce motion",
                                    checked=acc.reduced_motion, on_change=self._on_reduced_motion)
        self.rows.append(("", self.chk_reduced, ACCESSIBILITY))
        self.chk_shake = Checkbox((cx, body_y + 56), "Screen shake",
                                  checked=acc.screen_shake, on_change=self._on_screen_shake)
        self.rows.append(("", self.chk_shake, ACCESSIBILITY))
        self.cb_dropdown = Dropdown(
            pygame.Rect(control_x, body_y + 112, control_w, 42),
            [COLORBLIND_LABELS[m] for m in COLORBLIND_MODES],
            selected=COLORBLIND_MODES.index(acc.colorblind_mode),
            on_change=self._on_colorblind)
        self.rows.append(("Colorblind mode", self.cb_dropdown, ACCESSIBILITY))

        # ---- footer buttons ------------------------------------------------
        self.btn_back = Button(pygame.Rect(cx, self.panel.bottom - 84, 160, 46),
                               "Back", lambda: self.app.scenes.pop(),
                               primary=False, sound_cb=ui)
        self.btn_exit = Button(pygame.Rect(cx + cw - 160, self.panel.bottom - 84, 160, 46),
                               "Exit Game", self.app.quit, primary=False, sound_cb=ui)
        self.footer = [self.btn_back, self.btn_exit]
        self._sync_graphics_controls()

    def _visible_rows(self):
        return [(label, widget) for label, widget, menu in self.rows if menu == self.submenu]

    def _open_dropdowns(self):
        for _label, widget, menu in self.rows:
            if menu == self.submenu and isinstance(widget, Dropdown) and widget.open:
                yield widget

    # ------------------------------------------------------------ handlers
    def _on_mode(self, index: int) -> None:
        self._staged_mode = SCREEN_MODES[index]
        if self._staged_mode == "borderless":
            # borderless always renders at the desktop's native resolution
            self._staged_resolution = self.app.native_resolution
            self.res_dropdown.selected = self._resolution_options.index(self._staged_resolution)
        self._sync_graphics_controls()

    def _on_resolution(self, index: int) -> None:
        self._staged_resolution = self._resolution_options[index]

    def _sync_graphics_controls(self) -> None:
        self.res_dropdown.enabled = self._staged_mode != "borderless"
        dirty = (self._staged_mode != self.app.settings.graphics.screen_mode
                 or self._staged_resolution != self.app.current_resolution())
        self.btn_apply.enabled = dirty

    def _apply_graphics(self) -> None:
        self.app.apply_display(self._staged_mode, self._staged_resolution)
        self._staged_mode = self.app.settings.graphics.screen_mode
        self._staged_resolution = self.app.current_resolution()
        self.app.settings.save()
        self._build()

    def _on_volume(self, attr: str, value: float) -> None:
        setattr(self.app.settings.audio, attr, value)
        self.app.audio.apply_settings()

    def _on_mute(self, value: bool) -> None:
        self.app.settings.audio.muted = value
        self.app.audio.apply_settings()

    def _on_reduced_motion(self, value: bool) -> None:
        self.app.settings.accessibility.reduced_motion = value
        self.app.background.reduced_motion = value

    def _on_screen_shake(self, value: bool) -> None:
        self.app.settings.accessibility.screen_shake = value

    def _on_colorblind(self, index: int) -> None:
        self.app.settings.accessibility.colorblind_mode = COLORBLIND_MODES[index]

    # ------------------------------------------------------------- frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.pop()
            return
        # open dropdowns get first claim on clicks
        for dropdown in list(self._open_dropdowns()):
            if dropdown.handle_event(event):
                self._sync_graphics_controls()
                return
        # sidebar nav
        if event.type == pygame.MOUSEMOTION:
            self._nav_hover = None
            for i, rect in enumerate(self.nav_rects):
                if rect.collidepoint(event.pos):
                    self._nav_hover = i
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for i, rect in enumerate(self.nav_rects):
                if rect.collidepoint(event.pos) and SUBMENUS[i] != self.submenu:
                    self.submenu = SUBMENUS[i]
                    self.app.audio.ui_sound("click")
                    return
        for widget in self.footer:
            if widget.handle_event(event):
                return
        for _label, widget in self._visible_rows():
            if widget.handle_event(event):
                self._sync_graphics_controls()
                return

    def update(self, dt: float) -> None:
        for i in range(len(SUBMENUS)):
            target = 1.0 if (i == self._nav_hover or SUBMENUS[i] == self.submenu) else 0.0
            self._nav_glow[i] = approach(self._nav_glow[i], target, dt)
        for widget in self.footer:
            widget.update(dt)
        for _label, widget in self._visible_rows():
            widget.update(dt)
        nav_hand = (self._nav_hover is not None
                    and SUBMENUS[self._nav_hover] != self.submenu)
        widgets = self.footer + [w for _l, w in self._visible_rows()]
        apply_cursor(widgets, force_hand=nav_hand)

    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        theme.draw_glow_rect(surface, self.panel, theme.GOLD, 0.22, radius=14, spread=18)
        theme.draw_panel(surface, self.panel, fill=theme.NAVY, border=theme.GOLD_DIM, radius=14)

        theme.draw_text(surface, "Settings", (self.panel.x + 36, self.panel.y + 44),
                        theme.display_font(30), theme.GOLD_BRIGHT, anchor="midleft")

        # sidebar
        divider_x = self.panel.x + self.NAV_W
        pygame.draw.line(surface, theme.NAVY_EDGE,
                         (divider_x, self.panel.y + 84), (divider_x, self.panel.bottom - 24))
        for i, name in enumerate(SUBMENUS):
            rect = self.nav_rects[i]
            active = name == self.submenu
            glow = self._nav_glow[i]
            if active or glow > 0.05:
                fill = tuple(int(theme.NAVY[c] + (theme.NAVY_RAISED[c] - theme.NAVY[c]) * glow)
                             for c in range(3))
                pygame.draw.rect(surface, fill, rect, border_radius=8)
            if active:
                pygame.draw.rect(surface, theme.GOLD_DIM, rect, width=1, border_radius=8)
                marker = pygame.Rect(rect.x - 12, rect.y + 10, 4, rect.height - 20)
                pygame.draw.rect(surface, theme.GOLD_BRIGHT, marker, border_radius=2)
            color = theme.GOLD_BRIGHT if active else (
                theme.TEXT if glow > 0.3 else theme.TEXT_DIM)
            theme.draw_text(surface, name, (rect.x + 16, rect.centery),
                            theme.body_font(17, bold=active), color, anchor="midleft")

        # content rows
        label_x = self.panel.x + self.NAV_W + 28
        for label, widget in self._visible_rows():
            if label:
                theme.draw_text(surface, label, (label_x, widget.rect.centery),
                                theme.body_font(17), theme.TEXT_DIM, anchor="midleft")
            widget.draw(surface)

        # per-submenu hint text
        hint = ""
        if self.submenu == GRAPHICS:
            hint = ("Borderless uses your desktop resolution."
                    if self._staged_mode == "borderless"
                    else "Changes take effect when you press Apply.")
        elif self.submenu == ACCESSIBILITY:
            hint = "Colorblind mode and screen shake will apply to match visuals."
        if hint:
            theme.draw_text(surface, hint, (label_x, self.panel.bottom - 118),
                            theme.body_font(14), theme.TEXT_FAINT, anchor="midleft")

        for widget in self.footer:
            widget.draw(surface)

        # open dropdown lists draw last, above everything
        for dropdown in self._open_dropdowns():
            dropdown.draw_overlay(surface)
