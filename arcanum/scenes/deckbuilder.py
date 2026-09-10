"""Deck Builder — browse the whole library, shape a deck, save it to the cloud.

Left: the library. Search name or rules text; filter by CARD TYPE (all six —
heroes are heroes here, not "creatures"), EXACT cost (0-9, 10+), and rarity;
sort by cost, name, or rarity. Cards render as portrait thumbnails using the
real published card image when it's cached, with a styled mini-card fallback.
Champions, minions, and barriers appear in the browser but are locked until
their zones land in the engine.

Right: the working deck. Live count with legality color, per-cost mana curve
with counts, an unsaved-changes indicator, and Save / New / Delete plus the
saved-decks loader.

Left-click adds a copy · click a deck row to remove one · right-click any
card to pin a full-size inspect view · hover for a quick preview ·
mouse-wheel scrolls the grid.
"""
from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Optional

import pygame

from arcanum.core.scene import Scene
from arcanum.game import catalog as cat
from arcanum.game.catalog import DECK_SIZE, CardDef, max_copies, validate_deck
from arcanum.game.keywords import CardType, Rarity
from arcanum.game.match import Kind
from arcanum.services import cardimages
from arcanum.services import cards as card_library
from arcanum.services.decks import DeckRecord, DeckResult
from arcanum.ui import theme
from arcanum.ui.widgets import Button, Dropdown, TextInput, apply_cursor

log = logging.getLogger(__name__)

MANA_FILL = (86, 156, 255)
MANA_CORE = (170, 210, 255)
RARITY_COLORS = {Rarity.COMMON: (200, 205, 216),
                 Rarity.UNCOMMON: (96, 214, 140),
                 Rarity.RARE: MANA_FILL,
                 Rarity.EPIC: (186, 106, 255),
                 Rarity.LEGENDARY: theme.GOLD_BRIGHT}
RARITY_TINT = {Rarity.COMMON: (26, 32, 52), Rarity.UNCOMMON: (20, 40, 32),
               Rarity.RARE: (18, 30, 56), Rarity.EPIC: (34, 22, 54),
               Rarity.LEGENDARY: (44, 36, 16)}

TYPE_FILTERS = ["All types", "Heroes", "Champions", "Spells", "Relics",
                "Barriers"]
TYPE_KEYS = [None, "hero", "champion", "spell", "relic", "barrier"]
COST_FILTERS = ["Any cost"] + [str(i) for i in range(10)] + ["10+"]
RARITY_FILTERS = ["All rarities"] + [r.value.title() for r in Rarity]
SORTS = ["Sort: Cost", "Sort: Name", "Sort: Rarity"]

_KIND_TO_TYPE = {Kind.CREATURE: "hero", Kind.SPELL: "spell",
                 Kind.RELIC: "relic"}
RARITY_INDEX = {r: i for i, r in enumerate(Rarity)}


@dataclass
class BrowseCard:
    """One library entry, whatever its type — playable or awaiting engine v2."""
    card_id: str
    name: str
    type_key: str            # hero / champion / minion / spell / relic / barrier
    cost: int
    rarity: Rarity
    text: str
    attack: int = 0
    health: int = 0
    durability: int = 0
    playable: bool = True

    @property
    def type_label(self) -> str:
        return self.type_key.title()

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        return not q or q in self.name.lower() or q in self.text.lower()

    def stats_text(self) -> str:
        if self.type_key in ("hero", "minion"):
            return f"{self.attack}/{self.health}"
        if self.type_key == "champion":
            return f"♥{self.health}"
        if self.type_key == "barrier":
            return f"⛨{self.durability}"
        return ""


def build_library() -> list[BrowseCard]:
    """Everything deck-buildable: heroes, spells, relics, champions,
    barriers — built-in and published. Minions are tokens, never cards."""
    out: list[BrowseCard] = []
    seen: set[str] = set()
    for card in cat.all_cards():
        spec = cat.spec_by_id(card.card_id)
        type_key = spec.card_type.value if spec else \
            _KIND_TO_TYPE.get(card.kind, "hero")
        out.append(BrowseCard(card.card_id, card.name, type_key, card.cost,
                              card.rarity, card.text, card.attack,
                              card.health, playable=True))
        seen.add(card.card_id)
    for spec in cat.all_specs():
        if spec.id in seen:
            continue
        if spec.card_type is CardType.MINION:
            continue                       # tokens live on the board, not decks
        if spec.card_type in (CardType.CHAMPION, CardType.BARRIER):
            out.append(BrowseCard(spec.id, spec.name, spec.card_type.value,
                                  spec.cost, Rarity.parse(spec.rarity.value),
                                  spec.composed_text(), spec.attack,
                                  spec.health, spec.durability,
                                  playable=True))
    return out


