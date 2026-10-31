"""Motion video generator: Pillow frames -> ffmpeg -> 1080x1920 MP4 (Reels format)."""

import logging
import math
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from . import config

log = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 30

BG_STYLES = ("gradient", "diagonal", "radial", "waves", "grid", "bokeh", "image")

FONT_CANDIDATES = [
    config.FONT_PATH,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

# Emoji ketma-ketligi: bayroqlar, ZWJ birikmalari, teri rangi, FE0F
_EMOJI_CHAR = r"[\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\U0001F000-\U0001FAFF]"
_EMOJI_TAIL = r"(?:\uFE0F)?(?:[\U0001F3FB-\U0001F3FF])?"
EMOJI_RE = re.compile(
    rf"[\U0001F1E6-\U0001F1FF]{{2}}|{_EMOJI_CHAR}{_EMOJI_TAIL}(?:\u200D{_EMOJI_CHAR}{_EMOJI_TAIL})*"
)
EMOJI_DIR = config.MEDIA_DIR / "_emoji"
EMOJI_DIR.mkdir(parents=True, exist_ok=True)

STICKER_SLOTS = [(180, 300), (W - 180, 470), (170, H - 470), (W - 170, H - 330)]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _hex(color: str) -> tuple[int, int, int]:
    color = color.strip().lstrip("#")
    if len(color) != 6:
        return (124, 58, 237)
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


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


def _asset(ref: "str | Path") -> Image.Image | None:
    """Emoji string or image file path -> RGBA image."""
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


def _measure(text: str, font: ImageFont.FreeTypeFont) -> float:
    return sum(font.size * 1.15 if emo else font.getlength(chunk) for emo, chunk in _runs(text))


@dataclass
class Scene:
    title: str
    subtitle: str = ""
    duration: float = 3.0
    emoji: str = ""
    narration: str = ""
    bg_style: str = "gradient"
    bg_colors: tuple[str, str] = ("#1e1b4b", "#7c3aed")
    accent: str = "#facc15"
    bg_image: Path | None = None
    character: Path | None = None
    stickers: list[str | Path] = field(default_factory=list)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if _measure(candidate, font) <= max_width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _ease_out_back(t: float) -> float:
    t = max(0.0, min(1.0, t))
    c = 1.9
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1:
        return img
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * alpha))))


# ---------- Fonlar ----------

def _two_color(mask: Image.Image, c1: tuple, c2: tuple) -> Image.Image:
    return Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mask).convert("RGBA")


def _static_background(scene: Scene) -> Image.Image:
    c1, c2 = _hex(scene.bg_colors[0]), _hex(scene.bg_colors[1])
    if scene.bg_style == "radial":
        big = Image.radial_gradient("L").resize((int(H * 1.5), int(H * 1.5)))
        left, top = (big.width - W) // 2, (big.height - H) // 2
        return _two_color(big.crop((left, top, left + W, top + H)), c1, c2)
    if scene.bg_style == "diagonal":
        big = Image.linear_gradient("L").rotate(35, expand=True, fillcolor=128).resize((int(H * 1.6), int(H * 1.6)))
        left, top = (big.width - W) // 2, (big.height - H) // 2
        return _two_color(big.crop((left, top, left + W, top + H)), c1, c2)
    return _two_color(Image.linear_gradient("L").resize((W, H)), c1, c2)


