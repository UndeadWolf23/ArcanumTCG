"""Social — profile & friends.

Left column: your profile (username with live availability feedback, deck
count against the 30-deck account cap) and player search with Add buttons.
Right column: relationships — incoming requests (Accept / Decline, plus any
live match challenges), friends (online dot, Challenge, Remove), and
outgoing requests (Cancel).

Durable data (the friend graph) lives in Supabase; presence and challenges
flow through the game server, which pushes CHALLENGE_INCOMING the moment a
friend calls you out.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import pygame

from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.services.net.protocol import MsgType
from arcanum.services.social import SocialResult, username_problem
from arcanum.ui import theme
from arcanum.ui.widgets import Button, TextInput, apply_cursor

log = logging.getLogger(__name__)

PRESENCE_EVERY = 12.0
ROW_H = 44


class SocialScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self._toast = ""
        self._toast_timer = 0.0
        self.relations: list[dict] = []
        self.results: list[dict] = []
        self._online: set[str] = set()
        self._presence_timer = PRESENCE_EVERY   # query immediately
        self._pending: list[tuple[str, SocialResult]] = []
        self._lock = threading.Lock()
        self._row_buttons: list[tuple[pygame.Rect, str, dict]] = []
        self._name_note = ""
        self._name_note_color = theme.TEXT_FAINT
        self.app.bus.subscribe(Events.NET_MESSAGE, self._on_net_message)
        self._build()
        social = self.app.backend.social
        if social is not None:
            social.list_relations(lambda r: self._post("relations", r))
        profile = self.app.backend.profile
        if profile:
            self.name_input.text = str(profile.get("username", ""))
        self._deck_count: Optional[int] = None
        self.app.backend.deck_store.list_decks(
            lambda r: self._post("decks", r))

    def on_exit(self) -> None:
        self.app.bus.unsubscribe(Events.NET_MESSAGE, self._on_net_message)

    def on_resize(self, size) -> None:
        if hasattr(self, "_time"):
            self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.3, h / 1080))
        self.s = s
        ui = self.app.audio.ui_sound
        col_w = int(w * 0.40)
        self.left = pygame.Rect(int(w * 0.05), int(110 * s), col_w,
                                h - int(160 * s))
        self.right = pygame.Rect(int(w * 0.53), int(110 * s),
                                 int(w * 0.42), h - int(160 * s))

        self.btn_back = Button(pygame.Rect(int(24 * s), int(20 * s),
                                           int(110 * s), int(42 * s)),
                               "Back", lambda: self.app.scenes.pop(),
                               primary=False, font_size=16, sound_cb=ui)
        pad = int(20 * s)
        self.name_input = TextInput(
            pygame.Rect(self.left.x + pad, self.left.y + int(64 * s),
                        self.left.width - pad * 2 - int(120 * s),
                        int(42 * s)),
            placeholder="Choose a username")
        self.btn_save_name = Button(
            pygame.Rect(self.name_input.rect.right + int(10 * s),
                        self.name_input.rect.y, int(110 * s), int(42 * s)),
            "Save", self._save_name, font_size=15, sound_cb=ui)
        self.search_input = TextInput(
            pygame.Rect(self.left.x + pad, self.left.y + int(210 * s),
                        self.left.width - pad * 2 - int(120 * s),
                        int(42 * s)),
            placeholder="Name or friend code...")
        self.btn_search = Button(
            pygame.Rect(self.search_input.rect.right + int(10 * s),
                        self.search_input.rect.y, int(110 * s), int(42 * s)),
            "Search", self._search, primary=False, font_size=15,
            sound_cb=ui)
        self.widgets = [self.btn_back, self.name_input, self.btn_save_name,
                        self.search_input, self.btn_search]

    # -------------------------------------------------------------- results
    def _post(self, op: str, result) -> None:
        with self._lock:
            self._pending.append((op, result))

    def _apply(self, op: str, result) -> None:
        if op == "decks":
            if getattr(result, "ok", False):
                self._deck_count = len(result.decks)
            return
        if not result.ok:
            if op == "name":
                self._name_note = result.error
                self._name_note_color = theme.DANGER
            else:
                self._show_toast(result.error or "Something went wrong.")
            return
        if op == "relations":
            self.relations = result.relations
        elif op == "search":
            self.results = result.users
            if not result.users:
                self._show_toast("No players found by that name.")
        elif op == "name":
            if result.profile:
                self.app.backend.profile = result.profile
                self._name_note = "Saved — that name is yours."
                self._name_note_color = theme.SUCCESS
                self.app.bus.publish(Events.PROFILE_READY)
        elif op in ("sent", "accepted", "removed"):
            self._show_toast({"sent": "Friend request sent.",
                              "accepted": "You're now friends!",
                              "removed": "Done."}[op])
            self._refresh_relations()

    def _refresh_relations(self) -> None:
        social = self.app.backend.social
        if social is not None:
            social.list_relations(lambda r: self._post("relations", r))

    # -------------------------------------------------------------- actions
    def _save_name(self) -> None:
        social = self.app.backend.social
        name = self.name_input.text.strip()
        problem = username_problem(name)
        if problem:
            self._name_note = problem
            self._name_note_color = theme.DANGER
            return
        if social is None:
            self._name_note = "Sign in with a real account to set a name."
            self._name_note_color = theme.DANGER
            return
        self._name_note = "Checking..."
        self._name_note_color = theme.TEXT_FAINT
        social.set_username(name, lambda r: self._post("name", r))

    def _search(self) -> None:
        social = self.app.backend.social
        text = self.search_input.text.strip()
        if len(text) < 2:
            self._show_toast("Type at least 2 characters to search.")
            return
        if social is None:
            self._show_toast("Sign in with a real account to add friends.")
            return
        social.search_users(text, lambda r: self._post("search", r))

    def _act(self, action: str, data: dict) -> None:
        social = self.app.backend.social
        if action == "add" and social is not None:
            already = {r["user_id"] for r in self.relations}
            if data["id"] in already:
                self._show_toast("You already have a request or friendship "
                                 "with them.")
                return
            social.send_request(data["id"],
                                lambda r: self._post("sent", r))
        elif action == "accept" and social is not None:
            social.accept_request(data["id"],
                                  lambda r: self._post("accepted", r))
        elif action in ("decline", "cancel", "remove") and social is not None:
            social.remove_relation(data["id"],
                                   lambda r: self._post("removed", r))
        elif action == "challenge":
            from arcanum.scenes.matchmaking import MatchmakingScene
            self.app.scenes.push(MatchmakingScene(self.app), mode="friend",
                                 opponent=data["username"],
                                 opponent_id=data["user_id"])
        elif action == "accept_challenge":
            from arcanum.scenes.matchmaking import MatchmakingScene
            self.app.scenes.push(MatchmakingScene(self.app),
                                 mode="friend_accept",
                                 opponent=data["from"],
                                 opponent_id=data.get("from_id", ""))
        elif action == "decline_challenge":
            self.app.backend.net.send(
                MsgType.CHALLENGE_DECLINE,
                {"from_id": data.get("from_id", "")})
            self.app.backend.challenges = [
                c for c in self.app.backend.challenges
                if c.get("from") != data["from"]]

    def _show_toast(self, message: str) -> None:
        self._toast = message
        self._toast_timer = 2.8

    # -------------------------------------------------------------- network
    def _on_net_message(self, envelope=None, **_kw) -> None:
        if envelope is None:
            return
        if envelope.type == MsgType.PRESENCE_STATE.value:
            ids = envelope.payload.get("online", [])
            self._online = {str(i) for i in ids}
        elif envelope.type == MsgType.CHALLENGE_INCOMING.value:
            self._show_toast(f"{envelope.payload.get('from')} challenges "
                             "you to a match!")

    def _query_presence(self) -> None:
        friends = [r["user_id"] for r in self.relations
                   if r["kind"] == "friend"]
        if friends and self._net_connected():
            self.app.backend.net.send(MsgType.PRESENCE_QUERY,
                                      {"ids": friends})

    def _net_connected(self) -> bool:
        state = getattr(getattr(self.app.backend.net, "state", None),
                        "name", "")
        return state == "CONNECTED"

    # ---------------------------------------------------------------- frame
    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.scenes.pop()
            return
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
            if self.search_input.focused:
                self._search()
            elif self.name_input.focused:
                self._save_name()
            return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for rect, action, data in self._row_buttons:
                if rect.collidepoint(event.pos):
                    self._act(action, data)
                    return

    def update(self, dt: float) -> None:
        self._time += dt
        self._toast_timer = max(0.0, self._toast_timer - dt)
        with self._lock:
            pending, self._pending = self._pending, []
        for op, result in pending:
            try:
                self._apply(op, result)
            except Exception:  # noqa: BLE001
                log.exception("Social result handling failed")
        self._presence_timer += dt
        if self._presence_timer >= PRESENCE_EVERY:
            self._presence_timer = 0.0
            self._query_presence()
        for widget in self.widgets:
            widget.update(dt)
        hover_action = any(r.collidepoint(pygame.mouse.get_pos())
                           for r, _a, _d in self._row_buttons)
        apply_cursor(self.widgets, force_hand=hover_action)

    # ------------------------------------------------------------- drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, _h = surface.get_size()
        s = self.s
        theme.draw_text(surface, "Social", (w // 2, int(44 * s)),
                        theme.display_font(int(28 * s)), theme.GOLD_BRIGHT,
                        anchor="center")
        self.btn_back.draw(surface)
        self._row_buttons = []
        self._draw_left(surface)
        self._draw_right(surface)
        self._draw_toast(surface)

    def _panel_header(self, surface, rect, title) -> None:
        theme.draw_panel(surface, rect, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=12)
        theme.draw_text(surface, title,
                        (rect.x + int(20 * self.s),
                         rect.y + int(26 * self.s)),
                        theme.display_font(int(17 * self.s)),
                        theme.GOLD_BRIGHT, anchor="midleft")

    def _small_button(self, surface, rect, label, danger=False,
                      primary=False) -> None:
        hover = rect.collidepoint(pygame.mouse.get_pos())
        fill = theme.GOLD if primary else theme.NAVY_RAISED
        border = (theme.DANGER if danger
                  else theme.GOLD if (hover or primary) else theme.NAVY_EDGE)
        theme.draw_panel(surface, rect, fill=fill, border=border, radius=8)
        color = (theme.TEXT_ON_GOLD if primary
                 else theme.DANGER if danger else theme.TEXT)
        theme.draw_text(surface, label, rect.center,
                        theme.body_font(int(12 * self.s), bold=True), color,
                        anchor="center")

    def _draw_left(self, surface) -> None:
        s = self.s
        pad = int(20 * s)
        profile_rect = pygame.Rect(self.left.x, self.left.y,
                                   self.left.width, int(160 * s))
        self._panel_header(surface, profile_rect, "MY PROFILE")
        self.name_input.draw(surface)
        self.btn_save_name.draw(surface)
        if self._name_note:
            theme.draw_text(surface, self._name_note,
                            (profile_rect.x + pad,
                             self.name_input.rect.bottom + int(12 * s)),
                            theme.body_font(int(12 * s)),
                            self._name_note_color, anchor="topleft")
        code = str((self.app.backend.profile or {}).get("friend_code", ""))
        if code:
            pretty = f"{code[:4]}-{code[4:]}" if len(code) == 8 else code
            theme.draw_text(surface, f"Friend code:  {pretty}",
                            (profile_rect.x + pad,
                             self.name_input.rect.bottom + int(34 * s)),
                            theme.body_font(int(12 * s)), theme.GOLD_BRIGHT,
                            anchor="topleft")
        decks = "…" if self._deck_count is None else str(self._deck_count)
        theme.draw_text(surface, f"Decks saved:  {decks} / 30",
                        (profile_rect.right - pad,
                         self.name_input.rect.bottom + int(12 * s)),
                        theme.body_font(int(12 * s)), theme.TEXT_DIM,
                        anchor="topright")

        search_rect = pygame.Rect(self.left.x, profile_rect.bottom + int(16 * s),
                                  self.left.width,
                                  self.left.bottom - profile_rect.bottom
                                  - int(16 * s))
        self._panel_header(surface, search_rect, "FIND PLAYERS")
        self.search_input.draw(surface)
        self.btn_search.draw(surface)
        related = {r["user_id"] for r in self.relations}
        y = self.search_input.rect.bottom + int(16 * s)
        for user in self.results[:8]:
            row = pygame.Rect(search_rect.x + int(14 * s), y,
                              search_rect.width - int(28 * s),
                              int(ROW_H * s) - 4)
            if row.bottom > search_rect.bottom - int(10 * s):
                break
            pygame.draw.rect(surface, theme.NAVY_RAISED, row,
                             border_radius=8)
            theme.draw_text(surface, user["username"],
                            (row.x + int(14 * s), row.centery),
                            theme.body_font(int(14 * s)), theme.TEXT,
                            anchor="midleft")
            btn = pygame.Rect(0, 0, int(96 * s), int(30 * s))
            btn.midright = (row.right - int(10 * s), row.centery)
            if user["id"] in related:
                theme.draw_text(surface, "added", btn.center,
                                theme.body_font(int(12 * s)),
                                theme.TEXT_FAINT, anchor="center")
            else:
                self._small_button(surface, btn, "Add friend", primary=True)
                self._row_buttons.append((btn, "add", user))
            y += int(ROW_H * s)

    def _draw_right(self, surface) -> None:
        s = self.s
        self._panel_header(surface, self.right, "FRIENDS & REQUESTS")
        y = self.right.y + int(56 * s)
        challenges = list(self.app.backend.challenges)
        incoming = [r for r in self.relations if r["kind"] == "incoming"]
        friends = [r for r in self.relations if r["kind"] == "friend"]
        outgoing = [r for r in self.relations if r["kind"] == "outgoing"]

        def header(text, color=theme.TEXT_DIM):
            nonlocal y
            theme.draw_text(surface, text,
                            (self.right.x + int(20 * s), y),
                            theme.body_font(int(12 * s), bold=True), color,
                            anchor="topleft")
            y += int(24 * s)

        def row_rect():
            return pygame.Rect(self.right.x + int(14 * s), y,
                               self.right.width - int(28 * s),
                               int(ROW_H * s) - 4)

        if challenges:
            header("MATCH CHALLENGES", theme.GOLD_BRIGHT)
            for challenge in challenges[:3]:
                row = row_rect()
                theme.draw_glow_rect(surface, row, theme.GOLD_GLOW, 0.35,
                                     radius=8, spread=6)
                pygame.draw.rect(surface, theme.NAVY_RAISED, row,
                                 border_radius=8)
                theme.draw_text(surface,
                                f"{challenge['from']} challenges you!",
                                (row.x + int(14 * s), row.centery),
                                theme.body_font(int(13 * s), bold=True),
                                theme.GOLD_BRIGHT, anchor="midleft")
                accept = pygame.Rect(0, 0, int(84 * s), int(30 * s))
                accept.midright = (row.right - int(104 * s), row.centery)
                decline = pygame.Rect(0, 0, int(84 * s), int(30 * s))
                decline.midright = (row.right - int(10 * s), row.centery)
                self._small_button(surface, accept, "Accept", primary=True)
                self._small_button(surface, decline, "Decline", danger=True)
                self._row_buttons.append((accept, "accept_challenge",
                                          challenge))
                self._row_buttons.append((decline, "decline_challenge",
                                          challenge))
                y += int(ROW_H * s)
            y += int(8 * s)

        if incoming:
            header("FRIEND REQUESTS")
            for rel in incoming:
                row = row_rect()
                pygame.draw.rect(surface, theme.NAVY_RAISED, row,
                                 border_radius=8)
                theme.draw_text(surface, rel["username"],
                                (row.x + int(14 * s), row.centery),
                                theme.body_font(int(14 * s)), theme.TEXT,
                                anchor="midleft")
                accept = pygame.Rect(0, 0, int(84 * s), int(30 * s))
                accept.midright = (row.right - int(104 * s), row.centery)
                decline = pygame.Rect(0, 0, int(84 * s), int(30 * s))
                decline.midright = (row.right - int(10 * s), row.centery)
                self._small_button(surface, accept, "Accept", primary=True)
                self._small_button(surface, decline, "Decline", danger=True)
                self._row_buttons.append((accept, "accept", rel))
                self._row_buttons.append((decline, "decline", rel))
                y += int(ROW_H * s)
            y += int(8 * s)

        header(f"FRIENDS  ({len(friends)})")
        if not friends:
            theme.draw_text(surface,
                            "No friends yet — find players on the left.",
                            (self.right.x + int(20 * s), y + int(6 * s)),
                            theme.body_font(int(13 * s)), theme.TEXT_FAINT,
                            anchor="topleft")
            y += int(36 * s)
        for rel in friends:
            row = row_rect()
            if row.bottom > self.right.bottom - int(70 * s):
                theme.draw_text(surface, "…",
                                (row.centerx, row.y),
                                theme.body_font(int(14 * s)),
                                theme.TEXT_FAINT, anchor="midtop")
                break
            pygame.draw.rect(surface, theme.NAVY_RAISED, row,
                             border_radius=8)
            online = rel["user_id"] in self._online
            dot_color = theme.SUCCESS if online else theme.NAVY_EDGE
            theme.aa_circle(surface, dot_color,
                            (row.x + int(16 * s), row.centery), int(5 * s))
            theme.draw_text(surface, rel["username"],
                            (row.x + int(30 * s), row.centery),
                            theme.body_font(int(14 * s)), theme.TEXT,
                            anchor="midleft")
            challenge = pygame.Rect(0, 0, int(96 * s), int(30 * s))
            challenge.midright = (row.right - int(96 * s), row.centery)
            remove = pygame.Rect(0, 0, int(76 * s), int(30 * s))
            remove.midright = (row.right - int(10 * s), row.centery)
            if online:
                self._small_button(surface, challenge, "Challenge",
                                   primary=True)
                self._row_buttons.append((challenge, "challenge", rel))
            else:
                theme.draw_text(surface, "offline", challenge.center,
                                theme.body_font(int(11 * s)),
                                theme.TEXT_FAINT, anchor="center")
            self._small_button(surface, remove, "Remove", danger=True)
            self._row_buttons.append((remove, "remove", rel))
            y += int(ROW_H * s)

        if outgoing:
            y += int(8 * s)
            header("SENT REQUESTS")
            for rel in outgoing:
                row = row_rect()
                if row.bottom > self.right.bottom - int(12 * s):
                    break
                pygame.draw.rect(surface, theme.NAVY_RAISED, row,
                                 border_radius=8)
                theme.draw_text(surface, f"{rel['username']}  ·  pending",
                                (row.x + int(14 * s), row.centery),
                                theme.body_font(int(13 * s)),
                                theme.TEXT_DIM, anchor="midleft")
                cancel = pygame.Rect(0, 0, int(76 * s), int(30 * s))
                cancel.midright = (row.right - int(10 * s), row.centery)
                self._small_button(surface, cancel, "Cancel", danger=True)
                self._row_buttons.append((cancel, "cancel", rel))
                y += int(ROW_H * s)

    def _draw_toast(self, surface) -> None:
        if self._toast_timer <= 0 or not self._toast:
            return
        w, h = surface.get_size()
        fade = min(1.0, self._toast_timer / 0.4)
        box = pygame.Rect(0, 0, min(w - 80, 560), 42)
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
