"""OpenAI rasm generatsiyasi: qahramonlar va stikerlar (shaffof PNG), fon rasmlari (9:16),
hamda foydalanuvchining o'z rasmlaridan 3D avatar-stikerlar."""

import base64
import hashlib
import io
import re
import uuid
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

from . import config

IMAGES_DIR = config.MEDIA_DIR / "_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

API = "https://api.openai.com/v1/images"

STYLE_HINTS = {
    "character": "full-body character, centered, clean outlines, vibrant colors, "
                 "3D cartoon style, isolated subject with no scenery",
    "sticker": "single sticker-style icon, bold shapes, thick outline, vibrant colors, "
               "isolated object with no scenery",
    "background": "vertical 9:16 background scene for a social media video, "
                  "no text, no letters, no people in the foreground, soft depth of field, "
                  "leave calm empty space in the middle for text",
}


class ImageError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(config.OPENAI_API_KEY)


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}


GREEN_HINT = ("Place the subject on a perfectly flat, solid pure green (#00FF00) chroma-key background, "
              "no shadows on the background, no green on the subject.")


def _error(resp: httpx.Response) -> str | None:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    if resp.status_code >= 400 or "error" in data:
        return data.get("error", {}).get("message", resp.text[:300])
    return None


def _transparency_unsupported(resp: httpx.Response) -> bool:
    msg = (_error(resp) or "").lower()
    return resp.status_code == 400 and ("transparent" in msg or "background" in msg)


def chroma_key(png: bytes) -> bytes:
    """Remove a pure green background -> transparent PNG (with green spill suppression)."""
    img = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float32)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    green = g - np.maximum(r, b)
    alpha = 1 - np.clip((green - 25) / 70, 0, 1)
    g_fixed = np.where(green > 0, np.maximum(r, b) + green * 0.15 * alpha, g)  # yashil hoshiyani yo'qotish
    rgba = np.dstack([r, np.minimum(g, g_fixed), b, alpha * 255]).astype(np.uint8)
    out = Image.fromarray(rgba, "RGBA")
    bbox = out.getchannel("A").getbbox()
    out = out.crop(bbox) if bbox else out
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def _save(resp: httpx.Response, prefix: str, folder: Path = IMAGES_DIR, name: str = "", key_green: bool = False) -> Path:
    err = _error(resp)
    if err:
        raise ImageError(err)
    path = folder / (name or f"{prefix}_{uuid.uuid4().hex[:10]}.png")
    png = base64.b64decode(resp.json()["data"][0]["b64_json"])
    path.write_bytes(chroma_key(png) if key_green else png)
    return path


async def generate(prompt: str, kind: str) -> Path:
    """Generate an image and return its PNG path. kind: character | sticker | background."""
    if not is_configured():
        raise ImageError("OPENAI_API_KEY sozlanmagan")
    kind = kind if kind in STYLE_HINTS else "character"
    body = {
        "model": config.OPENAI_IMAGE_MODEL,
        "prompt": f"{prompt}. {STYLE_HINTS[kind]}",
        "size": "1024x1536" if kind == "background" else "1024x1024",
        "quality": config.OPENAI_IMAGE_QUALITY,
        "output_format": "png",
        "background": "opaque" if kind == "background" else "transparent",
        "n": 1,
    }
    async with httpx.AsyncClient(timeout=240) as client:
        resp = await client.post(f"{API}/generations", json=body, headers=_headers())
        key = False
        if kind != "background" and _transparency_unsupported(resp):
            # Shaffof fon qo'llanmasa: yashil fonda chizdirib, o'zimiz kesamiz
            body.update(background="opaque", prompt=f"{body['prompt']}. {GREEN_HINT}")
            resp = await client.post(f"{API}/generations", json=body, headers=_headers())
            key = True
    return _save(resp, kind[:2], key_green=key)


# ---------- Shaxsiy avatar (foydalanuvchi rasmlaridan) ----------

def avatar_available() -> bool:
    return is_configured() and bool(_reference_photos())


def _reference_photos() -> list[Path]:
    folder = config.AVATAR_DIR
    if not folder.is_dir():
        return []
    seen, photos = set(), []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        digest = hashlib.md5(p.read_bytes()).hexdigest()
        if digest not in seen:  # bir xil nusxalarni tashlab ketamiz
            seen.add(digest)
            photos.append(p)
    return photos[:6]


def _upload_bytes(path: Path) -> bytes:
    img = Image.open(path).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "default"


def cached_avatar(pose: str) -> Path | None:
    path = config.AVATAR_CACHE_DIR / f"{_slug(pose)}.png"
    return path if path.exists() else None


def list_cached_avatars() -> list[str]:
    return sorted(p.stem for p in config.AVATAR_CACHE_DIR.glob("*.png"))


async def avatar_sticker(pose: str) -> Path:
    """Stylized 3D sticker of the user (from AVATAR_DIR photos) in the given pose. Cached by pose."""
    cached = cached_avatar(pose)
    if cached:
        return cached
    photos = _reference_photos()
    if not is_configured() or not photos:
        raise ImageError("OPENAI_API_KEY yoki avatar rasmlari (AVATAR_DIR) yo'q")
    prompt = (
        "Create a 3D animated-movie style cartoon character of the SAME person shown in the reference "
        "photos. Keep the identity recognizable: same face shape, eyeglasses, hairstyle, skin tone. "
        f"{config.AVATAR_STYLE}. Pose and expression: {pose}. Half-body to full-body, centered, "
        "soft studio lighting, high detail, friendly look, isolated character with no scenery."
    )
    data = {
        "model": config.OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "size": "1024x1536",
        "quality": config.OPENAI_IMAGE_QUALITY,
        "background": "transparent",
        "output_format": "png",
        "input_fidelity": "high",
        "n": "1",
    }
    files = [("image[]", (p.stem + ".png", _upload_bytes(p), "image/png")) for p in photos]
    key = False
    async with httpx.AsyncClient(timeout=300) as client:
        for _ in range(3):
            resp = await client.post(f"{API}/edits", data=data, files=files, headers=_headers())
            msg = (_error(resp) or "").lower()
            if resp.status_code != 400:
                break
            if "input_fidelity" in msg and "input_fidelity" in data:
                data.pop("input_fidelity")
            elif ("transparent" in msg or "background" in msg) and not key:
                # edits shaffof fonni qo'llamaydi: yashil fonda chizdirib, keyin kesamiz
                data["background"] = "opaque"
                data["prompt"] = f"{data['prompt']} {GREEN_HINT}"
                key = True
            else:
                break
    return _save(resp, "av", config.AVATAR_CACHE_DIR, f"{_slug(pose)}.png", key_green=key)
