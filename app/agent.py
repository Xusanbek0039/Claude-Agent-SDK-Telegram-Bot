"""Claude agent (Claude Agent SDK) with video + Instagram tools as an in-process MCP server.

Autentifikatsiya: CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`, claude.ai obunasi)
yoki ANTHROPIC_API_KEY. SDK ichidagi Claude Code CLI uni muhitdan o'qiydi.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from . import config, images, instagram, tts
from .video import BG_STYLES, Scene, render_motion_video

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen foydalanuvchining Instagram akkauntini boshqaradigan ijodiy SMM yordamchi va
motion-dizaynersan. Foydalanuvchi bilan o'zbek tilida, qisqa va do'stona gaplash.
Javoblaring Telegram'da ko'rsatiladi.

## Ish tartibi
1. Foydalanuvchi umumiy topshiriq bersa ("kofe haqida video qil", "shunaqa video tayyorla"),
   o'zing ijodiy g'oyalar o'ylab top va ask_user orqali tugmali so'rovnoma qil:
   - avval 3-4 ta qisqa video g'oyasi (har biri 1 qator, emoji bilan);
   - keyin fon uslubi (masalan: "🌈 Gradient", "🌊 To'lqinlar", "✨ Bokeh", "🔲 Nuqtali setka",
     "🖼 AI rasm fon"), va kerak bo'lsa qahramon/stiker kerakmi yoki diktor ovozi
     (erkak/ayol).
   Ko'pi bilan 2-3 ta savol ber; foydalanuvchi "o'zing tanla" desa, o'zing hal qil.
   ask_user'dan keyin javobingni qisqa yakunla va foydalanuvchi tanlovini kut: tanlov keyingi
   xabar bo'lib keladi.
2. Topshiriq aniq bo'lsa, savolsiz to'g'ridan-to'g'ri video yasa.

## Video yasash (create_motion_video)
- 3-6 sahna, har sahnada boshqacha fon: uslub (bg_style) va ranglarni sahnadan sahnaga
  o'zgartir, video bir xil ko'rinmasin. Uslublar: gradient, diagonal, radial, waves, grid,
  bokeh, image (AI fon rasmi - avval generate_image kind=background).
- emoji: har sahnaga mos bitta katta animatsiyali emoji (☕ 🔥 🚀 💡 ...).
- stickers: 1-3 ta suzib yuruvchi emoji yoki generate_image(kind=sticker) natijasi.
- character: generate_image(kind=character) bilan yaratilgan qahramon (emoji o'rniga katta bo'lib
  chiqadi). Bitta qahramonni bir necha sahnada ishlatish brend uchun yaxshi.
- title: qisqa, kuchli sarlavha (5-7 so'z), ichida 1 ta emoji bo'lishi mumkin.
- narration: diktor o'qiydigan matn (1-2 jumla, emoji'siz, raqamlarni so'z bilan yoz).
  Ovozsiz video so'ralsa, bo'sh qoldir.
- Ovozlar (voice_id): voice_fa56cecf-9b0b-45ba-9ec3-494efb5b301f Javohir (erkak, default),
  voice_8bae02c6-d078-4757-9d62-0fce6da23cf7 Amir (erkak, hikoyachi),
  voice_5e592ada-eca8-42e3-98fa-469c1c8fc999 Botir (erkak, energik),
  voice_4ef06e6b-2229-4023-ad66-c2a7a2b736ff Madina (ayol, yumshoq),
  voice_7b350d91-4e56-4bc3-869c-8cd6ba646c3c Gulya (ayol, quvnoq).

## Rasmlar (generate_image)
Faqat OpenAI ulangan bo'lsa ishlaydi. Promptni ingliz tilida, aniq yoz (personaj tashqi
ko'rinishi, kiyimi, uslubi). Har bir rasm pullik va ~20-60 soniya oladi: keragidan ortiq yaratma
(odatda 1 qahramon + 0-2 fon).

## Instagram
- request_publish: chiqarishdan oldin har doim tasdiq so'ra. Caption: hook, qisqa matn,
  CTA va 5-10 ta hashtag.
- Video yaratilgach u foydalanuvchiga avtomatik yuboriladi; qayta yuborma."""

SERVER_NAME = "ig"
TOOL_NAMES = [
    "ask_user", "generate_image", "create_motion_video", "request_publish", "get_instagram_account",
]


@dataclass
class PendingPost:
    video_path: Path
    caption: str


@dataclass
class ChatSession:
    session_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[int, ChatSession] = defaultdict(ChatSession)
