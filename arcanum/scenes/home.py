"""Home hub — MTGA-style main menu.

Layout: top navigation bar (Home / Profile / Decks / Packs / Store / Mastery)
with currency chips and a settings gear on the right; a featured-news
carousel center-left; game-mode panels on the right column; a quest-medallion
row along the bottom; the player identity bottom-left; and the big Play
button (versus matchmaking, click again to cancel) bottom-right.
"""
from __future__ import annotations

import math

import pygame

from arcanum.core.constants import APP_NAME, IMAGES_DIR, ROOT_DIR
from arcanum.core.events import Events
from arcanum.core.scene import Scene
from arcanum.ui import theme
from arcanum.ui.animation import approach
from arcanum.ui.widgets import Button, LinkButton, apply_cursor

NAV_ITEMS = ("Home", "Social", "Decks", "Packs", "Store")

SLIDES = (
    {"title": "Welcome to Arcanum",
     "body": "A collectible card battler of stars and gold. Champions "
             "clash across the astral table.",
     "image": "hub.jpg", "logo": "arcanum_logo.png"},
    {"title": "Live Matchmaking",
     "body": "Every card you play is validated by the Arcanum server. "
             "Queue up and duel real opponents across the aether.",
     "image": "background.jpg"},
    {"title": "Introducing Packs",
     "body": "Tear open Adventure, Wonder, and Cosmic packs — collect "
             "heroes, spells, and relics to build your decks.",
     "image": "introducing_packs.png"},
)


def _load_art(name: str):
    """Load a background image (assets/images first, project root fallback)."""
    for path in (IMAGES_DIR / name, ROOT_DIR / name):
        if path.is_file():
            try:
                surface = pygame.image.load(str(path))
                if name.lower().endswith(".png"):
                    return surface.convert_alpha()
                return surface.convert()
            except pygame.error as exc:
                import logging
                logging.getLogger(__name__).warning("Could not load %s: %s",
                                                    path, exc)
    return None


