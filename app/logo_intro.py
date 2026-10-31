"""Logo intro motion video: terminal yozuvi -> ikonka chiqishi -> yozuv ochilishi -> nur o'tishi
-> teglar, diktor ovozi va so'zma-so'z subtitr -> CTA.

Ishlatish: python -m app.logo_intro husanbek-coder.png
"""

import asyncio
import math
import random
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import config, tts
from .video import (
    FPS, H, THEMES, W, Captions, ChipsBlock, _clamp, _ease_out, _ease_out_back, _paste,
    _text_alpha, font, narration_words, strip_markup,
)

ACCENT = (34, 211, 238)    # moviy-firuza (logodagi nur rangi)
ACCENT2 = (168, 85, 247)   # binafsha
BG = (5, 6, 15)

DEFAULT_SCRIPT = (
    "Assalomu alaykum! Bu *Husanbek koder*. Dasturlash, *sun'iy intellekt* va motion video "
    "bo'yicha eng foydali kontent shu yerda. Obuna bo'ling va birga *o'rganamiz!*"
)
# Subtitrda ko'rinadigan matn (diktor "Husanbek koder" deb o'qiydi, ekranda brend nomi)
DISPLAY_FIXES = {"*Husanbek koder*.": "*husanbek-coder.*"}


def _split_logo(path: Path, split_x: int = 600, feather: int = 70):
    """Logo -> (icon, text) RGBA sprites scaled to fit the frame width; soft seam between them."""
    logo = Image.open(path).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox())
    scale = (W - 120) / logo.width
    logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.LANCZOS)
    sx, fw = int(split_x * scale), max(2, int(feather * scale))
    ramp = Image.linear_gradient("L").rotate(90).resize((fw, logo.height))  # chapdan o'ngga 255 -> 0
    icon_mask = Image.new("L", logo.size, 0)
    icon_mask.paste(255, (0, 0, sx - fw // 2, logo.height))
    icon_mask.paste(ramp, (sx - fw // 2, 0))
    a = logo.getchannel("A")
    icon = logo.copy()
    icon.putalpha(ImageChops.multiply(a, icon_mask))
    text = logo.copy()
    text.putalpha(ImageChops.multiply(a, ImageChops.invert(icon_mask)))
    icon_box = icon.getchannel("A").getbbox()
    return logo, icon, text, icon_box, sx


def _glow(sprite: Image.Image, color: tuple, radius: int = 28) -> Image.Image:
    pad = radius * 3
    big = Image.new("RGBA", (sprite.width + pad * 2, sprite.height + pad * 2), (*color, 0))
    alpha = Image.new("L", big.size, 0)
    alpha.paste(sprite.getchannel("A"), (pad, pad))
    big.putalpha(alpha.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, v * 2)))
    return big


class LogoIntro:
    def __init__(self, logo_path: Path, words: list, handle: str, url: str, duration: float):
        self.logo, self.icon, self.text, self.icon_box, self.split = _split_logo(logo_path)
        self.icon_glow = _glow(self.icon, ACCENT, 34)
        self.logo_glow = _glow(self.logo, ACCENT2, 40)
        self.duration, self.handle, self.url = duration, handle, url
        self.theme = replace(THEMES["terminal"], accent=ACCENT, fg=(236, 242, 255), dim=(120, 130, 160))
        self.captions = Captions(words, self.theme, "bottom") if words else None
        self.chips = ChipsBlock(["💻 Dasturlash", "🤖 Sun'iy intellekt", "🎬 Motion video"], self.theme, 0)

        rnd = random.Random(7)
        self.particles = [
            (rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(30, 120), rnd.uniform(2, 6),
             ACCENT if rnd.random() < 0.6 else ACCENT2, rnd.uniform(0, 6.28))
            for _ in range(70)
        ]
        self.base = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(self.base, "RGBA")
        for x in range(0, W, 60):
            d.line((x, 0, x, H), fill=(*ACCENT, 7))
        for y in range(0, H, 60):
            d.line((0, y, W, y), fill=(*ACCENT, 7))
        self.blob_a = self._blob(ACCENT2, 900)
        self.blob_b = self._blob(ACCENT, 800)
        band = Image.linear_gradient("L").rotate(90).resize((90, 10))
        band = ImageChops.lighter(band, band.transpose(Image.FLIP_LEFT_RIGHT))  # 0 -> 255 -> 0
        self.band = band.resize((180, self.logo.height * 2)).rotate(18, expand=True)

    @staticmethod
    def _blob(color, size):
        mask = Image.new("L", (size, size), 0)
        r = size // 2 - 300
        ImageDraw.Draw(mask).ellipse((size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r), fill=120)
        img = Image.new("RGBA", (size, size), (*color, 0))
        img.putalpha(mask.filter(ImageFilter.GaussianBlur(110)))
        return img

    def _shine(self, sprite: Image.Image, p: float) -> Image.Image | None:
        """White light band sweeping across the sprite, masked by its alpha."""
        if not 0 < p < 1:
            return None
        mask = Image.new("L", sprite.size, 0)
        x = int(-self.band.width + p * (sprite.width + self.band.width * 2))
        mask.paste(self.band, (x, (sprite.height - self.band.height) // 2))
        mask = ImageChops.multiply(mask, sprite.getchannel("A"))
        out = Image.new("RGBA", sprite.size, (255, 255, 255, 0))
        out.putalpha(mask.point(lambda v: int(v * 0.85)))
        return out

    def render(self, t: float) -> Image.Image:
        frame = self.base.copy()
        d = ImageDraw.Draw(frame, "RGBA")
        fade_out = _ease_out((self.duration - t) / 0.5)

        # Fon: suzuvchi nurli dog'lar va zarrachalar
        _paste(frame, self.blob_a, W * 0.2 + math.sin(t * 0.4) * 200 - 450, H * 0.3 + math.cos(t * 0.3) * 250 - 450)
        _paste(frame, self.blob_b, W * 0.8 + math.cos(t * 0.35) * 200 - 400, H * 0.7 + math.sin(t * 0.25) * 250 - 400)
        for x, y0, speed, size, color, ph in self.particles:
            y = (y0 - t * speed) % H
            a = int(120 + 100 * math.sin(t * 2 + ph))
            d.ellipse((x - size, y - size, x + size, y + size), fill=(*color, max(0, a)))

        # 1) Terminal satri yoziladi (0 - 2.4 s)
        if t < 2.7:
            cmd = "> init husanbek-coder.uz"
            shown = cmd[: int(_clamp((t - 0.3) / 1.6) * len(cmd))]
            a = 1 - _clamp((t - 2.3) / 0.4)
            f = font("mono_bold", 54)
            x0 = (W - f.getlength(cmd)) / 2
            _text_alpha(frame, (x0, H / 2 - 40), shown, f, (198, 244, 50), a)
            if int(t * 3) % 2 == 0:
                cx = x0 + f.getlength(shown) + 6
                d.rectangle((cx, H / 2 - 34, cx + 26, H / 2 + 26), fill=(198, 244, 50, int(255 * a)))

        # 2) Chaqnash (2.4 - 3.2 s)
        burst = _clamp((t - 2.4) / 0.8)
        if 0 < burst < 1:
            r = 40 + burst * 900
            d.ellipse((W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r), outline=(*ACCENT, int(200 * (1 - burst))), width=10)
            d.rectangle((0, 0, W, H), fill=(255, 255, 255, int(120 * (1 - burst) ** 3)))

        if t < 2.5:
            return frame

        # Logo joylashuvi: avval markazda, keyin tepaga ko'tariladi (6.0 - 7.0 s)
        move = _ease_out((t - 6.0) / 1.0)
        scale = 1 - 0.12 * move
        lx = (W - self.logo.width * scale) / 2
        ly = H / 2 - self.logo.height * scale / 2 - move * 420 + math.sin(t * 1.6) * 8
        logo_alpha = fade_out

        def scaled(img):
            if abs(scale - 1) < 1e-3:
                return img
            return img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)

        # Orqa nur (logo to'liq chiqqach pulsatsiya)
        pulse = 0.55 + 0.25 * math.sin(t * 2.4)
        full = _ease_out((t - 4.2) / 0.8)
        if full > 0:
            g = scaled(self.logo_glow)
            _paste(frame, g, lx - (g.width - self.logo.width * scale) / 2,
                   ly - (g.height - self.logo.height * scale) / 2, full * pulse * logo_alpha)

        # 3) Ikonka: aylanib, kattalashib chiqadi (2.6 - 3.6 s)
        p = _ease_out_back((t - 2.6) / 0.9)
        if p > 0.02:
            icon = scaled(self.icon)
            ib = [int(v * scale) for v in self.icon_box]
            cx, cy = lx + (ib[0] + ib[2]) / 2, ly + (ib[1] + ib[3]) / 2
            crop = icon.crop(ib)
            k = max(0.05, p)
            spr = crop.resize((max(1, int(crop.width * k)), max(1, int(crop.height * k))), Image.BILINEAR)
            spr = spr.rotate((1 - _clamp((t - 2.6) / 0.9)) * -35, resample=Image.BICUBIC, expand=True)
            glow = scaled(self.icon_glow)
            _paste(frame, glow, cx - glow.width / 2 + (icon.width / 2 - (ib[0] + ib[2]) / 2) * 0,
                   cy - glow.height / 2, min(1, p) * (0.4 + 0.3 * math.sin(t * 3)) * logo_alpha)
            _paste(frame, spr, cx - spr.width / 2, cy - spr.height / 2, min(1, p) * logo_alpha)

        # 4) Yozuv chapdan o'ngga ochiladi (3.4 - 4.6 s)
        wipe = _ease_out((t - 3.4) / 1.2)
        if wipe > 0:
            text = scaled(self.text)
            start = int(self.split * scale * 0.9)
            cut = start + int((text.width - start) * wipe)
            part = text.crop((0, 0, cut, text.height))
            _paste(frame, part, lx + (1 - wipe) * 30, ly, logo_alpha)
            # yozuv uchidagi yorqin chiziq
            if wipe < 0.99:
                d.rectangle((lx + cut - 3, ly + text.height * 0.25, lx + cut + 3, ly + text.height * 0.75),
                            fill=(*ACCENT, 230))

        # 5) Nur o'tishi (4.6 - 5.6 s va har 4 soniyada takror)
        for start in (4.6, 9.6, 13.6):
            sh = self._shine(scaled(self.logo), (t - start) / 1.0)
            if sh is not None:
                _paste(frame, sh, lx, ly, logo_alpha)

        # 6) Teglar va sayt manzili (6.8 s dan)
        if t > 6.8:
            self.chips.t0 = 6.8
            self.chips.draw(frame, H / 2 - 40, t, fade_out)
            url_a = _ease_out((t - 7.6) / 0.6) * fade_out
            _text_alpha(frame, (W / 2, H / 2 + 200), self.url, font("mono", 40), (150, 160, 190), url_a, "mm")

        # 7) CTA (oxirgi 3.5 soniya)
        cta = _ease_out_back((t - (self.duration - 3.5)) / 0.6)
        if cta > 0.02:
            f = font("sans_black", 64)
            label = "Obuna bo'ling"
            tw = f.getlength(label) + 120
            bw, bh = tw * min(cta, 1.05), 120 * min(cta, 1.05)
            bx, by = (W - bw) / 2, H / 2 + 300
            d.rounded_rectangle((bx, by, bx + bw, by + bh), 60, fill=(*ACCENT, int(240 * fade_out)))
            if cta > 0.8:
                _text_alpha(frame, (W / 2, by + bh / 2), label, f, BG, fade_out, "mm")
            if self.handle:
                _text_alpha(frame, (W / 2, by + bh + 60), self.handle, font("sans_bold", 40),
                            (200, 210, 235), fade_out * _clamp((cta - 0.8) * 5), "mm")

        # Subtitr va progress
        if self.captions:
            self.captions.draw(frame, t, fade_out)
        d.rectangle((0, H - 10, W * t / self.duration, H), fill=(*ACCENT, 220))
        return frame


async def build(logo_path: Path, script: str = DEFAULT_SCRIPT, voice_id: str = "",
                handle: str = "@husanbek_coder", url: str = "husanbek-coder.uz") -> Path:
    start = 2.7  # ovoz chaqnashdan keyin boshlanadi
    clips, provider = await tts.synthesize_many([strip_markup(script)], voice_id or config.VOICELAB_VOICE_ID, "uz")
    clip = clips[0]
    seconds = tts.wav_duration(clip)
    duration = max(14.0, start + seconds + 4.0)
    display = script
    for a, b in DISPLAY_FIXES.items():
        display = display.replace(a, b)
    words = narration_words(display, start, seconds)
    audio = tts.build_track([(start, clip)], duration)
    print(f"Ovoz: {provider}, {seconds:.1f} s; video {duration:.1f} s")

    intro = LogoIntro(logo_path, words, handle, url, duration)
    out = config.MEDIA_DIR / f"logo_{uuid.uuid4().hex[:8]}.mp4"
    wav = out.with_suffix(".wav")
    wav.write_bytes(audio)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(wav), "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(int(duration * FPS)):
            proc.stdin.write(intro.render(i / FPS).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        wav.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    logo = Path(sys.argv[1] if len(sys.argv) > 1 else "husanbek-coder.png")
    print(asyncio.run(build(logo)))