videos: dict[str, Path] = {}
generated_images: dict[str, Path] = {}
pending_posts: dict[str, PendingPost] = {}


@dataclass
class ChatIO:
    """Callbacks the agent uses to talk back to the Telegram chat."""

    send_video: Callable[[Path, str], Awaitable[None]]
    send_photo: Callable[[Path, str], Awaitable[None]]
    send_publish_request: Callable[[str, Path, str], Awaitable[None]]
    ask: Callable[[str, list[str]], Awaitable[None]]


def _text(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}]}


async def _narrate(scenes: list[Scene], voice_id: str, language: str) -> tuple[bytes, str]:
    """Synthesize each scene's narration and stretch scene durations to fit."""
    clips, provider = await tts.synthesize_many([s.narration for s in scenes], voice_id, language)
    placed, start = [], 0.0
    for scene, clip in zip(scenes, clips):
        if clip:
            scene.duration = max(scene.duration, tts.wav_duration(clip) + 0.8)
            placed.append((start + 0.3, clip))
        start += scene.duration
    return tts.build_track(placed, start), provider


def _image_ref(value: str) -> str | Path:
    """Sticker value: generated image id -> Path, otherwise keep as emoji text."""
    return generated_images.get(value.strip(), value)


SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short punchy title, may contain one emoji"},
        "subtitle": {"type": "string", "description": "Optional smaller text"},
        "duration": {"type": "number", "description": "Minimum seconds, 2-6 (extended to fit narration)"},
        "emoji": {"type": "string", "description": "One big animated emoji (ignored if character is set)"},
        "character": {"type": "string", "description": "image_id of a generated character to show big"},
        "stickers": {
            "type": "array", "items": {"type": "string"},
            "description": "Up to 3 floating stickers: emoji characters or generated sticker image_ids",
        },
        "narration": {"type": "string", "description": "Voice-over text for this scene (no emoji)"},
        "bg_style": {"type": "string", "enum": list(BG_STYLES)},
        "bg_colors": {
            "type": "array", "items": {"type": "string"},
            "description": "Two hex colors for the background, e.g. ['#0f172a', '#2563eb']",
        },
        "bg_image": {"type": "string", "description": "image_id of a generated background (bg_style=image)"},
        "accent": {"type": "string", "description": "Accent hex color for shapes, underline, progress bar"},
    },
    "required": ["title"],
}


