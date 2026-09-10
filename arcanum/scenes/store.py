"""The Store — buy packs with gold (server-authoritative).

Three pack products × three bundle sizes (1, 3 at 5% off, 10 at 10% off).
Bundles render as literal bundles: a fan of three, a stack of ten. Prices
shown here are display-only — the Postgres `buy_packs` function is the
single pricing authority, so a modified client can render any number it
likes and still pay full freight.

Purchases animate: the bundle flies toward your inventory chip while the
gold counter ticks down to the server-confirmed balance.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

import pygame

from arcanum.core.constants import ASSETS_DIR
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.services.net.protocol import MsgType
from arcanum.ui import theme
from arcanum.ui.widgets import Button, apply_cursor

log = logging.getLogger(__name__)

PRODUCTS = (
    ("adventure", "Adventure Pack", 500,
     "Where every collection begins."),
    ("wonder", "Wonder Pack", 800,
     "Rarer skies, brighter cards."),
    ("cosmic", "Cosmic Pack", 1500,
     "Legends live at the zenith."),
)
BUNDLES = ((1, 0), (3, 5), (10, 10))       # (qty, discount %)


def bundle_price(unit: int, qty: int, discount: int) -> int:
    return (unit * qty * (100 - discount)) // 100


class StoreScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self._toast = ""
        self._toast_timer = 0.0
        self._buy_buttons: list[tuple[pygame.Rect, str, int, int]] = []
        self._flying: list[dict] = []       # purchase animations
        self._gold_shown = float(self.app.backend.gold)
        self._pack_art: dict[str, Optional[pygame.Surface]] = {}
        self._busy_key: Optional[str] = None
        self.app.bus.subscribe(Events.NET_MESSAGE, self._on_net)
        self._build()
        # freshen the wallet whenever the store opens
        if self._connected():
            self.app.backend.net.send(MsgType.ECONOMY_GET, {})

    def on_exit(self) -> None:
        self.app.bus.unsubscribe(Events.NET_MESSAGE, self._on_net)

    def on_resize(self, size) -> None:
        if hasattr(self, "_time"):
            self._build()

    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        self.s = max(0.72, min(1.3, h / 1080))
        ui = self.app.audio.ui_sound
        self.btn_back = Button(pygame.Rect(int(24 * self.s), int(20 * self.s),
                                           int(110 * self.s), int(42 * self.s)),
                               "Back", lambda: self.app.scenes.pop(),
                               primary=False, font_size=16, sound_cb=ui)
        self.widgets = [self.btn_back]

    def _connected(self) -> bool:
        state = getattr(getattr(self.app.backend.net, "state", None),
                        "name", "")
        return state == "CONNECTED"

    def _pack_face(self, pack_id: str, size) -> Optional[pygame.Surface]:
        key = (pack_id, size)
        cache = self._pack_art
        if key in cache:
            return cache[key]
        path = Path(ASSETS_DIR) / "images" / f"pack_{pack_id}.png"
        face = None
        try:
            raw = pygame.image.load(str(path)).convert_alpha()
            face = pygame.transform.smoothscale(raw, size)
        except (pygame.error, FileNotFoundError, OSError):
            face = None
        cache[key] = face
        return face

    # --------------------------------------------------------------- network
    def _on_net(self, envelope=None, **_kw) -> None:
        if envelope is None or envelope.type != MsgType.ECONOMY_STATE.value:
            return
        self._busy_key = None
        purchase = envelope.payload.get("purchase")
        if isinstance(purchase, dict):
            if purchase.get("ok"):
                self._toastmsg(f"Purchased!  −{purchase.get('cost', 0)} gold")
            else:
                reason = purchase.get("error", "purchase failed")
                self._toastmsg("Not enough gold." if "gold" in reason
                               else f"Store: {reason}")
                self._flying.clear()

    def _toastmsg(self, message: str) -> None:
        self._toast = message
        self._toast_timer = 2.6

    def _buy(self, pack_id: str, qty: int, origin: pygame.Rect) -> None:
        if not self._connected():
            self._toastmsg("Connect to the server to shop.")
            return
        if not self.app.backend.wallet.get("enabled", False):
            self._toastmsg("The economy isn't configured on this server "
                           "yet.")
            return
        if self._busy_key:
            return
        self._busy_key = f"{pack_id}:{qty}"
        self.app.backend.net.send(MsgType.SHOP_BUY,
                                  {"pack": pack_id, "qty": qty})
        w, _h = self.app.screen.get_size()
        self._flying.append({"pack": pack_id, "qty": qty, "t": 0.0,
                             "from": origin.center,
                             "to": (w - int(150 * self.s), int(40 * self.s))})
        self.app.audio.ui_sound("confirm")

    # ----------------------------------------------------------------- frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.pop()
            return
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for rect, pack_id, qty, _price in self._buy_buttons:
                if rect.collidepoint(event.pos):
                    self._buy(pack_id, qty, rect)
                    return

    def update(self, dt: float) -> None:
        self._time += dt
        self._toast_timer = max(0.0, self._toast_timer - dt)
        target = float(self.app.backend.gold)
        if abs(self._gold_shown - target) > 0.5:
            self._gold_shown += (target - self._gold_shown) * min(
                1.0, dt * 6.0)
        else:
            self._gold_shown = target
        for fly in self._flying:
            fly["t"] += dt * 1.6
        self._flying = [f for f in self._flying if f["t"] < 1.0]
        for widget in self.widgets:
            widget.update(dt)
        hover = any(r.collidepoint(pygame.mouse.get_pos())
                    for r, *_rest in self._buy_buttons)
        apply_cursor(self.widgets, force_hand=hover)

    # -------------------------------------------------------------- drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, h = surface.get_size()
        s = self.s
        self._buy_buttons = []
        theme.draw_text(surface, "The Store", (w // 2, int(44 * s)),
                        theme.display_font(int(28 * s)), theme.GOLD_BRIGHT,
                        anchor="center")
        self.btn_back.draw(surface)
        self._draw_gold_chip(surface)

        row_h = int((h - int(180 * s)) / 3)
        for i, (pack_id, name, unit, blurb) in enumerate(PRODUCTS):
            rect = pygame.Rect(int(w * 0.06), int(120 * s) + i * row_h,
                               int(w * 0.88), row_h - int(16 * s))
            self._draw_product(surface, rect, pack_id, name, unit, blurb)
        self._draw_flying(surface)
        self._draw_toast(surface)

    def _draw_gold_chip(self, surface) -> None:
        s = self.s
        w, _h = surface.get_size()
        chip = pygame.Rect(0, 0, int(190 * s), int(44 * s))
        chip.topright = (w - int(24 * s), int(20 * s))
        theme.draw_panel(surface, chip, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=12)
        theme.aa_circle(surface, theme.GOLD,
                        (chip.x + int(22 * s), chip.centery), int(10 * s))
        theme.draw_text(surface, f"{int(round(self._gold_shown)):,}",
                        (chip.x + int(42 * s), chip.centery),
                        theme.body_font(int(17 * s), bold=True),
                        theme.GOLD_BRIGHT, anchor="midleft")
        owned = self.app.backend.packs_owned
        total = sum(owned.values())
        theme.draw_text(surface, f"{total} unopened",
                        (chip.right - int(14 * s), chip.centery),
                        theme.body_font(int(11 * s)), theme.TEXT_DIM,
                        anchor="midright")

    def _draw_bundle(self, surface, center, pack_id, qty, size) -> None:
        """1 = single pack, 3 = a fan, 10 = a deep stack with a ×10 seal."""
        face = self._pack_face(pack_id, size)
        cx, cy = center

        def blit_one(offset, angle):
            if face is None:
                rect = pygame.Rect(0, 0, *size)
                rect.center = (cx + offset[0], cy + offset[1])
                theme.draw_panel(surface, rect, fill=theme.NAVY_RAISED,
                                 border=theme.GOLD_DIM, radius=8)
                return
            img = (pygame.transform.rotozoom(face, angle, 1.0)
                   if angle else face)
            surface.blit(img, img.get_rect(center=(cx + offset[0],
                                                   cy + offset[1])))

        if qty == 1:
            blit_one((0, 0), 0)
        elif qty == 3:
            blit_one((-int(size[0] * 0.34), 4), 10)
            blit_one((int(size[0] * 0.34), 4), -10)
            blit_one((0, -4), 0)
        else:
            for layer in range(4, 0, -1):
                blit_one((layer * 5, layer * 5), 0)
            blit_one((0, 0), 0)
            seal = pygame.Rect(0, 0, int(44 * self.s), int(26 * self.s))
            seal.center = (cx + size[0] // 2 - 6, cy - size[1] // 2 + 8)
            theme.draw_panel(surface, seal, fill=theme.GOLD,
                             border=theme.GOLD, radius=13)
            theme.draw_text(surface, "×10", seal.center,
                            theme.body_font(int(13 * self.s), bold=True),
                            theme.TEXT_ON_GOLD, anchor="center")

    def _draw_product(self, surface, rect, pack_id, name, unit, blurb) -> None:
        s = self.s
        theme.draw_panel(surface, rect, fill=theme.NAVY,
                         border=theme.NAVY_EDGE, radius=14)
        pack_h = rect.height - int(36 * s)
        pack_w = int(pack_h * 0.72)
        theme.draw_text(surface, name,
                        (rect.x + int(24 * s), rect.y + int(26 * s)),
                        theme.display_font(int(19 * s)), theme.GOLD_BRIGHT,
                        anchor="midleft")
        theme.draw_text(surface, blurb,
                        (rect.x + int(24 * s), rect.y + int(52 * s)),
                        theme.body_font(int(12 * s)), theme.TEXT_DIM,
                        anchor="midleft")
        owned = self.app.backend.packs_owned.get(pack_id, 0)
        if owned:
            theme.draw_text(surface, f"You own {owned} unopened",
                            (rect.x + int(24 * s), rect.y + int(76 * s)),
                            theme.body_font(int(11 * s)), theme.SUCCESS,
                            anchor="midleft")

        cell_w = int(rect.width * 0.24)
        for j, (qty, discount) in enumerate(BUNDLES):
            cx = rect.x + int(rect.width * 0.30) + j * cell_w + cell_w // 2
            cy = rect.centery - int(14 * s)
            bob = math.sin(self._time * 1.6 + j + hash(pack_id) % 7) * 3
            bundle_size = (int(pack_w * 0.62), int(pack_h * 0.62))
            self._draw_bundle(surface, (cx, cy + bob), pack_id, qty,
                              bundle_size)
            price = bundle_price(unit, qty, discount)
            btn = pygame.Rect(0, 0, int(cell_w * 0.78), int(36 * s))
            btn.center = (cx, rect.bottom - int(28 * s))
            hover = btn.collidepoint(pygame.mouse.get_pos())
            busy = self._busy_key == f"{pack_id}:{qty}"
            label = f"{qty} for {price:,}" if not busy else "..."
            theme.draw_panel(surface, btn,
                             fill=theme.GOLD if hover else theme.NAVY_RAISED,
                             border=theme.GOLD, radius=10)
            theme.draw_text(surface, label, btn.center,
                            theme.body_font(int(13 * s), bold=True),
                            theme.TEXT_ON_GOLD if hover else theme.GOLD_BRIGHT,
                            anchor="center")
            if discount:
                theme.draw_text(surface, f"save {discount}%",
                                (cx, btn.bottom + int(11 * s)),
                                theme.body_font(int(10 * s)), theme.SUCCESS,
                                anchor="center")
            self._buy_buttons.append((btn, pack_id, qty, price))

    def _draw_flying(self, surface) -> None:
        for fly in self._flying:
            t = fly["t"]
            ease = 1 - (1 - t) ** 3
            x = fly["from"][0] + (fly["to"][0] - fly["from"][0]) * ease
            y = fly["from"][1] + (fly["to"][1] - fly["from"][1]) * ease \
                - math.sin(t * math.pi) * 90
            size = (int(46 * self.s * (1 - t * 0.5)),
                    int(64 * self.s * (1 - t * 0.5)))
            face = self._pack_face(fly["pack"], size)
            if face is not None:
                face = face.copy()
                face.set_alpha(int(255 * (1 - t * 0.6)))
                surface.blit(face, face.get_rect(center=(int(x), int(y))))
            else:
                pygame.draw.circle(surface, theme.GOLD, (int(x), int(y)),
                                   max(3, int(10 * (1 - t))))

    def _draw_toast(self, surface) -> None:
        if self._toast_timer <= 0 or not self._toast:
            return
        w, h = surface.get_size()
        fade = min(1.0, self._toast_timer / 0.4)
        box = pygame.Rect(0, 0, min(w - 80, 540), 42)
        box.midbottom = (w // 2, h - 18)
        veil = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(235 * fade)),
                         veil.get_rect(), border_radius=10)
        pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                         veil.get_rect(), width=1, border_radius=10)
        surface.blit(veil, box.topleft)
        theme.draw_text(surface, self._toast, box.center,
                        theme.body_font(15), theme.TEXT, anchor="center",
                        alpha=int(255 * fade))
