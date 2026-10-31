"""Motion video generator: Pillow frames -> ffmpeg -> 1080x1920 MP4 (Reels format).

Uslublar (namunalar asosida):
- terminal: qora fon, yashil mono shrift, kartochkalar, kod oynalari, pastda so'zma-so'z subtitr
- glass:    och fon, rangli xira dog'lar, oq kartochkalar, tepada subtitr, o'ng pastda qahramon
- slides:   qora setkali fon, katta qalin harflar, raqamli ro'yxat, 01/10 hisoblagich
- kinetic:  qora fon, katta mono matn kursor bilan yoziladi, iliq yorug'lik
- classic:  rangli gradient / to'lqin / setka / bokeh fonlar
"""

import logging
import math
import re
import subprocess
import uuid
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

log = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 30
MARGIN = 80

THEME_NAMES = ("terminal", "glass", "slides", "kinetic", "classic")
BG_STYLES = ("gradient", "diagonal", "radial", "waves", "grid", "bokeh", "image")
ELEMENT_TYPES = ("cards", "list", "code", "number", "chips")

# ---------- Shriftlar ----------

FONT_DIR = Path(__file__).parent / "fonts"
FONT_FILES = {
    "mono": "JetBrainsMono-Medium.ttf",
    "mono_bold": "JetBrainsMono-Bold.ttf",
    "condensed": "Anton-Regular.ttf",
}
INTER_WEIGHTS = {"sans": 500, "sans_bold": 700, "sans_black": 850}
FALLBACK_FONTS = [
    config.FONT_PATH,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


@lru_cache(maxsize=128)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        if kind in INTER_WEIGHTS:
            f = ImageFont.truetype(str(FONT_DIR / "Inter-Variable.ttf"), size)
            f.set_variation_by_axes([32 if size >= 32 else 14, INTER_WEIGHTS[kind]])
            return f
        return ImageFont.truetype(str(FONT_DIR / FONT_FILES[kind]), size)
    except (OSError, KeyError):
        for path in FALLBACK_FONTS:
            if path and Path(path).exists():
                return ImageFont.truetype(path, size)
        return ImageFont.load_default(size)


def _hex(color, default=(124, 58, 237)) -> tuple[int, int, int]:
    if isinstance(color, tuple):
        return color[:3]
    color = (color or "").strip().lstrip("#")
    if len(color) != 6:
        return default
    try:
        return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


# ---------- Emoji ----------

_EMOJI_CHAR = r"[\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\U0001F000-\U0001FAFF]"
_EMOJI_TAIL = r"(?:\uFE0F)?(?:[\U0001F3FB-\U0001F3FF])?"
EMOJI_RE = re.compile(
    rf"[\U0001F1E6-\U0001F1FF]{{2}}|{_EMOJI_CHAR}{_EMOJI_TAIL}(?:\u200D{_EMOJI_CHAR}{_EMOJI_TAIL})*"
)
EMOJI_DIR = config.MEDIA_DIR / "_emoji"
EMOJI_DIR.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=256)
def _emoji_image(emoji: str) -> Image.Image | None:
    """Google Noto rangli emoji (512px PNG), diskda keshlanadi."""
    codes = [f"{ord(c):x}" for c in emoji]
    candidates = ["_".join(codes), "_".join(c for c in codes if c != "fe0f")]
    for name in dict.fromkeys(candidates):
        path = EMOJI_DIR / f"{name}.png"
        if not path.exists():
            try:
                resp = httpx.get(f"https://fonts.gstatic.com/s/e/notoemoji/latest/{name}/512.png", timeout=15)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            path.write_bytes(resp.content)
        return Image.open(path).convert("RGBA")
    log.warning("Emoji topilmadi: %r", emoji)
    return None


@lru_cache(maxsize=64)
def _file_image(path: str) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    bbox = img.getchannel("A").getbbox()  # shaffof chetlarni kesib tashlaymiz
    return img.crop(bbox) if bbox else img


def _asset(ref: "str | Path | None") -> Image.Image | None:
    """Emoji string or image file path -> RGBA image."""
    if not ref:
        return None
    if isinstance(ref, Path):
        return _file_image(str(ref)) if ref.exists() else None
    m = EMOJI_RE.search(ref)
    return _emoji_image(m.group()) if m else None


def _runs(text: str) -> list[tuple[bool, str]]:
    """Split text into (is_emoji, chunk) runs."""
    runs, pos = [], 0
    for m in EMOJI_RE.finditer(text):
        if m.start() > pos:
            runs.append((False, text[pos : m.start()]))
        runs.append((True, m.group()))
        pos = m.end()
    if pos < len(text):
        runs.append((False, text[pos:]))
    return runs


def _measure(text: str, fnt: ImageFont.FreeTypeFont) -> float:
    return sum(fnt.size * 1.15 if emo else fnt.getlength(chunk) for emo, chunk in _runs(text))


# ---------- Matn belgilash: *so'z* = urg'u rangi ----------

def parse_markup(text: str) -> list[tuple[str, bool]]:
    words = []
    for i, part in enumerate(text.split("*")):
        words.extend((w, i % 2 == 1) for w in part.split())
    return words


def strip_markup(text: str) -> str:
    return text.replace("*", "")


def narration_words(narration: str, offset: float, clip_seconds: float) -> list[tuple[str, float, float, bool]]:
    """Estimate per-word timings across the clip (proportional to word length)."""
    words = parse_markup(narration)
    if not words:
        return []
    weights = [len(w) + 2 for w, _ in words]
    total, speak = sum(weights), max(clip_seconds - 0.15, 0.3)
    out, cursor = [], offset
    for (w, acc), wt in zip(words, weights):
        dur = speak * wt / total
        out.append((w, cursor, cursor + dur, acc))
        cursor += dur
    return out


# ---------- Animatsiya yordamchilari ----------

def _clamp(t: float) -> float:
    return max(0.0, min(1.0, t))


def _ease_out(t: float) -> float:
    return 1 - (1 - _clamp(t)) ** 3


def _ease_out_back(t: float) -> float:
    t = _clamp(t)
    c = 1.7
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    r, g, b, a = img.split()
    lut = [int(v * alpha) for v in range(256)]
    return Image.merge("RGBA", (r, g, b, a.point(lut)))


