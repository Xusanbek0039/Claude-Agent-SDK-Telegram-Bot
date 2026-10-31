"""Motion video generator: Pillow frames -> ffmpeg -> 1080x1920 MP4 (Reels format).

Uslublar (namunalar asosida):
- terminal: qora fon, yashil mono shrift, kartochkalar, kod oynalari, pastda so'zma-so'z subtitr
- glass:    och fon, rangli xira dog'lar, oq kartochkalar, tepada subtitr, o'ng pastda qahramon
- slides:   qora setkali fon, katta qalin harflar, raqamli ro'yxat, 01/10 hisoblagich
- kinetic:  qora fon, katta mono matn kursor bilan yoziladi, iliq yorug'lik
- classic:  rangli gradient / to'lqin / setka / bokeh fonlar
"""

import colorsys
import logging
import math
import random
import re
import subprocess
import uuid
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import audio, config

log = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 30
MARGIN = 80

THEME_NAMES = ("terminal", "glass", "slides", "kinetic", "classic", "warm", "lime", "minimal", "neon",
               "space", "ember", "peach", "retro", "chatui", "dialog", "neonlab", "monitor")
BG_STYLES = ("gradient", "diagonal", "radial", "waves", "grid", "bokeh", "image")
ELEMENT_TYPES = ("cards", "list", "code", "number", "chips", "diagram", "phone", "steps", "profile")

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


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower().replace("ʻ", "").replace("'", "").replace("’", ""))


def align_words(narration: str, offset: float, boundaries: list | None, clip_seconds: float
                ) -> list[tuple[str, float, float, bool]]:
    """Map TTS word boundaries (text, start, end) onto the narration words (keeps punctuation/markup).
    Falls back to proportional timing when the boundaries cannot be matched."""
    words = parse_markup(narration)
    if not boundaries:
        return narration_words(narration, offset, clip_seconds)
    out, j = [], 0
    for w, acc in words:
        target = _norm(w)
        if not target:  # faqat tinish belgisi: oldingi so'zga yopishtiramiz
            if out:
                prev = out[-1]
                out[-1] = (prev[0] + w, prev[1], prev[2], prev[3])
            continue
        if j >= len(boundaries):
            return narration_words(narration, offset, clip_seconds)
        start = boundaries[j][1]
        got = _norm(boundaries[j][0])
        end = boundaries[j][2]
        j += 1
        while len(got) < len(target) and j < len(boundaries):
            got += _norm(boundaries[j][0])
            end = boundaries[j][2]
            j += 1
        out.append((w, offset + start, offset + end, acc))
    return out


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
    accent_style: str = "underline"  # underline | color | pill | box
    extrude: bool = False            # retro: 3D chuqurlikdagi harflar
    box_color: tuple = (59, 130, 246)


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

THEMES.update({
    # 7-namuna: iliq och fon, katta oq kartochka, 01/10 hisoblagich
    "warm": Theme("warm", (246, 241, 236), (20, 20, 24), (110, 104, 98), (240, 110, 50),
                  "sans_black", 76, "sans", "sans_bold", "center", False,
                  (255, 255, 255, 245), (0, 0, 0, 12), 36, "none", shadow=True),
    # 8-namuna: lime / krem fon, qora qalin matn, urg'u so'zlar plashkada
    "lime": Theme("lime", (243, 240, 230), (17, 17, 17), (95, 95, 95), (217, 240, 30),
                  "sans_black", 84, "sans", "sans_bold", "center", False,
                  (255, 255, 255, 250), (0, 0, 0, 18), 28, "none", accent_style="pill"),
    # 9-namuna: qora minimal, tepada subtitr va tanlov ramkasi, qorong'i kod oynalari
    "minimal": Theme("minimal", (10, 10, 11), (236, 236, 238), (120, 120, 126), (59, 130, 246),
                     "sans_bold", 64, "sans", "sans_bold", "center", False,
                     (22, 22, 25, 255), (255, 255, 255, 22), 14, "topbox", accent_style="box"),
    # 10-namuna: to'q ko'k neon, nurli shar, diagrammalar
    "neon": Theme("neon", (6, 16, 40), (255, 255, 255), (150, 170, 210), (56, 189, 248),
                  "sans_black", 96, "sans", "sans_bold", "center", True,
                  (20, 42, 86, 210), (120, 190, 255, 70), 16, "bottom", accent_style="color"),
    # 12-namuna: kosmik fon, telefon maketlari, plashkadagi subtitr
    "space": Theme("space", (7, 9, 28), (255, 255, 255), (155, 165, 195), (34, 211, 238),
                   "sans_black", 76, "sans", "sans_bold", "center", False,
                   (18, 22, 50, 235), (255, 255, 255, 28), 22, "pill", accent_style="color"),
    # 16-namuna: qora fon, to'q-sariq nur, tepada butun jumla
    "ember": Theme("ember", (8, 6, 5), (255, 255, 255), (150, 140, 130), (255, 92, 31),
                   "sans_black", 70, "sans", "sans_bold", "center", False,
                   (24, 20, 18, 240), (255, 255, 255, 22), 20, "topfull", accent_style="color"),
    # 18-namuna: pastel shaftoli, tepada so'zma-so'z subtitr va ko'k tanlov ramkasi
    "peach": Theme("peach", (246, 239, 233), (24, 24, 26), (150, 140, 134), (234, 88, 12),
                   "sans_black", 72, "sans", "sans_bold", "center", False,
                   (255, 255, 255, 250), (0, 0, 0, 14), 26, "topbox", shadow=True, accent_style="box"),
    # 15-namuna: retro krem, perspektiv setka, katta 3D harflar
    "retro": Theme("retro", (241, 235, 220), (28, 36, 64), (120, 110, 95), (232, 93, 40),
                   "condensed", 170, "sans", "sans_bold", "center", True,
                   (255, 250, 240, 245), (28, 36, 64, 60), 18, "bigword", accent_style="color", extrude=True),
    # 1-namuna: Claude ilovasi ekrani (chat interfeysi)
    "chatui": Theme("chatui", (31, 31, 31), (236, 236, 236), (140, 140, 140), (217, 119, 87),
                    "sans_bold", 58, "sans", "sans_bold", "center", False,
                    (44, 44, 44, 255), (255, 255, 255, 20), 22, "bottom", accent_style="color"),
    # 11-namuna: personajlar dialogi, kod devor qog'ozi, tepada subtitr
    "dialog": Theme("dialog", (10, 16, 40), (255, 255, 255), (170, 180, 210), (255, 200, 61),
                    "sans_black", 64, "sans", "sans_bold", "center", False,
                    (16, 24, 56, 230), (255, 255, 255, 30), 20, "topfull", accent_style="color"),
    # 13-14-namunalar: neon laboratoriya, diktor nomi bilan subtitr
    "neonlab": Theme("neonlab", (6, 8, 20), (255, 255, 255), (170, 175, 200), (255, 140, 30),
                     "sans_black", 64, "sans", "sans_bold", "center", False,
                     (12, 20, 48, 220), (90, 160, 255, 120), 14, "pill", accent_style="color"),
    # 17-namuna: monitor oldida monolog, ekranda yoziluvchi matn
    "monitor": Theme("monitor", (4, 4, 8), (255, 255, 255), (160, 160, 170), (255, 196, 61),
                     "sans_black", 64, "sans", "sans_bold", "center", False,
                     (14, 18, 32, 255), (255, 255, 255, 30), 14, "pill", accent_style="color"),
})

