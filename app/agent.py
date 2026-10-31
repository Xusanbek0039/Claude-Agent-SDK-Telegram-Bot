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

from . import config, instagram, tts
from .video import Scene, render_motion_video

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen foydalanuvchining Instagram akkauntini boshqaradigan SMM yordamchisan.
Foydalanuvchi bilan o'zbek tilida, qisqa va aniq gaplash. Javoblaring Telegram'da ko'rsatiladi.

Imkoniyatlaring:
- create_motion_video: vertikal (9:16) motion video yaratish, ovozli. Reels uchun 3-6 sahna.
  Har bir sahnada:
  * emoji: sahna mavzusiga mos bitta katta animatsiyali emoji (masalan ☕, 🔥, 🚀, 💡) - deyarli
    har sahnaga qo'y, video jonli chiqadi;
  * title: qisqa, kuchli sarlavha (5-7 so'z), ichida 1 ta emoji bo'lishi mumkin;
  * subtitle: ixtiyoriy kichik matn;
  * narration: diktor o'qiydigan matn (1-2 jumla, sahna matnini to'ldiradi, emoji'siz,
    raqamlarni so'z bilan yoz: "8:00" emas "soat sakkizda"). Sahna davomiyligi ovozga moslanadi.
  Mavzuga mos rang palitrasini tanla (hex ranglar). Diktor ovozini mavzuga qarab tanla:
  * voice_fa56cecf-9b0b-45ba-9ec3-494efb5b301f - Javohir, yorqin taqdimotchi (erkak, default)
  * voice_8bae02c6-d078-4757-9d62-0fce6da23cf7 - Amir, iliq hikoyachi (erkak)
  * voice_5e592ada-eca8-42e3-98fa-469c1c8fc999 - Botir, energik streamer (erkak)
  * voice_4ef06e6b-2229-4023-ad66-c2a7a2b736ff - Madina, yumshoq (ayol)
  * voice_3602ec76-5393-43b6-9e0d-784328cb77ba - Nozima, yumshoq (ayol)
  * voice_7b350d91-4e56-4bc3-869c-8cd6ba646c3c - Gulya, quvnoq streamer (ayol)
  Foydalanuvchi ovozsiz video so'rasa, narration'larni bo'sh qoldir.
- request_publish: tayyor videoni Instagram'ga chiqarish uchun foydalanuvchidan tasdiq so'rash.
  Caption'ni jozibali yoz: hook, qisqa matn, chaqiriq (CTA) va 5-10 ta mos hashtag.
- get_instagram_account: akkaunt statistikasini ko'rish.

Qoidalar:
- Hech qachon foydalanuvchi tasdiqisiz post chiqarma: faqat request_publish orqali tasdiq so'ra.
- Video yaratilgach u foydalanuvchiga avtomatik yuboriladi; uni qayta yuborishga urinma.
- Topshiriq noaniq bo'lsa ham, avval o'zing yaxshi variant taklif qil, keyin o'zgartirishni so'ra."""

SERVER_NAME = "ig"
TOOL_NAMES = ["create_motion_video", "request_publish", "get_instagram_account"]


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
pending_posts: dict[str, PendingPost] = {}


@dataclass
class ChatIO:
    """Callbacks the agent uses to talk back to the Telegram chat."""

    send_video: Callable[[Path, str], Awaitable[None]]
    send_publish_request: Callable[[str, Path, str], Awaitable[None]]


def _text(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}]}


async def _narrate(scenes: list[Scene], voice_id: str, language: str) -> bytes:
    """Synthesize each scene's narration and stretch scene durations to fit."""
    clips = await asyncio.gather(
        *(tts.synthesize(s.narration, voice_id, language) if s.narration else asyncio.sleep(0, None)
          for s in scenes)
    )
    placed, start = [], 0.0
    for scene, clip in zip(scenes, clips):
        if clip:
            scene.duration = max(scene.duration, tts.wav_duration(clip) + 0.8)
            placed.append((start + 0.3, clip))
        start += scene.duration
    return tts.build_track(placed, start)


def _build_server(io: ChatIO):
    @tool(
        "create_motion_video",
        "Create a vertical 1080x1920 motion-graphics video (Instagram Reels format) "
        "and send a preview to the user. Returns a video_id.",
        {
            "type": "object",
            "properties": {
                "scenes": {
                    "type": "array",
                    "description": "Ordered scenes",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Short punchy title"},
                            "subtitle": {"type": "string", "description": "Optional smaller text"},
                            "duration": {"type": "number", "description": "Minimum seconds, 2-6 (extended to fit narration)"},
                            "emoji": {"type": "string", "description": "One big animated emoji for the scene"},
                            "narration": {"type": "string", "description": "Voice-over text for this scene (no emoji)"},
                        },
                        "required": ["title"],
                    },
                },
                "color_top": {"type": "string", "description": "Gradient top hex, e.g. #1e1b4b"},
                "color_bottom": {"type": "string", "description": "Gradient bottom hex"},
                "accent": {"type": "string", "description": "Accent hex for shapes/underline"},
                "voice_id": {"type": "string", "description": "VoiceLab voice id for narration"},
                "language": {"type": "string", "description": "Narration language code: uz, ru, en"},
            },
            "required": ["scenes"],
        },
    )
    async def create_motion_video(args: dict[str, Any]) -> dict[str, Any]:
        scenes = [
            Scene(
                title=s["title"],
                subtitle=s.get("subtitle", ""),
                duration=max(1.5, min(8.0, float(s.get("duration", 3)))),
                emoji=s.get("emoji", ""),
                narration=s.get("narration", "").strip(),
            )
            for s in args["scenes"]
        ]
        audio = None
        note = ""
        if tts.is_configured() and any(s.narration for s in scenes):
            try:
                audio = await _narrate(scenes, args.get("voice_id", ""), args.get("language", "uz"))
            except (tts.TTSError, httpx.HTTPError) as e:
                log.exception("TTS xatosi")
                note = f" Ovoz qo'shilmadi (VoiceLab xatosi: {e})."
        path = await asyncio.to_thread(
            render_motion_video,
            scenes,
            args.get("color_top", "#1e1b4b"),
            args.get("color_bottom", "#7c3aed"),
            args.get("accent", "#facc15"),
            audio_wav=audio,
        )
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
        SERVER_NAME, tools=[create_motion_video, request_publish, get_instagram_account]
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
            # agent faqat bizning 3 ta toolimizdan foydalanadi
            tools=[],
            allowed_tools=[f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES],
            permission_mode="dontAsk",
            setting_sources=[],
            resume=session.session_id,
            max_turns=15,
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