def _paste(frame: Image.Image, sprite: Image.Image, x: float, y: float, alpha: float = 1.0) -> None:
    if alpha <= 0.01:
        return
    x, y = int(x), int(y)
    if x >= W or y >= H or x + sprite.width <= 0 or y + sprite.height <= 0:
        return
    box = (max(0, -x), max(0, -y), min(sprite.width, W - x), min(sprite.height, H - y))
    if box != (0, 0, sprite.width, sprite.height):
        sprite = sprite.crop(box)
    sprite = _with_alpha(sprite, alpha)
    frame.paste(sprite, (x + box[0], y + box[1]), sprite)


def _text_alpha(img: Image.Image, xy: tuple, text: str, fnt, color: tuple, alpha: float, anchor: str = "la") -> None:
    """Draw text with real transparency (ImageDraw ignores text alpha on RGB images)."""
    if alpha <= 0.01 or not text:
        return
    if alpha >= 0.99 or img.mode == "RGBA":
        ImageDraw.Draw(img).text(xy, text, font=fnt, fill=(*color[:3], int(255 * min(alpha, 1))), anchor=anchor)
        return
    left, top, right, bottom = fnt.getbbox(text, anchor=anchor)
    spr = Image.new("RGBA", (right - left + 2, bottom - top + 2), (0, 0, 0, 0))
    ImageDraw.Draw(spr).text((-left, -top), text, font=fnt, fill=(*color[:3], 255), anchor=anchor)
    _paste(img, spr, xy[0] + left, xy[1] + top, alpha)


def _rounded(size: tuple[int, int], radius: int, fill, outline=None, width: int = 2, shadow: bool = False) -> Image.Image:
    """Rounded card sprite, optionally with a soft drop shadow (sprite gets padding)."""
    pad = 40 if shadow else 0
    img = Image.new("RGBA", (size[0] + pad * 2, size[1] + pad * 2), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            (pad, pad + 14, pad + size[0], pad + size[1] + 14), radius, fill=(30, 40, 80, 38))
        img = sh.filter(ImageFilter.GaussianBlur(18))
    ImageDraw.Draw(img).rounded_rectangle(
        (pad, pad, pad + size[0] - 1, pad + size[1] - 1), radius, fill=fill, outline=outline, width=width)
    return img


