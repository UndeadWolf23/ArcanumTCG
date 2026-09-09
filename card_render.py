"""Card template renderer (PIL) — CardSpec + art -> finished card image.

Five rarity frames (assets/templates/{rarity}.png, 1065x1477) share one
layout; rarity is communicated by the frame itself. Layer order:

  1. navy backing  — fills the card silhouette so the finished card is
                     opaque inside the frame (the text panel is translucent)
  2. art           — cover-fits the core region (picture window through the
                     text box), pan/zoom applied, clipped so it can never
                     escape the frame silhouette or leak into filigree holes
  3. the frame     — composited over the art
  4. text          — bright white with a soft drop shadow

All coordinates measured per template; the five frames agree to within a few
pixels, so shared metrics use the measured consensus with per-frame values
where they differ (gem, stats plaque).
"""
from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "assets" / "templates"

TEMPLATE_SIZE = (1065, 1477)

# core region: art may live here (picture window + text panel + the frame
# above them); filigree holes outside it stay transparent
ART_CORE = (100, 262, 965, 1400)           # l, t, r, b

# shared text layout (trim-line consensus across all five frames)
NAME_CENTER = (545, 159)
NAME_MAX_W = 600
TYPE_CENTER = (535, 237)
TYPE_MAX_W = 620
RULES_BOX = (110, 986, 958, 1232)
FLAVOR_CENTER = (500, 1262)
FLAVOR_MAX_W = 620

# corrections requested after visual review: cost rides up-left in its gem,
# stats ride up-right in the plaque
COST_NUDGE = (-27, -23)
STATS_NUDGE = (14, -12)

# per-frame measured points
FRAME_METRICS = {
    "common":    {"gem": (161, 177), "stats": (874, 1327)},
    "uncommon":  {"gem": (162, 175), "stats": (873, 1326)},
    "rare":      {"gem": (159, 175), "stats": (866, 1323)},
    "epic":      {"gem": (160, 175), "stats": (865, 1325)},
    "legendary": {"gem": (157, 177), "stats": (860, 1317)},
}

DEFAULT_FONT_SIZES = {"name": 48, "type": 32, "cost": 92, "rules": 34,
                      "flavor": 28, "stats": 66}

BACKING = (9, 13, 28, 255)
TEXT_WHITE = (255, 255, 255)
SHADOW = (0, 0, 0, 175)

_FONT_REG = ("georgia.ttf", "times.ttf", "DejaVuSerif.ttf")
_FONT_BOLD = ("georgiab.ttf", "timesbd.ttf", "DejaVuSerif-Bold.ttf")


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in (_FONT_BOLD if bold else _FONT_REG) + _FONT_REG + _FONT_BOLD:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text: str, font_size: int, max_width: int, bold=False):
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