class HomeScene(Scene):
    def on_enter(self, **kwargs) -> None:
        self._time = 0.0
        self.toast = ""
        self._toast_timer = 0.0
        self._slide = 0
        self._slide_timer = 0.0
        self._nav_hover: int | None = None
        self._panel_hover: int | None = None
        self._nav_glow = [0.0] * len(NAV_ITEMS)
        self._hub_art = _load_art("hub.jpg")
        self.app.background.set_image(self._hub_art)
        self._build()

    def on_exit(self) -> None:
        pass

    def on_resize(self, size) -> None:
        if hasattr(self, "_time"):
            self._build()

    def _slide_art(self, name: str, size: tuple[int, int]):
        """Slide image cover-cropped to the carousel panel, cached."""
        cache = getattr(self, "_slide_cache", None)
        if cache is None:
            cache = self._slide_cache = {}
        key = (name, size)
        if key in cache:
            return cache[key]
        raw = _load_art(name)
        result = None
        if raw is not None:
            try:
                scale = max(size[0] / raw.get_width(),
                            size[1] / raw.get_height())
                scaled = pygame.transform.smoothscale(
                    raw, (max(1, round(raw.get_width() * scale)),
                          max(1, round(raw.get_height() * scale))))
                result = pygame.Surface(size, pygame.SRCALPHA)
                result.blit(scaled, ((size[0] - scaled.get_width()) // 2,
                                     (size[1] - scaled.get_height()) // 2))
                # bottom gradient so slide text stays readable
                grad_h = size[1] // 2
                grad = pygame.Surface((size[0], grad_h), pygame.SRCALPHA)
                for i in range(grad_h):
                    alpha = int(215 * (i / grad_h) ** 1.4)
                    grad.fill((6, 9, 22, alpha),
                              rect=pygame.Rect(0, i, size[0], 1))
                result.blit(grad, (0, size[1] - grad_h))
            except (pygame.error, ValueError):
                result = None
        cache[key] = result
        return result

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        w, h = self.app.screen.get_size()
        s = max(0.72, min(1.3, h / 1080))
        self.s = s
        ui = self.app.audio.ui_sound

        # top nav bar
        self.nav_rects: list[pygame.Rect] = []
        x = int(28 * s)
        for label in NAV_ITEMS:
            width = theme.body_font(int(19 * s), bold=True).size(label)[0]
            self.nav_rects.append(pygame.Rect(x, int(14 * s),
                                              width + int(28 * s), int(40 * s)))
            x += width + int(46 * s)
        self.btn_gear = Button(pygame.Rect(w - int(78 * s), int(14 * s),
                                           int(58 * s), int(40 * s)), "ESC",
                               self._settings, primary=False, font_size=15,
                               sound_cb=ui)

        # featured carousel (center-left)
        self.carousel = pygame.Rect(int(60 * s), int(120 * s),
                                    int(w * 0.52), int(h * 0.50))
        arrow_y = self.carousel.centery
        self.btn_prev = Button(pygame.Rect(self.carousel.x + int(10 * s),
                                           arrow_y - int(22 * s), int(40 * s),
                                           int(44 * s)), "‹",
                               lambda: self._change_slide(-1), primary=False,
                               font_size=24, sound_cb=ui)
        self.btn_next = Button(pygame.Rect(self.carousel.right - int(50 * s),
                                           arrow_y - int(22 * s), int(40 * s),
                                           int(44 * s)), "›",
                               lambda: self._change_slide(1), primary=False,
                               font_size=24, sound_cb=ui)

        # mode panels (right column)
        panel_x = self.carousel.right + int(28 * s)
        panel_w = w - panel_x - int(40 * s)
        self.mode_panels = []
        modes = [
            ("Duel the Umbral Adept", "Server-hosted match vs AI",
             self._play_ai_online),
            ("Practice Match", "Offline, on your machine", self._practice),
            ("Ranked Season", "Coming soon", lambda: self._todo("Ranked")),
        ]
        py = self.carousel.y
        ph = (self.carousel.height - int(24 * s)) // 3
        for title, sub, action in modes:
            rect = pygame.Rect(panel_x, py, panel_w, ph)
            self.mode_panels.append((rect, title, sub, action))
            py += ph + int(12 * s)

        # big Play (versus queue toggle), bottom-right
        self.btn_play = Button(pygame.Rect(w - int(300 * s), h - int(110 * s),
                                           int(250 * s), int(66 * s)), "Play",
                               self._play, sound_cb=ui, font_size=24)
        # identity + logout, bottom-left
        self.lnk_logout = LinkButton((int(150 * s), h - int(34 * s)),
                                     "Log out", self._logout, font_size=18,
                                     anchor="midleft")
        self.widgets = [self.btn_gear, self.btn_prev, self.btn_next,
                        self.btn_play, self.lnk_logout]


        # quest medallions (placeholder progression row)
        self.medallions = []
        mx = w // 2 - int(150 * s)
        for i in range(4):
            self.medallions.append((mx + i * int(90 * s), h - int(76 * s)))

    # ------------------------------------------------------------ helpers
    def _net_connected(self) -> bool:
        state = getattr(self.app.backend.net, "state", None)
        return getattr(state, "name", "") == "CONNECTED"

    def _local_name(self) -> str:
        user = self.app.backend.session.user
        return user.username if user else "You"

    def _change_slide(self, step: int) -> None:
        self._slide = (self._slide + step) % len(SLIDES)
        self._slide_timer = 0.0

    def _start_matchmaking(self, mode: str) -> None:
        from arcanum.scenes.matchmaking import MatchmakingScene
        self.app.scenes.push(MatchmakingScene(self.app), mode=mode)

    def _play(self) -> None:
        if self._net_connected():
            self._start_matchmaking("pvp")
        else:
            self._start_matchmaking("practice")

    def _play_ai_online(self) -> None:
        if not self._net_connected():
            self._show_toast("Server offline — try Practice instead.")
            return
        self._start_matchmaking("ai")

    def _practice(self) -> None:
        self._start_matchmaking("practice")

    def _open_decks(self) -> None:
        from arcanum.scenes.deckbuilder import DeckBuilderScene
        self.app.scenes.push(DeckBuilderScene(self.app))

    def _nav_action(self, index: int) -> None:
        label = NAV_ITEMS[index]
        if label == "Decks":
            self._open_decks()
        elif label == "Social":
            from arcanum.scenes.social import SocialScene
            self.app.scenes.push(SocialScene(self.app))
        elif label == "Packs":
            from arcanum.scenes.packs import PacksScene
            self.app.scenes.push(PacksScene(self.app))
        elif label == "Store":
            from arcanum.scenes.store import StoreScene
            self.app.scenes.push(StoreScene(self.app))
        elif label != "Home":
            self._todo(label)

    def _settings(self) -> None:
        from arcanum.scenes.settings import SettingsScene
        self.app.scenes.push(SettingsScene(self.app))

    def _logout(self) -> None:
        self.app.backend.session.end()
        self.app.bus.publish(Events.AUTH_LOGOUT)
        self.app.goto_login()

    def _todo(self, name: str) -> None:
        self._show_toast(f"{name} is on the roadmap — not built yet.")

    def _show_toast(self, message: str) -> None:
        self.toast = message
        self._toast_timer = 3.0

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for rect, slot in getattr(self, "_claim_rects", []):
                if rect.collidepoint(event.pos):
                    self._claim(slot)
                    return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._settings()
            return
        for widget in self.widgets:
            if widget.handle_event(event):
                return
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            for i, rect in enumerate(self.nav_rects):
                if rect.collidepoint(event.pos):
                    self._nav_action(i)
                    return
            for i, (rect, _t, _sub, action) in enumerate(self.mode_panels):
                if rect.collidepoint(event.pos):
                    action()
                    return

    def _claim(self, slot: int) -> None:
        from arcanum.services.net.protocol import MsgType
        self.app.backend.net.send(MsgType.CLAIM_CHALLENGE, {"slot": slot})
        self.app.audio.ui_sound("confirm")

    def _draw_dailies(self, surface) -> None:
        """Daily challenges + rewarded-wins pips, bottom-right of the hub."""
        self._claim_rects = []
        wallet = self.app.backend.wallet
        if not wallet.get("enabled"):
            return
        s = self.s
        w, h = surface.get_size()
        dailies = list(wallet.get("dailies", []))[:3]
        panel = pygame.Rect(0, 0, int(380 * s),
                            int(66 * s) + len(dailies) * int(52 * s))
        panel.bottomright = (w - int(24 * s), h - int(24 * s))
        theme.draw_panel(surface, panel, fill=theme.NAVY,
                         border=theme.NAVY_EDGE, radius=14)
        theme.draw_text(surface, "DAILY CHALLENGES",
                        (panel.x + int(16 * s), panel.y + int(20 * s)),
                        theme.body_font(int(12 * s), bold=True),
                        theme.GOLD_BRIGHT, anchor="midleft")
        wins = int(wallet.get("wins_today", 0) or 0)
        for i in range(3):
            color = theme.GOLD if i < wins else theme.NAVY_EDGE
            theme.aa_circle(surface, color,
                            (panel.right - int(16 * s) - i * int(18 * s),
                             panel.y + int(20 * s)), int(6 * s))
        theme.draw_text(surface, f"wins {wins}/3",
                        (panel.right - int(70 * s), panel.y + int(20 * s)),
                        theme.body_font(int(10 * s)), theme.TEXT_DIM,
                        anchor="midright")
        y = panel.y + int(42 * s)
        for entry in dailies:
            name = entry.get("name", entry.get("id", "?"))
            progress = int(entry.get("progress", 0))
            goal = max(1, int(entry.get("goal", 1)))
            claimed = bool(entry.get("claimed"))
            ready = progress >= goal and not claimed
            theme.draw_text(surface, name,
                            (panel.x + int(16 * s), y + int(8 * s)),
                            theme.body_font(int(12 * s)),
                            theme.TEXT_FAINT if claimed else theme.TEXT,
                            anchor="midleft")
            bar = pygame.Rect(panel.x + int(16 * s), y + int(22 * s),
                              int(240 * s), int(8 * s))
            pygame.draw.rect(surface, theme.NAVY_RAISED, bar,
                             border_radius=4)
            fill = bar.copy()
            fill.width = int(bar.width * min(1.0, progress / goal))
            if fill.width > 2:
                pygame.draw.rect(surface, theme.SUCCESS if ready or claimed
                                 else theme.GOLD, fill, border_radius=4)
            theme.draw_text(surface, f"{min(progress, goal)}/{goal}",
                            (bar.right + int(10 * s), bar.centery),
                            theme.body_font(int(10 * s)), theme.TEXT_DIM,
                            anchor="midleft")
            if claimed:
                theme.draw_text(surface, "claimed ✓",
                                (panel.right - int(16 * s), y + int(14 * s)),
                                theme.body_font(int(11 * s)), theme.SUCCESS,
                                anchor="midright")
            elif ready:
                btn = pygame.Rect(0, 0, int(76 * s), int(26 * s))
                btn.midright = (panel.right - int(14 * s), y + int(14 * s))
                pulse = 0.4 + 0.25 * abs(math.sin(self._time * 3.2))
                theme.draw_glow_rect(surface, btn, theme.GOLD_GLOW, pulse,
                                     radius=8, spread=7)
                theme.draw_panel(surface, btn, fill=theme.GOLD,
                                 border=theme.GOLD, radius=8)
                theme.draw_text(surface, "+300", btn.center,
                                theme.body_font(int(12 * s), bold=True),
                                theme.TEXT_ON_GOLD, anchor="center")
                self._claim_rects.append((btn, int(entry.get("slot", 0))))
            y += int(52 * s)

    def _pump_rewards(self, dt: float) -> None:
        if not hasattr(self, "_banner"):
            self._banner = None
            self._banner_t = 0.0
        if self._banner is not None:
            self._banner_t += dt
            if self._banner_t > 4.2:
                self._banner = None
            return
        inbox = self.app.backend.pending_rewards
        if not inbox:
            return
        reward = inbox.pop(0)
        lines: list[str] = []
        if reward.get("type") == "welcome":
            lines = ["Welcome to Arcanum!",
                     f"You've been gifted {reward.get('packs', 10)} "
                     "Adventure Packs.",
                     "Open them in the Packs tab — good luck, mage."]
        else:
            if reward.get("gold_awarded"):
                lines.append(f"Victory reward:  +{reward['gold_awarded']} "
                             f"gold  (win {reward.get('win_number', '?')}/3 "
                             "today)")
            for done in reward.get("challenges_completed", [])[:3]:
                lines.append(f"Challenge complete:  {done.get('name', '?')} "
                             "— 300 gold ready to claim!")
        if lines:
            self._banner = lines
            self._banner_t = 0.0
            self.app.audio.ui_sound("confirm")

    def _draw_banner(self, surface) -> None:
        banner = getattr(self, "_banner", None)
        if not banner:
            return
        w, h = surface.get_size()
        t = self._banner_t
        slide = min(1.0, t * 3.0)
        fade = min(1.0, (4.2 - t) / 0.5)
        alpha = int(255 * max(0.0, min(slide, fade)))
        box_h = 34 + 26 * len(banner)
        box = pygame.Rect(0, 0, min(w - 80, 640), box_h)
        box.midtop = (w // 2, int(-box_h + slide * (box_h + 84)))
        veil = pygame.Surface(box.size, pygame.SRCALPHA)
        pygame.draw.rect(veil, (*theme.NAVY, min(240, alpha)),
                         veil.get_rect(), border_radius=14)
        pygame.draw.rect(veil, (*theme.GOLD, alpha), veil.get_rect(),
                         width=2, border_radius=14)
        surface.blit(veil, box.topleft)
        pulse = 0.35 + 0.2 * abs(math.sin(self._time * 3))
        theme.draw_glow_rect(surface, box, theme.GOLD_GLOW, pulse * slide,
                             radius=14, spread=14)
        y = box.y + 22
        for i, line in enumerate(banner):
            theme.draw_text(surface, line, (box.centerx, y),
                            theme.body_font(16, bold=(i == 0)),
                            theme.GOLD_BRIGHT if i == 0 else theme.TEXT,
                            anchor="center", alpha=alpha)
            y += 26

    def _watch_challenges(self) -> None:
        count = len(self.app.backend.challenges)
        if count > getattr(self, "_seen_challenges", 0):
            newest = self.app.backend.challenges[-1].get("from", "Someone")
            self._show_toast(f"{newest} challenges you! Open Social to "
                             "answer.")
        self._seen_challenges = count

    def update(self, dt: float) -> None:
        self._time += dt
        self._watch_challenges()
        self._pump_rewards(dt)
        self._toast_timer = max(0.0, self._toast_timer - dt)
        self._slide_timer += dt
        if self._slide_timer > 7.0:
            self._change_slide(1)
        mouse = pygame.mouse.get_pos()
        self._nav_hover = next((i for i, r in enumerate(self.nav_rects)
                                if r.collidepoint(mouse)), None)
        self._panel_hover = next((i for i, (r, *_rest) in
                                  enumerate(self.mode_panels)
                                  if r.collidepoint(mouse)), None)
        for i in range(len(NAV_ITEMS)):
            target = 1.0 if i == self._nav_hover else 0.0
            self._nav_glow[i] = approach(self._nav_glow[i], target, dt)

        for widget in self.widgets:
            widget.update(dt)
        apply_cursor(self.widgets,
                     force_hand=(self._nav_hover is not None
                                 or self._panel_hover is not None))

    def _avatar_icon(self, size: int):
        cached = getattr(self, "_avatar_cache", None)
        if cached and cached[0] == size:
            return cached[1]
        icon = None
        raw = _load_art("arcanum_logo.png")
        if raw is not None:
            try:
                icon = pygame.transform.smoothscale(raw, (size, size))
            except (pygame.error, ValueError):
                icon = None
        self._avatar_cache = (size, icon)
        return icon

    def _coin_icon(self, size: int):
        cached = getattr(self, "_coin_cache", None)
        if cached and cached[0] == size:
            return cached[1]
        icon = None
        for path in (IMAGES_DIR / "coin.png", ROOT_DIR / "coin.png"):
            if path.is_file():
                try:
                    raw = pygame.image.load(str(path)).convert_alpha()
                    icon = pygame.transform.smoothscale(raw, (size, size))
                    break
                except pygame.error:
                    pass
        self._coin_cache = (size, icon)
        return icon

    # ------------------------------------------------------------ drawing
    def draw(self, surface: pygame.Surface) -> None:
        self.app.background.draw(surface)
        w, h = surface.get_size()
        s = self.s

        # nav bar
        bar = pygame.Rect(0, 0, w, int(66 * s))
        veil = pygame.Surface(bar.size, pygame.SRCALPHA)
        veil.fill((*theme.NAVY_ABYSS, 200))
        surface.blit(veil, (0, 0))
        pygame.draw.line(surface, theme.GOLD_DIM, (0, bar.bottom),
                         (w, bar.bottom))
        for i, label in enumerate(NAV_ITEMS):
            rect = self.nav_rects[i]
            active = label == "Home"
            glow = self._nav_glow[i]
            color = theme.GOLD_BRIGHT if active else tuple(
                int(theme.TEXT_DIM[c] + (theme.TEXT[c] - theme.TEXT_DIM[c]) * glow)
                for c in range(3))
            theme.draw_text(surface, label, rect.center,
                            theme.body_font(int(19 * s), bold=True), color,
                            anchor="center")
            if active:
                pygame.draw.line(surface, theme.GOLD,
                                 (rect.x + 6, bar.bottom - 2),
                                 (rect.right - 6, bar.bottom - 2), 2)
        # currency chips: coins (coin.png) + essence placeholder
        chip_x = w - int(300 * s)
        coin = self._coin_icon(int(22 * s))
        gold = f"{self.app.backend.gold:,}"
        packs_n = str(sum(self.app.backend.packs_owned.values()))
        for icon, icon_color, amount in ((coin, theme.GOLD, gold),
                                         (None, (86, 156, 255), packs_n)):
            chip = pygame.Rect(chip_x, int(18 * s), int(96 * s), int(32 * s))
            theme.draw_panel(surface, chip, fill=theme.NAVY,
                             border=theme.NAVY_EDGE, radius=16)
            if icon is not None:
                surface.blit(icon, icon.get_rect(
                    center=(chip.x + int(17 * s), chip.centery)))
            else:
                theme.aa_circle(surface, icon_color,
                                (chip.x + int(16 * s), chip.centery),
                                int(9 * s))
            theme.draw_text(surface, amount,
                            (chip.x + int(32 * s), chip.centery),
                            theme.body_font(int(14 * s)), theme.TEXT,
                            anchor="midleft")
            chip_x += int(108 * s)

        # carousel
        theme.draw_glow_rect(surface, self.carousel, theme.GOLD, 0.2,
                             radius=14, spread=12)
        theme.draw_panel(surface, self.carousel, fill=theme.NAVY,
                         border=theme.GOLD_DIM, radius=14)
        slide = SLIDES[self._slide]
        inner = self.carousel.inflate(-6, -6)
        art = self._slide_art(slide["image"], inner.size)
        if art is not None:
            surface.blit(art, inner.topleft)
        logo_name = slide.get("logo")
        if logo_name:
            logo = self._slide_art(logo_name, (int(220 * s), int(220 * s)))
            raw_logo = _load_art(logo_name)
            if raw_logo is not None:
                lh = int(self.carousel.height * 0.42)
                lw = int(raw_logo.get_width() * lh / raw_logo.get_height())
                logo_scaled = pygame.transform.smoothscale(raw_logo, (lw, lh))
                surface.blit(logo_scaled, logo_scaled.get_rect(
                    center=(self.carousel.centerx,
                            self.carousel.y + int(self.carousel.height * 0.34))))
        theme.draw_text(surface, slide["title"],
                        (self.carousel.x + int(70 * s),
                         self.carousel.bottom - int(104 * s)),
                        theme.display_font(int(30 * s), bold=True),
                        theme.GOLD_BRIGHT, anchor="topleft")
        font = theme.body_font(int(16 * s), bold=True)
        words, line = slide["body"].split(), ""
        ty = self.carousel.bottom - int(60 * s)
        max_w = self.carousel.width - int(140 * s)
        for word in words + ["\n"]:
            probe = f"{line} {word}".strip()
            if word == "\n" or font.size(probe)[0] > max_w:
                theme.draw_text(surface, line,
                                (self.carousel.x + int(70 * s), ty), font,
                                theme.TEXT, anchor="topleft")
                ty += font.get_linesize()
                line = word if word != "\n" else ""
            else:
                line = probe
        for i in range(len(SLIDES)):
            dot = (self.carousel.centerx + (i - 1) * int(22 * s),
                   self.carousel.bottom - int(16 * s))
            color = theme.GOLD if i == self._slide else theme.NAVY_EDGE
            theme.aa_circle(surface, color, dot, int(4 * s))

        # mode panels
        for i, (rect, title, sub, _a) in enumerate(self.mode_panels):
            hover = i == self._panel_hover
            if hover:
                theme.draw_glow_rect(surface, rect, theme.GOLD_GLOW, 0.4,
                                     radius=12, spread=8)
            theme.draw_panel(surface, rect, fill=theme.NAVY,
                             border=theme.GOLD if hover else theme.NAVY_EDGE,
                             radius=12)
            theme.draw_text(surface, title,
                            (rect.x + int(18 * s), rect.y + int(16 * s)),
                            theme.body_font(int(19 * s), bold=True),
                            theme.TEXT, anchor="topleft")
            theme.draw_text(surface, sub,
                            (rect.x + int(18 * s), rect.y + int(42 * s)),
                            theme.body_font(int(13 * s)), theme.TEXT_DIM,
                            anchor="topleft")

        # quest medallions (placeholders)
        for cx, cy in self.medallions:
            theme.aa_circle(surface, theme.NAVY_RAISED, (cx, cy), int(26 * s))
            theme.aa_circle(surface, theme.NAVY_EDGE, (cx, cy), int(26 * s),
                            width=1)
            theme.draw_text(surface, "?", (cx, cy),
                            theme.body_font(int(16 * s)), theme.TEXT_FAINT,
                            anchor="center")
        theme.draw_text(surface, "Quests — coming soon",
                        (self.medallions[0][0] - int(40 * s),
                         self.medallions[0][1] + int(40 * s)),
                        theme.body_font(int(12 * s)), theme.TEXT_FAINT,
                        anchor="topleft")

        # identity + connection status, bottom-left
        avatar = (int(60 * s), h - int(70 * s))
        theme.aa_circle(surface, theme.NAVY_RAISED, avatar, int(30 * s))
        logo = self._avatar_icon(int(52 * s))
        if logo is not None:
            surface.blit(logo, logo.get_rect(center=avatar))
        theme.aa_circle(surface, theme.GOLD_DIM, avatar, int(30 * s), width=2)
        user = self.app.backend.session.user
        name = self.app.backend.display_name + \
            ("  ·  temp" if user and user.is_guest else "")
        theme.draw_text(surface, name, (int(104 * s), h - int(84 * s)),
                        theme.body_font(int(18 * s), bold=True), theme.TEXT,
                        anchor="topleft")
        net = self.app.backend.net
        state_name = getattr(getattr(net, "state", None), "name", "DISCONNECTED")
        if state_name == "CONNECTED":
            bits = ["Online"]
            if getattr(net, "online_count", None):
                bits.append(f"{net.online_count} in the aether")
            if getattr(net, "latency_ms", None) is not None:
                bits.append(f"{net.latency_ms} ms")
            status, color = "  ·  ".join(bits), theme.SUCCESS
        elif state_name in ("CONNECTING", "RECONNECTING"):
            status, color = "Connecting...", theme.TEXT_DIM
        else:
            status, color = "Offline — practice only", theme.TEXT_FAINT
        theme.draw_text(surface, status, (int(104 * s), h - int(62 * s)),
                        theme.body_font(int(13 * s)), color, anchor="topleft")

        for widget in self.widgets:
            widget.draw(surface)

        if self._toast_timer > 0 and self.toast:
            fade = min(1.0, self._toast_timer / 0.4)
            box = pygame.Rect(0, 0, min(w - 80, 560), 44)
            box.midbottom = (w // 2, h - int(120 * s))
            veil = pygame.Surface(box.size, pygame.SRCALPHA)
            pygame.draw.rect(veil, (*theme.NAVY_RAISED, int(230 * fade)),
                             veil.get_rect(), border_radius=10)
            pygame.draw.rect(veil, (*theme.GOLD_DIM, int(255 * fade)),
                             veil.get_rect(), width=1, border_radius=10)
            surface.blit(veil, box.topleft)
            theme.draw_text(surface, self.toast, box.center,
                            theme.body_font(15), theme.TEXT, anchor="center",
                            alpha=int(255 * fade))
        self._draw_dailies(surface)
        self._draw_banner(surface)
