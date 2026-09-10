"""Pack opening — selection, the tear, and the reveal.

Flow: pick a pack (they float; hover shows the odds) -> the pack zooms to
center -> GRAB the foil top with the mouse and TEAR it across -> the top
flies off in a burst of sparks -> eight cards erupt from the mouth and
settle into a fan -> click each to flip it (or Reveal All), with a
rarity-colored shockwave, sparks, and — for epic and legendary — a screen
shake and lingering aura. Free while the economy is under construction.
"""
from __future__ import annotations

import logging
import math
import random
from typing import Optional

import pygame

from arcanum.core.constants import IMAGES_DIR, ROOT_DIR
from arcanum.core.scene import Scene
from arcanum.game import catalog as cat
from arcanum.game.catalog import CardDef
from arcanum.game.keywords import Rarity
from arcanum.game.packs import (PACK_SIZE, PACKS, RARITY_FX, RARITY_ORDER,
                                open_pack)
from arcanum.services import cardimages
from arcanum.services import cards as card_library
from arcanum.ui import theme
from arcanum.ui.animation import Tween, ease_out_cubic
from arcanum.ui.widgets import Button, apply_cursor

log = logging.getLogger(__name__)

SELECT, ZOOM, TEAR, BURST, REVEAL = range(5)
TEAR_STRIP = 0.14          # top fraction of the pack that tears away
TEAR_DISTANCE = 0.95       # horizontal drag (in pack widths) to finish


def self_card_lookup(scene, card_id: str) -> CardDef:
    card = cat.by_id(card_id)
    if card is not None:
        return card
    # library changed underneath us; synthesize a placeholder
    return CardDef(card_id=card_id, name=card_id, kind=cat.CATALOG[0].kind,
                   cost=0, rarity=Rarity.COMMON)


def _load_image(name: str) -> Optional[pygame.Surface]:
    for path in (IMAGES_DIR / name, ROOT_DIR / name):
        if path.is_file():
            try:
                return pygame.image.load(str(path)).convert_alpha()
            except pygame.error as exc:
                log.warning("Could not load %s: %s", path, exc)
    return None


class CardReveal:
    """One card flying out of the pack, then flippable in the fan."""

    def __init__(self, card: CardDef, start, slot, delay: float) -> None:
        self.card = card
        self.start = start
        self.slot = slot
        self.delay = delay
        self.t = -delay                 # negative = still waiting to launch
        self.flight = 0.62              # seconds of flight
        self.flipped = False
        self.flip = 0.0                 # 0 back .. 1 face
        self.flipping = False
        self.settle_bob = random.uniform(0, math.tau)

    @property
    def launched(self) -> bool:
        return self.t >= 0

    @property
    def landed(self) -> bool:
        return self.t >= self.flight

    def pos(self, time_now: float):
        if not self.launched:
            return self.start
        k = min(1.0, self.t / self.flight)
        k = ease_out_cubic(k)
        # quadratic bezier through a high apex for the "erupt" feel
        apex = ((self.start[0] + self.slot[0]) / 2,
                min(self.start[1], self.slot[1]) - 190)
        omk = 1 - k
        x = omk * omk * self.start[0] + 2 * omk * k * apex[0] + k * k * self.slot[0]
        y = omk * omk * self.start[1] + 2 * omk * k * apex[1] + k * k * self.slot[1]
        if self.landed:
            y += math.sin(time_now * 1.7 + self.settle_bob) * 3
        return (x, y)


class PacksScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self.phase = SELECT
        self.rng = random.Random()
        self.chosen = None                     # PackDef
        self.cards: list[CardReveal] = []
        self.particles: list[dict] = []
        self.rings: list[dict] = []
        self.flash = 0.0
        self.shake = 0.0
        self.tear = 0.0
        self._tearing = False
        self._tear_anchor = 0
        self._strip_fly = None                 # dict once the top rips free
        self.note = ""
        self.note_timer = 0.0
        self._zoom_tween: Optional[Tween] = None
        self._pack_drop = 0.0
        self._hover_pack = None
        self._hover_card: Optional[CardReveal] = None
        self._pinned: Optional[CardReveal] = None
        self._spotlight: Optional[dict] = None    # legendary celebration
        self._img_cache: dict = {}
        self._pack_art: dict = {}
        self._back: Optional[pygame.Surface] = None
        card_library.load_cache()
        self._build()

    def on_resize(self, size) -> None:
        if hasattr(self, "_time"):
            self._build()

    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.7, min(1.35, h / 1080))
        self.s = s
        ui = self.app.audio.ui_sound
        self.btn_back = Button(pygame.Rect(int(24 * s), int(20 * s),
                                           int(120 * s), int(42 * s)),
                               "Back", self._back_out, primary=False,
                               font_size=16, sound_cb=ui)
        self.btn_reveal_all = Button(pygame.Rect(0, 0, int(190 * s),
                                                 int(48 * s)),
                                     "Reveal All", self._reveal_all,
                                     primary=False, font_size=16, sound_cb=ui)
        self.btn_reveal_all.rect.midbottom = (w // 2, h - int(28 * s))
        self.btn_again = Button(pygame.Rect(0, 0, int(200 * s), int(50 * s)),
                                "Open Another", self._open_again,
                                font_size=17, sound_cb=ui)
        self.btn_again.rect.midbottom = (w // 2 - int(115 * s), h - int(24 * s))
        self.btn_done = Button(pygame.Rect(0, 0, int(200 * s), int(50 * s)),
                               "Choose a Pack", self._back_to_select,
                               primary=False, font_size=17, sound_cb=ui)
        self.btn_done.rect.midbottom = (w // 2 + int(115 * s), h - int(24 * s))

        # select-phase layout
        self.pack_h = int(430 * s)
        self.pack_w = int(self.pack_h * 1024 / 1536)
        gap = int(70 * s)
        total = 3 * self.pack_w + 2 * gap
        x0 = (w - total) // 2
        self.select_slots = []
        for i, pack in enumerate(PACKS):
            rect = pygame.Rect(x0 + i * (self.pack_w + gap), int(h * 0.20),
                               self.pack_w, self.pack_h)
            self.select_slots.append((pack, rect))

        # tear-phase pack placement
        self.big_h = int(h * 0.62)
        self.big_w = int(self.big_h * 1024 / 1536)
        self.big_rect = pygame.Rect(0, 0, self.big_w, self.big_h)
        self.big_rect.center = (w // 2, int(h * 0.46))

        # reveal fan slots: 2 rows x 4
        self.card_h = int(240 * s)
        self.card_w = int(self.card_h * 1065 / 1477)
        cgap = int(26 * s)
        row_w = 4 * self.card_w + 3 * cgap
        cx0 = (w - row_w) // 2 + self.card_w // 2
        self.slots = []
        for i in range(PACK_SIZE):
            row, col = divmod(i, 4)
            self.slots.append((cx0 + col * (self.card_w + cgap),
                               int(h * 0.30) + row * (self.card_h + cgap)))

    # ------------------------------------------------------------ assets
    def _pack_image(self, pack, height: int) -> Optional[pygame.Surface]:
        key = (pack.pack_id, height)
        if key not in self._pack_art:
            raw = _load_image(pack.image)
            if raw is None:
                self._pack_art[key] = None
            else:
                width = int(raw.get_width() * height / raw.get_height())
                self._pack_art[key] = pygame.transform.smoothscale(
                    raw, (width, height))
        return self._pack_art[key]

    def _card_back(self) -> pygame.Surface:
        from arcanum.ui import cardback
        image = cardback.get((self.card_w, self.card_h))
        if image is not None:
            return image
        if self._back is None:
            back = pygame.Surface((self.card_w, self.card_h), pygame.SRCALPHA)
            rect = back.get_rect()
            pygame.draw.rect(back, theme.NAVY, rect, border_radius=12)
            pygame.draw.rect(back, theme.GOLD_DIM, rect, width=3,
                             border_radius=12)
            cx, cy = rect.center
            for k in range(8):
                a = k * math.tau / 8
                tip = (cx + math.cos(a) * self.card_w * 0.26,
                       cy + math.sin(a) * self.card_w * 0.26)
                pygame.draw.line(back, theme.GOLD_DIM, (cx, cy), tip, 2)
            pygame.draw.circle(back, theme.GOLD, (cx, cy), int(9 * self.s))
            self._back = back
        return self._back

    def _card_face(self, card: CardDef, height: int):
        name = card_library.image_name(card.card_id)
        key = (card.card_id, height)
        if key in self._img_cache:
            return self._img_cache[key]
        if name:
            path = cardimages.get_path(name)
            if path is not None:
                try:
                    raw = pygame.image.load(str(path)).convert_alpha()
                    if not cardimages.plausible_card_image(raw.get_width(),
                                                           raw.get_height()):
                        log.warning("Card image %s isn't card-shaped; using "
                                    "fallback (republish to fix).", name)
                        self._img_cache[key] = self._vector_face(
                            self_card_lookup(self, key[0]), height)
                        return self._img_cache[key]
                    width = int(raw.get_width() * height / raw.get_height())
                    surface = pygame.transform.smoothscale(raw, (width, height))
                    self._img_cache[key] = surface
                    return surface
                except pygame.error:
                    pass
            return None       # still downloading: caller uses vector face
        self._img_cache[key] = self._vector_face(card, height)
        return self._img_cache[key]

    def _vector_face(self, card: CardDef, height: int) -> pygame.Surface:
        width = int(height * 1065 / 1477)
        face = pygame.Surface((width, height), pygame.SRCALPHA)
        rect = face.get_rect()
        color = RARITY_FX[card.rarity]
        pygame.draw.rect(face, theme.NAVY, rect, border_radius=12)
        pygame.draw.rect(face, color, rect, width=3, border_radius=12)
        s = height / 240
        theme.draw_text(face, str(card.cost), (int(18 * s), int(18 * s)),
                        theme.body_font(int(17 * s), bold=True),
                        (140, 190, 255), anchor="center")
        # wrapped name
        font = theme.body_font(int(13 * s), bold=True)
        words, line, y = card.name.split(), "", int(38 * s)
        for word in words + [None]:
            probe = f"{line} {word}".strip() if word else line
            if word is None or font.size(probe)[0] > width - int(16 * s):
                theme.draw_text(face, line, (rect.centerx, y), font,
                                theme.TEXT, anchor="center")
                y += font.get_linesize()
                line = word or ""
            else:
                line = probe
        theme.draw_text(face, card.rarity.value.title(),
                        (rect.centerx, rect.bottom - int(34 * s)),
                        theme.body_font(int(11 * s)), color, anchor="center")
        if card.kind.value == "creature":
            theme.draw_text(face, f"{card.attack}/{card.health}",
                            (rect.centerx, rect.bottom - int(16 * s)),
                            theme.body_font(int(15 * s), bold=True),
                            theme.GOLD_BRIGHT, anchor="center")
        return face

    # ------------------------------------------------------------ actions
    def _back_out(self) -> None:
        self.app.scenes.pop()

    def _back_to_select(self) -> None:
        self.phase = SELECT
        self.cards.clear()
        self.particles.clear()
        self.rings.clear()

    def _open_again(self) -> None:
        if self.chosen is not None:
            self._begin_tear(self.chosen)

    def _owned(self, pack_id: str) -> int:
        return int(self.app.backend.packs_owned.get(pack_id, 0))

    def _economy_on(self) -> bool:
        state = getattr(getattr(self.app.backend.net, "state", None),
                        "name", "")
        return state == "CONNECTED" and \
            bool(self.app.backend.wallet.get("enabled"))

    def _choose(self, pack) -> None:
        if self._economy_on() and self._owned(pack.pack_id) <= 0:
            self._show_note(f"No {pack.name}s owned — visit the Store.")
            return
        self.chosen = pack
        self.phase = ZOOM
        self._zoom_tween = Tween(0.0, 1.0, 0.45)
        self.app.audio.ui_sound("play")

    def _begin_tear(self, pack) -> None:
        self.chosen = pack
        self.phase = TEAR
        if self._economy_on():
            from arcanum.services.net.protocol import MsgType
            self.app.backend.pack_results.clear()
            self.app.backend.net.send(MsgType.PACK_OPEN,
                                      {"pack": self.chosen.pack_id})
        self.tear = 0.0
        self._tearing = False
        self._strip_fly = None
        self._pack_drop = 0.0
        self.cards.clear()

    def _finish_tear(self) -> None:
        self.phase = BURST
        self.flash = 1.0
        self.shake = max(self.shake, 10.0)
        self.app.audio.ui_sound("attack")
        rect = self.big_rect
        strip_h = int(rect.height * TEAR_STRIP)
        self._strip_fly = {"x": rect.x + rect.width * 0.55,
                           "y": rect.y + strip_h * 0.5,
                           "vx": 620.0, "vy": -420.0, "rot": 0.0,
                           "vrot": 260.0, "life": 1.0}
        seam_y = rect.y + strip_h
        for _ in range(70):
            self._spark((rect.x + random.uniform(0, rect.width), seam_y),
                        theme.GOLD_BRIGHT, speed=340)
        # roll the cards: the SERVER consumes the pack and rolls when the
        # economy is live (authoritative); offline practice rolls locally
        rolled = None
        if self._economy_on():
            result = self._take_pack_result()
            if result is not None and result.get("ok"):
                from arcanum.game import catalog as _cat
                rolled = [c for c in (_cat.by_id_safe(cid)
                                      for cid in result.get("cards", []))
                          if c is not None]
        if rolled is None:
            rolled = open_pack(self.chosen.pack_id, self.rng)
        mouth = (rect.centerx, seam_y + 10)
        for i, card in enumerate(rolled):
            self.cards.append(CardReveal(card, mouth, self.slots[i],
                                         delay=0.35 + i * 0.09))

    def _take_pack_result(self):
        results = self.app.backend.pack_results
        return results.pop(0) if results else None

    def _show_note(self, message: str) -> None:
        self.note = message
        self.note_timer = 2.6

    def _reveal_all(self) -> None:
        for reveal in self.cards:
            if reveal.landed and not reveal.flipped and not reveal.flipping:
                reveal.flipping = True

    def _flip(self, reveal: CardReveal) -> None:
        if reveal.landed and not reveal.flipped and not reveal.flipping:
            reveal.flipping = True
            self.app.audio.ui_sound("draw")

    def _on_flip_done(self, reveal: CardReveal) -> None:
        reveal.flipped = True
        color = RARITY_FX[reveal.card.rarity]
        x, y = reveal.slot
        tier = RARITY_ORDER.index(reveal.card.rarity)
        self.rings.append({"x": x, "y": y, "r": 8.0,
                           "max": 70 + tier * 34, "life": 1.0,
                           "color": color})
        for _ in range(16 + tier * 14):
            self._spark((x, y), color, speed=150 + tier * 70)
        if reveal.card.rarity in (Rarity.EPIC, Rarity.LEGENDARY):
            self.shake = max(self.shake, 5.0 + tier * 2.5)
            self.flash = max(self.flash, 0.35)
        if reveal.card.rarity is Rarity.LEGENDARY:
            self._enter_spotlight(reveal)

    def _enter_spotlight(self, reveal: CardReveal) -> None:
        """Legendary celebration: the world dims, god-rays blaze from behind
        the card, and gold detonates. Multiple legendaries share the stage."""
        if self._spotlight is None:
            self._spotlight = {"cards": [], "t": 0.0, "dur": 2.6}
        if reveal not in self._spotlight["cards"]:
            self._spotlight["cards"].append(reveal)
        self._spotlight["t"] = 0.0
        self.flash = 1.0
        self.shake = max(self.shake, 16.0)
        x, y = reveal.slot
        gold = RARITY_FX[Rarity.LEGENDARY]
        for radius in (60, 120, 190):
            self.rings.append({"x": x, "y": y, "r": 10.0, "max": radius,
                               "life": 1.0, "color": gold})
        for _ in range(140):
            self._spark((x, y), gold, speed=430)
        for _ in range(30):
            self._spark((x, y), (255, 255, 240), speed=560)
        self.app.audio.ui_sound("attack")

    def _spark(self, pos, color, speed: float = 220.0) -> None:
        angle = random.uniform(0, math.tau)
        vel = random.uniform(speed * 0.3, speed)
        self.particles.append({
            "x": pos[0], "y": pos[1],
            "vx": math.cos(angle) * vel, "vy": math.sin(angle) * vel - 60,
            "life": random.uniform(0.5, 1.1), "max": 1.1,
            "size": random.uniform(1.5, 4.0) * self.s, "color": color})

    # ------------------------------------------------------------ input
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back_out()
            return
        if self.btn_back.handle_event(event):
            return
        if self.phase == SELECT:
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                for pack, rect in self.select_slots:
                    if rect.collidepoint(event.pos):
                        self._choose(pack)
                        return
        elif self.phase == TEAR:
            strip = pygame.Rect(self.big_rect.x, self.big_rect.y,
                                self.big_rect.width,
                                int(self.big_rect.height * TEAR_STRIP * 1.6))
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 \
                    and strip.collidepoint(event.pos):
                self._tearing = True
                self._tear_anchor = event.pos[0] - \
                    self.tear * self.big_rect.width * TEAR_DISTANCE
            elif event.type == pygame.MOUSEMOTION and self._tearing:
                dragged = event.pos[0] - self._tear_anchor
                self.tear = max(0.0, min(1.0, dragged /
                                         (self.big_rect.width * TEAR_DISTANCE)))
                if self.tear >= 1.0:
                    self._tearing = False
                    self._finish_tear()
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self._tearing = False
        elif self.phase == REVEAL:
            if self._spotlight is not None:
                if event.type == pygame.MOUSEBUTTONUP:
                    self._spotlight = None       # click to continue
                return
            if self._pinned is not None:
                if event.type == pygame.MOUSEBUTTONDOWN:
                    self._pinned = None
                return
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                for reveal in self.cards:
                    if not reveal.flipped:
                        continue
                    rect = pygame.Rect(0, 0, self.card_w, self.card_h)
                    rect.center = reveal.slot
                    if rect.collidepoint(event.pos):
                        self._pinned = reveal
                        return
            if all(c.flipped for c in self.cards):
                if self.btn_again.handle_event(event):
                    return
                if self.btn_done.handle_event(event):
                    return
            elif self.btn_reveal_all.handle_event(event):
                return
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                for reveal in self.cards:
                    x, y = reveal.slot
                    rect = pygame.Rect(0, 0, self.card_w, self.card_h)
                    rect.center = (int(x), int(y))
                    if rect.collidepoint(event.pos):
                        self._flip(reveal)
                        return

    # ------------------------------------------------------------ update
    def update(self, dt: float) -> None:
        self.note_timer = max(0.0, getattr(self, "note_timer", 0.0) - dt)
        self._time += dt
        self.flash = max(0.0, self.flash - dt * 3.2)
        self.shake = max(0.0, self.shake - dt * 22)
        mouse = pygame.mouse.get_pos()

        if self.phase == SELECT:
            self._hover_pack = next((p for p, r in self.select_slots
                                     if r.collidepoint(mouse)), None)
        elif self.phase == ZOOM and self._zoom_tween is not None:
            self._zoom_tween.update(dt)
            if self._zoom_tween.done:
                self._begin_tear(self.chosen)
        elif self.phase == TEAR:
            if not self._tearing:
                self.tear = max(0.0, self.tear - dt * 1.4)   # eases back
        elif self.phase in (BURST, REVEAL):
            if self._strip_fly is not None:
                fly = self._strip_fly
                fly["x"] += fly["vx"] * dt
                fly["y"] += fly["vy"] * dt
                fly["vy"] += 900 * dt
                fly["rot"] += fly["vrot"] * dt
                fly["life"] -= dt * 0.9
                if fly["life"] <= 0:
                    self._strip_fly = None
            launched_all = True
            for reveal in self.cards:
                reveal.t += dt
                if reveal.flipping:
                    reveal.flip = min(1.0, reveal.flip + dt * 3.4)
                    if reveal.flip >= 1.0 and not reveal.flipped:
                        reveal.flipping = False
                        self._on_flip_done(reveal)
                launched_all &= reveal.landed
            if self.phase == BURST:
                self._pack_drop = min(1.0, self._pack_drop +
                                      (dt * 1.3 if self.cards and
                                       self.cards[0].launched else 0))
                if launched_all:
                    self.phase = REVEAL
            self._hover_card = None
            if self._spotlight is None and self._pinned is None:
                for reveal in self.cards:
                    rect = pygame.Rect(0, 0, self.card_w, self.card_h)
                    rect.center = reveal.slot
                    if rect.collidepoint(mouse) and reveal.landed:
                        self._hover_card = reveal
                        break

        if self._spotlight is not None:
            self._spotlight["t"] += dt
            if self._spotlight["t"] >= self._spotlight["dur"]:
                self._spotlight = None

        # particles + rings
        for p in self.particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += 420 * dt
            p["vx"] *= (1 - 1.6 * dt)
            p["life"] -= dt
        self.particles = [p for p in self.particles if p["life"] > 0]
        for ring in self.rings:
            ring["r"] += (ring["max"] - ring["r"]) * min(1, dt * 7)
            ring["life"] -= dt * 1.6
        self.rings = [r for r in self.rings if r["life"] > 0]

        widgets = [self.btn_back]
        if self.phase == REVEAL:
            widgets.append(self.btn_reveal_all if not
                           all(c.flipped for c in self.cards)
                           else self.btn_again)
            if all(c.flipped for c in self.cards):
                widgets.append(self.btn_done)
        for widget in widgets:
            widget.update(dt)
        hand = (self._hover_pack is not None or self._hover_card is not None
                or (self.phase == TEAR))
        apply_cursor(widgets, force_hand=hand)

    # ------------------------------------------------------------ drawing
    def _shake_offset(self):
        if self.shake <= 0:
            return (0, 0)
        return (random.uniform(-self.shake, self.shake),
                random.uniform(-self.shake, self.shake))

    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, h = surface.get_size()
        ox, oy = self._shake_offset()
        s = self.s

        theme.draw_text(surface, "Packs", (w // 2, int(36 * s)),
                        theme.display_font(int(30 * s)), theme.GOLD_BRIGHT,
                        anchor="center")

        if self.phase == SELECT:
            self._draw_select(surface, ox, oy)
        elif self.phase == ZOOM:
            self._draw_zoom(surface, ox, oy)
        elif self.phase in (TEAR, BURST, REVEAL):
            self._draw_open(surface, ox, oy)

        # particles / rings / flash on top
        for ring in self.rings:
            alpha = max(0, int(220 * ring["life"]))
            pygame.draw.circle(surface, ring["color"],
                               (int(ring["x"] + ox), int(ring["y"] + oy)),
                               int(ring["r"]), width=max(1, int(4 * ring["life"])))
        for p in self.particles:
            k = max(0.0, p["life"] / p["max"])
            size = max(1, int(p["size"] * k))
            pygame.draw.circle(surface, p["color"],
                               (int(p["x"] + ox), int(p["y"] + oy)), size)
        if self.phase == REVEAL and self._spotlight is not None:
            self._draw_spotlight(surface)
        elif self.phase == REVEAL and self._pinned is not None:
            self._draw_pinned(surface)
        elif self.phase == REVEAL and self._hover_card is not None \
                and self._hover_card.flipped:
            self._draw_hover_preview(surface)

        if self.flash > 0:
            veil = pygame.Surface((w, h), pygame.SRCALPHA)
            veil.fill((255, 245, 220, int(180 * self.flash)))
            surface.blit(veil, (0, 0))

        self.btn_back.draw(surface)
        if getattr(self, "note_timer", 0) > 0 and self.note:
            fade = min(1.0, self.note_timer / 0.4)
            box = pygame.Rect(0, 0, min(surface.get_width() - 80, 520), 42)
            box.midbottom = (surface.get_width() // 2,
                             surface.get_height() - 84)
            veil = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(235 * fade)),
                             veil.get_rect(), border_radius=10)
            pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                             veil.get_rect(), width=1, border_radius=10)
            surface.blit(veil, box.topleft)
            theme.draw_text(surface, self.note, box.center,
                            theme.body_font(15), theme.TEXT,
                            anchor="center", alpha=int(255 * fade))

    # ------------------------------------------------------ inspect + spotlight
    def _big_card(self, card: CardDef, height: int):
        return self._card_face(card, height) or self._vector_face(card, height)

    def _draw_hover_preview(self, surface) -> None:
        card = self._hover_card.card
        image = self._big_card(card, int(surface.get_height() * 0.52))
        mx, my = pygame.mouse.get_pos()
        x = mx + 26
        if x + image.get_width() > surface.get_width() - 10:
            x = mx - image.get_width() - 26
        y = max(10, min(my - image.get_height() // 2,
                        surface.get_height() - image.get_height() - 10))
        theme.draw_glow_rect(surface, pygame.Rect(x, y, image.get_width(),
                                                  image.get_height()),
                             RARITY_FX[card.rarity], 0.4, radius=12, spread=10)
        surface.blit(image, (x, y))

    def _draw_pinned(self, surface) -> None:
        w, h = surface.get_size()
        veil = pygame.Surface((w, h), pygame.SRCALPHA)
        veil.fill((*theme.NAVY_ABYSS, 185))
        surface.blit(veil, (0, 0))
        card = self._pinned.card
        image = self._big_card(card, int(h * 0.84))
        rect = image.get_rect(center=(w // 2, h // 2))
        theme.draw_glow_rect(surface, rect, RARITY_FX[card.rarity], 0.5,
                             radius=16, spread=18)
        surface.blit(image, rect)
        theme.draw_text(surface, "Click anywhere to close",
                        (w // 2, h - 24), theme.body_font(13),
                        theme.TEXT_FAINT, anchor="center")

    def _draw_spotlight(self, surface) -> None:
        spot = self._spotlight
        w, h = surface.get_size()
        t = spot["t"]
        appear = min(1.0, t * 5.0)
        fade = min(1.0, max(0.0, (spot["dur"] - t) / 0.5))
        strength = appear * fade
        veil = pygame.Surface((w, h), pygame.SRCALPHA)
        veil.fill((4, 6, 14, int(215 * strength)))
        surface.blit(veil, (0, 0))
        gold = RARITY_FX[Rarity.LEGENDARY]
        for reveal in spot["cards"]:
            x, y = reveal.slot
            # god-rays: rotating fan of translucent golden wedges behind
            rays = pygame.Surface((w, h), pygame.SRCALPHA)
            count = 14
            for k in range(count):
                angle = t * 0.55 + k * math.tau / count
                spread_a = 0.10 + 0.03 * math.sin(t * 2 + k)
                length = h * (0.55 + 0.08 * math.sin(t * 1.3 + k * 1.7))
                p1 = (x + math.cos(angle - spread_a) * length,
                      y + math.sin(angle - spread_a) * length)
                p2 = (x + math.cos(angle + spread_a) * length,
                      y + math.sin(angle + spread_a) * length)
                alpha = int(52 * strength * (0.6 + 0.4 * math.sin(t * 3 + k)))
                pygame.draw.polygon(rays, (*gold, max(0, alpha)),
                                    [(x, y), p1, p2])
            surface.blit(rays, (0, 0))
            # shining glimmer core behind the card
            pulse = 0.75 + 0.25 * math.sin(t * 6)
            for radius, alpha in ((int(150 * pulse), 60), (int(95 * pulse), 95),
                                  (int(55 * pulse), 140)):
                glow = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
                pygame.draw.circle(glow, (*gold, int(alpha * strength)),
                                   (radius, radius), radius)
                surface.blit(glow, (int(x - radius), int(y - radius)))
            # the card itself, enlarged, riding above the rays
            scale = 1.0 + 0.22 * min(1.0, t * 3.5)
            image = self._big_card(reveal.card, int(self.card_h * scale))
            rect = image.get_rect(center=(int(x), int(y - 14 * appear)))
            theme.draw_glow_rect(surface, rect, gold,
                                 min(1.0, 0.8 * strength * pulse),
                                 radius=14, spread=22)
            surface.blit(image, rect)
            # trickling sparkles
            if random.random() < 0.5:
                self._spark((x + random.uniform(-60, 60),
                             y + random.uniform(-90, 90)), gold, speed=70)
        theme.draw_text(surface, "LEGENDARY",
                        (w // 2, int(h * 0.10)),
                        theme.display_font(int(40 * self.s), bold=True),
                        gold, anchor="center",
                        alpha=int(255 * strength))
        theme.draw_text(surface, "click to continue",
                        (w // 2, h - 26), theme.body_font(12),
                        theme.TEXT_FAINT, anchor="center")

    def _draw_select(self, surface, ox, oy) -> None:
        s = self.s
        state = getattr(getattr(self.app.backend.net, "state", None),
                        "name", "")
        if self._economy_on():
            subtitle = "Choose a pack to open"
        elif state == "CONNECTED":
            subtitle = ("Practice mode — the server's economy is offline "
                        "(packs opened here aren't saved)")
        else:
            subtitle = "Practice mode — sign in online to use your real packs"
        theme.draw_text(surface, subtitle,
                        (surface.get_width() // 2, int(78 * s)),
                        theme.body_font(int(15 * s)), theme.TEXT_DIM,
                        anchor="center")
        for i, (pack, rect) in enumerate(self.select_slots):
            hover = pack is self._hover_pack
            bob = math.sin(self._time * 1.4 + i * 2.1) * 7 * s
            scale = 1.06 if hover else 1.0
            height = int(rect.height * scale)
            image = self._pack_image(pack, height)
            draw_rect = pygame.Rect(0, 0, int(rect.width * scale), height)
            draw_rect.center = (rect.centerx, int(rect.centery + bob))
            if hover:
                theme.draw_glow_rect(surface, draw_rect, theme.GOLD_GLOW,
                                     0.55, radius=18, spread=16)
            if image is not None:
                surface.blit(image, image.get_rect(center=draw_rect.center))
            else:
                theme.draw_panel(surface, draw_rect, fill=theme.NAVY,
                                 border=theme.GOLD_DIM, radius=16)
                theme.draw_text(surface, pack.name, draw_rect.center,
                                theme.body_font(16), theme.TEXT,
                                anchor="center")
            theme.draw_text(surface, pack.name,
                            (rect.centerx, rect.bottom + int(26 * s)),
                            theme.body_font(int(17 * s), bold=True),
                            theme.GOLD_BRIGHT if hover else theme.TEXT,
                            anchor="center")
            if self._economy_on():
                owned = self._owned(pack.pack_id)
                label = (f"{owned} owned" if owned
                         else "none — visit the Store")
                color = theme.SUCCESS if owned else theme.TEXT_FAINT
                theme.draw_text(surface, label,
                                (rect.centerx, rect.bottom + int(48 * s)),
                                theme.body_font(int(12 * s)), color,
                                anchor="center")
            if hover:
                self._draw_odds(surface, pack, rect)

    def _draw_odds(self, surface, pack, rect) -> None:
        s = self.s
        panel = pygame.Rect(0, 0, int(210 * s), int(150 * s))
        panel.midtop = (rect.centerx, rect.bottom + int(64 * s))
        panel.clamp_ip(surface.get_rect())
        theme.draw_panel(surface, panel, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=10)
        y = panel.y + int(12 * s)
        for rarity in reversed(RARITY_ORDER):
            pct = pack.weights.get(rarity, 0.0)
            color = RARITY_FX[rarity]
            pygame.draw.circle(surface, color,
                               (panel.x + int(18 * s), y + int(7 * s)),
                               int(5 * s))
            theme.draw_text(surface, rarity.value.title(),
                            (panel.x + int(32 * s), y),
                            theme.body_font(int(12 * s)), theme.TEXT,
                            anchor="topleft")
            theme.draw_text(surface, f"{pct:g}%",
                            (panel.right - int(14 * s), y),
                            theme.body_font(int(12 * s)), color,
                            anchor="topright")
            y += int(26 * s)

    def _draw_zoom(self, surface, ox, oy) -> None:
        k = ease_out_cubic(self._zoom_tween.value if self._zoom_tween else 1.0)
        src = next(r for p, r in self.select_slots if p is self.chosen)
        rect = pygame.Rect(
            int(src.x + (self.big_rect.x - src.x) * k),
            int(src.y + (self.big_rect.y - src.y) * k),
            int(src.width + (self.big_rect.width - src.width) * k),
            int(src.height + (self.big_rect.height - src.height) * k))
        image = self._pack_image(self.chosen, rect.height)
        for pack, slot in self.select_slots:
            if pack is self.chosen:
                continue
            ghost = self._pack_image(pack, slot.height)
            if ghost is not None:
                ghost = ghost.copy()
                ghost.set_alpha(int(255 * (1 - k)))
                surface.blit(ghost, ghost.get_rect(center=slot.center))
        if image is not None:
            surface.blit(image, image.get_rect(center=rect.center))

    def _draw_open(self, surface, ox, oy) -> None:
        s = self.s
        rect = self.big_rect.move(ox, oy + int(self._pack_drop * 320))
        strip_h = int(rect.height * TEAR_STRIP)
        image = self._pack_image(self.chosen, rect.height) \
            if self.chosen else None
        pack_alpha = int(255 * (1 - self._pack_drop))

        if self.phase == TEAR:
            wobble = math.sin(self._time * 26) * self.tear * 2.2
            # body
            if image is not None:
                body = image.subsurface(
                    pygame.Rect(0, strip_h, image.get_width(),
                                image.get_height() - strip_h))
                surface.blit(body, (rect.x, rect.y + strip_h + wobble * 0.4))
                strip = image.subsurface(
                    pygame.Rect(0, 0, image.get_width(), strip_h))
                dx = self.tear * self.tear * rect.width * 0.85
                angle = -self.tear * 13 + wobble
                rotated = pygame.transform.rotozoom(strip, angle, 1.0)
                surface.blit(rotated, rotated.get_rect(
                    center=(rect.centerx + dx,
                            rect.y + strip_h // 2 - self.tear * 34)))
            else:
                theme.draw_panel(surface, rect, fill=theme.NAVY,
                                 border=theme.GOLD_DIM, radius=14)
            # seam sparks while dragging
            if self._tearing and self.tear > 0.02:
                seam_x = rect.x + self.tear * rect.width
                for _ in range(2):
                    self._spark((seam_x, rect.y + strip_h),
                                theme.GOLD_BRIGHT, speed=170)
            # seam glow line
            pygame.draw.line(surface, theme.GOLD_BRIGHT,
                             (rect.x, rect.y + strip_h),
                             (rect.x + self.tear * rect.width,
                              rect.y + strip_h), 3)
            hint = "Grab the top and tear it across  ➜" if self.tear < 0.1 \
                else "Keep pulling!"
            theme.draw_text(surface, hint,
                            (rect.centerx, rect.bottom + int(36 * s)),
                            theme.body_font(int(16 * s)), theme.TEXT_DIM,
                            anchor="center")
        else:
            # BURST / REVEAL: pack body sinks + fades
            if image is not None and pack_alpha > 0:
                body = image.subsurface(
                    pygame.Rect(0, strip_h, image.get_width(),
                                image.get_height() - strip_h)).copy()
                body.set_alpha(pack_alpha)
                surface.blit(body, (rect.x, rect.y + strip_h))
            if self._strip_fly is not None and image is not None:
                fly = self._strip_fly
                strip = image.subsurface(
                    pygame.Rect(0, 0, image.get_width(), strip_h)).copy()
                strip.set_alpha(int(255 * max(0, fly["life"])))
                rotated = pygame.transform.rotozoom(strip, fly["rot"], 1.0)
                surface.blit(rotated, rotated.get_rect(
                    center=(fly["x"] + ox, fly["y"] + oy)))
            self._draw_cards(surface, ox, oy)

    def _draw_cards(self, surface, ox, oy) -> None:
        for reveal in self.cards:
            if not reveal.launched:
                continue
            x, y = reveal.pos(self._time)
            x += ox
            y += oy
            hover = reveal is self._hover_card and not reveal.flipped
            # flip: width squashes to 0 at the halfway point, swaps sides
            flip_k = reveal.flip
            showing_face = flip_k > 0.5 or reveal.flipped
            squash = abs(1 - 2 * min(1.0, flip_k)) if reveal.flipping else 1.0
            height = self.card_h
            if showing_face:
                face = self._card_face(reveal.card, height) \
                    or self._vector_face(reveal.card, height)
                image = face
            else:
                image = self._card_back()
            width = max(2, int(image.get_width() * squash))
            scaled = pygame.transform.smoothscale(
                image, (width, height)) if squash < 0.99 else image
            draw_rect = scaled.get_rect(center=(int(x), int(y)))
            if reveal.flipped:
                color = RARITY_FX[reveal.card.rarity]
                tier = RARITY_ORDER.index(reveal.card.rarity)
                pulse = 0.30 + 0.14 * math.sin(self._time * 3 +
                                               reveal.settle_bob)
                strength = pulse * (0.7 + tier * 0.35)
                theme.draw_glow_rect(surface, draw_rect, color,
                                     min(1.0, strength), radius=12,
                                     spread=10 + tier * 4)
            elif hover:
                theme.draw_glow_rect(surface, draw_rect, theme.GOLD_GLOW,
                                     0.5, radius=12, spread=8)
            surface.blit(scaled, draw_rect)

        if self.phase == REVEAL:
            if all(c.flipped for c in self.cards):
                self.btn_again.draw(surface)
                self.btn_done.draw(surface)
            elif all(c.landed for c in self.cards):
                self.btn_reveal_all.draw(surface)
                theme.draw_text(surface, "Click a card to reveal it",
                                (surface.get_width() // 2,
                                 self.btn_reveal_all.rect.y - int(16 * self.s)),
                                theme.body_font(int(13 * self.s)),
                                theme.TEXT_FAINT, anchor="center")