class CardRenderer:
    """One renderer per rarity frame; caches template + masks."""

    _cache: dict[str, "CardRenderer"] = {}

    def __init__(self, rarity: str) -> None:
        from PIL import Image
        import numpy as np
        rarity = rarity if rarity in FRAME_METRICS else "common"
        self.rarity = rarity
        self.metrics = FRAME_METRICS[rarity]
        self.template = Image.open(
            TEMPLATE_DIR / f"{rarity}.png").convert("RGBA")
        assert self.template.size == TEMPLATE_SIZE, \
            f"{rarity} template size changed: {self.template.size}"

        alpha = np.array(self.template)[:, :, 3].astype(np.int16)
        exterior = self._flood_exterior(alpha)
        interior = ~exterior                                   # card silhouette
        # backing mask: fully opaque inside the silhouette
        self._backing_mask = Image.fromarray(
            (interior * 255).astype("uint8"), "L")
        # art mask: how much art shows per pixel (inverse of frame opacity),
        # confined to the core region so filigree holes never leak art
        show = (255 - np.clip(alpha, 0, 255)).astype("uint8")
        core = np.zeros_like(show)
        l, t, r, b = ART_CORE
        core[t:b, l:r] = 1
        show = show * core * interior
        self._art_mask = Image.fromarray(show.astype("uint8"), "L")

    @staticmethod
    def _flood_exterior(alpha):
        import numpy as np
        trans = alpha < 16
        ext = np.zeros_like(trans)
        ext[0, :] = trans[0, :]; ext[-1, :] = trans[-1, :]
        ext[:, 0] = trans[:, 0]; ext[:, -1] = trans[:, -1]
        changed = True
        while changed:
            grown = ext.copy()
            grown[1:, :] |= ext[:-1, :]; grown[:-1, :] |= ext[1:, :]
            grown[:, 1:] |= ext[:, :-1]; grown[:, :-1] |= ext[:, 1:]
            grown &= trans
            changed = bool((grown != ext).any()); ext = grown
        return ext

    @classmethod
    def for_rarity(cls, rarity: str) -> "CardRenderer":
        if rarity not in cls._cache:
            cls._cache[rarity] = cls(rarity)
        return cls._cache[rarity]

    # ------------------------------------------------------------------ text
    @staticmethod
    def _shadow_text(base, pos, text, font, anchor="mm", fill=TEXT_WHITE,
                     trace: int = 0):
        """Bright white text over a soft drop shadow; optional black trace
        (stroke) for body text sitting on busy art."""
        from PIL import Image, ImageDraw, ImageFilter
        shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow)
        sdraw.text((pos[0] + 3, pos[1] + 3), text, font=font, fill=SHADOW,
                   anchor=anchor, stroke_width=trace,
                   stroke_fill=SHADOW)
        shadow = shadow.filter(ImageFilter.GaussianBlur(3))
        base.alpha_composite(shadow)
        ImageDraw.Draw(base).text(pos, text, font=font, fill=fill,
                                  anchor=anchor, stroke_width=trace,
                                  stroke_fill=(0, 0, 0, 235))

    # ------------------------------------------------------------------ render
    def render(self, spec, art=None, offset=(0.0, 0.0), zoom: float = 1.0,
               font_sizes: dict | None = None):
        from PIL import Image, ImageDraw
        sizes = dict(DEFAULT_FONT_SIZES)
        if font_sizes:
            sizes.update({k: int(v) for k, v in font_sizes.items() if v})

        card = Image.new("RGBA", TEMPLATE_SIZE, (0, 0, 0, 0))
        # 1) backing
        backing = Image.new("RGBA", TEMPLATE_SIZE, BACKING)
        card.paste(backing, (0, 0), self._backing_mask)
        # 2) art (masked)
        if art is not None:
            l, t, r, b = ART_CORE
            core_w, core_h = r - l, b - t
            art = art.convert("RGB")
            base = max(core_w / art.width, core_h / art.height)
            scale = base * max(0.2, zoom)
            scaled = art.resize((max(1, round(art.width * scale)),
                                 max(1, round(art.height * scale))),
                                Image.LANCZOS)
            layer = Image.new("RGBA", TEMPLATE_SIZE, (0, 0, 0, 0))
            x = l + (core_w - scaled.width) // 2 + round(offset[0])
            y = t + (core_h - scaled.height) // 2 + round(offset[1])
            layer.paste(scaled, (x, y))
            card.paste(layer, (0, 0), self._art_mask)
        # 3) frame
        card.alpha_composite(self.template)
        # 4) text
        draw = ImageDraw.Draw(card)

        font = _fit_text(draw, spec.name, sizes["name"], NAME_MAX_W, bold=True)
        self._shadow_text(card, NAME_CENTER, spec.name, font)

        type_line = spec.card_type.value.title()
        if spec.hero_types:
            type_line += "   —   " + " / ".join(spec.hero_types)
        font = _fit_text(draw, type_line, sizes["type"], TYPE_MAX_W)
        self._shadow_text(card, TYPE_CENTER, type_line, font)

        gem = (self.metrics["gem"][0] + COST_NUDGE[0],
               self.metrics["gem"][1] + COST_NUDGE[1])
        self._shadow_text(card, gem, str(spec.cost),
                          _load_font(sizes["cost"], bold=True))

        # rules text (auto-shrinks below the chosen size if it overflows)
        draw = ImageDraw.Draw(card)
        left, top, right, bottom = RULES_BOX
        box_w = right - left
        font_size = sizes["rules"]
        while font_size >= 16:
            font = _load_font(font_size)
            lines = _wrap(draw, spec.composed_text(), font, box_w)
            line_h = font_size + 8
            if len(lines) * line_h <= (bottom - top):
                break
            font_size -= 2
        y = top
        for line in lines:
            self._shadow_text(card, (left, y), line, font, anchor="la",
                              trace=2)
            y += line_h

        if spec.flavor:
            font = _fit_text(draw, f"“{spec.flavor}”", sizes["flavor"],
                             FLAVOR_MAX_W)
            self._shadow_text(card, FLAVOR_CENTER, f"“{spec.flavor}”", font,
                              fill=(230, 232, 240), trace=2)

        stats = self._stats_text(spec)
        if stats:
            pos = (self.metrics["stats"][0] + STATS_NUDGE[0],
                   self.metrics["stats"][1] + STATS_NUDGE[1])
            self._shadow_text(card, pos, stats,
                              _load_font(sizes["stats"], bold=True))
        return card

    @staticmethod
    def _stats_text(spec) -> str:
        kind = spec.card_type.value
        if kind in ("hero", "minion"):
            return f"{spec.attack}/{spec.health}"
        if kind == "champion":
            return str(spec.health)
        if kind == "barrier":
            return str(spec.durability)
        return ""

    def render_png(self, spec, art=None, offset=(0.0, 0.0), zoom: float = 1.0,
                   font_sizes: dict | None = None,
                   out_path: Path | None = None, height: int = 1024) -> Path:
        from PIL import Image
        card = self.render(spec, art, offset, zoom, font_sizes)
        scale = height / card.height
        card = card.resize((round(card.width * scale), height), Image.LANCZOS)
        if out_path is None:
            import tempfile
            out_path = Path(tempfile.gettempdir()) / "arcanum_render.png"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            card.save(out_path, "PNG", optimize=True)
        except OSError:
            import tempfile
            out_path = Path(tempfile.gettempdir()) / out_path.name
            card.save(out_path, "PNG", optimize=True)
        return out_path


def renderer_for(spec) -> CardRenderer:
    return CardRenderer.for_rarity(spec.rarity.value)
