"""Deck Builder.

Left: the card library — search (name or rules text), type & cost filters,
and a scrollable grid. Right: the working deck — name, card list with counts,
a mana curve, and Save / New / Delete plus a loader for saved decks.

Click a card to add a copy (up to your owned copies / rarity limit); click a
deck row (or right-click the grid card) to remove one. Hover anything for an
enlarged preview. Decks save to the account's cloud storage when signed in.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Callable, Optional

import pygame

from arcanum.core.scene import Scene
from arcanum.game.catalog import (BY_ID, CATALOG, DECK_SIZE, CardDef, Rarity,
                                  max_copies, starter_collection, validate_deck)
from arcanum.game.match import Kind
from arcanum.services.decks import DeckRecord, DeckResult
from arcanum.ui import theme
from arcanum.ui.widgets import Button, Dropdown, TextInput, apply_cursor

log = logging.getLogger(__name__)

MANA_FILL = (86, 156, 255)
MANA_CORE = (170, 210, 255)
RARITY_COLORS = {Rarity.COMMON: theme.TEXT_DIM, Rarity.UNCOMMON: theme.SUCCESS,
                 Rarity.RARE: MANA_FILL, Rarity.MYTHIC: theme.GOLD_BRIGHT}

KIND_FILTERS = [("All types", None), ("Creatures", Kind.CREATURE),
                ("Spells", Kind.SPELL), ("Relics", Kind.RELIC)]
COST_FILTERS = [("Any cost", None), ("1-2", (1, 2)), ("3-4", (3, 4)),
                ("5+", (5, 99))]


def filter_cards(query: str, kind: Optional[Kind],
                 cost: Optional[tuple[int, int]]) -> list[CardDef]:
    out = []
    for card in CATALOG:
        if kind is not None and card.kind is not kind:
            continue
        if cost is not None and not (cost[0] <= card.cost <= cost[1]):
            continue
        if not card.matches(query):
            continue
        out.append(card)
    out.sort(key=lambda c: (c.cost, c.name))
    return out


class DeckBuilderScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self.collection = starter_collection()
        self.deck = DeckRecord(id="", name="New Deck", cards={})
        self.saved: list[DeckRecord] = []
        self.scroll = 0.0
        self.busy = False
        self._results: list[tuple[str, DeckResult]] = []
        self._lock = threading.Lock()
        self._toast = ""
        self._toast_timer = 0.0
        self._time = 0.0
        self._hover_card: Optional[CardDef] = None
        self._build()
        self._refresh_decks()

    def on_resize(self, size) -> None:
        if hasattr(self, "deck"):
            self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.3, h / 1080))
        self.s = s
        ui = self.app.audio.ui_sound

        self.panel_w = int(w * 0.27)
        self.grid_rect = pygame.Rect(int(24 * s), int(150 * s),
                                     w - self.panel_w - int(64 * s),
                                     h - int(180 * s))
        self.side_rect = pygame.Rect(w - self.panel_w - int(16 * s), int(90 * s),
                                     self.panel_w, h - int(120 * s))
        self.cell = (int(150 * s), int(112 * s))
        self.cols = max(2, self.grid_rect.width // (self.cell[0] + int(12 * s)))

        top = int(84 * s)
        self.search = TextInput(pygame.Rect(int(24 * s), top, int(320 * s),
                                            int(44 * s)),
                                placeholder="Search name or text...")
        self.dd_kind = Dropdown(pygame.Rect(int(360 * s), top, int(180 * s),
                                            int(44 * s)),
                                [label for label, _ in KIND_FILTERS])
        self.dd_cost = Dropdown(pygame.Rect(int(556 * s), top, int(150 * s),
                                            int(44 * s)),
                                [label for label, _ in COST_FILTERS])
        self.lnk_back = Button(pygame.Rect(int(24 * s), int(20 * s),
                                           int(120 * s), int(42 * s)),
                               "Back", lambda: self.app.scenes.pop(),
                               primary=False, font_size=16, sound_cb=ui)

        px = self.side_rect.x + int(16 * s)
        pw = self.side_rect.width - int(32 * s)
        self.deck_name = TextInput(pygame.Rect(px, self.side_rect.y + int(14 * s),
                                               pw, int(42 * s)),
                                   placeholder="Deck name")
        self.deck_name.text = self.deck.name
        self.dd_saved = Dropdown(pygame.Rect(px, self.side_rect.y + int(66 * s),
                                             pw, int(40 * s)),
                                 ["My decks..."], on_change=self._on_pick_saved)
        by = self.side_rect.bottom - int(60 * s)
        bw = (pw - int(16 * s)) // 3
        self.btn_save = Button(pygame.Rect(px, by, bw, int(44 * s)), "Save",
                               self._save, sound_cb=ui, font_size=17)
        self.btn_new = Button(pygame.Rect(px + bw + int(8 * s), by, bw,
                                          int(44 * s)), "New", self._new,
                              primary=False, sound_cb=ui, font_size=17)
        self.btn_delete = Button(pygame.Rect(px + 2 * (bw + int(8 * s)), by, bw,
                                             int(44 * s)), "Delete",
                                 self._delete, primary=False, sound_cb=ui,
                                 font_size=17)
        self.widgets = [self.lnk_back, self.search, self.dd_kind, self.dd_cost,
                        self.deck_name, self.dd_saved, self.btn_save,
                        self.btn_new, self.btn_delete]
        # deck list geometry (rows filled at draw time)
        self.list_top = self.side_rect.y + int(120 * s)
        self.row_h = int(30 * s)

    # -------------------------------------------------------------- data
    def _filters(self):
        kind = KIND_FILTERS[self.dd_kind.selected][1]
        cost = COST_FILTERS[self.dd_cost.selected][1]
        return filter_cards(self.search.text, kind, cost)

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
            names = ["My decks..."] + [
                f"{d.name}  ({d.size})" for d in self.saved]
            self.dd_saved.options = names
            self.dd_saved.selected = 0
        elif op == "save":
            if result.deck is not None:
                self.deck = result.deck
            self._show_toast("Deck saved.")
            self._refresh_decks()
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
        self._show_toast(f"Loaded '{picked.name}'.")

    # ------------------------------------------------------------ actions
    def _save(self) -> None:
        if self.busy:
            return
        self.deck.name = (self.deck_name.text.strip() or "Unnamed Deck")[:40]
        ok, reason = validate_deck(self.deck.cards, self.collection)
        if not ok:
            self._show_toast(reason)
            return
        self.busy = True
        self.app.backend.deck_store.save_deck(
            self.deck, lambda r: self._post_result("save", r))

    def _new(self) -> None:
        self.deck = DeckRecord(id="", name="New Deck", cards={})
        self.deck_name.text = self.deck.name
        self.dd_saved.selected = 0

    def _delete(self) -> None:
        if self.busy or not self.deck.id:
            self._show_toast("This deck isn't saved yet.")
            return
        self.busy = True
        self.app.backend.deck_store.delete_deck(
            self.deck.id, lambda r: self._post_result("delete", r))

    def _add_card(self, card: CardDef) -> None:
        have = self.deck.cards.get(card.card_id, 0)
        limit = min(max_copies(card.card_id),
                    self.collection.get(card.card_id, 0))
        if self.deck.size >= DECK_SIZE:
            self._show_toast(f"Deck is full ({DECK_SIZE}).")
            return
        if have >= limit:
            self._show_toast(f"No more copies of {card.name} "
                             f"(limit {limit}).")
            return
        self.deck.cards[card.card_id] = have + 1
        self.app.audio.ui_sound("click")

    def _remove_card(self, card_id: str) -> None:
        have = self.deck.cards.get(card_id, 0)
        if have <= 1:
            self.deck.cards.pop(card_id, None)
        else:
            self.deck.cards[card_id] = have - 1

    def _show_toast(self, message: str) -> None:
        self._toast = message
        self._toast_timer = 2.8

    # ------------------------------------------------------------ geometry
    def _grid_cells(self) -> list[tuple[CardDef, pygame.Rect]]:
        cards = self._filters()
        gap = int(12 * self.s)
        cells = []
        for i, card in enumerate(cards):
            col, row = i % self.cols, i // self.cols
            x = self.grid_rect.x + col * (self.cell[0] + gap)
            y = self.grid_rect.y + row * (self.cell[1] + gap) - int(self.scroll)
            cells.append((card, pygame.Rect(x, y, *self.cell)))
        return cells

    def _max_scroll(self) -> float:
        rows = math.ceil(len(self._filters()) / self.cols)
        content = rows * (self.cell[1] + int(12 * self.s))
        return max(0.0, content - self.grid_rect.height)

    def _deck_rows(self) -> list[tuple[str, pygame.Rect]]:
        rows = []
        entries = sorted(self.deck.cards.items(),
                         key=lambda kv: (BY_ID[kv[0]].cost, BY_ID[kv[0]].name))
        for i, (card_id, _count) in enumerate(entries):
            rect = pygame.Rect(self.side_rect.x + int(12 * self.s),
                               self.list_top + i * self.row_h,
                               self.side_rect.width - int(24 * self.s),
                               self.row_h - 2)
            rows.append((card_id, rect))
        return rows

    # ------------------------------------------------------------ frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.pop()
            return
        for dd in (self.dd_kind, self.dd_cost, self.dd_saved):
            if dd.open and dd.handle_event(event):
                return
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0.0, min(self._max_scroll(),
                                       self.scroll - event.y * 60))
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
            if self.grid_rect.collidepoint(event.pos):
                for card, rect in self._grid_cells():
                    if rect.collidepoint(event.pos):
                        if event.button == 1:
                            self._add_card(card)
                        else:
                            self._remove_card(card.card_id)
                        return
            if event.button == 1:
                for card_id, rect in self._deck_rows():
                    if rect.collidepoint(event.pos):
                        self._remove_card(card_id)
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
        for widget in self.widgets:
            widget.update(dt)
        mouse = pygame.mouse.get_pos()
        self._hover_card = None
        if self.grid_rect.collidepoint(mouse):
            for card, rect in self._grid_cells():
                if rect.collidepoint(mouse):
                    self._hover_card = card
                    break
        else:
            for card_id, rect in self._deck_rows():
                if rect.collidepoint(mouse):
                    self._hover_card = BY_ID.get(card_id)
                    break
        apply_cursor(self.widgets, force_hand=self._hover_card is not None)

    # ------------------------------------------------------------ drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        s = self.s
        theme.draw_text(surface, "Deck Builder",
                        (surface.get_width() // 2, int(34 * s)),
                        theme.display_font(int(30 * s)), theme.GOLD_BRIGHT,
                        anchor="center")
        for widget in (self.lnk_back, self.search):
            widget.draw(surface)
        self.dd_kind.draw(surface)
        self.dd_cost.draw(surface)

        # card grid (clipped)
        surface.set_clip(self.grid_rect)
        for card, rect in self._grid_cells():
            if rect.bottom < self.grid_rect.y or rect.y > self.grid_rect.bottom:
                continue
            self._draw_card_cell(surface, card, rect)
        surface.set_clip(None)

        self._draw_side_panel(surface)
        for dd in (self.dd_kind, self.dd_cost, self.dd_saved):
            dd.draw_overlay(surface)
        self._draw_preview(surface)
        self._draw_toast(surface)

    def _draw_card_cell(self, surface, card: CardDef, rect: pygame.Rect) -> None:
        s = self.s
        in_deck = self.deck.cards.get(card.card_id, 0)
        owned = self.collection.get(card.card_id, 0)
        hover = card is self._hover_card
        border = RARITY_COLORS[card.rarity]
        if hover:
            theme.draw_glow_rect(surface, rect, theme.GOLD_GLOW, 0.5,
                                 radius=8, spread=6)
        theme.draw_panel(surface, rect, fill=theme.NAVY_RAISED,
                         border=theme.GOLD if hover else border, radius=8)
        # cost gem
        gem_r = int(12 * s)
        gem_c = (rect.x + gem_r + int(5 * s), rect.y + gem_r + int(5 * s))
        pygame.draw.circle(surface, MANA_FILL, gem_c, gem_r)
        theme.draw_text(surface, str(card.cost), gem_c,
                        theme.body_font(int(13 * s), bold=True), theme.TEXT,
                        anchor="center")
        theme.draw_text(surface, card.name,
                        (rect.x + int(30 * s), rect.y + int(8 * s)),
                        theme.body_font(int(12 * s)), theme.TEXT,
                        anchor="topleft")
        if card.kind is Kind.CREATURE:
            theme.draw_text(surface, f"{card.attack}/{card.health}",
                            (rect.right - int(8 * s), rect.bottom - int(8 * s)),
                            theme.body_font(int(13 * s), bold=True),
                            theme.GOLD_BRIGHT, anchor="bottomright")
        else:
            theme.draw_text(surface, card.kind.value.title(),
                            (rect.right - int(8 * s), rect.bottom - int(8 * s)),
                            theme.body_font(int(11 * s)), theme.TEXT_FAINT,
                            anchor="bottomright")
        theme.draw_text(surface, f"own {owned}",
                        (rect.x + int(8 * s), rect.bottom - int(8 * s)),
                        theme.body_font(int(11 * s)), theme.TEXT_FAINT,
                        anchor="bottomleft")
        if in_deck:
            badge = pygame.Rect(0, 0, int(30 * s), int(20 * s))
            badge.topright = (rect.right - int(6 * s), rect.y + int(6 * s))
            theme.draw_panel(surface, badge, fill=theme.GOLD,
                             border=theme.GOLD_BRIGHT, radius=6)
            theme.draw_text(surface, f"x{in_deck}", badge.center,
                            theme.body_font(int(12 * s), bold=True),
                            theme.TEXT_ON_GOLD, anchor="center")

    def _draw_side_panel(self, surface) -> None:
        s = self.s
        theme.draw_panel(surface, self.side_rect, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=12)
        self.deck_name.draw(surface)
        self.dd_saved.draw(surface)

        # deck rows
        for card_id, rect in self._deck_rows():
            card = BY_ID[card_id]
            count = self.deck.cards[card_id]
            hover = card is self._hover_card
            if hover:
                pygame.draw.rect(surface, theme.NAVY_RAISED, rect,
                                 border_radius=6)
            theme.draw_text(surface, str(card.cost),
                            (rect.x + int(10 * s), rect.centery),
                            theme.body_font(int(13 * s), bold=True),
                            MANA_CORE, anchor="midleft")
            theme.draw_text(surface, card.name,
                            (rect.x + int(32 * s), rect.centery),
                            theme.body_font(int(13 * s)),
                            RARITY_COLORS[card.rarity], anchor="midleft")
            theme.draw_text(surface, f"x{count}",
                            (rect.right - int(10 * s), rect.centery),
                            theme.body_font(int(13 * s), bold=True),
                            theme.TEXT, anchor="midright")

        # size + validity + mana curve above the buttons
        size = self.deck.size
        ok, _ = validate_deck(self.deck.cards, self.collection) \
            if size == DECK_SIZE else (False, "")
        color = theme.SUCCESS if ok else (
            theme.TEXT_DIM if size < DECK_SIZE else theme.DANGER)
        curve_y = self.btn_save.rect.y - int(74 * s)
        theme.draw_text(surface, f"{size} / {DECK_SIZE}",
                        (self.side_rect.centerx, curve_y - int(10 * s)),
                        theme.body_font(int(17 * s), bold=True), color,
                        anchor="center")
        # curve: bars for costs 1..7+
        counts = [0] * 7
        for card_id, count in self.deck.cards.items():
            counts[min(6, BY_ID[card_id].cost - 1)] += count
        peak = max(counts) or 1
        bar_w = int(20 * s)
        total_w = 7 * bar_w + 6 * int(6 * s)
        bx = self.side_rect.centerx - total_w // 2
        for i, count in enumerate(counts):
            height = int(34 * s * count / peak) if count else 2
            bar = pygame.Rect(bx + i * (bar_w + int(6 * s)),
                              curve_y + int(40 * s) - height, bar_w, height)
            pygame.draw.rect(surface, MANA_FILL if count else theme.NAVY_EDGE,
                             bar, border_radius=3)
            theme.draw_text(surface, str(i + 1) + ("+" if i == 6 else ""),
                            (bar.centerx, curve_y + int(48 * s)),
                            theme.body_font(int(10 * s)), theme.TEXT_FAINT,
                            anchor="center")

        for btn in (self.btn_save, self.btn_new, self.btn_delete):
            btn.draw(surface)
        if self.busy:
            dots = "." * (1 + int(self._time * 3) % 3)
            theme.draw_text(surface, f"Working{dots}",
                            (self.side_rect.centerx,
                             self.btn_save.rect.y - int(16 * s)),
                            theme.body_font(int(12 * s)), theme.TEXT_FAINT,
                            anchor="center")

    def _draw_preview(self, surface) -> None:
        card = self._hover_card
        if card is None:
            return
        s = self.s
        pw, ph = int(240 * s), int(300 * s)
        mx, my = pygame.mouse.get_pos()
        x = mx + 24
        if x + pw > surface.get_width() - 10:
            x = mx - pw - 24
        y = max(10, min(my - ph // 2, surface.get_height() - ph - 10))
        rect = pygame.Rect(x, y, pw, ph)
        theme.draw_glow_rect(surface, rect, theme.GOLD, 0.35, radius=12,
                             spread=8)
        theme.draw_panel(surface, rect, fill=theme.NAVY,
                         border=RARITY_COLORS[card.rarity], radius=12)
        pad = int(14 * s)
        theme.draw_text(surface, card.name, (rect.centerx, rect.y + pad),
                        theme.display_font(int(19 * s)), theme.GOLD_BRIGHT,
                        anchor="midtop")
        sub = f"{card.kind.value.title()}  ·  Cost {card.cost}  ·  " \
              f"{card.rarity.value.title()}"
        theme.draw_text(surface, sub,
                        (rect.centerx, rect.y + pad + int(26 * s)),
                        theme.body_font(int(12 * s)),
                        RARITY_COLORS[card.rarity], anchor="midtop")
        # rules text wrap
        font = theme.body_font(int(14 * s))
        words, lines, line = card.text.split(), [], ""
        for word in words:
            test = f"{line} {word}".strip()
            if font.size(test)[0] > pw - pad * 2 and line:
                lines.append(line)
                line = word
            else:
                line = test
        if line:
            lines.append(line)
        ty = rect.y + int(84 * s)
        for text_line in lines[:6]:
            theme.draw_text(surface, text_line, (rect.centerx, ty), font,
                            theme.TEXT, anchor="midtop")
            ty += font.get_linesize()
        if card.kind is Kind.CREATURE:
            theme.draw_text(surface, f"{card.attack} / {card.health}",
                            (rect.centerx, rect.bottom - pad),
                            theme.body_font(int(17 * s), bold=True),
                            theme.GOLD_BRIGHT, anchor="midbottom")
        owned = self.collection.get(card.card_id, 0)
        theme.draw_text(surface,
                        f"Owned: {owned}   ·   Deck limit: {max_copies(card.card_id)}",
                        (rect.centerx, rect.bottom - pad - int(26 * s)),
                        theme.body_font(int(11 * s)), theme.TEXT_FAINT,
                        anchor="midbottom")

    def _draw_toast(self, surface) -> None:
        if self._toast_timer <= 0 or not self._toast:
            return
        w, h = surface.get_size()
        fade = min(1.0, self._toast_timer / 0.4)
        box = pygame.Rect(0, 0, min(w - 80, 520), 42)
        box.midbottom = (self.grid_rect.centerx, h - 20)
        veil = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(235 * fade)),
                         veil.get_rect(), border_radius=10)
        pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                         veil.get_rect(), width=1, border_radius=10)
        surface.blit(veil, box.topleft)
        theme.draw_text(surface, self._toast, box.center,
                        theme.body_font(15), theme.TEXT, anchor="center",
                        alpha=int(255 * fade))