def _text_sprite(text: str, fnt: ImageFont.FreeTypeFont, color, emoji_scale: float = 1.0) -> Image.Image:
    asc, desc = fnt.getmetrics()
    img = Image.new("RGBA", (int(_measure(text, fnt)) + 10, asc + desc + 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 0.0
    for is_emoji, chunk in _runs(text):
        if is_emoji:
            icon = _emoji_image(chunk)
            if icon is not None:
                px = int(fnt.size * emoji_scale)
                img.alpha_composite(icon.resize((px, px), Image.LANCZOS), (int(x), max(0, int(asc - px * 0.9))))
            x += fnt.size * 1.15
        else:
            d.text((x, 0), chunk, font=fnt, fill=color)
            x += fnt.getlength(chunk)
    return img


def _wrap_words(words: list, fnt, max_width: int, max_words: int = 99) -> list[list]:
    """words: list of (text, ...) tuples -> lines (lists of the same tuples)."""
    lines, line, width = [], [], 0.0
    space = fnt.getlength(" ")
    for item in words:
        w = _measure(item[0], fnt)
        if line and (width + space + w > max_width or len(line) >= max_words):
            lines.append(line)
            line, width = [], 0.0
        width += (space if line else 0) + w
        line.append(item)
    if line:
        lines.append(line)
    return lines


# ---------- Uslublar ----------

@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple
    fg: tuple
    dim: tuple
    accent: tuple
    heading_font: str
    heading_size: int
    body_font: str
    body_bold: str
    align: str          # center | left
    upper: bool
    card_bg: tuple      # RGBA
    card_border: tuple  # RGBA
    radius: int
    captions: str       # bottom | top | kinetic | none
    shadow: bool = False
    glow: tuple = (255, 110, 40)


THEMES = {
    "terminal": Theme("terminal", (9, 12, 9), (232, 240, 226), (115, 130, 112), (198, 244, 50),
                      "mono_bold", 74, "mono", "mono_bold", "center", False,
                      (13, 19, 13, 245), (198, 244, 50, 150), 10, "bottom"),
    "glass": Theme("glass", (244, 246, 251), (17, 17, 20), (105, 110, 122), (37, 99, 235),
                   "sans_black", 82, "sans", "sans_bold", "center", False,
                   (255, 255, 255, 240), (0, 0, 0, 16), 34, "top", shadow=True),
    "slides": Theme("slides", (22, 24, 29), (245, 245, 245), (150, 150, 158), (255, 122, 69),
                    "condensed", 150, "sans", "sans_bold", "left", True,
                    (35, 37, 44, 255), (255, 255, 255, 16), 14, "none"),
    "kinetic": Theme("kinetic", (5, 5, 5), (240, 240, 240), (95, 95, 95), (255, 200, 61),
                     "mono_bold", 96, "mono", "mono_bold", "center", False,
                     (22, 22, 22, 235), (255, 255, 255, 30), 16, "kinetic"),
    "classic": Theme("classic", (30, 27, 75), (255, 255, 255), (220, 220, 235), (250, 204, 21),
                     "sans_black", 92, "sans", "sans_bold", "center", False,
                     (255, 255, 255, 38), (255, 255, 255, 60), 28, "bottom"),
}

GLASS_PALETTES = [
    ((147, 197, 253), (196, 181, 253), (167, 243, 208)),
    ((253, 186, 116), (252, 165, 165), (253, 224, 71)),
    ((134, 239, 172), (103, 232, 249), (190, 242, 100)),
    ((216, 180, 254), (249, 168, 212), (165, 180, 252)),
]


@dataclass
class Scene:
    heading: str = ""
    subheading: str = ""
    narration: str = ""
    duration: float = 3.0
    emoji: str = ""
    character: Path | None = None
    stickers: list = field(default_factory=list)
    elements: list = field(default_factory=list)
    variant: str = ""            # slides: light | kinetic: glow rangi | glass: palitra raqami
    accent: str = ""
    bg_style: str = "gradient"   # classic uchun
    bg_colors: tuple = ("#1e1b4b", "#7c3aed")
    bg_image: Path | None = None
    words: list = field(default_factory=list)  # narration_words() natijasi


@dataclass
class VideoSpec:
    theme: str = "terminal"
    handle: str = ""
    label: str = ""
    captions: bool = True


def _scene_theme(base: Theme, scene: Scene) -> Theme:
    t = base
    if scene.accent:
        t = replace(t, accent=_hex(scene.accent, t.accent))
    if base.name == "slides" and scene.variant == "light":
        t = replace(t, bg=(243, 239, 230), fg=(20, 20, 22), dim=(105, 100, 95),
                    card_bg=(255, 255, 255, 255), card_border=(0, 0, 0, 22))
    if base.name == "kinetic" and scene.variant.startswith("#"):
        t = replace(t, glow=_hex(scene.variant, t.glow))
    if base.name == "classic":
        c1, c2 = _hex(scene.bg_colors[0]), _hex(scene.bg_colors[1])
        lum = (0.2126 * (c1[0] + c2[0]) + 0.7152 * (c1[1] + c2[1]) + 0.0722 * (c1[2] + c2[2])) / 510
        if lum > 0.62 and scene.bg_style != "image":
            t = replace(t, fg=(17, 24, 39), dim=(70, 70, 80), card_bg=(255, 255, 255, 170),
                        card_border=(0, 0, 0, 25))
        if not scene.accent:
            t = replace(t, accent=(250, 204, 21))
    return t


# ---------- Fonlar ----------

def _grid(img: Image.Image, step: int, color: tuple) -> None:
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W, step):
        d.line((x, 0, x, H), fill=color)
    for y in range(0, H, step):
        d.line((0, y, W, y), fill=color)


def _vignette(img: Image.Image, strength: int) -> None:
    """Darken the edges (img must be RGBA)."""
    mask = Image.radial_gradient("L").resize((int(W * 1.5), int(H * 1.3)))
    mask = mask.crop(((mask.width - W) // 2, (mask.height - H) // 2,
                      (mask.width - W) // 2 + W, (mask.height - H) // 2 + H))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shade.putalpha(mask.point(lambda v: int(v / 255 * strength)))
    img.alpha_composite(shade)


@lru_cache(maxsize=16)
def _blob(color: tuple, size: int = 1000) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    r = size // 2 - 330
    ImageDraw.Draw(mask).ellipse((size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r), fill=170)
    img = Image.new("RGBA", (size, size), (*color, 0))
    img.putalpha(mask.filter(ImageFilter.GaussianBlur(105)))
    return img


def _two_color(mask: Image.Image, c1: tuple, c2: tuple) -> Image.Image:
    return Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mask)


class Background:
    def __init__(self, theme: Theme, scene: Scene, index: int):
        self.theme, self.scene, self.index = theme, scene, index
        self.photo = None
        if scene.bg_image and scene.bg_image.exists():
            img = Image.open(scene.bg_image).convert("RGB")
            scale = max(W * 1.12 / img.width, H * 1.12 / img.height)
            self.photo = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
            shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(70 + 0.4 * abs(v - 128)))
            self.shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            self.shade.putalpha(shade)
            return
        name = theme.name
        if name == "classic":
            c1, c2 = _hex(scene.bg_colors[0]), _hex(scene.bg_colors[1])
            if scene.bg_style == "radial":
                big = Image.radial_gradient("L").resize((int(H * 1.5), int(H * 1.5)))
                left, top = (big.width - W) // 2, (big.height - H) // 2
                self.base = _two_color(big.crop((left, top, left + W, top + H)), c1, c2)
            elif scene.bg_style == "diagonal":
                big = Image.linear_gradient("L").rotate(35, expand=True, fillcolor=128).resize((int(H * 1.6),) * 2)
                left, top = (big.width - W) // 2, (big.height - H) // 2
                self.base = _two_color(big.crop((left, top, left + W, top + H)), c1, c2)
            else:
                self.base = _two_color(Image.linear_gradient("L").resize((W, H)), c1, c2)
            return
        self._themed_base(theme, index)
        self.base = self.base.convert("RGB")

    def _themed_base(self, theme: Theme, index: int) -> None:
        name = theme.name
        self.base = Image.new("RGBA", (W, H), (*theme.bg, 255))
        if name == "terminal":
            _grid(self.base, 54, (*theme.accent, 9))
            _vignette(self.base, 150)
        elif name == "slides":
            _grid(self.base, 90, (*theme.fg, 7))
        elif name == "kinetic":
            mask = Image.new("L", (W, H), 255)
            mask.paste(Image.radial_gradient("L").resize((1700, 1700)), (int(W * 0.8) - 850, int(H * 0.78) - 850))
            layer = Image.new("RGBA", (W, H), (*theme.glow, 0))
            # radial_gradient chetida 181 (burchakda 255): chegarada alfa 0 bo'lsin
            layer.putalpha(mask.point(lambda v: max(0, int(125 * (1 - v / 181)))))
            self.base.alpha_composite(layer)
            _vignette(self.base, 120)
        elif name == "glass":
            self.blobs = [_blob(c) for c in GLASS_PALETTES[index % len(GLASS_PALETTES)]]

    def frame(self, t: float, g: float) -> Image.Image:
        if self.photo is not None:
            z = 1 + 0.08 * (t / max(self.scene.duration, 0.1))
            cw, ch = int(self.photo.width / (1.12 * z)), int(self.photo.height / (1.12 * z))
            ox = int((self.photo.width - cw) / 2 + math.sin(g * 0.3) * 20)
            oy = int((self.photo.height - ch) / 2)
            frame = self.photo.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR)
            frame.paste(self.shade, (0, 0), self.shade)
            return frame
        frame = self.base.copy()
        name, acc = self.theme.name, self.theme.accent
        if name == "glass":
            for k, blob in enumerate(self.blobs):
                cx = W * (0.25 + 0.5 * k / 2) + math.sin(g * 0.35 + k * 2.1) * 220
                cy = H * (0.25 + 0.3 * k) + math.cos(g * 0.3 + k * 1.3) * 260
                _paste(frame, blob, cx - blob.width / 2, cy - blob.height / 2)
        elif name == "classic":
            d = ImageDraw.Draw(frame, "RGBA")
            style = self.scene.bg_style
            if style == "waves":
                for k in range(3):
                    amp, base_y, speed = 70 + k * 25, H * (0.62 + k * 0.12), 0.8 + k * 0.35
                    pts = [(x, base_y + math.sin(x / 170 + g * speed + k) * amp) for x in range(0, W + 40, 40)]
                    d.polygon(pts + [(W, H), (0, H)], fill=(*acc, 45 + k * 15))
            elif style == "grid":
                step, off = 90, (g * 25) % 90
                for gx in range(-1, W // step + 2):
                    for gy in range(-1, H // step + 2):
                        x, y = gx * step + off, gy * step + off
                        r = 5 + 3 * math.sin(g * 2 + gx * 0.7 + gy * 0.4)
                        d.ellipse((x - r, y - r, x + r, y + r), fill=(*acc, 70))
            else:
                circles = [(380, 40), (260, 55), (180, 35)] if style == "bokeh" else [(300, 28), (200, 22)]
                for k, (r, a) in enumerate(circles):
                    cx = W / 2 + math.sin(g * 0.6 + k * 2.1) * 360
                    cy = H / 2 + math.cos(g * 0.45 + k * 1.7) * 640
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*acc, a))
        return frame