# Qahramon pastda turadigan uslublar: (qahramon balandligi, emoji o'lchami)
HERO_BOTTOM = {"dialog": (1000, 620), "neonlab": (900, 540), "monitor": (760, 470)}
# Sarlavha qanday ko'rsatiladi: captions = subtitr bo'lsa yashiriladi
HEADING_MODE = {"minimal": "captions", "peach": "captions", "ember": "captions", "retro": "captions",
                "dialog": "hidden", "neonlab": "section", "monitor": "screen"}
WARM_PALETTE = ((253, 186, 140), (147, 197, 253), (252, 211, 177))
PEACH_PALETTE = ((255, 200, 170), (255, 225, 200), (250, 205, 185))
LIGHT_THEMES = {"glass", "warm", "lime", "peach", "retro"}

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
    speaker: str = ""            # dialog / neonlab: kim gapiryapti (subtitr ustidagi nom)


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
    if base.name == "lime" and scene.variant == "lime":
        t = replace(t, bg=(217, 240, 30), accent=(17, 17, 17))
    if base.name == "lime" and scene.variant == "night":
        t = replace(t, bg=(28, 31, 74), fg=(255, 255, 255), dim=(170, 175, 210))
    if base.name in ("ember", "minimal") and scene.variant == "light":
        t = replace(t, bg=(241, 236, 227), fg=(22, 22, 24), dim=(110, 104, 98),
                    card_bg=(255, 255, 255, 250), card_border=(0, 0, 0, 18))
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


def _glow_spot(img: Image.Image, center: tuple, size: int, color: tuple, strength: int) -> None:
    """Soft radial light (alpha falls to 0 at the circle edge, no visible seams)."""
    mask = Image.new("L", (W, H), 255)
    mask.paste(Image.radial_gradient("L").resize((size, size)), (center[0] - size // 2, center[1] - size // 2))
    layer = Image.new("RGBA", (W, H), (*color[:3], 0))
    layer.putalpha(mask.point(lambda v: max(0, int(strength * (1 - v / 181)))))
    img.alpha_composite(layer)


def _stars(img: Image.Image, count: int, seed: int) -> None:
    rnd = random.Random(seed + 101)
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(count):
        x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.choice([1, 1, 1.5, 2, 2.5])
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randint(60, 220)))


def _two_color(mask: Image.Image, c1: tuple, c2: tuple) -> Image.Image:
    return Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mask)


def _shift_hue(color: tuple, amount: float) -> tuple:
    h, l, s_ = colorsys.rgb_to_hls(*(c / 255 for c in color[:3]))
    r, g, b = colorsys.hls_to_rgb((h + amount) % 1, l, s_)
    return (int(r * 255), int(g * 255), int(b * 255))