def filter_cards(library: list[BrowseCard], query: str,
                 type_key: Optional[str], cost: Optional[str],
                 rarity: Optional[Rarity], sort: int) -> list[BrowseCard]:
    out = []
    for card in library:
        if type_key is not None and card.type_key != type_key:
            continue
        if cost is not None:
            if cost == "10+":
                if card.cost < 10:
                    continue
            elif card.cost != int(cost):
                continue
        if rarity is not None and card.rarity is not rarity:
            continue
        if not card.matches(query):
            continue
        out.append(card)
    if sort == 1:
        out.sort(key=lambda c: c.name.lower())
    elif sort == 2:
        out.sort(key=lambda c: (-RARITY_INDEX[c.rarity], c.cost, c.name))
    else:
        out.sort(key=lambda c: (c.cost, c.name.lower()))
    return out


class DeckBuilderScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self.library = build_library()
        self.collection = cat.full_collection()
        self.deck = DeckRecord(id="", name="New Deck", cards={})
        self.saved: list[DeckRecord] = []
        self.dirty = False
        self.scroll = 0.0
        self.busy = False
        self._results: list[tuple[str, DeckResult]] = []
        self._lock = threading.Lock()
        self._toast = ""
        self._toast_timer = 0.0
        self._time = 0.0
        self._hover: Optional[BrowseCard] = None
        self._pinned: Optional[BrowseCard] = None
        self._img_cache: dict = {}
        self._filtered: list[BrowseCard] = []
        self._filter_key = None
        self._lib_fingerprint = self._library_fingerprint()
        self._lib_poll = 0.0
        card_library.refresh()          # sync with the database on entry
        self._build()
        self._refresh_decks()

    @staticmethod
    def _library_fingerprint():
        return tuple(sorted(spec.id for spec in card_library.official_cards()))

    def _resync_library(self) -> None:
        self.library = build_library()
        self.collection = cat.full_collection()
        self._filter_key = None                    # force refilter
        self._img_cache.clear()

    def on_resize(self, size) -> None:
        if hasattr(self, "deck"):
            self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.3, h / 1080))
        self.s = s
        ui = self.app.audio.ui_sound

        self.panel_w = int(w * 0.25)
        top_bar = int(84 * s)
        self.grid_rect = pygame.Rect(int(24 * s), int(150 * s),
                                     w - self.panel_w - int(72 * s),
                                     h - int(180 * s))
        self.side_rect = pygame.Rect(w - self.panel_w - int(16 * s),
                                     int(20 * s), self.panel_w,
                                     h - int(44 * s))
        # portrait cells at card aspect
        self.cell_w = int(148 * s)
        self.cell_h = int(self.cell_w * 1477 / 1065)
        self.cols = max(2, (self.grid_rect.width + int(14 * s)) //
                        (self.cell_w + int(14 * s)))

        self.search = TextInput(pygame.Rect(int(24 * s), top_bar,
                                            int(270 * s), int(44 * s)),
                                placeholder="Search name or text...")
        x = int(310 * s)
        self.dd_type = Dropdown(pygame.Rect(x, top_bar, int(150 * s),
                                            int(44 * s)), TYPE_FILTERS)
        x += int(162 * s)
        self.dd_cost = Dropdown(pygame.Rect(x, top_bar, int(120 * s),
                                            int(44 * s)), COST_FILTERS)
        x += int(132 * s)
        self.dd_rarity = Dropdown(pygame.Rect(x, top_bar, int(150 * s),
                                              int(44 * s)), RARITY_FILTERS)
        x += int(162 * s)
        self.dd_sort = Dropdown(pygame.Rect(x, top_bar, int(140 * s),
                                            int(44 * s)), SORTS)
        self.lnk_back = Button(pygame.Rect(int(24 * s), int(20 * s),
                                           int(110 * s), int(42 * s)),
                               "Back", self._request_exit,
                               primary=False, font_size=16, sound_cb=ui)
        self._confirm_exit = False
        w0, h0 = self.app.screen.get_size()
        dw = int(150 * s)
        dy = h0 // 2 + int(48 * s)
        self.btn_exit_save = Button(pygame.Rect(0, 0, dw, int(46 * s)),
                                    "Save", self._exit_save, font_size=15,
                                    sound_cb=ui)
        self.btn_exit_discard = Button(pygame.Rect(0, 0, dw, int(46 * s)),
                                       "Discard", self._exit_discard,
                                       primary=False, font_size=15,
                                       sound_cb=ui)
        self.btn_exit_cancel = Button(pygame.Rect(0, 0, dw, int(46 * s)),
                                      "Cancel", self._exit_cancel,
                                      primary=False, font_size=15,
                                      sound_cb=ui)
        self.btn_exit_save.rect.center = (w0 // 2 - dw - int(14 * s), dy)
        self.btn_exit_discard.rect.center = (w0 // 2, dy)
        self.btn_exit_cancel.rect.center = (w0 // 2 + dw + int(14 * s), dy)

        px = self.side_rect.x + int(16 * s)
        pw = self.side_rect.width - int(32 * s)
        self.deck_name = TextInput(pygame.Rect(px,
                                               self.side_rect.y + int(52 * s),
                                               pw, int(42 * s)),
                                   placeholder="Deck name")
        self.deck_name.text = self.deck.name if hasattr(self, "deck") \
            else "New Deck"
        self.dd_saved = Dropdown(pygame.Rect(px,
                                             self.side_rect.y + int(102 * s),
                                             pw, int(40 * s)),
                                 ["My decks..."],
                                 on_change=self._on_pick_saved)
        self.btn_new = Button(pygame.Rect(px, self.side_rect.y + int(150 * s),
                                          pw, int(40 * s)), "+  New Deck",
                              self._new, primary=False, sound_cb=ui,
                              font_size=15)
        by = self.side_rect.bottom - int(60 * s)
        bw = (pw - int(8 * s)) // 2
        self.btn_save = Button(pygame.Rect(px, by, bw, int(44 * s)), "Save",
                               self._save, sound_cb=ui, font_size=17)
        self.btn_delete = Button(pygame.Rect(px + bw + int(8 * s), by, bw,
                                             int(44 * s)), "Delete",
                                 self._delete, primary=False, sound_cb=ui,
                                 font_size=17)
        self.widgets = [self.lnk_back, self.search, self.dd_type,
                        self.dd_cost, self.dd_rarity, self.dd_sort,
                        self.deck_name, self.dd_saved, self.btn_save,
                        self.btn_new, self.btn_delete]
        self.list_top = self.side_rect.y + int(204 * s)
        self.row_h = int(29 * s)
        self._filter_key = None            # force refilter after rebuild

    # -------------------------------------------------------------- filtering
    def _current_filters(self):
        type_key = TYPE_KEYS[self.dd_type.selected]
        cost = None if self.dd_cost.selected == 0 \
            else COST_FILTERS[self.dd_cost.selected]
        rarity = None if self.dd_rarity.selected == 0 \
            else list(Rarity)[self.dd_rarity.selected - 1]
        return (self.search.text, type_key, cost, rarity,
                self.dd_sort.selected)

    def _filtered_cards(self) -> list[BrowseCard]:
        key = self._current_filters()
        if key != self._filter_key:
            self._filter_key = key
            self._filtered = filter_cards(self.library, *key)
            self.scroll = min(self.scroll, self._max_scroll())
        return self._filtered

    # -------------------------------------------------------------- data
    def _refresh_decks(self) -> None:
        self.busy = True
        self.app.backend.deck_store.list_decks(
            lambda r: self._post_result("list", r))

    def _post_result(self, op: str, result: DeckResult) -> None:
        with self._lock:
            self._results.append((op, result))

    def _apply_result(self, op: str, result: DeckResult) -> None:
        self.busy = False
        if not result.ok:
            self._show_toast(result.error or "Something went wrong.")
            return
        if op == "list":
            self.saved = result.decks
            self.dd_saved.options = [
                f"My decks...  {len(self.saved)}/{self.MAX_DECKS}"] + [
                f"{d.name}  ({d.size})" for d in self.saved]
            self.dd_saved.selected = 0
        elif op == "save":
            if result.deck is not None:
                self.deck = result.deck
            self.dirty = False
            self._show_toast("Deck saved.")
            self._refresh_decks()
            if getattr(self, "_exit_after_save", False):
                self._exit_after_save = False
                self.app.scenes.pop()
        elif op == "delete":
            self._show_toast("Deck deleted.")
            self._new()
            self._refresh_decks()

    def _on_pick_saved(self, index: int) -> None:
        if index <= 0 or index > len(self.saved):
            return
        picked = self.saved[index - 1]
        self.deck = DeckRecord(picked.id, picked.name, dict(picked.cards))
        self.deck_name.text = picked.name
        self.dirty = False
        self._show_toast(f"Loaded '{picked.name}'.")

    # ------------------------------------------------------------ actions
    MAX_DECKS = 30

    def _save(self) -> None:
        if self.busy:
            return
        if not self.deck.id and len(self.saved) >= self.MAX_DECKS:
            self._show_toast(f"Account deck limit reached "
                             f"({self.MAX_DECKS}). Delete one first.")
            return
        self.deck.name = (self.deck_name.text.strip() or "Unnamed Deck")[:40]
        ok, reason = validate_deck(self.deck.cards, self.collection)
        if not ok:
            self._exit_after_save = False
            self._show_toast(reason)
            return
        self.busy = True
        self.app.backend.deck_store.save_deck(
            self.deck, lambda r: self._post_result("save", r))

    def _new(self) -> None:
        self.deck = DeckRecord(id="", name="New Deck", cards={})
        self.deck_name.text = self.deck.name
        self.dd_saved.selected = 0
        self.dirty = False

    def _delete(self) -> None:
        if self.busy or not self.deck.id:
            self._show_toast("This deck isn't saved yet.")
            return
        self.busy = True
        self.app.backend.deck_store.delete_deck(
            self.deck.id, lambda r: self._post_result("delete", r))

    def _add_card(self, card: BrowseCard) -> None:
        if not card.playable:
            self._show_toast(f"{card.type_label}s aren't playable yet — "
                             "coming with the next engine update.")
            return
        if card.type_key == "champion":
            current = next((cid for cid in self.deck.cards
                            if cat.card_type_of(cid) == "champion"), None)
            if current is not None and current != card.card_id:
                holder = self._browse_by_id(current)
                self._show_toast("A deck can only have one champion "
                                 f"({holder.name if holder else current} is "
                                 "already in this deck).")
                return
        have = self.deck.cards.get(card.card_id, 0)
        limit = min(max_copies(card.card_id),
                    self.collection.get(card.card_id, 0))
        if self.deck.size >= DECK_SIZE:
            self._show_toast(f"Deck is full ({DECK_SIZE}).")
            return
        if have >= limit:
            self._show_toast(f"No more copies of {card.name} (limit {limit}).")
            return
        self.deck.cards[card.card_id] = have + 1
        self.dirty = True
        self.app.audio.ui_sound("click")

    def _remove_card(self, card_id: str) -> None:
        have = self.deck.cards.get(card_id, 0)
        if have <= 0:
            return
        if have == 1:
            self.deck.cards.pop(card_id, None)
        else:
            self.deck.cards[card_id] = have - 1
        self.dirty = True

    def _request_exit(self) -> None:
        if self.dirty:
            self._confirm_exit = True
        else:
            self.app.scenes.pop()

    def _exit_save(self) -> None:
        self._confirm_exit = False
        self._exit_after_save = True
        self._save()

    def _exit_discard(self) -> None:
        self._confirm_exit = False
        self.app.scenes.pop()

    def _exit_cancel(self) -> None:
        self._confirm_exit = False

    def _show_toast(self, message: str) -> None:
        self._toast = message
        self._toast_timer = 2.8

    # ------------------------------------------------------------ geometry
    def _grid_cells(self) -> list[tuple[BrowseCard, pygame.Rect]]:
        gap = int(14 * self.s)
        cells = []
        for i, card in enumerate(self._filtered_cards()):
            col, row = i % self.cols, i // self.cols
            x = self.grid_rect.x + col * (self.cell_w + gap)
            y = self.grid_rect.y + row * (self.cell_h + gap) - int(self.scroll)
            cells.append((card, pygame.Rect(x, y, self.cell_w, self.cell_h)))
        return cells

    def _max_scroll(self) -> float:
        rows = math.ceil(len(self._filtered_cards()) / self.cols)
        content = rows * (self.cell_h + int(14 * self.s))
        return max(0.0, content - self.grid_rect.height)

    def _deck_rows(self) -> list[tuple[str, pygame.Rect]]:
        rows = []
        entries = sorted(self.deck.cards.items(),
                         key=lambda kv: ((c := cat.by_id(kv[0])) and
                                         (c.cost, c.name) or (0, kv[0])))
        for i, (card_id, _n) in enumerate(entries):
            rect = pygame.Rect(self.side_rect.x + int(12 * self.s),
                               self.list_top + i * self.row_h,
                               self.side_rect.width - int(24 * self.s),
                               self.row_h - 2)
            rows.append((card_id, rect))
        return rows

    def _browse_by_id(self, card_id: str) -> Optional[BrowseCard]:
        return next((c for c in self.library if c.card_id == card_id), None)

    # ------------------------------------------------------------ images
    def _card_image(self, card_id: str, height: int):
        name = card_library.image_name(card_id)
        if not name:
            return None
        key = (name, height)
        if key in self._img_cache:
            return self._img_cache[key]
        path = cardimages.get_path(name)
        if path is None:
            return None
        try:
            raw = pygame.image.load(str(path)).convert_alpha()
            if not cardimages.plausible_card_image(raw.get_width(),
                                                   raw.get_height()):
                surface = None
            else:
                width = int(raw.get_width() * height / raw.get_height())
                surface = pygame.transform.smoothscale(raw, (width, height))
        except pygame.error:
            surface = None
        self._img_cache[key] = surface
        return surface

    # ------------------------------------------------------------ frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if self._confirm_exit:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._exit_cancel()
            else:
                for btn in (self.btn_exit_save, self.btn_exit_discard,
                            self.btn_exit_cancel):
                    if btn.handle_event(event):
                        break
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self._pinned is not None:
                self._pinned = None
            else:
                self._request_exit()
            return
        if self._pinned is not None:
            if event.type == pygame.MOUSEBUTTONDOWN:
                self._pinned = None
            return
        for dd in (self.dd_type, self.dd_cost, self.dd_rarity, self.dd_sort,
                   self.dd_saved):
            if dd.open and dd.handle_event(event):
                return
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if event.type == pygame.MOUSEWHEEL:
            if self.grid_rect.collidepoint(pygame.mouse.get_pos()):
                self.scroll = max(0.0, min(self._max_scroll(),
                                           self.scroll - event.y * 70))
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
            if self.grid_rect.collidepoint(event.pos):
                for card, rect in self._grid_cells():
                    if rect.collidepoint(event.pos):
                        if event.button == 1:
                            self._add_card(card)
                        else:
                            self._pinned = card
                        return
            for card_id, rect in self._deck_rows():
                if rect.collidepoint(event.pos):
                    if event.button == 1:
                        self._remove_card(card_id)
                    else:
                        self._pinned = self._browse_by_id(card_id)
                    return

    def update(self, dt: float) -> None:
        self._time += dt
        self._toast_timer = max(0.0, self._toast_timer - dt)
        with self._lock:
            pending, self._results = self._results, []
        for op, result in pending:
            try:
                self._apply_result(op, result)
            except Exception:  # noqa: BLE001
                log.exception("Deck result handling failed")
        self._lib_poll += dt
        if self._lib_poll > 1.0:
            self._lib_poll = 0.0
            fingerprint = self._library_fingerprint()
            if fingerprint != self._lib_fingerprint:
                self._lib_fingerprint = fingerprint
                self._resync_library()
                self._show_toast("Card library updated.")
        if self._confirm_exit:
            for btn in (self.btn_exit_save, self.btn_exit_discard,
                        self.btn_exit_cancel):
                btn.update(dt)
        for widget in self.widgets:
            widget.update(dt)
        mouse = pygame.mouse.get_pos()
        self._hover = None
        if self._pinned is None and not any(
                dd.open for dd in (self.dd_type, self.dd_cost, self.dd_rarity,
                                   self.dd_sort, self.dd_saved)):
            if self.grid_rect.collidepoint(mouse):
                for card, rect in self._grid_cells():
                    if rect.collidepoint(mouse):
                        self._hover = card
                        break
            else:
                for card_id, rect in self._deck_rows():
                    if rect.collidepoint(mouse):
                        self._hover = self._browse_by_id(card_id)
                        break
        apply_cursor(self.widgets, force_hand=self._hover is not None)

    # ------------------------------------------------------------ drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        s = self.s
        theme.draw_text(surface, "Deck Builder",
                        ((surface.get_width() - self.panel_w) // 2,
                         int(36 * s)),
                        theme.display_font(int(28 * s)), theme.GOLD_BRIGHT,
                        anchor="center")
        count = len(self._filtered_cards())
        theme.draw_text(surface, f"{count} card{'s' if count != 1 else ''}",
                        (self.grid_rect.right, self.grid_rect.y - int(14 * s)),
                        theme.body_font(int(12 * s)), theme.TEXT_FAINT,
                        anchor="bottomright")
        self.lnk_back.draw(surface)
        self.search.draw(surface)
        for dd in (self.dd_type, self.dd_cost, self.dd_rarity, self.dd_sort):
            dd.draw(surface)

        surface.set_clip(self.grid_rect)
        for card, rect in self._grid_cells():
            if rect.bottom < self.grid_rect.y or rect.y > self.grid_rect.bottom:
                continue
            self._draw_cell(surface, card, rect)
        surface.set_clip(None)
        self._draw_scrollbar(surface)
        self._draw_side_panel(surface)
        for dd in (self.dd_type, self.dd_cost, self.dd_rarity, self.dd_sort,
                   self.dd_saved):
            dd.draw_overlay(surface)
        if self._pinned is not None:
            self._draw_inspect(surface, self._pinned)
        elif self._hover is not None:
            self._draw_hover(surface, self._hover)
        if self._confirm_exit:
            w, h = surface.get_size()
            veil = pygame.Surface((w, h), pygame.SRCALPHA)
            veil.fill((*theme.NAVY_ABYSS, 190))
            surface.blit(veil, (0, 0))
            box = pygame.Rect(0, 0, int(540 * self.s), int(176 * self.s))
            box.center = (w // 2, h // 2)
            theme.draw_glow_rect(surface, box, theme.GOLD, 0.35, radius=14,
                                 spread=14)
            theme.draw_panel(surface, box, fill=theme.NAVY,
                             border=theme.GOLD_DIM, radius=14)
            theme.draw_text(surface, "You have unsaved deck changes.",
                            (box.centerx, box.y + int(40 * self.s)),
                            theme.body_font(int(17 * self.s), bold=True),
                            theme.TEXT, anchor="center")
            theme.draw_text(surface, "Save them before leaving?",
                            (box.centerx, box.y + int(66 * self.s)),
                            theme.body_font(int(14 * self.s)),
                            theme.TEXT_DIM, anchor="center")
            for btn in (self.btn_exit_save, self.btn_exit_discard,
                        self.btn_exit_cancel):
                btn.draw(surface)
        self._draw_toast(surface)

    def _draw_scrollbar(self, surface) -> None:
        max_scroll = self._max_scroll()
        if max_scroll <= 0:
            return
        track = pygame.Rect(self.grid_rect.right + int(8 * self.s),
                            self.grid_rect.y, int(5 * self.s),
                            self.grid_rect.height)
        pygame.draw.rect(surface, theme.NAVY_RAISED, track, border_radius=3)
        view_frac = self.grid_rect.height / (self.grid_rect.height + max_scroll)
        thumb_h = max(int(30 * self.s), int(track.height * view_frac))
        thumb_y = track.y + int((track.height - thumb_h) *
                                (self.scroll / max_scroll))
        pygame.draw.rect(surface, theme.GOLD_DIM,
                         pygame.Rect(track.x, thumb_y, track.width, thumb_h),
                         border_radius=3)

    def _draw_cell(self, surface, card: BrowseCard, rect: pygame.Rect) -> None:
        s = self.s
        in_deck = self.deck.cards.get(card.card_id, 0)
        owned = self.collection.get(card.card_id, 0)
        hover = card is self._hover
        color = RARITY_COLORS[card.rarity]
        if hover:
            theme.draw_glow_rect(surface, rect, color, 0.55, radius=10,
                                 spread=8)
        image = self._card_image(card.card_id, rect.height)
        if image is not None:
            surface.blit(image, image.get_rect(center=rect.center))
            pygame.draw.rect(surface, color if hover else theme.NAVY_EDGE,
                             rect, width=1, border_radius=8)
        else:
            self._draw_mini_card(surface, card, rect, hover)
        if not card.playable:
            veil = pygame.Surface(rect.size, pygame.SRCALPHA)
            veil.fill((8, 10, 20, 150))
            surface.blit(veil, rect.topleft)
            tag = pygame.Rect(0, 0, int(64 * s), int(20 * s))
            tag.midbottom = (rect.centerx, rect.bottom - int(8 * s))
            theme.draw_panel(surface, tag, fill=theme.NAVY_RAISED,
                             border=theme.NAVY_EDGE, radius=6)
            theme.draw_text(surface, "soon", tag.center,
                            theme.body_font(int(11 * s)), theme.TEXT_FAINT,
                            anchor="center")
        if in_deck:
            badge = pygame.Rect(0, 0, int(32 * s), int(22 * s))
            badge.topright = (rect.right - int(5 * s), rect.y + int(5 * s))
            theme.draw_panel(surface, badge, fill=theme.GOLD,
                             border=theme.GOLD_BRIGHT, radius=7)
            theme.draw_text(surface, f"x{in_deck}", badge.center,
                            theme.body_font(int(12 * s), bold=True),
                            theme.TEXT_ON_GOLD, anchor="center")

    def _draw_mini_card(self, surface, card: BrowseCard, rect: pygame.Rect,
                        hover: bool) -> None:
        s = self.s
        color = RARITY_COLORS[card.rarity]
        theme.draw_panel(surface, rect, fill=RARITY_TINT[card.rarity],
                         border=color if hover else
                         tuple(c // 2 for c in color), radius=10)
        # header band
        band = pygame.Rect(rect.x + 2, rect.y + 2, rect.width - 4,
                           int(26 * s))
        pygame.draw.rect(surface, theme.NAVY, band,
                         border_top_left_radius=9, border_top_right_radius=9)
        gem_r = int(10 * s)
        gem_c = (rect.x + gem_r + int(6 * s), band.centery)
        theme.aa_circle(surface, MANA_FILL, gem_c, gem_r)
        theme.draw_text(surface, str(card.cost), gem_c,
                        theme.body_font(int(12 * s), bold=True), theme.TEXT,
                        anchor="center")
        # wrapped name (2 lines max)
        font = theme.body_font(int(12 * s), bold=True)
        words, line, lines = card.name.split(), "", []
        for word in words:
            probe = f"{line} {word}".strip()
            if font.size(probe)[0] > rect.width - int(40 * s) and line:
                lines.append(line)
                line = word
            else:
                line = probe
        lines.append(line)
        y = band.bottom + int(8 * s)
        for text in lines[:2]:
            theme.draw_text(surface, text, (rect.centerx, y), font,
                            theme.TEXT, anchor="midtop")
            y += font.get_linesize()
        theme.draw_text(surface, card.type_label,
                        (rect.centerx, y + int(4 * s)),
                        theme.body_font(int(10 * s)), color, anchor="midtop")
        # star sigil filler
        cx, cy = rect.centerx, rect.centery + int(20 * s)
        for k in range(4):
            a = k * math.tau / 4 + math.pi / 4
            pygame.draw.line(surface, tuple(c // 2 for c in color), (cx, cy),
                             (cx + math.cos(a) * 14 * s,
                              cy + math.sin(a) * 14 * s))
        stats = card.stats_text()
        if stats:
            theme.draw_text(surface, stats,
                            (rect.right - int(8 * s),
                             rect.bottom - int(8 * s)),
                            theme.body_font(int(13 * s), bold=True),
                            theme.GOLD_BRIGHT, anchor="bottomright")
        theme.draw_text(surface, f"own {self.collection.get(card.card_id, 0)}",
                        (rect.x + int(8 * s), rect.bottom - int(8 * s)),
                        theme.body_font(int(10 * s)), theme.TEXT_FAINT,
                        anchor="bottomleft")

    def _draw_side_panel(self, surface) -> None:
        s = self.s
        theme.draw_panel(surface, self.side_rect, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=12)
        title = "DECK" + ("  •" if self.dirty else "")
        theme.draw_text(surface, title,
                        (self.side_rect.centerx,
                         self.side_rect.y + int(26 * s)),
                        theme.display_font(int(19 * s)),
                        theme.GOLD_BRIGHT, anchor="center")
        if self.dirty:
            theme.draw_text(surface, "unsaved changes",
                            (self.side_rect.centerx,
                             self.side_rect.y + int(44 * s)),
                            theme.body_font(int(10 * s)), theme.TEXT_FAINT,
                            anchor="center")
        self.deck_name.draw(surface)
        self.dd_saved.draw(surface)

        for card_id, rect in self._deck_rows():
            card = self._browse_by_id(card_id)
            count = self.deck.cards[card_id]
            if card is None:                     # card removed from the library
                theme.draw_text(surface, f"{card_id}  (removed)",
                                (rect.x + int(10 * s), rect.centery),
                                theme.body_font(int(12 * s)),
                                theme.DANGER, anchor="midleft")
                theme.draw_text(surface, f"x{count}",
                                (rect.right - int(8 * s), rect.centery),
                                theme.body_font(int(13 * s), bold=True),
                                theme.TEXT_FAINT, anchor="midright")
                continue
            if self._hover is not None and card is self._hover:
                pygame.draw.rect(surface, theme.NAVY_RAISED, rect,
                                 border_radius=6)
            gem_r = int(9 * s)
            theme.aa_circle(surface, MANA_FILL,
                            (rect.x + gem_r + 2, rect.centery), gem_r)
            theme.draw_text(surface, str(card.cost),
                            (rect.x + gem_r + 2, rect.centery),
                            theme.body_font(int(11 * s), bold=True),
                            theme.TEXT, anchor="center")
            theme.draw_text(surface, card.name,
                            (rect.x + int(28 * s), rect.centery),
                            theme.body_font(int(13 * s)),
                            RARITY_COLORS[card.rarity], anchor="midleft")
            theme.draw_text(surface, f"x{count}",
                            (rect.right - int(8 * s), rect.centery),
                            theme.body_font(int(13 * s), bold=True),
                            theme.TEXT, anchor="midright")

        # champion status
        champ_id = next((cid for cid in self.deck.cards
                         if cat.card_type_of(cid) == "champion"), None)
        champ = self._browse_by_id(champ_id) if champ_id else None
        champ_text = f"Champion:  {champ.name}" if champ \
            else "No champion — every deck needs one!"
        champ_color = theme.SUCCESS if champ else theme.DANGER
        theme.draw_text(surface, champ_text,
                        (self.side_rect.centerx,
                         self.btn_save.rect.y - int(118 * s)),
                        theme.body_font(int(12 * s), bold=champ is None),
                        champ_color, anchor="center")

        # count + curve
        size = self.deck.size
        legal = size == DECK_SIZE and \
            validate_deck(self.deck.cards, self.collection)[0]
        color = theme.SUCCESS if legal else (
            theme.TEXT_DIM if size < DECK_SIZE else theme.DANGER)
        curve_y = self.btn_save.rect.y - int(86 * s)
        theme.draw_text(surface, f"{size} / {DECK_SIZE}",
                        (self.side_rect.centerx, curve_y - int(12 * s)),
                        theme.body_font(int(18 * s), bold=True), color,
                        anchor="center")
        counts = [0] * 8
        for card_id, count in self.deck.cards.items():
            card = self._browse_by_id(card_id)
            cost = card.cost if card else 0
            counts[min(7, cost)] += count
        peak = max(counts) or 1
        bar_w = int(18 * s)
        total_w = 8 * bar_w + 7 * int(5 * s)
        bx = self.side_rect.centerx - total_w // 2
        for i, count in enumerate(counts):
            height = int(34 * s * count / peak) if count else 2
            bar = pygame.Rect(bx + i * (bar_w + int(5 * s)),
                              curve_y + int(40 * s) - height, bar_w, height)
            pygame.draw.rect(surface, MANA_FILL if count else theme.NAVY_EDGE,
                             bar, border_radius=3)
            if count:
                theme.draw_text(surface, str(count),
                                (bar.centerx, bar.y - int(2 * s)),
                                theme.body_font(int(9 * s)), MANA_CORE,
                                anchor="midbottom")
            label = str(i) if i < 7 else "7+"
            theme.draw_text(surface, label,
                            (bar.centerx, curve_y + int(46 * s)),
                            theme.body_font(int(9 * s)), theme.TEXT_FAINT,
                            anchor="midtop")

        for btn in (self.btn_save, self.btn_new, self.btn_delete):
            btn.draw(surface)
        if self.busy:
            dots = "." * (1 + int(self._time * 3) % 3)
            theme.draw_text(surface, f"Working{dots}",
                            (self.side_rect.centerx,
                             self.btn_save.rect.y - int(16 * s)),
                            theme.body_font(int(12 * s)), theme.TEXT_FAINT,
                            anchor="center")

    # ------------------------------------------------------ preview/inspect
    def _big_face(self, card: BrowseCard, height: int) -> pygame.Surface:
        image = self._card_image(card.card_id, height)
        if image is not None:
            return image
        width = int(height * 1065 / 1477)
        face = pygame.Surface((width, height), pygame.SRCALPHA)
        rect = face.get_rect()
        color = RARITY_COLORS[card.rarity]
        theme.draw_panel(face, rect, fill=theme.NAVY, border=color, radius=14)
        k = height / 420
        theme.draw_text(face, card.name, (rect.centerx, int(22 * k)),
                        theme.display_font(int(20 * k)), theme.GOLD_BRIGHT,
                        anchor="center")
        sub = f"{card.type_label}  ·  Cost {card.cost}  ·  " \
              f"{card.rarity.value.title()}"
        theme.draw_text(face, sub, (rect.centerx, int(46 * k)),
                        theme.body_font(int(12 * k)), color, anchor="center")
        font = theme.body_font(int(14 * k))
        words, line, y = card.text.split(), "", int(80 * k)
        for word in words + [None]:
            probe = f"{line} {word}".strip() if word else line
            if word is None or font.size(probe)[0] > width - int(30 * k):
                theme.draw_text(face, line, (rect.centerx, y), font,
                                theme.TEXT, anchor="midtop")
                y += font.get_linesize()
                line = word or ""
                if y > height - int(70 * k):
                    break
            else:
                line = probe
        stats = card.stats_text()
        if stats:
            theme.draw_text(face, stats,
                            (rect.centerx, rect.bottom - int(24 * k)),
                            theme.body_font(int(18 * k), bold=True),
                            theme.GOLD_BRIGHT, anchor="center")
        return face

    def _draw_hover(self, surface, card: BrowseCard) -> None:
        image = self._big_face(card, int(surface.get_height() * 0.5))
        mx, my = pygame.mouse.get_pos()
        x = mx + 24
        if x + image.get_width() > self.side_rect.x - 6:
            x = mx - image.get_width() - 24
        y = max(10, min(my - image.get_height() // 2,
                        surface.get_height() - image.get_height() - 10))
        theme.draw_glow_rect(surface,
                             pygame.Rect(x, y, image.get_width(),
                                         image.get_height()),
                             RARITY_COLORS[card.rarity], 0.4, radius=12,
                             spread=10)
        surface.blit(image, (x, y))

    def _draw_inspect(self, surface, card: BrowseCard) -> None:
        w, h = surface.get_size()
        veil = pygame.Surface((w, h), pygame.SRCALPHA)
        veil.fill((*theme.NAVY_ABYSS, 185))
        surface.blit(veil, (0, 0))
        image = self._big_face(card, int(h * 0.84))
        rect = image.get_rect(center=(w // 2, h // 2))
        theme.draw_glow_rect(surface, rect, RARITY_COLORS[card.rarity], 0.5,
                             radius=16, spread=18)
        surface.blit(image, rect)
        note = "own {}  ·  deck limit {}".format(
            self.collection.get(card.card_id, 0), max_copies(card.card_id)) \
            if card.playable else "Not playable yet — engine update coming"
        theme.draw_text(surface, note, (w // 2, rect.bottom + 18),
                        theme.body_font(13), theme.TEXT_DIM, anchor="center")
        theme.draw_text(surface, "Click anywhere to close",
                        (w // 2, h - 22), theme.body_font(12),
                        theme.TEXT_FAINT, anchor="center")

    def _draw_toast(self, surface) -> None:
        if self._toast_timer <= 0 or not self._toast:
            return
        w, h = surface.get_size()
        fade = min(1.0, self._toast_timer / 0.4)
        box = pygame.Rect(0, 0, min(w - 80, 520), 42)
        box.midbottom = (self.grid_rect.centerx, h - 18)
        veil = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(235 * fade)),
                         veil.get_rect(), border_radius=10)
        pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                         veil.get_rect(), width=1, border_radius=10)
        surface.blit(veil, box.topleft)
        theme.draw_text(surface, self._toast, box.center,
                        theme.body_font(15), theme.TEXT, anchor="center",
                        alpha=int(255 * fade))
