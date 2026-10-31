"""OpenAI rasm generatsiyasi: qahramonlar va stikerlar (shaffof PNG), fon rasmlari (9:16)."""

import base64
import uuid
from pathlib import Path

import httpx

from . import config

IMAGES_DIR = config.MEDIA_DIR / "_images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

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
        resp = await client.post(
            "https://api.openai.com/v1/images/generations",
            json=body,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        )
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        raise ImageError(data.get("error", {}).get("message", resp.text[:300]))
    path = IMAGES_DIR / f"{kind[:2]}_{uuid.uuid4().hex[:10]}.png"
    path.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
    return path