@lru_cache(maxsize=48)
def _gradient_shape(size: int, c1: tuple, c2: tuple, kind: str, opacity: float) -> Image.Image:
    """Translucent gradient shape sprite: circle / ring / rounded square / pill."""
    grad = Image.linear_gradient("L").rotate(45, expand=True).resize((size, size))
    color = Image.composite(Image.new("RGB", (size, size), c2), Image.new("RGB", (size, size), c1), grad)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    m = size // 10
    if kind == "ring":
        d.ellipse((m, m, size - m, size - m), outline=255, width=size // 9)
    elif kind == "square":
        d.rounded_rectangle((m, m, size - m, size - m), size // 5, fill=255)
    elif kind == "pill":
        d.rounded_rectangle((m, size // 3, size - m, size - size // 3), size // 6, fill=255)
    else:
        d.ellipse((m, m, size - m, size - m), fill=255)
    # yumshoq chet va shaffof gradient (bir tomoni xiraroq)
    fade = Image.linear_gradient("L").rotate(-135, expand=True).resize((size, size)).point(lambda v: 90 + v * 165 // 255)
    mask = ImageChops.multiply(mask.filter(ImageFilter.GaussianBlur(size // 60 + 2)), fade)
    img = color.convert("RGBA")
    img.putalpha(mask.point(lambda v: int(v * opacity)))
    return img


SHAPE_OPACITY = {"terminal": 0.16, "slides": 0.15, "kinetic": 0.2, "glass": 0.5, "classic": 0.3,
                 "warm": 0.32, "lime": 0.0, "minimal": 0.0, "neon": 0.12, "space": 0.12, "ember": 0.14,
                 "peach": 0.3, "retro": 0.22, "chatui": 0.0, "dialog": 0.0, "neonlab": 0.0, "monitor": 0.0}


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
            self._make_shapes(theme, index)
            return
        self._themed_base(theme, index)
        self.base = self.base.convert("RGB")
        self._make_shapes(theme, index)

    def _make_shapes(self, theme: Theme, index: int) -> None:
        rnd = random.Random(index * 7 + 3)
        c1 = theme.glow if theme.name == "kinetic" else theme.accent
        c2 = _shift_hue(c1, 0.12 + rnd.random() * 0.2)
        opacity = SHAPE_OPACITY.get(theme.name, 0.2)
        if opacity <= 0:
            self.shapes = []
            return
        kinds = ["circle", "ring", "square", "pill"]
        rnd.shuffle(kinds)
        self.shapes = []
        for k in range(3):
            size = rnd.choice([380, 460, 560, 640])
            sprite = _gradient_shape(size, c1 if k % 2 == 0 else c2, c2 if k % 2 == 0 else c1, kinds[k], opacity)
            self.shapes.append((sprite, rnd.uniform(0.05, 0.95) * W, rnd.uniform(0.1, 0.9) * H,
                                rnd.uniform(40, 120), rnd.uniform(0.15, 0.35), rnd.uniform(0, 6.28)))

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
        elif name in ("warm", "peach"):
            top = theme.bg
            bottom = tuple(max(0, c - 10) for c in theme.bg)
            self.base = _two_color(Image.linear_gradient("L").resize((W, H)), top, bottom).convert("RGBA")
            self.blobs = [_blob(c) for c in (WARM_PALETTE if name == "warm" else PEACH_PALETTE)]
        elif name == "lime":
            if self.scene.variant == "night":
                _stars(self.base, 140, index)
            else:
                _glow_spot(self.base, (W // 2, int(H * 0.45)), 1300, (255, 238, 110), 150)
        elif name == "minimal":
            _grid(self.base, 60, (*theme.fg, 5))
        elif name == "neon":
            self.base = _two_color(Image.linear_gradient("L").resize((W, H)), (5, 14, 34), (10, 26, 62)).convert("RGBA")
            _glow_spot(self.base, (W // 2, int(H * 0.42)), 1500, (40, 100, 220), 90)
            _grid(self.base, 80, (120, 180, 255, 6))
            _vignette(self.base, 150)
        elif name == "space":
            for c, (x, y), r in [((110, 60, 200), (0.2, 0.25), 1300), ((40, 90, 210), (0.85, 0.6), 1400),
                                 ((150, 50, 160), (0.4, 0.9), 1100)]:
                _glow_spot(self.base, (int(W * x), int(H * y)), r, c, 80)
            _stars(self.base, 220, index)
        elif name == "ember":
            if self.scene.variant != "light":
                _glow_spot(self.base, (W // 2, int(H * 0.5)), 1500, theme.accent, 70)
                _vignette(self.base, 140)
        elif name == "retro":
            d = ImageDraw.Draw(self.base, "RGBA")
            cx, cy = W // 2, int(H * 0.56)
            for k in range(1, 16):
                rx, ry = k * 110, k * 38
                d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(*theme.fg, 22), width=2)
            for k in range(28):
                a = k / 28 * math.tau
                d.line((cx, cy, cx + math.cos(a) * 2200, cy + math.sin(a) * 2200), fill=(*theme.fg, 16), width=2)
            d.ellipse((W * 0.08, H * 0.1, W * 0.92, H * 0.1 + W * 0.84), outline=(*theme.accent, 60), width=4)
        elif name == "dialog":
            rnd = random.Random(5)
            d = ImageDraw.Draw(self.base, "RGBA")
            f = font("mono", 24)
            tokens = ["const", "return", "await", "import", "function", "=>", "{ }", "if", "else", "agent",
                      "model", "voice", "clone()", "render", "async", "data", "[]", "true", "null", "export"]
            for y in range(20, H, 38):
                x = rnd.randint(-40, 40)
                while x < W:
                    tok = rnd.choice(tokens)
                    d.text((x, y), tok, font=f, fill=(70, 100, 170, rnd.randint(25, 60)))
                    x += f.getlength(tok) + rnd.randint(20, 70)
            self.base = self.base.filter(ImageFilter.GaussianBlur(1.6))
            _vignette(self.base, 170)
        elif name == "neonlab":
            rnd = random.Random(9)
            d = ImageDraw.Draw(self.base, "RGBA")
            for row in range(5):
                for col in range(14):
                    if rnd.random() < 0.7:
                        x, y = 30 + col * 75, 70 + row * 70
                        d.rectangle((x, y, x + 60, y + 52), fill=(50, 110, 255, rnd.randint(60, 150)))
            for y in (470, 1380):
                d.rectangle((0, y, W, y + 6), fill=(255, 140, 30, 200))
                _glow_spot(self.base, (W // 2, y), 900, (255, 140, 30), 40)
            cx, cy = W // 2, 1500
            for k in range(-10, 11):
                d.line((cx, cy, cx + k * 220, H), fill=(80, 140, 255, 40), width=2)
            _vignette(self.base, 120)
        elif name == "monitor":
            _stars(self.base, 120, index)
            d = ImageDraw.Draw(self.base, "RGBA")
            for y in range(1560, H):
                k = (y - 1560) / (H - 1560)
                d.line((0, y, W, y), fill=(int(70 - 25 * k), int(42 - 15 * k), int(26 - 10 * k), 255))
            d.rounded_rectangle((40, 130, W - 40, 880), 18, fill=(26, 26, 32, 255), outline=(70, 70, 80, 255), width=3)
            d.rectangle((W / 2 - 60, 880, W / 2 + 60, 960), fill=(30, 30, 36, 255))
            d.rounded_rectangle((60, 150, W - 60, 860), 10, fill=(14, 18, 32, 255))
            d.text((90, 185), "Kontekst", font=font("sans_bold", 28), fill=(220, 220, 230, 255), anchor="lm")
            d.rectangle((250, 182, W - 90, 188), fill=(230, 60, 60, 255))
        elif name == "chatui":
            pass

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
        for sprite, x, y, amp, speed, ph in getattr(self, "shapes", []):
            _paste(frame, sprite, x - sprite.width / 2 + math.sin(g * speed + ph) * amp,
                   y - sprite.height / 2 + math.cos(g * speed * 0.8 + ph) * amp * 1.4)
        if name in ("neon", "space", "neonlab", "monitor"):
            d = ImageDraw.Draw(frame, "RGBA")
            rnd = random.Random(self.index)
            for k in range(40):
                x0, y0, sp = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(10, 40)
                y = (y0 - g * sp) % H
                a = int(90 + 80 * math.sin(g * 2.3 + k))
                col = (255, 160, 60) if name == "neonlab" and k % 2 else (120, 200, 255)
                r = 2 + (k % 3)
                d.ellipse((x0 - r, y - r, x0 + r, y + r), fill=(*col, max(0, a)))
        if name in ("glass", "warm", "peach"):
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
                if acc and theme.accent_style == "pill":
                    color = (17, 17, 17) if sum(theme.accent) > 450 else (255, 255, 255)
                if acc and theme.accent_style == "box":
                    color = theme.fg
                sprite = (_extrude_sprite(word, fnt, color, theme.fg if acc else theme.accent)
                          if theme.extrude else _text_sprite(word, fnt, (*color, 255)))
                self.items.append((sprite, x, li * self.line_h, acc, li, k, wd))
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
                style = self.theme.accent_style
                if acc and style == "pill" and p > 0:
                    pw = (wd + 28) * _ease_out((t - k * 0.07) / 0.25)
                    d.rounded_rectangle((x - 14, top + y + 4, x - 14 + pw, top + y + self.line_h * 0.95), 16,
                                        fill=(*self.theme.accent, int(255 * fade)))
                _paste(frame, sprite, x, top + y + (1 - p) * 34, p * fade)
                if acc and style == "box" and p > 0.5:
                    _selection_box(d, x - 10, top + y + 6, x + wd + 10, top + y + self.line_h * 0.95,
                                   self.theme.box_color, fade)
                if acc and style == "underline" and name in ("glass", "classic", "terminal", "warm"):
                    u = _ease_out((t - k * 0.07 - 0.3) / 0.35)
                    if u > 0:
                        uy = top + y + self.line_h * 0.9
                        d.rounded_rectangle((x, uy, x + wd * u, uy + 8), 4,
                                            fill=(*self.theme.accent, int(255 * fade)))
        if last_visible and int(t * 2.5) % 2 == 0:
            cx, cy = last_visible
            d.rectangle((cx + 10, cy + 8, cx + 10 + self.line_h * 0.32, cy + self.line_h * 0.92),
                        fill=(255, 45, 45, int(255 * fade)))


def _extrude_sprite(text: str, fnt, color: tuple, depth_color: tuple, depth: int = 9) -> Image.Image:
    """Retro 3D letters: the word repeated diagonally in a darker colour behind the face."""
    face = _text_sprite(text, fnt, (*color[:3], 255))
    back = _text_sprite(text, fnt, (*depth_color[:3], 255))
    out = Image.new("RGBA", (face.width + depth, face.height + depth), (0, 0, 0, 0))
    for k in range(depth, 0, -1):
        out.alpha_composite(back, (k, k))
    out.alpha_composite(face, (0, 0))
    return out


def _selection_box(d: ImageDraw.ImageDraw, x0, y0, x1, y1, color: tuple, fade: float) -> None:
    """Editor-style selection box with small square handles (samples 9 and 18)."""
    a = int(255 * fade)
    d.rectangle((x0, y0, x1, y1), outline=(*color[:3], a), width=3)
    for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        d.rectangle((hx - 6, hy - 6, hx + 6, hy + 6), fill=(255, 255, 255, a), outline=(*color[:3], a), width=2)


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


def _is_dark(theme: Theme) -> bool:
    return sum(theme.bg) < 360


@lru_cache(maxsize=8)
def _orb(size: int, color: tuple) -> Image.Image:
    """Glowing glass sphere (sample 10)."""
    pad = size // 2
    full = size + pad * 2
    img = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    glow = Image.new("L", (full, full), 0)
    ImageDraw.Draw(glow).ellipse((pad - 30, pad - 30, pad + size + 30, pad + size + 30), fill=170)
    halo = Image.new("RGBA", (full, full), (*color[:3], 0))
    halo.putalpha(glow.filter(ImageFilter.GaussianBlur(size // 5)))
    img.alpha_composite(halo)
    grad = Image.radial_gradient("L").resize((size * 2, size * 2)).crop((size // 2 - size // 5, size // 2 - size // 5,
                                                                         size // 2 - size // 5 + size, size // 2 - size // 5 + size))
    dark, light = (8, 40, 120), tuple(min(255, c + 120) for c in color[:3])
    sphere = Image.composite(Image.new("RGB", (size, size), dark), Image.new("RGB", (size, size), light), grad).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    sphere.putalpha(mask)
    hl = Image.new("L", (size, size), 0)
    ImageDraw.Draw(hl).ellipse((size * 0.22, size * 0.14, size * 0.5, size * 0.34), fill=200)
    white = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    white.putalpha(ImageChops.multiply(hl.filter(ImageFilter.GaussianBlur(size // 18)), mask))
    sphere.alpha_composite(white)
    img.alpha_composite(sphere, (pad, pad))
    return img


class OrbBlock(Block):
    def __init__(self, theme: Theme, t0: float, size: int = 260):
        self.theme, self.t0 = theme, t0
        self.sprite = _orb(size, theme.accent)
        self.size = size
        self.height = size + 60

    def draw(self, frame, top, t, fade):
        p = _ease_out_back((t - self.t0) / 0.7)
        if p <= 0.02:
            return
        pulse = 1 + 0.03 * math.sin(t * 3)
        k = p * pulse
        spr = self.sprite.resize((max(2, int(self.sprite.width * k)), max(2, int(self.sprite.height * k))), Image.BILINEAR)
        cy = top + self.height / 2
        _paste(frame, spr, W / 2 - spr.width / 2, cy - spr.height / 2, fade)
        d = ImageDraw.Draw(frame, "RGBA")
        for j in range(2):  # orbitadagi kichik nuqtalar
            a = t * (1.6 + j * 0.7) + j * 2
            ox, oy = W / 2 + math.cos(a) * self.size * 0.75, cy + math.sin(a) * self.size * 0.28
            d.ellipse((ox - 7, oy - 7, ox + 7, oy + 7), fill=(*self.theme.accent, int(230 * fade)))


def _pill_sprite(text: str, fnt, theme: Theme, active: bool = False, icon: str = "") -> Image.Image:
    tw = int(_measure(text, fnt)) + (fnt.size + 14 if icon else 0)
    h = int(fnt.size * 2.1)
    fill = (*theme.accent, 60) if active else theme.card_bg
    border = (*theme.accent, 220) if active else theme.card_border
    sp = _rounded((tw + 52, h), h // 2, fill, border, 2)
    x = 26
    if icon:
        ic = _asset(icon)
        if ic is not None:
            ic = ic.resize((fnt.size + 4, fnt.size + 4), Image.LANCZOS)
            sp.alpha_composite(ic, (x, (h - ic.height) // 2))
        x += fnt.size + 14
    ts = _text_sprite(text, fnt, (*theme.fg, 255))
    sp.alpha_composite(ts, (x, (h - ts.height) // 2 + 3))
    return sp


class DiagramBlock(Block):
    """Center node with branches to child nodes (samples 7, 10): lines draw in, nodes pop."""

    def __init__(self, center: str, nodes: list[str], theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        up = theme.name in ("neon", "neonlab")
        self.center = _pill_sprite(center.upper() if up else center, font(theme.body_bold, 34), theme, True,
                                   "🔵" if theme.name == "neon" else "")
        f = font(theme.body_bold, 28)
        self.nodes = [_pill_sprite(n.upper() if up else n, f, theme) for n in nodes[:5]]
        n = len(self.nodes)
        span = W - 2 * MARGIN
        self.xs = [MARGIN + span * (i + 0.5) / n for i in range(n)]
        self.row = 300
        self.height = self.row + (self.nodes[0].height if self.nodes else 0)

    def draw(self, frame, top, t, fade):
        t = t - self.t0
        d = ImageDraw.Draw(frame, "RGBA")
        cp = _ease_out_back(t / 0.5)
        if cp > 0.02:
            c = self.center
            spr = c if cp > 0.99 else c.resize((max(2, int(c.width * cp)), max(2, int(c.height * cp))))
            _paste(frame, spr, W / 2 - spr.width / 2, top + (c.height - spr.height) / 2, fade)
        y1, y2 = top + self.center.height, top + self.row
        ymid = (y1 + y2) / 2
        acc = self.theme.accent
        for i, (x, node) in enumerate(zip(self.xs, self.nodes)):
            lp = _ease_out((t - 0.35 - i * 0.15) / 0.5)
            if lp <= 0:
                continue
            pts = [(W / 2, y1), (W / 2, ymid), (x, ymid), (x, y2)]
            segs = [math.dist(pts[k], pts[k + 1]) for k in range(3)]
            remain = sum(segs) * lp
            path = [pts[0]]
            for k in range(3):
                if remain <= 0:
                    break
                frac = min(1, remain / segs[k]) if segs[k] else 1
                a, b = pts[k], pts[k + 1]
                path.append((a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac))
                remain -= segs[k]
            d.line(path, fill=(*acc, int(60 * fade)), width=10, joint="curve")
            d.line(path, fill=(*acc, int(230 * fade)), width=3, joint="curve")
            if lp >= 1:
                q = (t * 0.8 + i * 0.3) % 1  # chiziq bo'ylab yuguruvchi nuqta
                k = min(2, int(q * 3))
                a, b = pts[k], pts[k + 1]
                f = q * 3 - k
                px, py = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                d.ellipse((px - 6, py - 6, px + 6, py + 6), fill=(255, 255, 255, int(230 * fade)))
            np_ = _ease_out_back((t - 0.75 - i * 0.15) / 0.4)
            if np_ > 0.02:
                spr = node if np_ > 0.99 else node.resize((max(2, int(node.width * np_)), max(2, int(node.height * np_))))
                _paste(frame, spr, x - spr.width / 2, y2 + (node.height - spr.height) / 2, fade)


class PhoneBlock(Block):
    """Phone mockup with a chat; bubbles appear one by one (samples 12, 18)."""

    def __init__(self, title: str, messages: list, theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        pw, ph = 560, 980
        dark = _is_dark(theme)
        screen = (16, 18, 34) if dark else (255, 255, 255)
        txt = (240, 240, 245) if dark else (20, 20, 24)
        self.phone = _rounded((pw, ph), 74, (18, 18, 22, 255), (90, 90, 100, 255), 3, shadow=not dark)
        off = 40 if not dark else 0
        dr = ImageDraw.Draw(self.phone)
        dr.rounded_rectangle((off + 16, off + 16, off + pw - 16, off + ph - 16), 60, fill=screen)
        dr.rounded_rectangle((off + pw / 2 - 70, off + 30, off + pw / 2 + 70, off + 62), 16, fill=(18, 18, 22))
        dr.ellipse((off + 44, off + 96, off + 104, off + 156), fill=(*theme.accent, 255))
        dr.text((off + 74, off + 126), (title or "Bot")[:1].upper(), font=font("sans_black", 30), fill=(255, 255, 255), anchor="mm")
        dr.text((off + 122, off + 112), title or "Bot", font=font("sans_bold", 30), fill=txt, anchor="lm")
        dr.text((off + 122, off + 144), "online", font=font("sans", 22), fill=(80, 200, 120), anchor="lm")
        dr.line((off + 30, off + 182, off + pw - 30, off + 182), fill=(*txt, 30), width=2)
        self.off, self.pw, self.ph = off, pw, ph
        f = font("sans", 28)
        self.bubbles, y = [], 210
        for m in messages[:7]:
            mine = str(m.get("from", "user")) in ("user", "men", "me", "mijoz")
            text = str(m.get("text", ""))
            lines = _wrap_words([(w,) for w in text.split()], f, 330)
            bw = int(max(f.getlength(" ".join(w for (w,) in ln)) for ln in lines)) + 44 if lines else 80
            bh = len(lines) * 38 + 28
            fill = (*theme.accent, 255) if mine else ((44, 48, 72, 255) if dark else (238, 238, 242, 255))
            color = (255, 255, 255) if mine else txt
            b = _rounded((bw, bh), 24, fill)
            bd = ImageDraw.Draw(b)
            for li, ln in enumerate(lines):
                bd.text((22, 14 + li * 38), " ".join(w for (w,) in ln), font=f, fill=color)
            x = pw - 40 - bw if mine else 40
            if y + bh > ph - 60:
                break
            self.bubbles.append((b, x, y, mine))
            y += bh + 18
        self.height = ph

    def draw(self, frame, top, t, fade):
        t = t - self.t0
        p = _ease_out(t / 0.5)
        if p <= 0:
            return
        x0 = W / 2 - self.pw / 2 - self.off
        y0 = top + (1 - p) * 120 - self.off
        _paste(frame, self.phone, x0, y0, p * fade)
        for k, (b, bx, by, mine) in enumerate(self.bubbles):
            q = _ease_out_back((t - 0.5 - k * 0.7) / 0.35)
            if q <= 0.02:
                continue
            spr = b if q > 0.99 else b.resize((max(2, int(b.width * q)), max(2, int(b.height * q))))
            ax = bx + (b.width - spr.width if mine else 0)
            _paste(frame, spr, x0 + self.off + ax, y0 + self.off + by + (b.height - spr.height) / 2, fade)


class StepsBlock(Block):
    """Numbered stepper that fills up to the active step (sample 12)."""

    def __init__(self, items: list[str], active: int, theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        self.items = [str(x) for x in items[:5]]
        self.active = max(1, min(len(self.items), int(active or len(self.items))))
        self.f, self.nf = font(theme.body_bold, 26), font("sans_black", 30)
        self.height = 170

    def draw(self, frame, top, t, fade):
        t = t - self.t0
        n = len(self.items)
        if not n:
            return
        d = ImageDraw.Draw(frame, "RGBA")
        xs = [MARGIN + 60 + (W - 2 * MARGIN - 120) * (i / max(1, n - 1)) for i in range(n)] if n > 1 else [W / 2]
        cy = top + 45
        a = int(255 * fade * _ease_out(t / 0.4))
        d.line((xs[0], cy, xs[-1], cy), fill=(*self.theme.fg, int(a * 0.2)), width=6)
        prog = _clamp((t - 0.3) / (0.6 * self.active))
        reach = xs[0] + (xs[self.active - 1] - xs[0]) * prog
        d.line((xs[0], cy, reach, cy), fill=(*self.theme.accent, a), width=6)
        for i, x in enumerate(xs):
            done = reach >= x - 1
            fill = (*self.theme.accent, a) if done else (*self.theme.card_bg[:3], a)
            d.ellipse((x - 34, cy - 34, x + 34, cy + 34), fill=fill, outline=(*self.theme.accent, a), width=3)
            label = "✓" if done and i < self.active - 1 else str(i + 1)
            _text_alpha(frame, (x, cy), label, self.nf, (255, 255, 255) if done else self.theme.fg, a / 255, "mm")
            _text_alpha(frame, (x, cy + 70), self.items[i][:18], self.f,
                        self.theme.fg if done else self.theme.dim, a / 255, "mm")


class ProfileBlock(Block):
    """Profile / subscribe CTA card; the button gets 'pressed' (samples 7, 9, 16, 18)."""

    def __init__(self, name: str, bio: str, button: str, avatar: str, theme: Theme, t0: float):
        self.theme, self.t0 = theme, t0
        w, h = W - 2 * MARGIN, 440
        self.card = _rounded((w, h), 30, theme.card_bg, theme.card_border, 2, shadow=theme.shadow)
        off = 40 if theme.shadow else 0
        dr = ImageDraw.Draw(self.card)
        av = _asset(Path(avatar)) if avatar and Path(avatar).exists() else _asset("🧑‍💻")
        mask = Image.new("L", (130, 130), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 129, 129), fill=255)
        dr.ellipse((off + 36, off + 36, off + 172, off + 172), fill=(*theme.accent, 255))
        if av is not None:
            side = min(av.width, av.height)
            sq = av.crop(((av.width - side) // 2, 0, (av.width + side) // 2, side)).resize((130, 130), Image.LANCZOS)
            bgc = Image.new("RGBA", (130, 130), (255, 255, 255, 255))
            bgc.alpha_composite(sq)
            bgc.putalpha(mask)
            self.card.alpha_composite(bgc, (off + 39, off + 39))
        dr.text((off + 200, off + 80), name or "@handle", font=font("sans_black", 40), fill=(*theme.fg, 255), anchor="lm")
        bf = font("sans", 28)
        for i, ln in enumerate(_wrap_words([(x,) for x in (bio or "").split()], bf, w - 240)[:3]):
            dr.text((off + 200, off + 125 + i * 38), " ".join(x for (x,) in ln), font=bf, fill=(*theme.dim, 255), anchor="lm")
        self.off, self.w, self.h = off, w, h
        self.button = button or "Obuna bo'lish"
        self.height = h

    def draw(self, frame, top, t, fade):
        t = t - self.t0
        p = _ease_out(t / 0.5)
        if p <= 0:
            return
        y0 = top + (1 - p) * 60
        _paste(frame, self.card, MARGIN - self.off, y0 - self.off, p * fade)
        d = ImageDraw.Draw(frame, "RGBA")
        bx0, by0, bx1, by1 = MARGIN + 36, y0 + self.h - 130, MARGIN + self.w - 36, y0 + self.h - 40
        pressed = t > 1.7
        squeeze = 6 if 1.55 < t < 1.75 else 0
        fill = (*self.theme.dim, int(120 * fade)) if pressed else (*self.theme.accent, int(255 * fade))
        d.rounded_rectangle((bx0 + squeeze, by0 + squeeze, bx1 - squeeze, by1 - squeeze), 26, fill=fill)
        label = "✓ Obuna bo'lindi" if pressed else self.button
        _text_alpha(frame, ((bx0 + bx1) / 2, (by0 + by1) / 2), label, font("sans_black", 36),
                    (255, 255, 255), fade * p, "mm")
        cur = _asset("👆")
        if cur is not None and 0.9 < t < 2.6:
            k = _ease_out((t - 0.9) / 0.6)
            cx = W * 0.85 + ((bx0 + bx1) / 2 - W * 0.85) * k
            cy = by1 + 160 + ((by0 + by1) / 2 + 10 - by1 - 160) * k
            _paste(frame, cur.resize((90, 90), Image.LANCZOS), cx - 30, cy, fade)


class SectionPill:
    """Small section label at the top (neonlab: '1 · VAZIFA BERAMIZ')."""

    def __init__(self, text: str, theme: Theme):
        self.sprite = _pill_sprite(strip_markup(text).upper()[:34], font("mono_bold", 26), theme, True)

    def draw(self, frame, t, fade):
        p = _ease_out((t - 0.1) / 0.4)
        _paste(frame, self.sprite, W / 2 - self.sprite.width / 2, 170 - (1 - p) * 30, p * fade)


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
        if kind == "diagram" and el.get("nodes"):
            return DiagramBlock(str(el.get("center", "")), [str(x) for x in el["nodes"]], theme, t0)
        if kind == "phone" and el.get("messages"):
            return PhoneBlock(str(el.get("title", "Bot")), el["messages"], theme, t0)
        if kind == "steps" and el.get("items"):
            return StepsBlock(el["items"], el.get("active", 0), theme, t0)
        if kind == "profile":
            return ProfileBlock(str(el.get("name", "")), str(el.get("bio", "")), str(el.get("button", "")),
                                str(el.get("avatar", "")), theme, t0)
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
        elif mode == "topbox":
            self.f = font("sans_bold", 54)
        elif mode == "topfull":
            self.f = font("sans_black", 58)
        elif mode == "pill":
            self.f = font("sans_black", 44)
        elif mode == "bigword":
            self.f = font("condensed", 190)
        else:
            self.f = font(theme.body_bold if theme.name != "terminal" else "mono_bold", 46)
        max_w = {"kinetic": 820, "pill": W - 2 * MARGIN - 80, "topfull": int((W - 2 * MARGIN) * 1.8),
                 "topbox": int((W - 2 * MARGIN) * 1.6), "bigword": W - 2 * MARGIN}.get(mode, W - 2 * MARGIN)
        max_words = {"kinetic": 3, "bigword": 2, "pill": 5, "topfull": 9, "topbox": 8}.get(mode, 6)
        if mode == "bigword":
            words = [(w.upper(), *rest) for w, *rest in words]
            self.words = words
        indexed = [(w, i) for i, (w, *_rest) in enumerate(words)]
        self.groups = _wrap_words(indexed, self.f, max_w, max_words)
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
        if self.mode in ("topbox", "topfull"):
            self._draw_top_lines(frame, d, grp, idx, fade)
            return
        if self.mode == "pill":
            self._draw_pill(frame, d, grp, idx, total, fade, y_center)
            return
        if self.mode == "bigword":
            self._draw_bigword(frame, gi, grp, t, fade)
            return
        if self.mode == "top":
            shown = [(w, i) for w, i in grp if i <= idx]
            stotal = sum(self.f.getlength(w) for w, _ in shown) + space * (len(shown) - 1)
            _, last = self._line(frame, grp, idx, (W - stotal) / 2, 150, fade, True)
            if last:
                d.rounded_rectangle((last[0], 232, last[0] + last[1], 240), 4,
                                    fill=(*self.theme.accent, int(255 * fade)))
            return
        self._line(frame, grp, idx, (W - total) / 2, H - 300, fade, False)


def _captions_extra(cls):
    def _draw_top_lines(self, frame, d, grp, idx, fade):
        lines = _wrap_words(grp, self.f, W - 2 * MARGIN)
        lh = int(self.f.size * 1.3)
        y = 150 if self.mode == "topfull" else 190
        space = self.f.getlength(" ")
        shadow = self.theme.name == "dialog"
        for ln in lines:
            total = sum(self.f.getlength(w) for w, _ in ln) + space * (len(ln) - 1)
            x = (W - total) / 2
            for w, i in ln:
                acc = self.words[i][3]
                wd = self.f.getlength(w)
                if self.mode == "topfull":
                    color, a = (self.theme.accent if acc else self.theme.fg), 1.0
                else:
                    color = self.theme.fg
                    a = 1.0 if i <= idx else 0.32
                if shadow:
                    _text_alpha(frame, (x + 3, y + 4), w, self.f, (0, 0, 0), 0.6 * fade)
                _text_alpha(frame, (x, y), w, self.f, color, a * fade)
                if self.mode == "topbox" and i == idx:
                    _selection_box(d, x - 10, y + 2, x + wd + 10, y + lh - 8, self.theme.box_color, fade)
                x += wd + space
            y += lh

    def _draw_pill(self, frame, d, grp, idx, total, fade, y_center):
        y = y_center or H - 330
        pad_x, pad_y = 34, 18
        lh = int(self.f.size * 1.25)
        x0 = (W - total) / 2
        d.rounded_rectangle((x0 - pad_x, y - pad_y, x0 + total + pad_x, y + lh + pad_y), 22,
                            fill=(0, 0, 0, int(165 * fade)))
        speaker = getattr(self, "speaker", "")
        if speaker:
            sf = font("mono_bold", 24)
            sw = sf.getlength(speaker.upper()) + 30
            d.rounded_rectangle((x0 - pad_x, y - pad_y - 52, x0 - pad_x + sw, y - pad_y - 12), 10,
                                fill=(*self.theme.accent, int(255 * fade)))
            _text_alpha(frame, (x0 - pad_x + sw / 2, y - pad_y - 32), speaker.upper(), sf, (20, 20, 20), fade, "mm")
        space = self.f.getlength(" ")
        x = x0
        for w, i in grp:
            acc = self.words[i][3]
            if i == idx or (acc and i < idx):
                color, a = self.theme.accent, 1.0
            else:
                color, a = self.theme.fg, (1.0 if i < idx else 0.5)
            _text_alpha(frame, (x, y), w, self.f, color, a * fade)
            x += self.f.getlength(w) + space

    def _draw_bigword(self, frame, gi, grp, t, fade):
        cache = self.__dict__.setdefault("_big", {})
        if gi not in cache:
            lines = []
            for w, i in grp:
                acc = self.words[i][3]
                color = self.theme.accent if acc else self.theme.fg
                depth = self.theme.fg if acc else self.theme.accent
                lines.append(_extrude_sprite(w, self.f, color, depth, 12))
            hgt = sum(sp.height for sp in lines) - 30 * (len(lines) - 1)
            wid = max(sp.width for sp in lines)
            img = Image.new("RGBA", (wid, hgt), (0, 0, 0, 0))
            y = 0
            for sp in lines:
                img.alpha_composite(sp, ((wid - sp.width) // 2, y))
                y += sp.height - 30
            if img.width > W - 80:
                k = (W - 80) / img.width
                img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
            cache[gi] = (img, self.words[grp[0][1]][1])
        img, start = cache[gi]
        p = _ease_out_back((t - start + 0.05) / 0.3)
        k = max(0.05, p)
        spr = img if k > 0.99 else img.resize((max(2, int(img.width * k)), max(2, int(img.height * k))))
        spr = spr.rotate(-4 + 2 * math.sin(t), resample=Image.BICUBIC, expand=True)
        _paste(frame, spr, W / 2 - spr.width / 2, H * 0.46 - spr.height / 2, fade)

    cls._draw_top_lines = _draw_top_lines
    cls._draw_pill = _draw_pill
    cls._draw_bigword = _draw_bigword
    return cls


_captions_extra(Captions)


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
        if self.captions:
            self.captions.speaker = scene.speaker

        character = _asset(scene.character) if scene.character else None
        self.side_character = None
        self.bottom_hero = None
        self.section = None
        self.screen_lines: list[str] = []
        blocks: list[Block] = []
        t0 = 0.1
        hmode = HEADING_MODE.get(base.name, "normal")
        if base.name in HERO_BOTTOM:
            ch_px, em_px = HERO_BOTTOM[base.name]
            img = character if character is not None else (_asset(scene.emoji) if scene.emoji else None)
            if img is not None:
                px = ch_px if character is not None else em_px
                ratio = px / max(img.height, 1) if character is not None else px / max(img.width, img.height)
                self.bottom_hero = img.resize((max(2, int(img.width * ratio)), max(2, int(img.height * ratio))),
                                              Image.LANCZOS)
            character = None
            scene_emoji_used = True
        else:
            scene_emoji_used = False
        if base.name == "neon" and character is None and not scene.emoji:
            blocks.append(OrbBlock(theme, t0))
            t0 += 0.25
        if scene_emoji_used:
            pass
        elif character is not None and base.name == "glass":
            ratio = 760 / character.height
            self.side_character = character.resize((int(character.width * ratio), 760), Image.LANCZOS)
        elif character is not None:
            blocks.append(HeroBlock(character, 560, t0, True))
            t0 += 0.2
        elif scene.emoji and _asset(scene.emoji) is not None:
            blocks.append(HeroBlock(_asset(scene.emoji), 260, t0, False))
            t0 += 0.2
        heading = scene.heading
        show_heading = bool(heading) and not (cap_mode == "kinetic" and not scene.elements and not blocks)
        if hmode == "captions" and self.captions:
            show_heading = False
        elif hmode == "hidden":
            show_heading = False
        elif hmode == "section":
            if heading:
                self.section = SectionPill(heading, theme)
            show_heading = False
        elif hmode == "screen":
            if heading:
                self.screen_lines.append("# " + strip_markup(heading))
            show_heading = False
        if show_heading:
            blocks.append(HeadingBlock(heading, theme, t0))
            t0 += 0.35
        if scene.subheading:
            blocks.append(TextBlock(scene.subheading, theme, t0))
            t0 += 0.3
        for el in scene.elements[:2]:
            if base.name == "monitor" and el.get("type") == "code":
                self.screen_lines += [str(x) for x in el.get("lines", [])][:10]
                continue
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
        elif base.name in ("minimal", "peach"):
            top, bottom = 360, 1640
        elif base.name == "ember":
            top, bottom = 400, 1640
        elif base.name == "chatui":
            top, bottom = 320, 1520
        elif base.name == "warm":
            top, bottom = 230, 1700
        elif base.name in ("neonlab", "dialog"):
            top, bottom = (300, 900) if self.bottom_hero is not None else (300, 1450)
        elif base.name == "monitor":
            top, bottom = 900, 1500
        elif base.name == "space":
            top, bottom = 230, 1450
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
        if cap_mode == "pill":
            self.caption_y = {"monitor": 1330, "neonlab": H - 470}.get(base.name, H - 330)
        # warm: butun kontent bitta katta oq kartochka ichida
        self.panel = None
        if base.name == "warm" and self.layout:
            y_a = self.layout[0][1] - 70
            y_b = self.layout[-1][1] + self.layout[-1][0].height + 70
            self.panel = (_rounded((W - 2 * MARGIN + 60, int(y_b - y_a)), 40, theme.card_bg, theme.card_border, 2,
                                   shadow=True), MARGIN - 30 - 40, y_a - 40)

        self.stickers = [img for img in (_asset(s) for s in scene.stickers[:4]) if img is not None]
        self._chrome_static()

    def events(self) -> list[tuple[float, str]]:
        """Sound-effect cues (seconds from scene start) matching the animations."""
        ev: list[tuple[float, str]] = []
        for block, _top in self.layout:
            t0 = getattr(block, "t0", 0.1)
            if isinstance(block, HeroBlock):
                ev.append((t0 + 0.08, "pop"))
            elif isinstance(block, HeadingBlock):
                ev.append((t0, "swoosh"))
            elif isinstance(block, CardsBlock):
                ev += [(t0 + k * 0.18 + 0.05, "pop") for k in range(len(block.sprites))]
            elif isinstance(block, ListBlock):
                ev += [(t0 + k * 0.2 + 0.05, "pop") for k in range(len(block.sprites))]
            elif isinstance(block, ChipsBlock):
                ev += [(t0 + k * 0.1 + 0.05, "pop") for k in range(len(block.items))]
            elif isinstance(block, CodeBlock):
                typing = block.total_chars / 45
                ev += [(t0 + 0.3 + k * 0.075, "tick") for k in range(int(typing / 0.075))]
            elif isinstance(block, NumberBlock):
                if block.target is not None:
                    ev += [(t0 + k * 0.09, "tick") for k in range(13)]
                ev.append((t0 + 1.3, "ding"))
            elif isinstance(block, DiagramBlock):
                ev.append((t0, "pop"))
                ev += [(t0 + 0.75 + k * 0.15, "pop") for k in range(len(block.nodes))]
            elif isinstance(block, PhoneBlock):
                ev.append((t0, "swoosh"))
                ev += [(t0 + 0.5 + k * 0.7, "pop") for k in range(len(block.bubbles))]
            elif isinstance(block, StepsBlock):
                ev += [(t0 + 0.3 + k * 0.6, "pop") for k in range(block.active)]
            elif isinstance(block, ProfileBlock):
                ev += [(t0, "swoosh"), (t0 + 1.6, "tick"), (t0 + 1.75, "ding")]
            elif isinstance(block, OrbBlock):
                ev.append((t0, "pop"))
        if self.side_character is not None or self.bottom_hero is not None:
            ev.append((0.1, "swoosh"))
        if self.section is not None:
            ev.append((0.1, "swoosh"))
        if self.screen_lines:
            chars = sum(len(x) for x in self.screen_lines)
            ev += [(0.2 + k * 0.08, "tick") for k in range(int(chars / 38 / 0.08))]
        ev += [(0.5 + k * 0.2, "pop") for k in range(len(self.stickers))]
        if self.captions and self.captions.mode == "kinetic":
            ev += [(w[1], "tick") for w in self.scene.words]
        return [(t, k) for t, k in ev if t < self.scene.duration]

    def _draw_screen(self, frame: Image.Image, t: float, fade: float) -> None:
        """Monitor theme: lines typed on the monitor screen with a blinking cursor."""
        f = font("mono", 32)
        chars = int(max(0.0, t - 0.2) * 38)
        x0, y = 90, 230
        cursor = None
        for line in self.screen_lines:
            if chars <= 0:
                break
            shown = line[:chars]
            chars -= len(line)
            color = (120, 200, 255) if line.startswith("#") else ((120, 220, 140) if line.startswith((">", "$", "✓", "+"))
                                                                   else (225, 228, 235))
            _text_alpha(frame, (x0, y), shown, f, color, fade)
            cursor = (x0 + f.getlength(shown), y)
            y += 48
        if cursor and int(t * 2.5) % 2 == 0:
            ImageDraw.Draw(frame, "RGBA").rectangle((cursor[0] + 4, cursor[1] + 4, cursor[0] + 22, cursor[1] + 38),
                                                    fill=(240, 240, 240, int(230 * fade)))

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
        elif th.name == "warm":
            label = f"{self.index + 1:02d} / {self.count:02d}"
            lf = font("mono_bold", 26)
            lw = lf.getlength(label) + 36
            d.rounded_rectangle((MARGIN, 66, MARGIN + lw, 114), 24, fill=(255, 255, 255, 220), outline=(0, 0, 0, 20), width=2)
            d.text((MARGIN + lw / 2, 90), label, font=lf, fill=(*th.accent, 255), anchor="mm")
            if spec.handle:
                d.text((W / 2, H - 150), spec.handle, font=font("sans_bold", 28), fill=(*th.dim, 255), anchor="mm")
        elif th.name in ("neon",) and spec.label:
            d.text((W / 2, 120), "  ".join(spec.label.upper()), font=font("sans_bold", 24), fill=(*th.dim, 255), anchor="mm")
        elif th.name == "space":
            d.text((MARGIN, 90), "✦ " + (spec.label or spec.handle or ""), font=font("sans_bold", 28),
                   fill=(*th.dim, 255), anchor="lm")
        elif th.name == "ember":
            hf = font("mono_bold", 26)
            name = (spec.handle or "").lstrip("@").upper()
            d.text((MARGIN, 90), name, font=hf, fill=(*th.fg, 255), anchor="lm")
            d.text((W - MARGIN, 90), (spec.label or "").upper(), font=font("mono", 22), fill=(*th.dim, 255), anchor="rm")
        elif th.name == "peach" and spec.handle:
            d.text((MARGIN, 80), "▶ " + spec.handle, font=font("sans_bold", 24), fill=(*th.dim, 255), anchor="lm")
        elif th.name == "chatui":
            for k in range(3):
                d.rounded_rectangle((60, 78 + k * 14, 100, 84 + k * 14), 3, fill=(*th.dim, 255))
            d.rounded_rectangle((W / 2 - 160, 62, W / 2 + 160, 122), 30, fill=(45, 45, 45, 255))
            d.rounded_rectangle((W / 2 + 4, 68, W / 2 + 154, 116), 24, fill=(70, 70, 70, 255))
            d.text((W / 2 - 78, 92), "Chat", font=font("sans_bold", 28), fill=(*th.dim, 255), anchor="mm")
            d.text((W / 2 + 79, 92), "Code", font=font("sans_bold", 28), fill=(*th.fg, 255), anchor="mm")
            cmd = "/" + (spec.label or "effekt").lstrip("/")
            cf = font("mono_bold", 30)
            cw = cf.getlength(cmd) + 40
            d.rounded_rectangle((W - MARGIN - cw, 160, W - MARGIN, 214), 14, fill=(*th.accent, 50))
            d.text((W - MARGIN - cw / 2, 187), cmd, font=cf, fill=(*th.accent, 255), anchor="mm")
            d.text((MARGIN, 262), "✳", font=font("sans_black", 34), fill=(*th.accent, 255), anchor="lm")
            d.text((MARGIN + 46, 262), f"Editing with {cmd.lstrip('/')}...", font=font("sans", 28),
                   fill=(*th.dim, 255), anchor="lm")
            d.rounded_rectangle((MARGIN, H - 210, W - MARGIN, H - 110), 34, fill=(42, 42, 42, 255),
                                outline=(70, 70, 70, 255), width=2)
            d.text((MARGIN + 40, H - 160), "+   Reply to Claude...", font=font("sans", 30), fill=(*th.dim, 255), anchor="lm")
            d.ellipse((W - MARGIN - 80, H - 190, W - MARGIN - 20, H - 130), fill=(*th.accent, 255))
            d.text((W - MARGIN - 50, H - 160), "↑", font=font("sans_black", 34), fill=(255, 255, 255, 255), anchor="mm")
        elif th.name == "neonlab":
            hf = font("mono_bold", 26)
            left = (spec.handle or "").lstrip("@").upper()
            right = (spec.label or "").upper()
            text = f"{left}  ×  {right}" if left and right else (left or right)
            d.text((W / 2, 110), text, font=hf, fill=(*th.fg, 255), anchor="mm")

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
        elif th.name == "warm":
            lf = font("mono_bold", 26)
            lw = lf.getlength(f"{self.index + 1:02d} / {self.count:02d}") + 36
            x0, x1 = MARGIN + lw + 24, W - MARGIN
            seg = (x1 - x0 - 8 * (self.count - 1)) / max(1, self.count)
            for i in range(self.count):
                sx = x0 + i * (seg + 8)
                d.rectangle((sx, 88, sx + seg, 92), fill=(*th.fg, 40))
                fill = 1.0 if i < self.index else (scene_p if i == self.index else 0)
                if fill:
                    d.rectangle((sx, 88, sx + seg * fill, 92), fill=(*th.accent, 255))
        elif th.name in ("chatui", "monitor", "dialog"):
            pass
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
        if self.panel is not None:
            sp, px, py = self.panel
            p = _ease_out((t - 0.05) / 0.45)
            _paste(frame, sp, px, py + (1 - p) * 60, p * fade)
        if self.screen_lines:
            self._draw_screen(frame, t, fade)
        if self.section is not None:
            self.section.draw(frame, t, fade)
        if self.bottom_hero is not None:
            hero = self.bottom_hero
            p = _ease_out((t - 0.05) / 0.5)
            speaking = any(a <= t <= b + 0.1 for _w, a, b, _acc in scene.words)
            sq = 1 + (0.018 * abs(math.sin(t * 13)) if speaking else 0)
            spr = hero if sq == 1 else hero.resize((hero.width, int(hero.height * sq)), Image.BILINEAR)
            y = H - spr.height + 30 + (1 - p) * 300 + math.sin(g * 1.8) * 6
            if self.theme.name == "monitor":
                y = 1560 - spr.height + 120 + (1 - p) * 300
            _paste(frame, spr, W / 2 - spr.width / 2, y, p * fade)
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


def _outro_seconds() -> float:
    path = config.OUTRO_PATH
    if not path or not path.exists():
        return 0.0
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True, check=True).stdout
        return float(out.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0


def render_motion_video(scenes: list[Scene], spec: VideoSpec | None = None, audio_wav: bytes | None = None,
                        music: str | None = "auto") -> Path:
    """Render scenes into an MP4 with a full soundtrack (narration + music bed + SFX) and return its path.
    music: mood name (tech/bright/upbeat/emotional/calm), "auto" (by theme) or "none"."""
    spec = spec or VideoSpec()
    out = config.MEDIA_DIR / f"{uuid.uuid4().hex[:12]}.mp4"
    total_frames = sum(int(s.duration * FPS) for s in scenes)
    renderers = [SceneRenderer(scene, spec, idx, len(scenes)) for idx, scene in enumerate(scenes)]

    # Saundtrek: voqealar vaqt chizig'i
    events, start = [], 0.0
    for idx, (scene, r) in enumerate(zip(scenes, renderers)):
        events.append((max(0.0, start - 0.12), "whoosh" if idx else "swoosh"))
        events += [(start + t, kind) for t, kind in r.events()]
        start += int(scene.duration * FPS) / FPS
    outro = _outro_seconds()
    if outro:
        events += [(max(0.0, start - 1.1), "riser"), (start + 0.05, "impact")]
    mood = audio.THEME_MOOD.get(spec.theme, "tech") if music in (None, "auto") else music
    soundtrack = audio.mix(start + outro, audio_wav, events, mood)
    audio_path = out.with_suffix(".wav")
    audio_path.write_bytes(soundtrack)
    audio_input = ["-i", str(audio_path)]

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
        for scene, renderer in zip(scenes, renderers):
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
        audio_path.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg xatosi (kod {code})")
    return out
