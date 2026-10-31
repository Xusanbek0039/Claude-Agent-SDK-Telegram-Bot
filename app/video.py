"""Motion video generator: Pillow frames -> ffmpeg -> 1080x1920 MP4 (Reels format)."""

import math
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

W, H = 1080, 1920
FPS = 30

FONT_CANDIDATES = [
    config.FONT_PATH,
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


@dataclass
class Scene:
    title: str
    subtitle: str = ""
    duration: float = 3.0


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if font.getlength(candidate) <= max_width:
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


def _gradient(top: tuple, bottom: tuple) -> Image.Image:
    mask = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), mask)


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
    line_h = int(font.size * 1.2)
    y = center_y - (line_h * len(lines)) // 2 + int(offset_y)
    for line in lines:
        x = (W - font.getlength(line)) / 2
        draw.text((x + 4, y + 6), line, font=font, fill=(0, 0, 0, int(90 * alpha)))
        draw.text((x, y), line, font=font, fill=(*fill, int(255 * alpha)))
        y += line_h
    canvas.alpha_composite(layer)


def render_motion_video(
    scenes: list[Scene],
    color_top: str = "#1e1b4b",
    color_bottom: str = "#7c3aed",
    accent: str = "#facc15",
    text_color: str = "#ffffff",
) -> Path:
    """Render scenes into an MP4 and return its path."""
    out = config.MEDIA_DIR / f"{uuid.uuid4().hex[:12]}.mp4"
    title_font, sub_font = _font(96), _font(52)
    top, bottom, acc, txt = _hex(color_top), _hex(color_bottom), _hex(accent), _hex(text_color)
    base = _gradient(top, bottom).convert("RGBA")
    total_frames = sum(int(s.duration * FPS) for s in scenes)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        # Instagram uchun jim AAC audio trek
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frame_no = 0
    try:
        for scene in scenes:
            n = int(scene.duration * FPS)
            title_lines = _wrap(scene.title, title_font, W - 160)
            sub_lines = _wrap(scene.subtitle, sub_font, W - 200) if scene.subtitle else []
            for i in range(n):
                t = i / FPS
                fade_out = _ease_out((scene.duration - t) / 0.4)
                frame = base.copy()

                # Harakatlanuvchi fon doiralari
                deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(deco)
                g = frame_no / FPS
                for k, (r, a) in enumerate([(380, 40), (260, 55), (180, 35)]):
                    cx = W / 2 + math.sin(g * 0.6 + k * 2.1) * 360
                    cy = H / 2 + math.cos(g * 0.45 + k * 1.7) * 640
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*acc, a))
                frame.alpha_composite(deco)

                # Sarlavha: pastdan ko'tarilib chiqadi
                p = _ease_out(t / 0.6)
                _draw_text_block(frame, title_lines, title_font, H // 2 - 80,
                                 p * fade_out, (1 - p) * 120, txt)
                # Urg'u chizig'i
                bar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                bw = int(320 * _ease_out((t - 0.3) / 0.5))
                if bw > 0:
                    ImageDraw.Draw(bar).rounded_rectangle(
                        ((W - bw) / 2, H // 2 + 60, (W + bw) / 2, H // 2 + 72),
                        radius=6, fill=(*acc, int(255 * fade_out)))
                    frame.alpha_composite(bar)
                # Kichik matn: biroz kechikib chiqadi
                if sub_lines:
                    q = _ease_out((t - 0.4) / 0.6)
                    _draw_text_block(frame, sub_lines, sub_font, H // 2 + 200,
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
    if code != 0:
        raise RuntimeError(f"ffmpeg xatosi (kod {code})")
    return out