def _build_server(io: ChatIO):
    @tool(
        "ask_user",
        "Send the user a question with tappable option buttons (a quick poll). "
        "The user's choice arrives as their next message, so end your turn after calling this. "
        "A 'let the assistant decide' button is added automatically; do not include one.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array", "items": {"type": "string"},
                    "description": "2-6 short options (max ~40 chars each), emoji welcome",
                },
            },
            "required": ["question", "options"],
        },
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        options = [o.strip()[:60] for o in args["options"] if o.strip()][:6]
        await io.ask(args["question"], options)
        return _text("Savol yuborildi. Javobingni qisqa yakunla va foydalanuvchi tanlovini kut.")

    @tool(
        "generate_image",
        "Generate an AI image with OpenAI. kind=character or sticker gives a transparent PNG; "
        "kind=background gives a 9:16 scene. Returns an image_id to use in create_motion_video.",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Detailed English description"},
                "kind": {"type": "string", "enum": ["character", "sticker", "background"]},
            },
            "required": ["prompt", "kind"],
        },
    )
    async def generate_image(args: dict[str, Any]) -> dict[str, Any]:
        if not images.is_configured():
            return _text("OpenAI ulanmagan (OPENAI_API_KEY yo'q): rasm yaratib bo'lmaydi. "
                         "Emoji va stikerlardan foydalan.")
        try:
            path = await images.generate(args["prompt"], args["kind"])
        except (images.ImageError, httpx.HTTPError) as e:
            return _text(f"Rasm yaratilmadi: {e}")
        image_id = path.stem
        generated_images[image_id] = path
        await io.send_photo(path, f"🖼 {args['kind']} tayyor (id: {image_id})")
        return _text(f"Rasm yaratildi va foydalanuvchiga ko'rsatildi. image_id={image_id}")

    @tool(
        "create_motion_video",
        "Render a vertical 1080x1920 motion-graphics video with voice-over (Instagram Reels format) "
        "and send it to the user. Returns a video_id.",
        {
            "type": "object",
            "properties": {
                "scenes": {"type": "array", "items": SCENE_SCHEMA, "description": "Ordered scenes"},
                "voice_id": {"type": "string", "description": "Voice id for narration"},
                "language": {"type": "string", "description": "Narration language code: uz, ru, en"},
            },
            "required": ["scenes"],
        },
    )
    async def create_motion_video(args: dict[str, Any]) -> dict[str, Any]:
        scenes = []
        for s in args["scenes"]:
            colors = [c for c in s.get("bg_colors", []) if c][:2]
            style = s.get("bg_style", "gradient")
            scenes.append(Scene(
                title=s["title"],
                subtitle=s.get("subtitle", ""),
                duration=max(1.5, min(8.0, float(s.get("duration", 3)))),
                emoji=s.get("emoji", ""),
                narration=s.get("narration", "").strip(),
                bg_style=style if style in BG_STYLES else "gradient",
                bg_colors=tuple(colors) if len(colors) == 2 else ("#1e1b4b", "#7c3aed"),
                accent=s.get("accent", "#facc15"),
                bg_image=generated_images.get(s.get("bg_image", "")),
                character=generated_images.get(s.get("character", "")),
                stickers=[_image_ref(x) for x in s.get("stickers", [])][:3],
            ))
        audio, note = None, ""
        if any(s.narration for s in scenes):
            try:
                audio, provider = await _narrate(scenes, args.get("voice_id", ""), args.get("language", "uz"))
                note = f" Ovoz: {provider}."
            except (tts.TTSError, httpx.HTTPError, OSError) as e:
                log.exception("TTS xatosi")
                note = f" Ovoz qo'shilmadi ({e})."
        path = await asyncio.to_thread(render_motion_video, scenes, audio)
        video_id = path.stem
        videos[video_id] = path
        await io.send_video(path, f"🎬 Video tayyor (id: {video_id})")
        return _text(f"Video yaratildi va foydalanuvchiga yuborildi. video_id={video_id}.{note}")

    @tool(
        "request_publish",
        "Ask the user to approve publishing a created video to Instagram as a Reel. "
        "Publishing happens only after they press Approve.",
        {"video_id": str, "caption": str},
    )
    async def request_publish(args: dict[str, Any]) -> dict[str, Any]:
        path = videos.get(args["video_id"])
        if not path or not path.exists():
            return _text("Xato: video topilmadi. Avval create_motion_video chaqiring.")
        if not instagram.is_configured():
            return _text("Instagram hali ulanmagan (IG_USER_ID/IG_ACCESS_TOKEN yo'q). Foydalanuvchiga ayting.")
        post_id = uuid.uuid4().hex[:10]
        pending_posts[post_id] = PendingPost(path, args["caption"])
        await io.send_publish_request(post_id, path, args["caption"])
        return _text("Tasdiq so'rovi yuborildi. Foydalanuvchi tugmani bosishini kutyapmiz.")

    @tool(
        "get_instagram_account",
        "Get the connected Instagram account's username, follower count and media count.",
        {},
    )
    async def get_instagram_account(args: dict[str, Any]) -> dict[str, Any]:
        if not instagram.is_configured():
            return _text("Instagram hali ulanmagan.")
        try:
            return _text(str(await instagram.account_info()))
        except instagram.InstagramError as e:
            return _text(f"Instagram xatosi: {e}")

    return create_sdk_mcp_server(
        SERVER_NAME,
        tools=[ask_user, generate_image, create_motion_video, request_publish, get_instagram_account],
    )


def reset(chat_id: int) -> None:
    sessions.pop(chat_id, None)


async def run(chat_id: int, user_text: str, io: ChatIO) -> str:
    session = sessions[chat_id]
    async with session.lock:
        options = ClaudeAgentOptions(
            model=config.CLAUDE_MODEL,
            effort=config.CLAUDE_EFFORT,
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={SERVER_NAME: _build_server(io)},
            # Claude Code'ning o'z toollari (Bash, Read, Write...) o'chirilgan:
            # agent faqat bizning toollarimizdan foydalanadi
            tools=[],
            allowed_tools=[f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES],
            permission_mode="dontAsk",
            setting_sources=[],
            resume=session.session_id,
            max_turns=25,
        )
        result: ResultMessage | None = None
        async for message in query(prompt=user_text, options=options):
            if isinstance(message, ResultMessage):
                result = message

        if result is None:
            return "Javob olinmadi."
        session.session_id = result.session_id
        if result.is_error:
            log.error("Agent xatosi: %s %s", result.subtype, result.errors)
            return f"⚠️ Xatolik: {result.result or result.subtype}"
        return (result.result or "").strip() or "✅ Bajarildi."
