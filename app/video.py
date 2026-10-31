"""Motion video generator: Pillow frames -> ffmpeg -> 1080x1920 MP4 (Reels format)."""

import logging
import math
import re
import subprocess
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from . import config

log = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 30

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


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
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


def _gradient(top: tuple, bottom: tuple) -> Image.Image:
    mask = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), mask)


def _with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1:
        return img
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * alpha))))


def _draw_text_block(
    canvas: Image.Image,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    center_y: int,
    alpha: float,
    offset_y: float,
    fill: tuple,
) -> None:
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
                draw.text((x + 4, y + 6), chunk, font=font, fill=(0, 0, 0, 90))
                draw.text((x, y), chunk, font=font, fill=(*fill, 255))
                x += font.getlength(chunk)
        y += line_h
    canvas.alpha_composite(_with_alpha(layer, alpha))


def _draw_hero_emoji(canvas: Image.Image, emoji: str, t: float, fade: float, g: float) -> None:
    img = _emoji_image(emoji)
    if img is None or fade <= 0:
        return
    scale = _ease_out_back(t / 0.7)
    size = int(320 * max(0.01, scale))
    if size < 4:
        return
    icon = img.resize((size, size), Image.LANCZOS).rotate(
        math.sin(g * 2.2) * 6, resample=Image.BICUBIC, expand=True
    )
    cy = H // 2 - 460 + math.sin(g * 2.6) * 18
    canvas.alpha_composite(
        _with_alpha(icon, fade), (int((W - icon.width) / 2), int(cy - icon.height / 2))
    )


def render_motion_video(
    scenes: list[Scene],
    color_top: str = "#1e1b4b",
    color_bottom: str = "#7c3aed",
    accent: str = "#facc15",
    text_color: str = "#ffffff",
    audio_wav: bytes | None = None,
) -> Path:
    """Render scenes into an MP4 (optionally with a narration WAV track) and return its path."""
    out = config.MEDIA_DIR / f"{uuid.uuid4().hex[:12]}.mp4"
    title_font, sub_font = _font(92), _font(52)
    top, bottom, acc, txt = _hex(color_top), _hex(color_bottom), _hex(accent), _hex(text_color)
    base = _gradient(top, bottom).convert("RGBA")
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
            title_lines = _wrap(scene.title, title_font, W - 160)
            sub_lines = _wrap(scene.subtitle, sub_font, W - 200) if scene.subtitle else []
            hero = EMOJI_RE.search(scene.emoji)
            for i in range(n):
                t = i / FPS
                fade_out = _ease_out((scene.duration - t) / 0.4)
                frame = base.copy()
                g = frame_no / FPS

                # Harakatlanuvchi fon doiralari
                deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(deco)
                for k, (r, a) in enumerate([(380, 40), (260, 55), (180, 35)]):
                    cx = W / 2 + math.sin(g * 0.6 + k * 2.1) * 360
                    cy = H / 2 + math.cos(g * 0.45 + k * 1.7) * 640
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*acc, a))
                frame.alpha_composite(deco)

                # Katta emoji: "pop" bo'lib chiqadi va tebranib turadi
                if hero:
                    _draw_hero_emoji(frame, hero.group(), t, fade_out, g)

                # Sarlavha: pastdan ko'tarilib chiqadi
                p = _ease_out((t - 0.15) / 0.6)
                _draw_text_block(frame, title_lines, title_font, H // 2 - 60,
                                 p * fade_out, (1 - p) * 120, txt)
                # Urg'u chizig'i
                bw = int(320 * _ease_out((t - 0.45) / 0.5))
                if bw > 0:
                    bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    bar_y = H // 2 - 60 + (len(title_lines) * int(title_font.size * 1.25)) // 2 + 30
                    ImageDraw.Draw(bar).rounded_rectangle(
                        ((W - bw) / 2, bar_y, (W + bw) / 2, bar_y + 12),
                        radius=6, fill=(*acc, int(255 * fade_out)))
                    frame.alpha_composite(bar)
                # Kichik matn: biroz kechikib chiqadi
                if sub_lines:
                    q = _ease_out((t - 0.55) / 0.6)
                    _draw_text_block(frame, sub_lines, sub_font, H // 2 + 260,
                                     q * fade_out, (1 - q) * 80, txt)
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