# ---------- Bloklar ----------

class Block:
    height = 0

    def draw(self, frame: Image.Image, top: float, t: float, fade: float) -> None:
        raise NotImplementedError


class HeroBlock(Block):
    def __init__(self, img: Image.Image, max_px: int, t0: float, slide: bool):
        ratio = max_px / max(img.width, img.height)
        self.sprite = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
        self.height = self.sprite.height
        self.t0, self.slide = t0, slide

    def draw(self, frame, top, t, fade):
        t = t - self.t0
        s = _ease_out_back(t / 0.6)
        if s <= 0.02:
            return
        g = t
        sprite = self.sprite
        if s < 0.999 or not self.slide:
            w, h = max(4, int(sprite.width * s)), max(4, int(sprite.height * s))
            sprite = sprite.resize((w, h), Image.BILINEAR)
            if not self.slide:
                sprite = sprite.rotate(math.sin(g * 2.2) * 5, resample=Image.BICUBIC, expand=True)
        y = top + (self.height - sprite.height) / 2 + math.sin(g * 2.4) * 12
        _paste(frame, sprite, (W - sprite.width) / 2, y, fade)


class HeadingBlock(Block):
    def __init__(self, text: str, theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        if theme.upper:
            text = text.upper()
        words = parse_markup(text)
        size = theme.heading_size
        max_w = W - 2 * MARGIN
        max_lines = 4 if theme.name == "slides" else 3
        while True:
            fnt = font(theme.heading_font, size)
            lines = _wrap_words(words, fnt, max_w)
            if len(lines) <= max_lines or size < 40:
                break
            size = int(size * 0.88)
        self.line_h = int(size * (1.08 if theme.heading_font == "condensed" else 1.2))
        space = fnt.getlength(" ")
        self.items = []  # (sprite, x, y, accent, line_idx, word_idx, width)
        k = 0
        for li, line in enumerate(lines):
            widths = [_measure(w, fnt) for w, _ in line]
            total = sum(widths) + space * (len(line) - 1)
            x = MARGIN if theme.align == "left" else (W - total) / 2
            for (word, acc), wd in zip(line, widths):
                color = theme.accent if acc else theme.fg
                self.items.append((_text_sprite(word, fnt, (*color, 255)), x, li * self.line_h, acc, li, k, wd))
                x += wd + space
                k += 1
        self.height = len(lines) * self.line_h
        self.n_words = k

    def draw(self, frame, top, t, fade):
        name = self.theme.name
        t = t - self.t0
        d = ImageDraw.Draw(frame, "RGBA")
        last_visible = None
        for sprite, x, y, acc, li, k, wd in self.items:
            if name == "slides":
                p = _ease_out((t - li * 0.12) / 0.45)
                _paste(frame, sprite, x - (1 - p) * 90, top + y, p * fade)
            elif name == "kinetic":
                if t >= k * 0.12:
                    _paste(frame, sprite, x, top + y, fade)
                    last_visible = (x + wd, top + y)
            else:
                p = _ease_out((t - k * 0.07) / 0.35)
                _paste(frame, sprite, x, top + y + (1 - p) * 34, p * fade)
                if acc and name in ("glass", "classic", "terminal"):
                    u = _ease_out((t - k * 0.07 - 0.3) / 0.35)
                    if u > 0:
                        uy = top + y + self.line_h * 0.9
                        d.rounded_rectangle((x, uy, x + wd * u, uy + 8), 4,
                                            fill=(*self.theme.accent, int(255 * fade)))
        if last_visible and int(t * 2.5) % 2 == 0:
            cx, cy = last_visible
            d.rectangle((cx + 10, cy + 8, cx + 10 + self.line_h * 0.32, cy + self.line_h * 0.92),
                        fill=(255, 45, 45, int(255 * fade)))


class TextBlock(Block):
    def __init__(self, text: str, theme: Theme, t0: float, size: int = 44):
        self.theme, self.t0 = theme, t0
        fnt = font(theme.body_font, size)
        lines = _wrap_words([(w,) for w in text.split()], fnt, W - 2 * MARGIN - 40)
        self.sprites = [_text_sprite(" ".join(w for (w,) in line), fnt, (*theme.dim, 255)) for line in lines]
        self.line_h = int(size * 1.35)
        self.height = len(self.sprites) * self.line_h

    def draw(self, frame, top, t, fade):
        p = _ease_out((t - self.t0) / 0.5)
        for i, sp in enumerate(self.sprites):
            x = MARGIN if self.theme.align == "left" else (W - sp.width) / 2
            _paste(frame, sp, x, top + i * self.line_h + (1 - p) * 24, p * fade)


class CardsBlock(Block):
    def __init__(self, items: list[dict], theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        items = items[:4]
        cols = 1 if len(items) <= 2 else 2
        gap = 28
        cw = (W - 2 * MARGIN - gap * (cols - 1)) // cols
        title_f = font(theme.body_bold, 44 if cols == 1 else 38)
        text_f = font(theme.body_font, 32 if cols == 1 else 28)
        pad, icon_px = 34, (72 if cols == 1 else 64)
        cards = []
        for it in items:
            icon = _asset(it.get("icon", ""))
            text_x = pad + (icon_px + 26 if icon is not None and cols == 1 else 0)
            inner = cw - text_x - pad
            t_lines = _wrap_words([(w,) for w in str(it.get("title", "")).split()], title_f, inner)
            d_lines = _wrap_words([(w,) for w in str(it.get("text", "")).split()], text_f, inner)
            h = pad * 2 + len(t_lines) * int(title_f.size * 1.25) + len(d_lines) * int(text_f.size * 1.4)
            if d_lines:
                h += 8
            if icon is not None and cols == 2:
                h += icon_px + 18
            h = max(h, icon_px + pad * 2 if icon is not None else 0)
            cards.append((it, icon, text_x, t_lines, d_lines, h))
        rows = [cards[i : i + cols] for i in range(0, len(cards), cols)]
        self.sprites = []  # (sprite, x, y)
        y = 0
        for row in rows:
            rh = max(c[5] for c in row)
            for ci, (it, icon, text_x, t_lines, d_lines, _) in enumerate(row):
                border = theme.card_border
                sprite = _rounded((cw, rh), theme.radius, theme.card_bg, border, 2, theme.shadow)
                off = 40 if theme.shadow else 0
                dr = ImageDraw.Draw(sprite)
                ty = off + pad
                if icon is not None:
                    ic = icon.resize((icon_px, int(icon_px * icon.height / icon.width)), Image.LANCZOS)
                    if cols == 1:
                        sprite.alpha_composite(ic, (off + pad, off + (rh - ic.height) // 2))
                    else:
                        sprite.alpha_composite(ic, (off + pad, ty))
                        ty += icon_px + 18
                for line in t_lines:
                    dr.text((off + text_x, ty), " ".join(w for (w,) in line), font=title_f, fill=(*theme.fg, 255))
                    ty += int(title_f.size * 1.25)
                ty += 8
                for line in d_lines:
                    dr.text((off + text_x, ty), " ".join(w for (w,) in line), font=text_f, fill=(*theme.dim, 255))
                    ty += int(text_f.size * 1.4)
                self.sprites.append((sprite, MARGIN + ci * (cw + gap) - off, y - off))
            y += rh + gap
        self.height = y - gap

    def draw(self, frame, top, t, fade):
        for k, (sp, x, y) in enumerate(self.sprites):
            p = _ease_out((t - self.t0 - k * 0.18) / 0.45)
            _paste(frame, sp, x, top + y + (1 - p) * 60, p * fade)


class ListBlock(Block):
    def __init__(self, items: list[str], theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        f = font(theme.body_bold, 40)
        num_f = font("mono_bold", 34)
        rh, gap, w = 104, 18, W - 2 * MARGIN
        self.sprites = []
        for i, text in enumerate(items[:6]):
            sp = _rounded((w, rh), theme.radius, theme.card_bg, theme.card_border, 2)
            d = ImageDraw.Draw(sp)
            d.rounded_rectangle((22, 20, 22 + 64, 20 + 64), 10, fill=(*theme.accent, 255))
            num = f"{i + 1:02d}"
            d.text((22 + 32, 52), num, font=num_f, fill=(*theme.bg, 255), anchor="mm")
            label = str(text)
            while f.getlength(label) > w - 140 and len(label) > 4:
                label = label[:-2]
            d.text((110, rh / 2), label if label == str(text) else label + "…", font=f,
                   fill=(*theme.fg, 255), anchor="lm")
            self.sprites.append((sp, i * (rh + gap)))
        self.height = len(self.sprites) * (rh + gap) - gap

    def draw(self, frame, top, t, fade):
        for k, (sp, y) in enumerate(self.sprites):
            p = _ease_out((t - self.t0 - k * 0.2) / 0.4)
            _paste(frame, sp, MARGIN - (1 - p) * 80, top + y, p * fade)


class CodeBlock(Block):
    def __init__(self, title: str, lines: list[str], theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        self.f = font("mono", 33)
        self.lines = [str(x) for x in lines[:10]]
        self.lh, self.header = 50, 70
        w = W - 2 * MARGIN
        self.h = self.header + len(self.lines) * self.lh + 36
        bg = (12, 14, 18, 245) if theme.name in ("glass", "classic") else theme.card_bg
        border = theme.card_border if theme.name == "terminal" else (255, 255, 255, 30)
        self.sprite = _rounded((w, self.h), 14, bg, border, 2)
        d = ImageDraw.Draw(self.sprite)
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse((26 + i * 34, 26, 26 + i * 34 + 18, 44), fill=c)
        d.text((140, 35), title, font=font("mono", 26), fill=(150, 160, 150, 255), anchor="lm")
        self.height = self.h
        self.total_chars = sum(len(x) for x in self.lines)

    def _color(self, line: str) -> tuple:
        s = line.strip()
        if s.startswith("$") or s.startswith(">"):
            return (198, 244, 50) if self.theme.name != "terminal" else self.theme.accent
        if s.startswith("//") or s.startswith("#"):
            return (120, 130, 125)
        if s.startswith("!") or s.startswith("✗"):
            return (255, 95, 86)
        return (230, 235, 228)

    def draw(self, frame, top, t, fade):
        p = _ease_out((t - self.t0) / 0.4)
        if p <= 0:
            return
        _paste(frame, self.sprite, MARGIN, top + (1 - p) * 40, p * fade)
        chars = int(max(0.0, t - self.t0 - 0.3) * 45)
        d = ImageDraw.Draw(frame, "RGBA")
        y = top + self.header + (1 - p) * 40
        cursor = None
        for line in self.lines:
            if chars <= 0:
                break
            shown = line[:chars]
            chars -= len(line)
            _text_alpha(frame, (MARGIN + 30, y), shown, self.f, self._color(line), fade)
            cursor = (MARGIN + 30 + self.f.getlength(shown), y)
            y += self.lh
        if cursor and int(t * 2.5) % 2 == 0:
            d.rectangle((cursor[0] + 4, cursor[1] + 4, cursor[0] + 22, cursor[1] + 40),
                        fill=(*self.theme.accent, int(230 * fade)))


class NumberBlock(Block):
    def __init__(self, value: str, label: str, theme: Theme, t0: float, prefix: str = "", suffix: str = ""):
        self.theme, self.t0 = theme, t0
        self.raw, self.prefix, self.suffix = str(value), prefix, suffix
        digits = re.sub(r"[^\d]", "", self.raw)
        self.target = int(digits) if digits and re.fullmatch(r"[\d\s,.]+", self.raw.strip()) else None
        self.sep = "," if "," in self.raw else (" " if " " in self.raw.strip() else "")
        big = 240 if theme.heading_font == "condensed" else 190
        self.f = font(theme.heading_font, big)
        self.label = TextBlock(label, theme, t0 + 0.3, 42) if label else None
        self.big_h = int(big * 1.15)
        self.height = self.big_h + (self.label.height + 10 if self.label else 0)

    def draw(self, frame, top, t, fade):
        p = _ease_out((t - self.t0) / 1.3)
        if t < self.t0:
            return
        if self.target is not None:
            cur = int(self.target * p)
            txt = f"{cur:,}".replace(",", self.sep) if self.sep else str(cur)
        else:
            txt = self.raw
        txt = f"{self.prefix}{txt}{self.suffix}"
        a = fade * _ease_out((t - self.t0) / 0.3)
        x = MARGIN if self.theme.align == "left" else W / 2
        _text_alpha(frame, (x, top + self.big_h / 2), txt, self.f, self.theme.accent, a,
                    "lm" if self.theme.align == "left" else "mm")
        if self.label:
            self.label.draw(frame, top + self.big_h + 10, t, fade)


class ChipsBlock(Block):
    def __init__(self, items: list[str], theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        f = font(theme.body_bold, 36)
        sprites = []
        for text in items[:8]:
            tw = int(_measure(str(text), f))
            sp = _rounded((tw + 56, 76), 38, (*theme.accent, 40), (*theme.accent, 200), 2)
            ts = _text_sprite(str(text), f, (*theme.fg, 255))
            sp.alpha_composite(ts, (28, (76 - ts.height) // 2 + 4))
            sprites.append(sp)
        rows, row, width = [], [], 0
        for sp in sprites:
            if row and width + 18 + sp.width > W - 2 * MARGIN:
                rows.append(row)
                row, width = [], 0
            width += (18 if row else 0) + sp.width
            row.append(sp)
        if row:
            rows.append(row)
        self.items = []
        for ri, r in enumerate(rows):
            total = sum(s.width for s in r) + 18 * (len(r) - 1)
            x = MARGIN if theme.align == "left" else (W - total) / 2
            for sp in r:
                self.items.append((sp, x, ri * 94))
                x += sp.width + 18
        self.height = len(rows) * 94 - 18

    def draw(self, frame, top, t, fade):
        for k, (sp, x, y) in enumerate(self.items):
            s = _ease_out_back((t - self.t0 - k * 0.1) / 0.4)
            if s <= 0.05:
                continue
            spr = sp if s > 0.99 else sp.resize((max(2, int(sp.width * s)), max(2, int(sp.height * s))))
            _paste(frame, spr, x + (sp.width - spr.width) / 2, top + y + (sp.height - spr.height) / 2, fade)


def _element_block(el: dict, theme: Theme, t0: float) -> Block | None:
    kind = el.get("type")
    try:
        if kind == "cards" and el.get("items"):
            return CardsBlock(el["items"], theme, t0)
        if kind == "list" and el.get("items"):
            return ListBlock(el["items"], theme, t0)
        if kind == "code" and el.get("lines"):
            return CodeBlock(el.get("title", "terminal"), el["lines"], theme, t0)
        if kind == "number" and el.get("value") is not None:
            return NumberBlock(el["value"], el.get("label", ""), theme, t0, el.get("prefix", ""), el.get("suffix", ""))
        if kind == "chips" and el.get("items"):
            return ChipsBlock(el["items"], theme, t0)
    except Exception:
        log.exception("Element chizilmadi: %s", el)
    return None


# ---------- Subtitr ----------

class Captions:
    def __init__(self, words: list, theme: Theme, mode: str):
        self.theme, self.mode, self.words = theme, mode, words
        if mode == "top":
            self.f = font("sans_black", 60)
        elif mode == "kinetic":
            self.f = font("mono_bold", 92)
        else:
            self.f = font(theme.body_bold if theme.name != "terminal" else "mono_bold", 46)
        max_w = 820 if mode == "kinetic" else W - 2 * MARGIN
        indexed = [(w, i) for i, (w, *_rest) in enumerate(words)]
        self.groups = _wrap_words(indexed, self.f, max_w, 3 if mode == "kinetic" else 6)
        self.group_of = {i: gi for gi, grp in enumerate(self.groups) for _, i in grp}

    def _current(self, t: float) -> int | None:
        idx = None
        for i, (_, start, _end, _acc) in enumerate(self.words):
            if start <= t:
                idx = i
            else:
                break
        if idx is None and self.words and t >= self.words[0][1] - 0.2:
            idx = 0
        return idx

    def _line(self, img, grp, idx, x0, y, fade, hide_future: bool):
        space = self.f.getlength(" ")
        x = x0
        last = None
        for w, i in grp:
            acc = self.words[i][3]
            if i > idx:
                if hide_future:
                    break
                color, a = self.theme.fg, 0.28
            elif i == idx:
                color, a = self.theme.accent, 1.0
            else:
                color, a = (self.theme.accent if acc else self.theme.fg), 1.0
            _text_alpha(img, (x, y), w, self.f, color, a * fade)
            wd = self.f.getlength(w)
            if i == idx:
                last = (x, wd)
            x += wd + space
        return x, last

    def draw(self, frame: Image.Image, t: float, fade: float, y_center: float = 0) -> None:
        idx = self._current(t)
        if idx is None:
            return
        gi = self.group_of[idx]
        grp = self.groups[gi]
        space = self.f.getlength(" ")
        total = sum(self.f.getlength(w) for w, _ in grp) + space * (len(grp) - 1)
        if self.mode == "kinetic":
            layer = Image.new("RGBA", (W, 520), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer, "RGBA")
            if gi > 0:
                prev = self.groups[gi - 1]
                ptotal = sum(self.f.getlength(w) for w, _ in prev) + space * (len(prev) - 1)
                self._line(layer, prev, idx, (W - ptotal) / 2, 90, fade * 0.3, False)
            end_x, _ = self._line(layer, grp, idx, (W - total) / 2, 240, fade, True)
            if int(t * 2.5) % 2 == 0:
                d.rectangle((end_x - space + 12, 252, end_x - space + 44, 352), fill=(255, 45, 45, int(255 * fade)))
            layer = layer.rotate(6, resample=Image.BICUBIC)
            _paste(frame, layer, 0, y_center - 300)
            return
        d = ImageDraw.Draw(frame, "RGBA")
        if self.mode == "top":
            shown = [(w, i) for w, i in grp if i <= idx]
            stotal = sum(self.f.getlength(w) for w, _ in shown) + space * (len(shown) - 1)
            _, last = self._line(frame, grp, idx, (W - stotal) / 2, 150, fade, True)
            if last:
                d.rounded_rectangle((last[0], 232, last[0] + last[1], 240), 4,
                                    fill=(*self.theme.accent, int(255 * fade)))
            return
        self._line(frame, grp, idx, (W - total) / 2, H - 300, fade, False)


# ---------- Sahna ----------

STICKER_SLOTS = [(170, 330), (W - 170, 520), (160, H - 560), (W - 160, H - 430)]


class SceneRenderer:
    def __init__(self, scene: Scene, spec: VideoSpec, index: int, count: int):
        base = THEMES.get(spec.theme, THEMES["terminal"])
        self.theme = theme = _scene_theme(base, scene)
        self.scene, self.spec, self.index, self.count = scene, spec, index, count
        self.bg = Background(theme, scene, index)

        cap_mode = theme.captions if (spec.captions and scene.words) else "none"
        if base.name == "kinetic" and scene.words:
            cap_mode = "kinetic"
        self.captions = Captions(scene.words, theme, cap_mode) if cap_mode != "none" else None

        character = _asset(scene.character) if scene.character else None
        self.side_character = None
        blocks: list[Block] = []
        t0 = 0.1
        if character is not None and base.name == "glass":
            ratio = 760 / character.height
            self.side_character = character.resize((int(character.width * ratio), 760), Image.LANCZOS)
        elif character is not None:
            blocks.append(HeroBlock(character, 560, t0, True))
            t0 += 0.2
        elif scene.emoji and _asset(scene.emoji) is not None:
            blocks.append(HeroBlock(_asset(scene.emoji), 260, t0, False))
            t0 += 0.2
        heading = scene.heading
        if heading and not (cap_mode == "kinetic" and not scene.elements and not blocks):
            blocks.append(HeadingBlock(heading, theme, t0))
            t0 += 0.35
        if scene.subheading:
            blocks.append(TextBlock(scene.subheading, theme, t0))
            t0 += 0.3
        for el in scene.elements[:2]:
            b = _element_block(el, theme, t0)
            if b:
                blocks.append(b)
                t0 += 0.4
        self.blocks = blocks

        # Kontent maydoni
        top, bottom = 220, 1560
        if base.name == "glass":
            top, bottom = (320 if self.captions else 200), (1150 if self.side_character else 1700)
        elif base.name == "slides":
            top, bottom = 200, 1720
        elif cap_mode == "kinetic":
            top, bottom = 200, 860
        gap = 48
        total = sum(b.height for b in blocks) + gap * max(0, len(blocks) - 1)
        if theme.align == "left":
            y = top + 40
        else:
            y = top + max(0, (bottom - top - total) / 2)
        self.layout = []
        for b in blocks:
            self.layout.append((b, y))
            y += b.height + gap
        self.caption_y = 1250 if blocks else H / 2 + 120

        self.stickers = [img for img in (_asset(s) for s in scene.stickers[:4]) if img is not None]
        self._chrome_static()

    def _chrome_static(self) -> None:
        """Header/footer that stay constant for the scene (drawn into the background base)."""
        base = getattr(self.bg, "base", None)
        if base is None:
            return
        th, spec = self.theme, self.spec
        d = ImageDraw.Draw(base, "RGBA")
        small = font("mono", 26)
        if th.name == "terminal":
            for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
                d.ellipse((50 + i * 30, 62, 50 + i * 30 + 16, 78), fill=c)
            label = spec.label or "product"
            d.text((150, 70), f"root@{label}: ~/{(spec.handle or 'video').lstrip('@')}", font=small,
                   fill=(*th.dim, 255), anchor="lm")
            if spec.handle:
                d.text((W / 2, H - 110), f"• {spec.handle}", font=small, fill=(*th.dim, 255), anchor="mm")
        elif th.name == "slides":
            if spec.handle:
                d.text((MARGIN, 90), spec.handle, font=small, fill=(*th.dim, 255), anchor="lm")
            d.text((W - MARGIN, 90), f"{self.index + 1:02d}/{self.count:02d}", font=small,
                   fill=(*th.fg, 255), anchor="rm")
        elif th.name == "kinetic":
            if spec.handle:
                d.text((W - MARGIN, 90), spec.handle.upper(), font=font("mono_bold", 26),
                       fill=(*th.dim, 255), anchor="rm")
        elif th.name == "glass" and spec.handle:
            d.text((MARGIN, H - 90), spec.handle, font=font("sans_bold", 28), fill=(*th.dim, 255), anchor="lm")

    def _chrome_dynamic(self, frame: Image.Image, progress: float, scene_p: float) -> None:
        th = self.theme
        d = ImageDraw.Draw(frame, "RGBA")
        if th.name == "slides":
            seg_w = (W - 2 * MARGIN - 10 * (self.count - 1)) / self.count
            for i in range(self.count):
                x = MARGIN + i * (seg_w + 10)
                fill = 1.0 if i < self.index else (scene_p if i == self.index else 0.0)
                d.rectangle((x, H - 120, x + seg_w, H - 114), fill=(*th.fg, 40))
                if fill > 0:
                    d.rectangle((x, H - 120, x + seg_w * fill, H - 114), fill=(*th.accent, 255))
        elif th.name == "terminal":
            d.rectangle((0, 0, W * progress, 6), fill=(*th.accent, 230))
        else:
            d.rectangle((0, H - 10, W * progress, H), fill=(*th.accent, 220))

    def render(self, t: float, g: float, progress: float) -> Image.Image:
        scene = self.scene
        fade = _ease_out((scene.duration - t) / 0.35)
        frame = self.bg.frame(t, g)
        for k, (img, (sx, sy)) in enumerate(zip(self.stickers, STICKER_SLOTS)):
            p = _ease_out_back((t - 0.5 - k * 0.2) / 0.5)
            if p > 0.02:
                size = int(160 * p)
                icon = img.resize((size, max(2, int(size * img.height / img.width))), Image.BILINEAR)
                icon = icon.rotate(math.sin(g * 1.8 + k) * 12, resample=Image.BICUBIC, expand=True)
                _paste(frame, icon, sx - icon.width / 2, sy - icon.height / 2 + math.sin(g * 2 + k) * 20, fade)
        if self.side_character is not None:
            ch = self.side_character
            p = _ease_out((t - 0.1) / 0.6)
            d = ImageDraw.Draw(frame, "RGBA")
            d.rounded_rectangle((W - ch.width - 90, H - 560 + (1 - p) * 200, W - 30, H + 60), 60,
                                fill=(*self.theme.accent, int(40 * p * fade)))
            _paste(frame, ch, W - ch.width - 60, H - ch.height + 40 + (1 - p) * 300 + math.sin(g * 2) * 8, p * fade)
        for block, top in self.layout:
            block.draw(frame, top, t, fade)
        if self.captions:
            self.captions.draw(frame, t, fade, self.caption_y)
        self._chrome_dynamic(frame, progress, _clamp(t / scene.duration))
        return frame


@lru_cache(maxsize=1)
def watermark() -> Image.Image | None:
    """Brand stamp (pechat) for the bottom-right corner of every video, or None if not configured."""
    path = config.WATERMARK_PATH
    if not path or not path.exists():
        return None
    img = Image.open(path).convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    img = img.crop(bbox) if bbox else img
    w = config.WATERMARK_WIDTH
    img = img.resize((w, max(1, int(img.height * w / img.width))), Image.LANCZOS)
    # Yarim shaffof to'q plashka: oq logo och fonlarda ham o'qilsin
    pad_x, pad_y = 18, 10
    stamp = _rounded((img.width + pad_x * 2, img.height + pad_y * 2), (img.height + pad_y * 2) // 2,
                     (8, 10, 24, 205), (255, 255, 255, 40), 2)
    stamp.alpha_composite(img, (pad_x, pad_y))
    return _with_alpha(stamp, config.WATERMARK_OPACITY)


DARK_INK = np.array([0.06, 0.08, 0.16], dtype=np.float32)


def _outro_frames(bg: "Background", last: Image.Image, g0: float):
    """Yield outro frames: the brand clip (black background) keyed out as light and composited
    over the last scene's background. Light backgrounds get dark-ink text so it stays readable."""
    path = config.OUTRO_PATH
    if not path or not path.exists():
        return
    try:
        info = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
        sw, sh = (int(v) for v in info.strip().split(",")[:2])
    except (subprocess.CalledProcessError, ValueError):
        log.warning("Outro o'qilmadi: %s", path)
        return
    ow = W - 40
    oh = int(sh * ow / sw) // 2 * 2
    y0 = (H - oh) // 2
    reader = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={ow}:{oh},fps={FPS}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    first_bg = np.asarray(bg.frame(0, g0), dtype=np.float32) / 255
    light = float(first_bg.mean()) > 0.55
    size = ow * oh * 3
    i = 0
    try:
        while True:
            raw = reader.stdout.read(size)
            if len(raw) < size:
                break
            src = np.frombuffer(raw, np.uint8).reshape(oh, ow, 3).astype(np.float32) / 255
            alpha = np.clip((src.max(axis=2, keepdims=True) - 0.05) / 0.95, 0, 1)
            color = np.clip(src / np.maximum(alpha, 1e-3), 0, 1)
            if light:
                white = np.clip((color.min(axis=2, keepdims=True) - 0.5) / 0.35, 0, 1)
                color = color * (1 - white) + DARK_INK * white
                alpha = np.clip(alpha * 1.25, 0, 1)
            t = i / FPS
            frame = np.asarray(bg.frame(t, g0 + t), dtype=np.float32) / 255
            frame[y0 : y0 + oh, 20 : 20 + ow] = frame[y0 : y0 + oh, 20 : 20 + ow] * (1 - alpha) + color * alpha
            out = Image.fromarray((frame * 255).astype(np.uint8))
            blend = _clamp(t / 0.4)  # oxirgi sahnadan silliq o'tish
            if blend < 1:
                out = Image.blend(last, out, blend)
            yield out
            i += 1
    finally:
        reader.stdout.close()
        reader.wait()


def render_motion_video(scenes: list[Scene], spec: VideoSpec | None = None, audio_wav: bytes | None = None) -> Path:
    """Render scenes into an MP4 (optionally with a narration WAV track) and return its path."""
    spec = spec or VideoSpec()
    out = config.MEDIA_DIR / f"{uuid.uuid4().hex[:12]}.mp4"
    total_frames = sum(int(s.duration * FPS) for s in scenes)

    audio_path = None
    if audio_wav:
        audio_path = out.with_suffix(".wav")
        audio_path.write_bytes(audio_wav)
        # apad: ovoz tugagach jimlik, video (outro bilan) oxirigacha davom etadi
        audio_input = ["-i", str(audio_path), "-af", "apad"]
    else:
        # Instagram uchun jim AAC audio trek
        audio_input = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        *audio_input,
        "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ]
    mark = watermark()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frame_no = 0
    try:
        for idx, scene in enumerate(scenes):
            renderer = SceneRenderer(scene, spec, idx, len(scenes))
            for i in range(int(scene.duration * FPS)):
                frame = renderer.render(i / FPS, frame_no / FPS, (frame_no + 1) / total_frames)
                if mark is not None:
                    frame.paste(mark, (W - mark.width - 30, H - 135 - mark.height), mark)
                proc.stdin.write(frame.convert("RGB").tobytes())
                frame_no += 1
        # Outro: brend videosi oxirgi sahna foni ustida (shaffof)
        if scenes:
            last = frame.convert("RGB")
            for frame in _outro_frames(renderer.bg, last, frame_no / FPS):
                proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        code = proc.wait()
        if audio_path:
            audio_path.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg xatosi (kod {code})")
    return out
