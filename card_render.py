"""Card template renderer (PIL) — turns a CardSpec + art into a finished card.

Used by the Card Designer for the live preview and for the final image that
gets published. Art is composited UNDER the frame (the template's art window
is transparent), so the gold border overlaps it naturally. All coordinates
were measured from assets/templates/hero.png (1033 x 1522).
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "assets" / "templates"

# measured geometry for hero.png (template-space pixels)
TEMPLATE_SIZE = (1033, 1522)
ART_WINDOW = (116, 261, 917, 881)          # l, t, r, b  (801 x 620)
NAME_CENTER = (540, 141)
NAME_MAX_W = 600
TYPE_CENTER = (516, 218)
TYPE_MAX_W = 620
COST_CENTER = (110, 150)
RARITY_CENTER = (924, 147)
RARITY_RADIUS = 54
RULES_BOX = (140, 940, 895, 1240)          # l, t, r, b
FLAVOR_CENTER = (516, 1285)
FLAVOR_MAX_W = 680
ID_POS = (150, 1392)
STATS_CENTER = (822, 1387)

RARITY_COLORS = {"common": (154, 163, 178), "uncommon": (76, 175, 125),
                 "rare": (86, 156, 255), "mythic": (212, 175, 55)}

_FONT_CANDIDATES = ("georgia.ttf", "georgiab.ttf", "times.ttf",
                    "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf")


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (("georgiab.ttf", "timesbd.ttf", "DejaVuSerif-Bold.ttf")
             if bold else ("georgia.ttf", "times.ttf", "DejaVuSerif.ttf"))
    for name in names + _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text: str, font_size: int, max_width: int, bold=False):
    """Shrink the font until the text fits max_width."""
    size = font_size
    while size > 10:
        font = _load_font(size, bold)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 2
    return _load_font(10, bold)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words, line = paragraph.split(), ""
        for word in words:
            probe = f"{line} {word}".strip()
            if draw.textlength(probe, font=font) > max_width and line:
                lines.append(line)
                line = word
            else:
                line = probe
        lines.append(line)
    return lines


class HeroCardRenderer:
    """Renders hero cards. More templates slot in as they're provided."""

    def __init__(self, template_path: Path | None = None) -> None:
        from PIL import Image
        path = template_path or (TEMPLATE_DIR / "hero.png")
        self.template = Image.open(path).convert("RGBA")
        assert self.template.size == TEMPLATE_SIZE, \
            f"template size changed: {self.template.size}"

    # ------------------------------------------------------------------ art
    def art_layer(self, art, offset=(0.0, 0.0), zoom: float = 1.0):
        """Place art in the window: cover-fit * zoom, panned by offset
        (template-space pixels), clipped to the window."""
        from PIL import Image
        left, top, right, bottom = ART_WINDOW
        win_w, win_h = right - left, bottom - top
        layer = Image.new("RGBA", TEMPLATE_SIZE, (10, 16, 36, 255))
        if art is not None:
            art = art.convert("RGB")
            base = max(win_w / art.width, win_h / art.height)
            scale = base * max(0.2, zoom)
            new_size = (max(1, round(art.width * scale)),
                        max(1, round(art.height * scale)))
            scaled = art.resize(new_size, Image.LANCZOS)
            x = left + (win_w - scaled.width) // 2 + round(offset[0])
            y = top + (win_h - scaled.height) // 2 + round(offset[1])
            layer.paste(scaled, (x, y))
        # clip to the window only
        window = Image.new("RGBA", TEMPLATE_SIZE, (0, 0, 0, 0))
        window.paste(layer.crop(ART_WINDOW), (left, top))
        return window

    # ------------------------------------------------------------------ card
    def render(self, spec, art=None, offset=(0.0, 0.0), zoom: float = 1.0):
        """Full card image (template resolution)."""
        from PIL import Image, ImageDraw
        card = Image.new("RGBA", TEMPLATE_SIZE, (0, 0, 0, 0))
        card.alpha_composite(self.art_layer(art, offset, zoom))
        card.alpha_composite(self.template)
        draw = ImageDraw.Draw(card)
        gold, text_c, dim = (232, 205, 128), (232, 230, 223), (150, 156, 178)

        # name + type line
        font = _fit_text(draw, spec.name, 46, NAME_MAX_W, bold=True)
        draw.text(NAME_CENTER, spec.name, font=font, fill=gold, anchor="mm")
        type_line = spec.card_type.value.title()
        if spec.hero_types:
            type_line += "   —   " + " / ".join(spec.hero_types)
        font = _fit_text(draw, type_line, 34, TYPE_MAX_W)
        draw.text(TYPE_CENTER, type_line, font=font, fill=text_c, anchor="mm")

        # cost + rarity ring
        draw.text(COST_CENTER, str(spec.cost), font=_load_font(84, bold=True),
                  fill=(255, 255, 255), anchor="mm",
                  stroke_width=3, stroke_fill=(20, 40, 90))
        ring = RARITY_COLORS.get(spec.rarity.value, dim)
        cx, cy = RARITY_CENTER
        draw.ellipse((cx - RARITY_RADIUS, cy - RARITY_RADIUS,
                      cx + RARITY_RADIUS, cy + RARITY_RADIUS),
                     outline=ring, width=6)

        # rules text
        left, top, right, bottom = RULES_BOX
        box_w = right - left
        font_size = 30
        while font_size >= 16:
            font = _load_font(font_size)
            lines = _wrap(draw, spec.composed_text(), font, box_w)
            line_h = font_size + 8
            if len(lines) * line_h <= (bottom - top):
                break
            font_size -= 2
        y = top
        for line in lines:
            draw.text((left, y), line, font=font, fill=text_c)
            y += line_h

        # flavor + id + stats
        if spec.flavor:
            font = _fit_text(draw, f"“{spec.flavor}”", 28, FLAVOR_MAX_W)
            draw.text(FLAVOR_CENTER, f"“{spec.flavor}”", font=font,
                      fill=dim, anchor="mm")
        draw.text(ID_POS, f"ID {spec.id}", font=_load_font(24),
                  fill=dim, anchor="lm")
        draw.text(STATS_CENTER, f"{spec.attack} / {spec.health}",
                  font=_load_font(60, bold=True), fill=(255, 244, 214),
                  anchor="mm", stroke_width=2, stroke_fill=(40, 30, 8))
        return card

    def render_png(self, spec, art=None, offset=(0.0, 0.0), zoom: float = 1.0,
                   out_path: Path | None = None, height: int = 914) -> Path:
        """Publish-quality PNG scaled to `height` (keeps card bandwidth sane)."""
        from PIL import Image
        card = self.render(spec, art, offset, zoom)
        scale = height / card.height
        card = card.resize((round(card.width * scale), height), Image.LANCZOS)
        out = out_path or Path("data") / "_render.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        card.convert("RGBA").save(out, "PNG", optimize=True)
        return out
