"""Claude agent: Tool Runner loop with video + Instagram tools."""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, TypedDict

from anthropic import AsyncAnthropic, beta_async_tool

from . import config, instagram
from .video import Scene, render_motion_video

log = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """Sen foydalanuvchining Instagram akkauntini boshqaradigan SMM yordamchisan.
Foydalanuvchi bilan o'zbek tilida, qisqa va aniq gaplash.

Imkoniyatlaring:
- create_motion_video: vertikal (9:16) motion video yaratish. Har bir sahna qisqa, kuchli sarlavha
  (5-7 so'z) va ixtiyoriy kichik matndan iborat. Reels uchun 3-6 sahna, jami 10-25 soniya yaxshi.
  Mavzuga mos rang palitrasini tanla (hex ranglar).
- request_publish: tayyor videoni Instagram'ga chiqarish uchun foydalanuvchidan tasdiq so'rash.
  Caption'ni jozibali yoz: hook, qisqa matn, chaqiriq (CTA) va 5-10 ta mos hashtag.
- get_instagram_account: akkaunt statistikasini ko'rish.

Qoidalar:
- Hech qachon foydalanuvchi tasdiqisiz post chiqarma: faqat request_publish orqali tasdiq so'ra.
- Video yaratilgach u foydalanuvchiga avtomatik yuboriladi; uni qayta yuborishga urinma.
- Topshiriq noaniq bo'lsa ham, avval o'zing yaxshi variant taklif qil, keyin o'zgartirishni so'ra."""

MAX_HISTORY = 60


@dataclass
class PendingPost:
    video_path: Path
    caption: str


@dataclass
class ChatSession:
    messages: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[int, ChatSession] = defaultdict(ChatSession)
videos: dict[str, Path] = {}
pending_posts: dict[str, PendingPost] = {}


class SceneInput(TypedDict):
    title: str
    subtitle: str
    duration: float


@dataclass
class ChatIO:
    """Callbacks the agent uses to talk back to the Telegram chat."""

    send_video: Callable[[Path, str], Awaitable[None]]
    send_publish_request: Callable[[str, Path, str], Awaitable[None]]


def _build_tools(io: ChatIO) -> list:
    @beta_async_tool
    async def create_motion_video(
        scenes: list[SceneInput],
        color_top: str = "#1e1b4b",
        color_bottom: str = "#7c3aed",
        accent: str = "#facc15",
    ) -> str:
        """Create a vertical 1080x1920 motion-graphics video (Instagram Reels format) and send a preview to the user.

        Args:
            scenes: Ordered scenes. Each has a title (short, punchy), subtitle (may be empty) and duration in seconds (2-6).
            color_top: Background gradient top color, hex like #1e1b4b.
            color_bottom: Background gradient bottom color, hex.
            accent: Accent color for shapes and underline, hex.
        """
        parsed = [
            Scene(s["title"], s.get("subtitle", ""), max(1.5, min(8.0, float(s.get("duration", 3)))))
            for s in scenes
        ]
        path = await asyncio.to_thread(
            render_motion_video, parsed, color_top, color_bottom, accent
        )
        video_id = path.stem
        videos[video_id] = path
        await io.send_video(path, f"🎬 Video tayyor (id: {video_id})")
        return f"Video yaratildi va foydalanuvchiga yuborildi. video_id={video_id}"

    @beta_async_tool
    async def request_publish(video_id: str, caption: str) -> str:
        """Ask the user to approve publishing a created video to Instagram as a Reel. Publishing happens only after they press Approve.

        Args:
            video_id: The id returned by create_motion_video.
            caption: Full Instagram caption including hashtags.
        """
        path = videos.get(video_id)
        if not path or not path.exists():
            return f"Xato: video_id={video_id} topilmadi. Avval create_motion_video chaqiring."
        if not instagram.is_configured():
            return "Instagram hali ulanmagan (IG_USER_ID/IG_ACCESS_TOKEN yo'q). Foydalanuvchiga ayting."
        post_id = uuid.uuid4().hex[:10]
        pending_posts[post_id] = PendingPost(path, caption)
        await io.send_publish_request(post_id, path, caption)
        return "Tasdiq so'rovi yuborildi. Foydalanuvchi tugmani bosishini kutyapmiz."

    @beta_async_tool
    async def get_instagram_account() -> str:
        """Get the connected Instagram account's username, follower count and media count."""
        if not instagram.is_configured():
            return "Instagram hali ulanmagan."
        try:
            return str(await instagram.account_info())
        except instagram.InstagramError as e:
            return f"Instagram xatosi: {e}"

    return [create_motion_video, request_publish, get_instagram_account]


def reset(chat_id: int) -> None:
    sessions.pop(chat_id, None)


async def run(chat_id: int, user_text: str, io: ChatIO) -> str:
    session = sessions[chat_id]
    async with session.lock:
        if len(session.messages) > MAX_HISTORY:
            session.messages.clear()
        session.messages.append({"role": "user", "content": user_text})

        runner = client.beta.messages.tool_runner(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=_build_tools(io),
            messages=list(session.messages),
            output_config={"effort": config.CLAUDE_EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            max_iterations=15,
        )
        last = None
        async for message in runner:
            last = message
            # Tarixni append-only saqlaymiz (runner o'z nusxasini ko'rsatmaydi)
            session.messages.append({"role": "assistant", "content": message.content})
            tool_response = await runner.generate_tool_call_response()
            if tool_response is not None:
                session.messages.append(tool_response)

        if last is None:
            return "Javob olinmadi."
        if last.stop_reason == "refusal":
            return "Kechirasiz, bu so'rovni bajara olmayman."
        text = "\n".join(b.text for b in last.content if b.type == "text").strip()
        return text or "✅ Bajarildi."
