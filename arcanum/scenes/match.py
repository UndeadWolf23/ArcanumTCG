"""Dev-test match scene.

The table art fills the screen (via the shared background's image layer)
and every HUD element floats over it. Zones have no borders — destinations
glow while you drag.

Interactions:
* MAIN: drag to reorder your hand; drag a card over the table to play it.
  Targeted spells stage instead of resolving: the card hovers mid-table, a
  blue arrow follows your cursor, legal targets pulse — click one to cast,
  or Cancel / right-click / Esc to back out (no mana spent).
* COMBAT: drag a red arrow from one of your ready creatures onto an enemy
  creature (or their champion once the row is clear) to attack. Attackers
  lunge at the target and return; the wounded keep their scars (health
  persists); the slain dissolve. Each creature attacks once per turn, and
  you can resolve any number of separate attacks before ending the turn.
* Hover any field card for an enlarged preview; right-click pins it.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Optional

import pygame

from arcanum.core.constants import IMAGES_DIR, ROOT_DIR
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.game.controller import (LocalController, MatchController,
                                     RemoteController)
from arcanum.game.match import MAX_MANA, CardInstance, Kind, Phase
from arcanum.game.serialize import card_from_dict
from arcanum.ui import theme
from arcanum.ui.animation import Tween, approach, ease_out_back, ease_out_cubic
from arcanum.ui.widgets import Button, LinkButton, apply_cursor

log = logging.getLogger(__name__)

TABLE_CANDIDATES = (IMAGES_DIR / "table.png", ROOT_DIR / "table.png")

MANA_FILL = (86, 156, 255)
MANA_CORE = (170, 210, 255)
MANA_SPENT = (30, 44, 78)
HEALTH_RED = (214, 96, 96)
ARROW_ATTACK = (232, 120, 90)
ARROW_SPELL = (110, 170, 255)

PHASE_LABELS = {Phase.DRAW: "Draw", Phase.MAIN: "Main",
                Phase.COMBAT: "Combat", Phase.END: "End"}
KIND_LABELS = {Kind.CREATURE: "Creature", Kind.SPELL: "Spell",
               Kind.RELIC: "Relic", Kind.CHAMPION: "Champion"}


# ---------------------------------------------------------------------------
# Dynamic targeting arrow (quadratic bezier with an animated core)
# ---------------------------------------------------------------------------
def draw_arrow(surface: pygame.Surface, start: tuple[float, float],
               end: tuple[float, float], color: tuple[int, int, int],
               time_s: float) -> None:
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if dist < 8:
        return
    lift = min(150.0, dist * 0.32)
    cx, cy = (sx + ex) / 2, (sy + ey) / 2 - lift

    def bez(t: float) -> tuple[float, float]:
        u = 1 - t
        return (u * u * sx + 2 * u * t * cx + t * t * ex,
                u * u * sy + 2 * u * t * cy + t * t * ey)

    steps = max(12, int(dist / 22))
    points = [bez(i / steps) for i in range(steps + 1)]
    dark = tuple(max(0, c - 140) for c in color)
    pygame.draw.lines(surface, dark, False, points, 7)
    pygame.draw.lines(surface, color, False, points, 3)
    # traveling pulse along the arc
    pulse_t = (time_s * 1.4) % 1.0
    px, py = bez(pulse_t)
    pygame.draw.circle(surface, (255, 240, 220), (int(px), int(py)), 4)
    # arrowhead aligned with the end tangent
    tx, ty = bez(max(0.0, 1.0 - 1.0 / steps))
    angle = math.atan2(ey - ty, ex - tx)
    size = 15
    left = (ex - size * math.cos(angle - 0.5), ey - size * math.sin(angle - 0.5))
    right = (ex - size * math.cos(angle + 0.5), ey - size * math.sin(angle + 0.5))
    pygame.draw.polygon(surface, color, [(ex, ey), left, right])
    pygame.draw.polygon(surface, dark, [(ex, ey), left, right], width=1)


# ---------------------------------------------------------------------------
# Card sprite
# ---------------------------------------------------------------------------
class CardSprite:
    def __init__(self, card: CardInstance, pos: tuple[float, float]) -> None:
        self.card = card
        self.x, self.y = float(pos[0]), float(pos[1])
        self.tx, self.ty = self.x, self.y
        self.scale = 1.0
        self.tscale = 1.0
        self.dragging = False
        self.hover = False
        self.dying = False
        self.alpha = 255
        self.spawn: Optional[Tween] = None
        self.drop: Optional[Tween] = None
        self.lunge: Optional[Tween] = None
        self.lunge_vec = (0.0, 0.0)

    def start_spawn(self) -> None:
        self.scale = 0.35
        self.spawn = Tween(0.0, 1.0, 0.35, easing=ease_out_cubic)

    def start_drop(self) -> None:
        self.drop = Tween(1.0, 0.0, 0.45, easing=ease_out_back)

    def start_lunge(self, toward: tuple[float, float]) -> None:
        dx, dy = toward[0] - self.x, toward[1] - self.y
        dist = math.hypot(dx, dy) or 1.0
        reach = max(0.0, dist - 30.0)
        self.lunge_vec = (dx / dist * reach, dy / dist * reach)
        self.lunge = Tween(0.0, 1.0, 0.50)

    def start_die(self) -> None:
        """Dissolve: fade + drift upward + gentle shrink."""
        self.dying = True
        self.tscale = 0.75
        self.ty -= 26

    def update(self, dt: float) -> None:
        for name in ("spawn", "drop", "lunge"):
            tween = getattr(self, name)
            if tween is not None:
                tween.update(dt)
                if tween.done:
                    setattr(self, name, None)
        if self.dying:
            self.alpha = max(0, self.alpha - int(430 * dt))
        if not self.dragging:
            self.x = approach(self.x, self.tx, dt, speed=14.0)
            self.y = approach(self.y, self.ty, dt, speed=14.0)
        self.scale = approach(self.scale, self.tscale, dt, speed=12.0)

    @property
    def gone(self) -> bool:
        return self.dying and self.alpha <= 4

    def draw_offset(self) -> tuple[float, float]:
        ox = oy = 0.0
        if self.drop is not None:
            oy -= 46.0 * self.drop.value
        if self.lunge is not None:
            k = math.sin(math.pi * self.lunge.value)   # out and back
            ox += self.lunge_vec[0] * k
            oy += self.lunge_vec[1] * k
        return ox, oy

    def rect(self, size: tuple[int, int]) -> pygame.Rect:
        w = max(2, int(size[0] * self.scale))
        h = max(2, int(size[1] * self.scale))
        ox, oy = self.draw_offset()
        rect = pygame.Rect(0, 0, w, h)
        rect.center = (int(self.x + ox), int(self.y + oy))
        return rect


class FloatText:
    """Rising, fading combat number."""

    def __init__(self, text: str, pos: tuple[float, float],
                 color: tuple[int, int, int]) -> None:
        self.text = text
        self.x, self.y = float(pos[0]), float(pos[1])
        self.color = color
        self.life = 1.0

    def update(self, dt: float) -> None:
        self.life -= dt * 1.1
        self.y -= 34 * dt

    @property
    def gone(self) -> bool:
        return self.life <= 0


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------
class MatchScene(Scene):
    def on_enter(self, controller: MatchController | None = None, **kwargs) -> None:
        # The scene renders whatever controller it is given and cannot tell
        # local practice from an online match. `self.match` is READ-ONLY here:
        # a state view for layout and instant UI hints. Every change arrives
        # as a controller event.
        self.controller = controller or LocalController(local_name=self._local_name())
        self.match = self.controller.state

        self.hand: list[CardSprite] = []
        self.board: list[CardSprite] = []
        self.opp_board: list[CardSprite] = []
        self.relics: list[CardSprite] = []
        self.opp_relics: list[CardSprite] = []
        self.champ: Optional[CardSprite] = None
        self.opp_champ: Optional[CardSprite] = None
        self.effects: list[CardSprite] = []
        self.floats: list[FloatText] = []
        self.opp_hand_count = 0

        self.drag: Optional[CardSprite] = None
        self._drag_dx = self._drag_dy = 0.0
        self.pending_spell: Optional[CardSprite] = None    # staged, awaiting target
        self.attack_source: Optional[CardSprite] = None    # combat arrow origin
        self._target_uid: Optional[int] = None
        self._pinned: Optional[tuple[CardInstance, pygame.Rect]] = None
        self._guard_flash = 0.0            # opp creatures flash: champion guarded
        self.result: Optional[str] = None                  # "victory" / "defeat"

        self._timers: list[list] = []
        self._toast = ""
        self._toast_timer = 0.0
        self._flow_busy = True
        self._time = 0.0

        self.app.background.set_image(self._load_table_art())
        self._layout()
        self.controller.start()

    def on_exit(self) -> None:
        try:
            self.controller.concede()
            self.controller.close()
        except Exception:  # noqa: BLE001
            log.exception("Controller close failed")
        self._timers.clear()
        self.app.background.set_image(None)

    def _local_name(self) -> str:
        user = self.app.backend.session.user
        return user.username if user else "You"

    @staticmethod
    def _load_table_art() -> Optional[pygame.Surface]:
        for path in TABLE_CANDIDATES:
            if path.is_file():
                try:
                    return pygame.image.load(str(path)).convert()
                except pygame.error as exc:
                    log.warning("Could not load table art %s: %s", path, exc)
        log.info("No table.png found; using procedural backdrop.")
        return None

    # ------------------------------------------------------------ layout
    def on_resize(self, size: tuple[int, int]) -> None:
        if hasattr(self, "match"):
            self._layout()

    def _layout(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.3, h / 1080))
        self.ui_scale = s
        self.s = s
        self.card_size = (int(158 * s), int(220 * s))        # hand
        self.board_card_size = (int(102 * s), int(143 * s))
        self.champ_size = (int(168 * s), int(234 * s))       # champions
        self.relic_size = (int(78 * s), int(109 * s))

        self.champ_pos = (int(w * 0.130), int(h * 0.655))
        self.opp_champ_pos = (int(w * 0.130), int(h * 0.235))
        self.relic_anchor = (int(w * 0.845), int(h * 0.660))
        self.opp_relic_anchor = (int(w * 0.845), int(h * 0.235))
        self.creature_span = (int(w * 0.27), int(w * 0.73))
        self.own_row_y = int(h * 0.585)
        self.opp_row_y = int(h * 0.295)
        self.stage_pos = (w // 2, int(h * 0.62))             # staged spell hover

        self.hand_y = h - int(self.card_size[1] * 0.52)
        self.play_line = int(h * 0.75)
        self.deck_pos = (w - int(86 * s), h - int(120 * s))

        ui = self.app.audio.ui_sound
        self.btn_turn = Button(pygame.Rect(w - int(198 * s), int(h * 0.455),
                                           int(166 * s), int(52 * s)),
                               "To Combat", self._on_turn_button, sound_cb=ui)
        self.lnk_leave = LinkButton((int(70 * s), int(26 * s)), "Leave match",
                                    self._ask_leave, font_size=15)
        self._confirm_leave = False
        dw = int(190 * s)
        self.btn_leave_yes = Button(pygame.Rect(0, 0, dw, int(48 * s)),
                                    "Leave match", self._leave,
                                    font_size=16, sound_cb=ui)
        self.btn_leave_no = Button(pygame.Rect(0, 0, dw, int(48 * s)),
                                   "Keep playing", self._dismiss_leave,
                                   primary=False, font_size=16, sound_cb=ui)
        cx, cy = w // 2, h // 2
        self.btn_leave_yes.rect.center = (cx - dw // 2 - int(12 * s),
                                          cy + int(46 * s))
        self.btn_leave_no.rect.center = (cx + dw // 2 + int(12 * s),
                                         cy + int(46 * s))
        self.btn_cancel = Button(pygame.Rect(0, 0, int(120 * s), int(40 * s)),
                                 "Cancel", self._cancel_stage, primary=False,
                                 font_size=16, sound_cb=ui)
        self.btn_cancel.rect.center = (self.stage_pos[0],
                                       self.stage_pos[1] + int(self.card_size[1] * 0.72))
        self.btn_cancel.visible = False
        self.btn_home = Button(pygame.Rect(0, 0, int(220 * s), int(54 * s)),
                               "Return Home", self._leave, sound_cb=ui)
        self.btn_home.rect.center = (w // 2, int(h * 0.60))
        self.btn_home.visible = False
        self.widgets = [self.btn_turn, self.lnk_leave, self.btn_cancel, self.btn_home]

        if self.champ is not None:
            self.champ.tx, self.champ.ty = self.champ_pos
        if self.opp_champ is not None:
            self.opp_champ.tx, self.opp_champ.ty = self.opp_champ_pos

    # ------------------------------------------------------------ scheduling
    def _schedule(self, delay, callback):
        self._timers.append([max(0.0, delay), callback])

    def _run_timers(self, dt):
        due = [t for t in self._timers if (t.__setitem__(0, t[0] - dt) or t[0] <= 0)]
        for timer in due:
            self._timers.remove(timer)
            try:
                timer[1]()
            except Exception:  # noqa: BLE001
                log.exception("Scene timer step failed")

    # ------------------------------------------------------------ controller events
    def _pump_controller(self, dt: float) -> None:
        self.controller.update(dt)
        for event in self.controller.poll_events():
            try:
                self._on_controller_event(event)
            except Exception:  # noqa: BLE001
                log.exception("Failed handling controller event %r", event.get("type"))

    def _on_controller_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "match_start":
            self._on_match_start()
        elif etype == "phase":
            self._on_phase_event(event)
        elif etype == "draw":
            self._on_draw_event(event)
        elif etype == "played":
            self._on_played_event(event)
        elif etype == "mana":
            if event.get("player") == 0 and event.get("amount"):
                self._show_toast("+1 maximum mana")
        elif etype == "destroy":
            self._kill_sprite(event["player"], event["uid"])
        elif etype == "attack":
            self._on_attack_event(event)
        elif etype == "damage":
            self._on_damage_event(event)
        elif etype == "death":
            self._kill_sprite(event["player"], event["uid"])
        elif etype == "victory":
            self._finish(event["player"])
        elif etype == "rejected":
            self._cancel_stage()
            self._show_toast(event.get("reason", "That action was refused."))

    def _spawn_hand_sprite(self, card):
        sprite = CardSprite(card, self.deck_pos)
        sprite.start_spawn()
        self.hand.append(sprite)
        self.app.audio.ui_sound("draw")

    def _on_match_start(self) -> None:
        self.opp_hand_count = self.controller.opp_hand_count
        self.champ = CardSprite(self.match.player(0).champion, self.champ_pos)
        self.champ.start_spawn()
        self.opp_champ = CardSprite(self.match.player(1).champion, self.opp_champ_pos)
        self.opp_champ.start_spawn()
        for i, card in enumerate(self.match.player(0).hand):
            self._schedule(0.10 * i, lambda c=card: self._spawn_hand_sprite(c))

    def _on_phase_event(self, event: dict) -> None:
        your_turn = event.get("your_turn")
        if your_turn is None:                      # fall back to the mirror
            your_turn = self.match.is_local_turn()
        self._flow_busy = not (your_turn and
                               event.get("phase") in ("main", "combat"))

    def _on_draw_event(self, event: dict) -> None:
        if event.get("skipped"):
            return
        if event.get("player") == 0 and not event.get("hidden"):
            uid = event.get("card_uid")
            card = self.match.find_in_hand(0, uid) if uid is not None else None
            if event.get("burned") or card is None:
                if event.get("burned"):
                    self._show_toast("Hand full — card burned!")
                return
            sprite = CardSprite(card, self.deck_pos)
            sprite.start_spawn()
            self.hand.append(sprite)
            self.app.audio.ui_sound("draw")
        elif event.get("hidden") and not event.get("burned"):
            self.opp_hand_count = self.controller.opp_hand_count

    def _on_played_event(self, event: dict) -> None:
        if event.get("player") == 0:
            # placed at intent time; rebind the sprite to the live mirror
            # object in case the state sync created a fresh instance
            data = event.get("card") or {}
            uid = int(data.get("uid", -1))
            live = next((c for c in (*self.match.player(0).board,
                                     *self.match.player(0).relics)
                         if c.uid == uid), None)
            if live is not None:
                for sprite in (*self.board, *self.relics, *self.effects):
                    if sprite.card.uid == uid and sprite.card is not live:
                        sprite.card = live
            return
        data = event.get("card")
        if data is None:
            return
        card = card_from_dict(data)
        self.opp_hand_count = self.controller.opp_hand_count
        w = self.app.screen.get_width()
        sprite = CardSprite(card, (w // 2, -60))
        self._place_played_sprite(sprite, owner=1)
        self.app.audio.ui_sound("play")

    def _on_attack_event(self, event: dict) -> None:
        owner = event["player"]
        attacker = self._sprite_for(owner, event["attacker"])
        target = self._sprite_for(1 - owner, event["target"])
        if attacker is not None and target is not None:
            attacker.start_lunge((target.x, target.y))
            self.app.audio.ui_sound("attack")

    def _on_damage_event(self, event: dict) -> None:
        sprite = self._sprite_for(event["player"], event["uid"])
        if sprite is not None:
            self.floats.append(FloatText(f"-{event['amount']}",
                                         (sprite.x, sprite.y - 30), HEALTH_RED))

    # ------------------------------------------------------------ event fx
    def _place_played_sprite(self, sprite: CardSprite, owner: int) -> None:
        sprite.start_drop()
        if sprite.card.kind is Kind.CREATURE:
            (self.board if owner == 0 else self.opp_board).append(sprite)
        elif sprite.card.kind is Kind.RELIC:
            (self.relics if owner == 0 else self.opp_relics).append(sprite)
        else:
            w, h = self.app.screen.get_size()
            sprite.tx = w // 2
            sprite.ty = int(h * (0.52 if owner == 0 else 0.40))
            self.effects.append(sprite)
            self._schedule(0.55, sprite.start_die)

    def _sprite_for(self, owner: int, uid: int) -> Optional[CardSprite]:
        pools: list[CardSprite] = list(self.board if owner == 0 else self.opp_board)
        champ = self.champ if owner == 0 else self.opp_champ
        if champ is not None:
            pools.append(champ)
        return next((s for s in pools if s.card.uid == uid), None)

    def _kill_sprite(self, owner: int, uid: int) -> None:
        row = self.board if owner == 0 else self.opp_board
        sprite = next((s for s in row if s.card.uid == uid), None)
        if sprite is None:
            log.warning("death event for unknown sprite uid=%s", uid)
            return
        row.remove(sprite)
        sprite.start_die()
        self.effects.append(sprite)
        if self._pinned and self._pinned[0].uid == uid:
            self._pinned = None

    def _finish(self, winner: int) -> None:
        self.result = "victory" if winner == 0 else "defeat"
        self._flow_busy = True
        self._cancel_stage()
        self.attack_source = None
        self.btn_home.visible = True

    def _on_turn_button(self) -> None:
        if self._flow_busy or not self.match.is_local_turn() or self.result:
            return
        if self.match.phase in (Phase.MAIN, Phase.COMBAT):
            self._cancel_drag()
            self._cancel_stage()
            self.attack_source = None
            self.controller.pass_phase()

    def _leave(self) -> None:
        self.app.goto_home()

    def _show_toast(self, message: str) -> None:
        self._toast = message
        self._toast_timer = 2.6

    # ------------------------------------------------------------ hand drag
    def _hand_index_for_x(self, x: float) -> int:
        return sum(1 for s in self.hand if s is not self.drag and x > s.tx)

    def _begin_drag(self, sprite: CardSprite, pos: tuple[int, int]) -> None:
        self.drag = sprite
        sprite.dragging = True
        sprite.tscale = 1.06
        self._drag_dx = sprite.x - pos[0]
        self._drag_dy = sprite.y - pos[1]
        self.hand.remove(sprite)
        self.hand.append(sprite)

    def _cancel_drag(self) -> None:
        if self.drag is not None:
            self.drag.dragging = False
            self.drag.tscale = 1.0
            self.drag = None
        self._target_uid = None

    def _release_drag(self, pos: tuple[int, int]) -> None:
        sprite = self.drag
        if sprite is None:
            return
        sprite.dragging = False
        sprite.tscale = 1.0
        self.drag = None
        if pos[1] >= self.play_line:
            return
        card = sprite.card
        ok, reason = self.match.can_play(0, card.uid)
        if not ok:
            self._show_toast(reason)
            return
        if card.needs_target:                     # stage; arrow picks the target
            self.hand.remove(sprite)
            self.pending_spell = sprite
            sprite.tx, sprite.ty = self.stage_pos
            sprite.tscale = 1.1
            self.btn_cancel.visible = True
            return
        self.hand.remove(sprite)
        self._place_played_sprite(sprite, owner=0)
        self.app.audio.ui_sound("play")
        self.controller.play_card(card.uid)

    # ------------------------------------------------------------ staging
    def _cancel_stage(self) -> None:
        if self.pending_spell is not None:
            sprite = self.pending_spell
            self.pending_spell = None
            sprite.tscale = 1.0
            self.hand.append(sprite)              # back to hand, nothing spent
        self.btn_cancel.visible = False
        self._target_uid = None

    def _execute_stage(self, target: CardSprite) -> None:
        sprite = self.pending_spell
        if sprite is None:
            return
        target_uid = target.card.uid
        self.pending_spell = None
        self.btn_cancel.visible = False
        self._target_uid = None
        self._place_played_sprite(sprite, owner=0)
        self.app.audio.ui_sound("play")
        self.controller.play_card(sprite.card.uid, target_uid)

    # ------------------------------------------------------------ target lookup
    def _spell_target_under(self, pos: tuple[int, int],
                            card: CardInstance) -> Optional[CardSprite]:
        legal = {c.uid for c in self.match.valid_targets(0, card)}
        for sprite in reversed(self.opp_board):
            if sprite.card.uid in legal and \
                    sprite.rect(self.board_card_size).collidepoint(pos):
                return sprite
        return None

    def _attack_target_under(self, pos: tuple[int, int]) -> Optional[CardSprite]:
        legal = {c.uid for c in self.match.valid_attack_targets(0)}
        for sprite in reversed(self.opp_board):
            if sprite.card.uid in legal and \
                    sprite.rect(self.board_card_size).collidepoint(pos):
                return sprite
        champ = self.opp_champ
        if champ is not None and champ.card.uid in legal and \
                champ.rect(self.champ_size).collidepoint(pos):
            return champ
        return None

    # ------------------------------------------------------------ hover helpers
    def _field_sprites(self) -> list[CardSprite]:
        sprites = self.opp_relics + self.relics + self.opp_board + self.board
        if self.opp_champ is not None:
            sprites.append(self.opp_champ)
        if self.champ is not None:
            sprites.append(self.champ)
        return sprites

    def _sprite_size(self, sprite: CardSprite) -> tuple[int, int]:
        if sprite in (self.champ, self.opp_champ):
            return self.champ_size
        if sprite in self.relics or sprite in self.opp_relics:
            return self.relic_size
        if sprite in self.hand or sprite is self.pending_spell:
            return self.card_size
        return self.board_card_size

    def _field_sprite_under(self, pos: tuple[int, int]) -> Optional[CardSprite]:
        for sprite in reversed(self._field_sprites()):
            if sprite.rect(self._sprite_size(sprite)).collidepoint(pos):
                return sprite
        return None

    def _hand_sprite_under(self, pos: tuple[int, int]) -> Optional[CardSprite]:
        for sprite in reversed(self.hand):
            if sprite.rect(self.card_size).collidepoint(pos):
                return sprite
        return None

    def _own_creature_under(self, pos: tuple[int, int]) -> Optional[CardSprite]:
        for sprite in reversed(self.board):
            if sprite.rect(self.board_card_size).collidepoint(pos):
                return sprite
        return None

    # ------------------------------------------------------------ frame
    def _ask_leave(self) -> None:
        self._confirm_leave = True

    def _dismiss_leave(self) -> None:
        self._confirm_leave = False

    def handle_event(self, event: pygame.event.Event) -> None:
        if self._confirm_leave:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._dismiss_leave()
            else:
                self.btn_leave_yes.handle_event(event)
                self.btn_leave_no.handle_event(event)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self.pending_spell is not None:
                self._cancel_stage()
                return
            self._cancel_drag()
            self.attack_source = None
            self._ask_leave()
            return
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if self.result is not None:
            return

        # ---- targeting mode for a staged spell -----------------------------
        if self.pending_spell is not None:
            if event.type == pygame.MOUSEMOTION:
                target = self._spell_target_under(event.pos, self.pending_spell.card)
                self._target_uid = target.card.uid if target else None
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                target = self._spell_target_under(event.pos, self.pending_spell.card)
                if target is not None:
                    self._execute_stage(target)
                else:
                    self._show_toast("Click an enemy creature — or Cancel.")
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                self._cancel_stage()
            return

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._pinned = None
            sprite = self._hand_sprite_under(event.pos)
            if sprite is not None:
                self._begin_drag(sprite, event.pos)
                return
            mine = self._own_creature_under(event.pos)
            if mine is not None and self.match.is_local_turn():
                if self.match.phase is Phase.COMBAT:
                    ok, reason = self.match.can_attack(0, mine.card.uid)
                    if ok:
                        self.attack_source = mine
                    else:
                        self._show_toast(reason)
                elif self.match.phase is Phase.MAIN:
                    self._show_toast("Creatures attack during your combat phase.")
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            sprite = self._field_sprite_under(event.pos)
            if sprite is not None:
                self._pinned = (sprite.card, sprite.rect(self._sprite_size(sprite)))
        elif event.type == pygame.MOUSEMOTION:
            if self.drag is not None:
                self.drag.x = event.pos[0] + self._drag_dx
                self.drag.y = event.pos[1] + self._drag_dy
                if event.pos[1] >= self.play_line:
                    new_index = self._hand_index_for_x(self.drag.x)
                    others = [s for s in self.hand if s is not self.drag]
                    others.insert(new_index, self.drag)
                    self.hand[:] = others
                    model_hand = self.match.player(0).hand
                    card = self.drag.card
                    if card in model_hand and model_hand.index(card) != new_index:
                        self.match.move_in_hand(0, card.uid, new_index)
            elif self.attack_source is not None:
                target = self._attack_target_under(event.pos)
                self._target_uid = target.card.uid if target else None
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.drag is not None:
                self._release_drag(event.pos)
            elif self.attack_source is not None:
                attacker = self.attack_source
                self.attack_source = None
                self._target_uid = None
                target = self._attack_target_under(event.pos)
                if target is None:
                    champ = self.opp_champ
                    if (champ is not None and self.match.player(1).board
                            and champ.rect(self.champ_size).collidepoint(event.pos)):
                        self._guard_flash = 1.0   # creatures block the champion
                        self._show_toast("Enemy creatures must be dealt with first.")
                    return                       # arrow released on nothing
                ok, reason = self.match.can_attack(0, attacker.card.uid)
                if not ok:
                    self._show_toast(reason)
                    return
                self.controller.attack(attacker.card.uid, target.card.uid)

    def update(self, dt: float) -> None:
        self._time += dt
        self._run_timers(dt)
        self._pump_controller(dt)
        self._toast_timer = max(0.0, self._toast_timer - dt)
        self._guard_flash = max(0.0, self._guard_flash - dt / 0.9)

        self._update_targets()
        mouse = pygame.mouse.get_pos()
        busy_pointer = (self.drag is not None or self.pending_spell is not None
                        or self.attack_source is not None)
        hand_hover = self._hand_sprite_under(mouse) if not busy_pointer else None
        self._field_hover = (self._field_sprite_under(mouse)
                             if not busy_pointer else None)

        everything = (self.hand + self.board + self.opp_board + self.relics
                      + self.opp_relics + self.effects)
        if self.pending_spell is not None:
            everything.append(self.pending_spell)
        if self.champ is not None:
            everything.append(self.champ)
        if self.opp_champ is not None:
            everything.append(self.opp_champ)
        for sprite in everything:
            sprite.hover = sprite is hand_hover or sprite is self._field_hover
            if sprite in self.hand and not sprite.dragging:
                sprite.tscale = 1.08 if sprite.hover else 1.0
            sprite.update(dt)
        self.effects[:] = [s for s in self.effects if not s.gone]

        for ft in self.floats:
            ft.update(dt)
        self.floats[:] = [f for f in self.floats if not f.gone]

        local = self.match.is_local_turn()
        if self.result is not None:
            self.btn_turn.label = "Match Over"
        elif local and self.match.phase is Phase.MAIN:
            self.btn_turn.label = "To Combat"
        elif local and self.match.phase is Phase.COMBAT:
            self.btn_turn.label = "End Turn"
        else:
            self.btn_turn.label = "Enemy Turn"
        self.btn_turn.enabled = (self.result is None and local
                                 and not self._flow_busy
                                 and self.match.phase in (Phase.MAIN, Phase.COMBAT))

        if self._confirm_leave:
            self.btn_leave_yes.update(dt)
            self.btn_leave_no.update(dt)
        for widget in self.widgets:
            widget.update(dt)
        apply_cursor(self.widgets,
                     force_hand=(hand_hover is not None or busy_pointer
                                 or self._field_hover is not None))

    def _update_targets(self) -> None:
        w, _h = self.app.screen.get_size()
        n = len(self.hand)
        if n:
            spacing = min(int(self.card_size[0] * 0.74), int((w * 0.55) / max(1, n)))
            start = w // 2 - spacing * (n - 1) // 2
            for i, sprite in enumerate(self.hand):
                sprite.tx = start + i * spacing
                sprite.ty = self.hand_y - (28 if sprite.hover and not sprite.dragging else 0)
        self._row_targets(self.board, self.own_row_y, self.board_card_size,
                          self.creature_span)
        self._row_targets(self.opp_board, self.opp_row_y, self.board_card_size,
                          self.creature_span)
        self._relic_targets(self.relics, self.relic_anchor)
        self._relic_targets(self.opp_relics, self.opp_relic_anchor)
        if self.champ is not None:
            self.champ.tx, self.champ.ty = self.champ_pos
        if self.opp_champ is not None:
            self.opp_champ.tx, self.opp_champ.ty = self.opp_champ_pos
        if self.pending_spell is not None:
            self.pending_spell.tx, self.pending_spell.ty = self.stage_pos

    def _row_targets(self, sprites: list[CardSprite], y: int,
                     size: tuple[int, int], span: tuple[int, int]) -> None:
        n = len(sprites)
        if not n:
            return
        x0, x1 = span
        center = (x0 + x1) // 2
        spacing = min(size[0] + 14, (x1 - x0) // max(1, n))
        start = center - spacing * (n - 1) // 2
        for i, sprite in enumerate(sprites):
            sprite.tx = start + i * spacing
            sprite.ty = y

    def _relic_targets(self, sprites: list[CardSprite],
                       anchor: tuple[int, int]) -> None:
        spacing = self.relic_size[0] * 0.55
        for i, sprite in enumerate(sprites):
            sprite.tx = anchor[0] + i * spacing
            sprite.ty = anchor[1] - i * 6

    # ------------------------------------------------------------ drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        self._draw_zone_hints(surface)
        self._draw_opponent(surface)
        self._draw_mana(surface)
        self._draw_phase_tracker(surface)
        self._draw_deck(surface)

        for sprite in self.opp_relics + self.relics:
            self._draw_card(surface, sprite, self.relic_size, compact=True)
        if self.opp_champ is not None:
            targeted = (self.attack_source is not None
                        and self._target_uid == self.opp_champ.card.uid)
            self._draw_card(surface, self.opp_champ, self.champ_size,
                            targeted=targeted)
        if self.champ is not None:
            self._draw_card(surface, self.champ, self.champ_size)
        for sprite in self.opp_board:
            self._draw_card(surface, sprite, self.board_card_size,
                            targeted=(sprite.card.uid == self._target_uid))
        for sprite in self.board:
            ready = (self.match.is_local_turn()
                     and self.match.phase is Phase.COMBAT
                     and self.match.can_attack(0, sprite.card.uid)[0])
            self._draw_card(surface, sprite, self.board_card_size, playable=ready)
        for sprite in self.effects:
            self._draw_card(surface, sprite, self.board_card_size)
        for sprite in self.hand:
            playable = (not self.drag and not self.pending_spell
                        and self.match.can_play(0, sprite.card.uid)[0])
            self._draw_card(surface, sprite, self.card_size, playable=playable)
        if self.pending_spell is not None:
            self._draw_card(surface, self.pending_spell, self.card_size)

        self._draw_arrows(surface)
        for widget in self.widgets:
            widget.draw(surface)
        self._draw_floats(surface)
        self._draw_preview(surface)
        self._draw_toast(surface)
        if self._confirm_leave:
            dw, dh = surface.get_size()
            veil = pygame.Surface((dw, dh), pygame.SRCALPHA)
            veil.fill((*theme.NAVY_ABYSS, 190))
            surface.blit(veil, (0, 0))
            box = pygame.Rect(0, 0, int(470 * self.s), int(170 * self.s))
            box.center = (dw // 2, dh // 2)
            theme.draw_glow_rect(surface, box, theme.GOLD, 0.35, radius=14,
                                 spread=14)
            theme.draw_panel(surface, box, fill=theme.NAVY,
                             border=theme.GOLD_DIM, radius=14)
            theme.draw_text(surface,
                            "Are you sure you want to leave the match?",
                            (box.centerx, box.y + int(44 * self.s)),
                            theme.body_font(int(17 * self.s), bold=True),
                            theme.TEXT, anchor="center")
            self.btn_leave_yes.draw(surface)
            self.btn_leave_no.draw(surface)
        self._draw_result(surface)

    def _draw_arrows(self, surface: pygame.Surface) -> None:
        mouse = pygame.mouse.get_pos()
        if self.pending_spell is not None:
            draw_arrow(surface, (self.pending_spell.x, self.pending_spell.y),
                       mouse, ARROW_SPELL, self._time)
        elif self.attack_source is not None:
            draw_arrow(surface, (self.attack_source.x, self.attack_source.y),
                       mouse, ARROW_ATTACK, self._time)

    def _draw_zone_hints(self, surface: pygame.Surface) -> None:
        pulse = 0.28 + 0.10 * math.sin(self._time * 5)
        if self._guard_flash > 0.01:               # "the champion is guarded"
            blink = 0.5 + 0.5 * math.sin(self._time * 16)
            for sprite in self.opp_board:
                theme.draw_glow_rect(surface, sprite.rect(self.board_card_size),
                                     HEALTH_RED, self._guard_flash * blink,
                                     radius=10, spread=10)
        # staged spell / combat arrow: pulse legal targets
        if self.pending_spell is not None:
            legal = {c.uid for c in self.match.valid_targets(0, self.pending_spell.card)}
            for sprite in self.opp_board:
                if sprite.card.uid in legal:
                    strong = sprite.card.uid == self._target_uid
                    theme.draw_glow_rect(surface, sprite.rect(self.board_card_size),
                                         ARROW_SPELL, 0.8 if strong else pulse,
                                         radius=10, spread=9)
            return
        if self.attack_source is not None:
            legal = {c.uid for c in self.match.valid_attack_targets(0)}
            for sprite in self.opp_board:
                if sprite.card.uid in legal:
                    strong = sprite.card.uid == self._target_uid
                    theme.draw_glow_rect(surface, sprite.rect(self.board_card_size),
                                         ARROW_ATTACK, 0.8 if strong else pulse,
                                         radius=10, spread=9)
            champ = self.opp_champ
            if champ is not None and champ.card.uid in legal:
                strong = champ.card.uid == self._target_uid
                theme.draw_glow_rect(surface, champ.rect(self.champ_size),
                                     ARROW_ATTACK, 0.8 if strong else pulse,
                                     radius=12, spread=10)
            return
        if self.drag is None:
            return
        card = self.drag.card
        ok, _ = self.match.can_play(0, card.uid)
        color = theme.GOLD if ok else theme.DANGER
        if card.needs_target:
            zone = pygame.Rect(0, 0, int(self.card_size[0] * 1.6),
                               int(self.card_size[1] * 1.3))
            zone.center = self.stage_pos
        elif card.kind is Kind.RELIC:
            zone = pygame.Rect(0, 0, int(self.relic_size[0] * 2.4),
                               int(self.relic_size[1] * 1.5))
            zone.center = self.relic_anchor
        else:
            zone = pygame.Rect(self.creature_span[0],
                               self.own_row_y - self.board_card_size[1] // 2 - 12,
                               self.creature_span[1] - self.creature_span[0],
                               self.board_card_size[1] + 24)
        theme.draw_glow_rect(surface, zone, color, pulse, radius=16, spread=10)

    # -- card faces ----------------------------------------------------------
    def _draw_card(self, surface: pygame.Surface, sprite: CardSprite,
                   size: tuple[int, int], playable: bool = False,
                   targeted: bool = False, compact: bool = False) -> None:
        rect = sprite.rect(size)
        if rect.width < 8:
            return
        if sprite.dying:
            # dissolve: render the face offscreen, blit with fading alpha
            temp = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            self._draw_card_face(temp, pygame.Rect(0, 0, rect.width, rect.height),
                                 sprite, playable=False, targeted=False,
                                 compact=compact, hover=False)
            temp.set_alpha(sprite.alpha)
            surface.blit(temp, rect.topleft)
            return
        lift = 6 if (sprite.dragging or sprite.drop is not None
                     or sprite.lunge is not None) else 3
        shadow = pygame.Surface((rect.width + lift * 3, rect.height + lift * 3),
                                pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 90), shadow.get_rect(), border_radius=10)
        surface.blit(shadow, (rect.x - lift, rect.y + lift))
        if playable:
            pulse = 0.35 + 0.15 * math.sin(self._time * 4 + sprite.card.uid)
            theme.draw_glow_rect(surface, rect, theme.GOLD_GLOW, pulse,
                                 radius=10, spread=7)
        if targeted:
            theme.draw_glow_rect(surface, rect, HEALTH_RED, 0.8, radius=10, spread=9)
        self._draw_card_face(surface, rect, sprite, playable, targeted, compact,
                             hover=sprite.hover)

    def _draw_card_face(self, surface: pygame.Surface, rect: pygame.Rect,
                        sprite: CardSprite, playable: bool, targeted: bool,
                        compact: bool, hover: bool) -> None:
        card = sprite.card
        is_champ = card.kind is Kind.CHAMPION
        border = (theme.GOLD if (is_champ or playable or hover or targeted)
                  else theme.NAVY_EDGE)
        theme.draw_panel(surface, rect, fill=theme.NAVY_RAISED, border=border,
                         radius=10)
        if is_champ:
            pygame.draw.rect(surface, theme.GOLD_DIM, rect.inflate(-6, -6),
                             width=1, border_radius=8)
        s = rect.width / 118
        if not is_champ:
            gem_r = int(14 * s)
            gem_c = (rect.x + gem_r + int(5 * s), rect.y + gem_r + int(5 * s))
            pygame.draw.circle(surface, MANA_FILL, gem_c, gem_r)
            pygame.draw.circle(surface, MANA_CORE, gem_c, gem_r, width=2)
            theme.draw_text(surface, str(card.cost), gem_c,
                            theme.body_font(max(10, int(15 * s)), bold=True),
                            theme.TEXT, anchor="center")
        theme.draw_text(surface, card.name if not compact else card.name.split()[0],
                        (rect.centerx + int(6 * s), rect.y + int(12 * s)),
                        theme.body_font(max(9, int(11 * s))), theme.TEXT_DIM,
                        anchor="midtop")
        art = pygame.Rect(rect.x + int(9 * s), rect.y + int(30 * s),
                          rect.width - int(18 * s), int(58 * s))
        theme.draw_panel(surface, art, fill=theme.NAVY, border=theme.NAVY_EDGE,
                         radius=6)
        cx, cy = art.centerx, art.centery
        for angle in range(0, 360, 45):
            end = (cx + int(13 * s * math.cos(math.radians(angle))),
                   cy + int(13 * s * math.sin(math.radians(angle))))
            pygame.draw.line(surface, theme.GOLD_DIM, (cx, cy), end)

        if card.kind is Kind.CREATURE and card.sick and not sprite.dying:
            self._draw_summon_swirl(surface, rect)
        if card.kind is Kind.CREATURE:
            theme.draw_text(surface, str(card.attack),
                            (rect.x + int(13 * s), rect.bottom - int(14 * s)),
                            theme.body_font(max(10, int(16 * s)), bold=True),
                            theme.GOLD_BRIGHT, anchor="center")
            health_color = HEALTH_RED if card.damaged else theme.SUCCESS
            theme.draw_text(surface, str(max(0, card.health)),
                            (rect.right - int(13 * s), rect.bottom - int(14 * s)),
                            theme.body_font(max(10, int(16 * s)), bold=True),
                            health_color, anchor="center")
            if card.exhausted and not sprite.dying:
                veil = pygame.Surface(rect.size, pygame.SRCALPHA)
                pygame.draw.rect(veil, (8, 12, 26, 120), veil.get_rect(),
                                 border_radius=10)
                surface.blit(veil, rect.topleft)
        elif is_champ:
            heart = (rect.centerx, rect.bottom - int(15 * s))
            pygame.draw.circle(surface, HEALTH_RED, heart, int(14 * s))
            pygame.draw.circle(surface, (150, 60, 60), heart, int(14 * s), width=1)
            theme.draw_text(surface, str(max(0, card.health)), heart,
                            theme.body_font(max(10, int(14 * s)), bold=True),
                            theme.TEXT, anchor="center")
        elif not compact:
            theme.draw_text(surface, KIND_LABELS[card.kind],
                            (rect.centerx, rect.bottom - int(13 * s)),
                            theme.body_font(max(9, int(10 * s))),
                            theme.TEXT_FAINT, anchor="center")

    def _draw_summon_swirl(self, surface: pygame.Surface,
                           rect: pygame.Rect) -> None:
        """Cosmic swirl: star motes orbiting a creature still materializing."""
        veil = pygame.Surface(rect.size, pygame.SRCALPHA)
        wave = int(26 + 10 * math.sin(self._time * 2.4))
        pygame.draw.rect(veil, (110, 160, 255, wave), veil.get_rect(),
                         border_radius=10)
        surface.blit(veil, rect.topleft)
        cx, cy = rect.center
        rx, ry = rect.width * 0.60, rect.height * 0.52
        motes = 7
        for i in range(motes):
            a = self._time * 2.1 + i * (math.tau / motes)
            wobble = math.sin(self._time * 3.0 + i * 1.7) * 3
            x = cx + rx * math.cos(a)
            y = cy + ry * math.sin(a) * 0.92 + wobble
            color = MANA_CORE if i % 2 else theme.GOLD_GLOW
            pygame.draw.circle(surface, color, (int(x), int(y)), 3)
            trail = a - 0.22
            pygame.draw.circle(surface, color,
                               (int(cx + rx * math.cos(trail)),
                                int(cy + ry * math.sin(trail) * 0.92)), 1)

    def _draw_card_back(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        from arcanum.ui import cardback
        image = cardback.get(rect.size)
        if image is not None:
            surface.blit(image, rect)
            return
        theme.draw_panel(surface, rect, fill=theme.NAVY, border=theme.GOLD_DIM,
                         radius=6)
        pygame.draw.circle(surface, theme.GOLD_DIM, rect.center,
                           max(3, rect.width // 5), width=1)

    def _draw_floats(self, surface: pygame.Surface) -> None:
        for ft in self.floats:
            alpha = int(255 * min(1.0, ft.life / 0.7))
            theme.draw_text(surface, ft.text, (int(ft.x), int(ft.y)),
                            theme.body_font(22, bold=True), ft.color,
                            anchor="center", alpha=alpha)

    # -- preview ---------------------------------------------------------------
    def _draw_preview(self, surface: pygame.Surface) -> None:
        pinned = self._pinned
        if pinned is not None:
            card, anchor = pinned
        elif getattr(self, "_field_hover", None) is not None:
            sprite = self._field_hover
            card = sprite.card
            anchor = sprite.rect(self._sprite_size(sprite))
        else:
            return
        w, h = surface.get_size()
        pw, ph = int(230 * self.ui_scale), int(322 * self.ui_scale)
        x = anchor.right + 18
        if x + pw > w - 12:
            x = anchor.x - pw - 18
        y = max(12, min(anchor.centery - ph // 2, h - ph - 12))
        rect = pygame.Rect(x, y, pw, ph)
        theme.draw_glow_rect(surface, rect, theme.GOLD, 0.4, radius=12, spread=10)
        theme.draw_panel(surface, rect, fill=theme.NAVY, border=theme.GOLD_DIM,
                         radius=12)
        pad = int(16 * self.ui_scale)
        theme.draw_text(surface, card.name, (rect.centerx, rect.y + pad),
                        theme.display_font(int(20 * self.ui_scale)),
                        theme.GOLD_BRIGHT, anchor="midtop")
        subtitle = KIND_LABELS[card.kind]
        if card.kind is not Kind.CHAMPION:
            subtitle += f"  ·  Cost {card.cost}"
        if card.kind is Kind.CREATURE and card.exhausted:
            subtitle += "  ·  exhausted"
        if card.kind is Kind.CREATURE and card.sick:
            subtitle += "  ·  summoning"
        theme.draw_text(surface, subtitle,
                        (rect.centerx, rect.y + pad + int(28 * self.ui_scale)),
                        theme.body_font(int(13 * self.ui_scale)),
                        theme.TEXT_DIM, anchor="midtop")
        art = pygame.Rect(rect.x + pad, rect.y + int(72 * self.ui_scale),
                          rect.width - pad * 2, int(110 * self.ui_scale))
        theme.draw_panel(surface, art, fill=theme.NAVY_RAISED,
                         border=theme.NAVY_EDGE, radius=8)
        for angle in range(0, 360, 30):
            end = (art.centerx + int(28 * math.cos(math.radians(angle))),
                   art.centery + int(28 * math.sin(math.radians(angle))))
            pygame.draw.line(surface, theme.GOLD_DIM, art.center, end)
        font = theme.body_font(int(14 * self.ui_scale))
        words, lines, line = card.text.split(), [], ""
        for word in words:
            test = f"{line} {word}".strip()
            if font.size(test)[0] > rect.width - pad * 2 and line:
                lines.append(line)
                line = word
            else:
                line = test
        if line:
            lines.append(line)
        ty = art.bottom + int(14 * self.ui_scale)
        for text_line in lines[:4]:
            theme.draw_text(surface, text_line, (rect.centerx, ty), font,
                            theme.TEXT, anchor="midtop")
            ty += font.get_linesize()
        if card.kind is Kind.CREATURE:
            stats = f"{card.attack} / {max(0, card.health)}"
            if card.damaged:
                stats += f"  (of {card.max_health})"
            theme.draw_text(surface, stats, (rect.centerx, rect.bottom - pad),
                            theme.body_font(int(18 * self.ui_scale), bold=True),
                            theme.GOLD_BRIGHT, anchor="midbottom")
        elif card.kind is Kind.CHAMPION:
            theme.draw_text(surface, f"{max(0, card.health)} Health",
                            (rect.centerx, rect.bottom - pad),
                            theme.body_font(int(16 * self.ui_scale), bold=True),
                            HEALTH_RED, anchor="midbottom")
        if pinned is not None:
            theme.draw_text(surface, "left-click to close",
                            (rect.centerx, rect.bottom + 8),
                            theme.body_font(int(11 * self.ui_scale)),
                            theme.TEXT_FAINT, anchor="midtop")

    # -- HUD ---------------------------------------------------------------
    def _draw_opponent(self, surface: pygame.Surface) -> None:
        w = surface.get_width()
        cx, cy = w // 2, 34
        pygame.draw.circle(surface, theme.NAVY_RAISED, (cx, cy), 24)
        pygame.draw.circle(surface,
                           theme.GOLD if not self.match.is_local_turn()
                           else theme.NAVY_EDGE, (cx, cy), 24, width=2)
        theme.draw_text(surface, self.match.player(1).name, (cx, cy + 38),
                        theme.body_font(14), theme.TEXT_DIM, anchor="center")
        spacing = 22
        start = cx + 60
        for i in range(self.opp_hand_count):
            self._draw_card_back(surface, pygame.Rect(start + i * spacing, 12, 30, 42))

    def _draw_deck(self, surface: pygame.Surface) -> None:
        rect = pygame.Rect(0, 0, int(self.card_size[0] * 0.55),
                           int(self.card_size[1] * 0.55))
        rect.center = self.deck_pos
        self._draw_card_back(surface, rect)

    def _energy_icons(self, size: int):
        """Three cached states of the energy crystal: bright (available),
        grey (spent this turn), silhouette (not yet unlocked)."""
        cached = getattr(self, "_energy_cache", None)
        if cached and cached[0] == size:
            return cached[1]
        base = None
        for path in (IMAGES_DIR / "energy.png", ROOT_DIR / "energy.png"):
            if path.is_file():
                try:
                    base = pygame.image.load(str(path)).convert_alpha()
                    break
                except pygame.error:
                    pass
        if base is None:
            self._energy_cache = (size, None)
            return None
        bright = pygame.transform.smoothscale(base, (size, size))
        # spent: desaturated + dimmed
        spent = bright.copy()
        grey = pygame.Surface((size, size), pygame.SRCALPHA)
        grey.fill((120, 120, 130, 0))
        spent.blit(grey, (0, 0), special_flags=pygame.BLEND_RGB_MIN)
        spent.set_alpha(150)
        # locked: near-black silhouette
        locked = bright.copy()
        dark = pygame.Surface((size, size), pygame.SRCALPHA)
        dark.fill((16, 22, 40, 0))
        locked.blit(dark, (0, 0), special_flags=pygame.BLEND_RGB_MIN)
        locked.set_alpha(105)
        icons = {"bright": bright, "spent": spent, "locked": locked}
        self._energy_cache = (size, icons)
        return icons

    def _draw_mana(self, surface: pygame.Surface) -> None:
        player = self.match.player(0)
        h = surface.get_height()
        size = 36
        step = size + 6
        x = 34
        top = h // 2 - (MAX_MANA * step) // 2
        theme.draw_text(surface, f"{player.mana}/{player.max_mana}",
                        (x + size // 2, top - 28),
                        theme.body_font(20, bold=True), MANA_CORE,
                        anchor="center")
        icons = self._energy_icons(size)
        for i in range(MAX_MANA):
            cy = top + i * step
            if icons is not None:
                if i < player.mana:
                    icon = icons["bright"]
                elif i < player.max_mana:
                    icon = icons["spent"]
                else:
                    icon = icons["locked"]
                surface.blit(icon, (x - size // 2 + size // 2 - size // 2, cy))
            else:                                  # fallback: old diamonds
                mid = cy + size // 2
                points = [(x + 8, mid - 8), (x + 16, mid), (x + 8, mid + 8),
                          (x, mid)]
                if i < player.mana:
                    pygame.draw.polygon(surface, MANA_FILL, points)
                elif i < player.max_mana:
                    pygame.draw.polygon(surface, MANA_SPENT, points)
                else:
                    pygame.draw.polygon(surface, theme.NAVY_EDGE, points,
                                        width=1)

    def _draw_phase_tracker(self, surface: pygame.Surface) -> None:
        w, h = surface.get_size()
        x = w - 116
        top = int(h * 0.15)
        local = self.match.is_local_turn()
        theme.draw_text(surface, "Your Turn" if local else "Enemy Turn",
                        (x, top - 34), theme.body_font(16, bold=True),
                        theme.GOLD_BRIGHT if local else theme.TEXT_DIM,
                        anchor="center")
        theme.draw_text(surface, f"Turn {self.match.turn_number}", (x, top - 12),
                        theme.body_font(13), theme.TEXT_FAINT, anchor="center")
        for i, phase in enumerate(Phase):
            y = top + 22 + i * 34
            active = phase is self.match.phase
            if active:
                marker = pygame.Rect(x - 54, y - 14, 108, 28)
                theme.draw_panel(surface, marker, fill=theme.NAVY_RAISED,
                                 border=theme.GOLD_DIM, radius=8)
            theme.draw_text(surface, PHASE_LABELS[phase], (x, y),
                            theme.body_font(15, bold=active),
                            theme.GOLD_BRIGHT if active else theme.TEXT_FAINT,
                            anchor="center")

    def _draw_toast(self, surface: pygame.Surface) -> None:
        if self._toast_timer <= 0 or not self._toast:
            return
        w, _h = surface.get_size()
        fade = min(1.0, self._toast_timer / 0.4)
        box = pygame.Rect(0, 0, min(w - 80, 520), 42)
        box.midbottom = (w // 2, self.hand_y - int(self.card_size[1] * 0.75))
        veil = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(235 * fade)),
                         veil.get_rect(), border_radius=10)
        pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                         veil.get_rect(), width=1, border_radius=10)
        surface.blit(veil, box.topleft)
        theme.draw_text(surface, self._toast, box.center, theme.body_font(15),
                        theme.TEXT, anchor="center", alpha=int(255 * fade))

    def _draw_result(self, surface: pygame.Surface) -> None:
        if self.result is None:
            return
        w, h = surface.get_size()
        veil = pygame.Surface((w, h), pygame.SRCALPHA)
        veil.fill((4, 7, 18, 170))
        surface.blit(veil, (0, 0))
        won = self.result == "victory"
        theme.draw_text(surface, "VICTORY" if won else "DEFEAT",
                        (w // 2, int(h * 0.40)),
                        theme.display_font(58, bold=True),
                        theme.GOLD_BRIGHT if won else HEALTH_RED, anchor="center")
        theme.gold_gradient_rule(surface, (w // 2, int(h * 0.40) + 44), 280)
        theme.draw_text(surface,
                        "The enemy champion has fallen." if won
                        else "Your champion has fallen.",
                        (w // 2, int(h * 0.40) + 66),
                        theme.body_font(16), theme.TEXT_DIM, anchor="center")
        self.btn_home.draw(surface)