class _BackgroundRenderer:
    def __init__(self, scene: Scene):
        self.scene = scene
        self.acc = _hex(scene.accent)
        self.photo = None
        if scene.bg_style == "image" and scene.bg_image and scene.bg_image.exists():
            img = Image.open(scene.bg_image).convert("RGB")
            scale = max(W * 1.12 / img.width, H * 1.12 / img.height)
            self.photo = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
            # Matn o'qilishi uchun yumshoq qorong'i qatlam
            shade = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(60 + 0.35 * abs(v - 128)))
            self.shade = Image.merge("RGBA", (*[Image.new("L", (W, H), 0)] * 3, shade))
        self.base = _static_background(scene)

    def frame(self, t: float, g: float) -> Image.Image:
        style = self.scene.bg_style
        if self.photo is not None:
            # Ken Burns: sekin zoom va siljish
            z = 1 + 0.08 * (t / max(self.scene.duration, 0.1))
            cw, ch = int(self.photo.width / (1.12 * z) * 1.0), int(self.photo.height / (1.12 * z))
            ox = int((self.photo.width - cw) / 2 + math.sin(g * 0.3) * 20)
            oy = int((self.photo.height - ch) / 2)
            frame = self.photo.crop((ox, oy, ox + cw, oy + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
            frame.alpha_composite(self.shade)
            return frame

        frame = self.base.copy()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if style == "waves":
            for k in range(3):
                amp, base_y, speed = 70 + k * 25, H * (0.62 + k * 0.12), 0.8 + k * 0.35
                pts = [(x, base_y + math.sin(x / 170 + g * speed + k) * amp) for x in range(0, W + 40, 40)]
                d.polygon(pts + [(W, H), (0, H)], fill=(*self.acc, 45 + k * 15))
        elif style == "grid":
            step, off = 90, (g * 25) % 90
            for gx in range(-1, W // step + 2):
                for gy in range(-1, H // step + 2):
                    x, y = gx * step + off, gy * step + off
                    r = 5 + 3 * math.sin(g * 2 + gx * 0.7 + gy * 0.4)
                    d.ellipse((x - r, y - r, x + r, y + r), fill=(*self.acc, 70))
        elif style in ("bokeh", "gradient", "diagonal", "radial"):
            circles = [(380, 40), (260, 55), (180, 35)] if style == "bokeh" else [(300, 28), (200, 22)]
            for k, (r, a) in enumerate(circles):
                cx = W / 2 + math.sin(g * 0.6 + k * 2.1) * 360
                cy = H / 2 + math.cos(g * 0.45 + k * 1.7) * 640
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*self.acc, a))
        frame.alpha_composite(layer)
        return frame


# ---------- Elementlar ----------

def _text_colors(scene: Scene) -> tuple[tuple, tuple]:
    """Light background -> dark text, otherwise white text. Returns (fill, stroke)."""
    if scene.bg_style != "image":
        c1, c2 = _hex(scene.bg_colors[0]), _hex(scene.bg_colors[1])
        lum = (0.2126 * (c1[0] + c2[0]) + 0.7152 * (c1[1] + c2[1]) + 0.0722 * (c1[2] + c2[2])) / 510
        if lum > 0.62:
            return (17, 24, 39), (255, 255, 255, 120)
    return (255, 255, 255), (0, 0, 0, 110)


def _draw_text_block(canvas, lines, font, center_y, alpha, offset_y, fill, stroke=(0, 0, 0, 110)) -> None:
    if alpha <= 0:
        return
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    line_h = int(font.size * 1.25)
    emoji_px = int(font.size * 1.05)
    y = center_y - (line_h * len(lines)) // 2 + int(offset_y)
    for line in lines:
        x = (W - _measure(line, font)) / 2
        for is_emoji, chunk in _runs(line):
            if is_emoji:
                img = _emoji_image(chunk)
                if img is not None:
                    icon = img.resize((emoji_px, emoji_px), Image.LANCZOS)
                    layer.alpha_composite(icon, (int(x), int(y - font.size * 0.05)))
                x += font.size * 1.15
            else:
                draw.text((x, y), chunk, font=font, fill=(*fill, 255),
                          stroke_width=3, stroke_fill=stroke)
                x += font.getlength(chunk)
        y += line_h
    canvas.alpha_composite(_with_alpha(layer, alpha))


def _draw_hero(canvas, img: Image.Image, max_px: int, cy: float, t, fade, g, slide: bool) -> None:
    if fade <= 0:
        return
    scale = _ease_out_back(t / 0.7)
    ratio = max_px / max(img.width, img.height)
    w, h = int(img.width * ratio * max(scale, 0.01)), int(img.height * ratio * max(scale, 0.01))
    if w < 4 or h < 4:
        return
    icon = img.resize((w, h), Image.LANCZOS)
    if not slide:
        icon = icon.rotate(math.sin(g * 2.2) * 6, resample=Image.BICUBIC, expand=True)
    y = cy + math.sin(g * 2.6) * 18 + (1 - _ease_out(t / 0.6)) * (300 if slide else 0)
    canvas.alpha_composite(_with_alpha(icon, fade), (int((W - icon.width) / 2), int(y - icon.height / 2)))


def _draw_stickers(canvas, stickers: list[Image.Image], t, fade, g) -> None:
    for k, (img, (sx, sy)) in enumerate(zip(stickers, STICKER_SLOTS)):
        p = _ease_out_back((t - 0.5 - k * 0.2) / 0.5)
        if p <= 0.02:
            continue
        size = int(170 * p)
        icon = img.resize((size, int(size * img.height / img.width)), Image.LANCZOS)
        icon = icon.rotate(math.sin(g * 1.8 + k) * 12, resample=Image.BICUBIC, expand=True)
        y = sy + math.sin(g * 2 + k * 1.3) * 22
        canvas.alpha_composite(
            _with_alpha(icon, fade * 0.95), (int(sx - icon.width / 2), int(y - icon.height / 2))
        )


def render_motion_video(scenes: list[Scene], audio_wav: bytes | None = None) -> Path:
    """Render scenes into an MP4 (optionally with a narration WAV track) and return its path."""
    out = config.MEDIA_DIR / f"{uuid.uuid4().hex[:12]}.mp4"
    title_font, sub_font = _font(92), _font(52)
    total_frames = sum(int(s.duration * FPS) for s in scenes)

    audio_path = None
    if audio_wav:
        audio_path = out.with_suffix(".wav")
        audio_path.write_bytes(audio_wav)
        audio_input = ["-i", str(audio_path)]
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
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frame_no = 0
    try:
        for scene in scenes:
            n = int(scene.duration * FPS)
            acc = _hex(scene.accent)
            bg = _BackgroundRenderer(scene)
            title_lines = _wrap(scene.title, title_font, W - 160)
            sub_lines = _wrap(scene.subtitle, sub_font, W - 200) if scene.subtitle else []
            character = _asset(scene.character) if scene.character else None
            hero = character or (_asset(scene.emoji) if scene.emoji else None)
            stickers = [img for img in (_asset(s) for s in scene.stickers[:4]) if img is not None]
            title_y = H // 2 - 20 if character else H // 2 - 60
            txt, stroke = _text_colors(scene)
            for i in range(n):
                t = i / FPS
                g = frame_no / FPS
                fade_out = _ease_out((scene.duration - t) / 0.4)
                frame = bg.frame(t, g)

                if stickers:
                    _draw_stickers(frame, stickers, t, fade_out, g)
                if hero is not None:
                    if character:
                        _draw_hero(frame, hero, 600, H // 2 - 470, t, fade_out, g, slide=True)
                    else:
                        _draw_hero(frame, hero, 320, H // 2 - 460, t, fade_out, g, slide=False)

                # Sarlavha: pastdan ko'tarilib chiqadi
                p = _ease_out((t - 0.15) / 0.6)
                _draw_text_block(frame, title_lines, title_font, title_y, p * fade_out, (1 - p) * 120, txt, stroke)
                # Urg'u chizig'i
                bw = int(320 * _ease_out((t - 0.45) / 0.5))
                bar_y = title_y + (len(title_lines) * int(title_font.size * 1.25)) // 2 + 30
                if bw > 0:
                    bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    ImageDraw.Draw(bar).rounded_rectangle(
                        ((W - bw) / 2, bar_y, (W + bw) / 2, bar_y + 12),
                        radius=6, fill=(*acc, int(255 * fade_out)))
                    frame.alpha_composite(bar)
                # Kichik matn: biroz kechikib chiqadi
                if sub_lines:
                    q = _ease_out((t - 0.55) / 0.6)
                    sub_y = bar_y + 60 + (len(sub_lines) * int(sub_font.size * 1.25)) // 2
                    _draw_text_block(frame, sub_lines, sub_font, sub_y, q * fade_out, (1 - q) * 80, txt, stroke)
                # Progress bar
                prog = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(prog).rectangle(
                    (0, H - 14, int(W * (frame_no + 1) / total_frames), H), fill=(*acc, 220))
                frame.alpha_composite(prog)

                proc.stdin.write(frame.convert("RGB").tobytes())
                frame_no += 1
    finally:
        proc.stdin.close()
        code = proc.wait()
        if audio_path:
            audio_path.unlink(missing_ok=True)
    if code != 0:
        raise RuntimeError(f"ffmpeg xatosi (kod {code})")
    return out
