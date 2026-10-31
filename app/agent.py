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
from .video import (
    BG_STYLES,
    ELEMENT_TYPES,
    THEME_NAMES,
    Scene,
    VideoSpec,
    align_words,
    render_motion_video,
    strip_markup,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen foydalanuvchining Instagram akkauntini boshqaradigan ijodiy SMM yordamchi va
motion-dizaynersan. Foydalanuvchi bilan o'zbek tilida, qisqa va do'stona gaplash.
Javoblaring Telegram'da ko'rsatiladi.

## Ish tartibi (video so'ralganda)
Har bir savoldan keyin javobingni qisqa yakunla va tanlovni kut: u keyingi xabar bo'lib keladi.
1. Topshiriq umumiy bo'lsa ("kofe haqida video", "shunaqa video qil"): o'zing 3-4 ta ijodiy
   g'oya o'ylab top va ask_user bilan tanlat (har biri 1 qator, emoji bilan).
2. Orqa fon / uslubni ask_user bilan so'ra (agar foydalanuvchi aytmagan bo'lsa):
   "🖥 Terminal — qora, yashil kod uslubi", "🪟 Glass — och, qahramonli",
   "📑 Slaydlar — katta sarlavha, ro'yxatlar", "⌨️ Kinetik matn — qora, yoziluvchi matn",
   "🌈 Rangli gradient".
3. Diktor ovozi: foydalanuvchining O'Z klon ovozi bor (voice_id="men") - odatiy shu ovozni
   ishlat va ovoz haqida so'rama. Faqat foydalanuvchi boshqa ovoz xohlasa ask_voice bilan
   so'ra (Sardor / Madina uslublari ham bor) va send_voice_samples bilan namuna yubor.
   Foydalanuvchi ovozsiz desa - narration yo'q.
4. Keyin videoni yasa. "O'zing tanla" deyilsa, o'zing eng yaxshisini tanla.
Topshiriq to'liq aniq bo'lsa (uslub va ovoz aytilgan), savolsiz yasa.

## Video tuzilishi (create_motion_video) — namunalar uslubida
- theme: terminal | glass | slides | kinetic | classic | warm | lime | minimal | neon | space | ember |
  peach | retro | chatui | dialog | neonlab | monitor. Butun video bitta uslubda. Foydalanuvchi
  /shablon orqali shablon tanlasa, xabar boshida [SHABLON: ...] ko'rsatmasi keladi - unga aniq amal qil.
- handle: foydalanuvchi nikneymi (bilmasang so'ramasdan bo'sh qoldir), label: mavzu so'zi.
- 5-10 sahna, har sahna 1 fikr. HAR sahnada narration bo'lsin — dublaj butun video bo'ylab
  uzluksiz, jonli va professional eshitilsin:
  * jonli og'zaki o'zbek tili, qisqa jumlalar (8-16 so'z), savol va undovlar bilan ritm ber
    ("Bilasizmi?", "Mana shu yerda eng qizig'i!");
  * 1-sahna kuchli hook, oxirgi sahna CTA (obuna, izoh, saqlash);
  * emoji'siz; raqamlarni so'z bilan ("o'n ikki", "yigirma foiz"); inglizcha atamalarni
    o'qilishicha yoz ("Klod Kod", "ey-ay"), ekranda esa heading'da asl yozilishi tursin;
  * tinish belgilarini to'g'ri qo'y: vergul va nuqta diktorga nafas beradi.
  So'zma-so'z subtitr ovozga aniq moslanadi. Fon musiqasi va ovoz effektlari (shuvillash, "pop",
  klaviatura) avtomatik qo'shiladi; music: auto (uslubga qarab) yoki tech/bright/upbeat/
  emotional/calm/none.
- heading: qisqa sarlavha (3-7 so'z). *yulduzcha* ichidagi so'z urg'u rangida chiqadi:
  "Ular *buzishmaydi* — qidiradi". narration'da ham *so'z* bilan urg'u berish mumkin.
- elements (sahnaga 0-2 ta) — namunalardagidek vizual bloklar:
  * cards: {"type":"cards","items":[{"icon":"⚡","title":"Tez","text":"izoh"}]} (1-4 ta)
  * list: {"type":"list","items":["1-qadam","2-qadam"]} (raqamli ro'yxat, 2-6 ta)
  * code: {"type":"code","title":"app.js","lines":["$ npm run build","// izoh"]} (yoziladi)
  * number: {"type":"number","value":"13,889","suffix":"+","label":"izoh"} (sanab o'sadi)
  * chips: {"type":"chips","items":["#teg","OWASP"]}
  * diagram: {"type":"diagram","center":"Coordinator","nodes":["Frontend","Backend","Review"]}
  * phone: {"type":"phone","title":"Biznes Bot","messages":[{"from":"user","text":"Narxi?"},{"from":"bot","text":"..."}]}
  * steps: {"type":"steps","items":["Savol","Tanlov","Buyurtma"],"active":2}
  * profile: {"type":"profile","name":"@handle","bio":"...","button":"Obuna bo'lish"} (oxirgi CTA sahna)
- emoji: HAR sahnaga mos katta animatsiyali emoji qo'y (asosiy bezak shu). stickers maydoniga
  ham faqat emoji yoz (1-2 ta, masalan ["✨","🔥"]). AI stikerlar/rasmlarni faqat foydalanuvchi
  aniq so'rasa ishlat.
- character: qahramon image_id (glass uslubida o'ng pastda, boshqalarida markazda).
- variant: slides uchun "light" (och slayd), kinetic uchun yorug'lik rangi "#ff6a00".
- accent: sahna urg'u rangi (hex), ixtiyoriy. classic uslubida bg_style va bg_colors ber,
  sahnadan sahnaga o'zgartir.
Uslublar bo'yicha maslahat: terminal - texnologiya/xavfsizlik/kod; glass - ta'lim, qahramon
bilan; slides - qadamma-qadam qo'llanma, 01/10 ko'rinish; kinetic - his-tuyg'u, she'riy matn.

## Shaxsiy qahramon (faqat so'ralsa)
Foydalanuvchi qahramon / o'z obrazini so'rasa, avatar_sticker ishlat: u foydalanuvchining o'z
rasmlaridan 3D multfilm uslubidagi qahramon yasaydi (masalan: "thinking, hand on chin",
"pointing up, excited", "thumbs up, smiling", "surprised, hands on cheeks", "waving hello").
Bitta videoda 2-4 xil poza ishlat (har sahnada boshqa poza = jonli video). Pozalar keshlanadi,
qayta yaratilmaydi. Avatar ishlamasa, generate_image yoki oddiy emoji ishlat.

## Rasmlar (generate_image)
Promptni ingliz tilida aniq yoz. Har rasm pullik va ~20-60 soniya oladi: keragidan ortiq yaratma.

## Instagram
- request_publish: chiqarishdan oldin har doim tasdiq so'ra. Caption: hook, qisqa matn,
  CTA va 5-10 ta hashtag.
- Video yaratilgach u foydalanuvchiga avtomatik yuboriladi; qayta yuborma."""

SERVER_NAME = "ig"
TOOL_NAMES = [
    "ask_user", "ask_voice", "send_voice_samples", "avatar_sticker", "generate_image",
    "create_motion_video", "request_publish", "get_instagram_account",
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
    send_audio: Callable[[bytes, str], Awaitable[None]]
    send_publish_request: Callable[[str, Path, str], Awaitable[None]]
    # (savol, tugma matnlari, agentga qaytadigan qiymatlar yoki None, ustunlar soni)
    ask: Callable[[str, list[str], list[str] | None, int], Awaitable[None]]


def _text(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}]}


async def _narrate(scenes: list[Scene], voice_id: str, language: str) -> tuple[bytes, str]:
    """Synthesize each scene's narration, stretch durations to fit and time the caption words."""
    texts = [strip_markup(s.narration) for s in scenes]
    clips, times, provider = await tts.synthesize_many(texts, voice_id, language)
    placed, start = [], 0.0
    for scene, clip, bounds in zip(scenes, clips, times):
        if clip:
            seconds = tts.wav_duration(clip)
            scene.duration = max(scene.duration, seconds + 0.8)
            scene.words = align_words(scene.narration, 0.35, bounds, seconds)
            placed.append((start + 0.35, clip))
        # render kadr soniga yaxlitlaydi: ovoz bilan sinxron qolish uchun shu qiymatni ishlatamiz
        start += int(scene.duration * 30) / 30
    return tts.build_track(placed, start), provider


def _image_ref(value: str) -> str | Path:
    """Sticker value: generated image id -> Path, otherwise keep as emoji text."""
    return generated_images.get(value.strip(), value)


def _resolve_element(el: dict) -> dict:
    """Map image ids inside elements (profile avatar) to file paths."""
    el = dict(el)
    if el.get("avatar") in generated_images:
        el["avatar"] = str(generated_images[el["avatar"]])
    return el


def _voice_label(v: dict) -> str:
    icon = "👩" if v.get("gender") == "female" else "👨" if v.get("gender") == "male" else "🎙"
    name = v["name"].split("—")[0].strip()
    return f"{icon} {name}"


ELEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(ELEMENT_TYPES)},
        "items": {"type": "array", "description": "cards: [{icon,title,text}], list/chips: [string]"},
        "title": {"type": "string", "description": "code: window title"},
        "lines": {"type": "array", "items": {"type": "string"}, "description": "code: lines"},
        "value": {"type": "string", "description": "number: e.g. '13,889'"},
        "prefix": {"type": "string"},
        "suffix": {"type": "string"},
        "label": {"type": "string", "description": "number: caption under the number"},
        "center": {"type": "string", "description": "diagram: center node"},
        "nodes": {"type": "array", "items": {"type": "string"}, "description": "diagram: child nodes (2-5)"},
        "messages": {"type": "array", "description": "phone: [{from: 'user'|'bot', text}]"},
        "active": {"type": "integer", "description": "steps: active step number (1-based)"},
        "name": {"type": "string", "description": "profile: @handle"},
        "bio": {"type": "string", "description": "profile: short bio"},
        "button": {"type": "string", "description": "profile: button text"},
        "avatar": {"type": "string", "description": "profile: image_id for the avatar"},
    },
    "required": ["type"],
}

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "heading": {"type": "string", "description": "Short heading; wrap accent words in *stars*"},
        "subheading": {"type": "string", "description": "Optional smaller text"},
        "narration": {"type": "string", "description": "Voice-over text (no emoji); *stars* accent caption words"},
        "duration": {"type": "number", "description": "Minimum seconds (extended to fit narration)"},
        "elements": {"type": "array", "items": ELEMENT_SCHEMA, "description": "0-2 visual blocks"},
        "emoji": {"type": "string", "description": "One big animated emoji"},
        "character": {"type": "string", "description": "image_id of a character / avatar sticker"},
        "stickers": {"type": "array", "items": {"type": "string"}, "description": "Up to 3: emoji or image_ids"},
        "variant": {"type": "string", "description": "slides/ember/minimal: 'light'; lime: '', 'lime', 'night'; kinetic: glow hex"},
        "speaker": {"type": "string", "description": "dialog/neonlab: who is speaking (label above captions)"},
        "accent": {"type": "string", "description": "Accent hex color for this scene"},
        "bg_style": {"type": "string", "enum": list(BG_STYLES), "description": "classic theme only"},
        "bg_colors": {"type": "array", "items": {"type": "string"}, "description": "classic: two hex colors"},
        "bg_image": {"type": "string", "description": "image_id of a generated background"},
    },
}


def _build_server(io: ChatIO):
    @tool(
        "ask_user",
        "Send the user a question with tappable option buttons (a quick poll). The user's choice "
        "arrives as their next message, so end your turn after calling this. A 'let the assistant "
        "decide' button is added automatically; do not include one.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array", "items": {"type": "string"},
                    "description": "2-8 short options (max ~45 chars each), emoji welcome",
                },
            },
            "required": ["question", "options"],
        },
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        options = [o.strip()[:64] for o in args["options"] if o.strip()][:8]
        await io.ask(args["question"], options, None, 1)
        return _text("Savol yuborildi. Javobingni qisqa yakunla va foydalanuvchi tanlovini kut.")

    @tool(
        "ask_voice",
        "Show the user buttons with ALL available Uzbek narrator voices (VoiceLab + free voices) to pick "
        "one. The choice (with voice_id) arrives as the next message; end your turn after calling this.",
        {"type": "object", "properties": {"question": {"type": "string"}}},
    )
    async def ask_voice(args: dict[str, Any]) -> dict[str, Any]:
        voices = await tts.list_uzbek_voices()
        labels = [_voice_label(v) for v in voices]
        values = [f"Ovoz: {v['name']} — {v.get('description', '')} (voice_id={v['id']})" for v in voices]
        await io.ask(args.get("question") or "🎙 Diktor ovozini tanlang", labels, values, 2)
        return _text(f"{len(voices)} ta o'zbekcha ovoz tugmalari yuborildi. Tanlovni kut.")

    @tool(
        "send_voice_samples",
        "Send short audio samples of up to 4 voices so the user can hear them before choosing. "
        "Call with no ids to get the voice catalog (id, name, gender, description) without sending.",
        {
            "type": "object",
            "properties": {"voice_ids": {"type": "array", "items": {"type": "string"}}},
        },
    )
    async def send_voice_samples(args: dict[str, Any]) -> dict[str, Any]:
        ids = args.get("voice_ids") or []
        voices = await tts.list_uzbek_voices(fresh=bool(ids))
        if not ids:
            catalog = "\n".join(f"{v['id']} | {v['name']} | {v['gender'] or '-'} | {v['description']}" for v in voices)
            return _text(f"O'zbekcha ovozlar:\n{catalog}")
        by_id = {v["id"]: v for v in voices}
        sent = []
        for vid in ids[:4]:
            v = by_id.get(vid)
            if not v:
                continue
            try:
                await io.send_audio(await tts.voice_sample(v), _voice_label(v))
                sent.append(v["name"])
            except Exception as e:
                log.warning("Ovoz namunasi yuborilmadi (%s): %s", vid, e)
        return _text(f"Namunalar yuborildi: {', '.join(sent) or 'hech biri'}.")

    @tool(
        "avatar_sticker",
        "Create (or reuse from cache) a 3D cartoon sticker of the USER based on their own photos, in "
        "the given pose/emotion. Returns an image_id for scene.character or stickers.",
        {
            "type": "object",
            "properties": {"pose": {"type": "string", "description": "English pose + emotion, e.g. 'thumbs up, smiling'"}},
            "required": ["pose"],
        },
    )
    async def avatar_sticker(args: dict[str, Any]) -> dict[str, Any]:
        pose = args["pose"]
        cached = images.cached_avatar(pose)
        if not cached and not images.avatar_available():
            have = images.list_cached_avatars()
            return _text("Yangi avatar yaratib bo'lmaydi (OPENAI_API_KEY yoki AVATAR_DIR rasmlari yo'q). "
                         + (f"Keshdagi pozalar: {', '.join(have)}" if have else "Emoji ishlat."))
        try:
            path = cached or await images.avatar_sticker(pose)
        except (images.ImageError, httpx.HTTPError) as e:
            return _text(f"Avatar yaratilmadi: {e}")
        image_id = f"avatar_{path.stem}"
        generated_images[image_id] = path
        if not cached:
            await io.send_photo(path, f"🧑 Avatar: {pose}")
        return _text(f"Avatar tayyor{' (keshdan)' if cached else ''}. image_id={image_id}")

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
        "Render a vertical 1080x1920 motion-graphics video with voice-over and word-by-word captions "
        "(Instagram Reels format) and send it to the user. Returns a video_id.",
        {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "enum": list(THEME_NAMES)},
                "handle": {"type": "string", "description": "Account handle shown in the frame, e.g. @name"},
                "label": {"type": "string", "description": "Topic label (terminal header)"},
                "captions": {"type": "boolean", "description": "Word-by-word captions (default true)"},
                "scenes": {"type": "array", "items": SCENE_SCHEMA, "description": "Ordered scenes"},
                "voice_id": {"type": "string", "description": "Voice id for narration"},
                "language": {"type": "string", "description": "Narration language code (default uz)"},
                "music": {"type": "string", "enum": ["auto", "tech", "bright", "upbeat", "emotional", "calm", "none"],
                          "description": "Background music mood (auto = by theme)"},
            },
            "required": ["theme", "scenes"],
        },
    )
    async def create_motion_video(args: dict[str, Any]) -> dict[str, Any]:
        scenes = []
        for s in args["scenes"]:
            colors = [c for c in s.get("bg_colors", []) if c][:2]
            style = s.get("bg_style", "gradient")
            scenes.append(Scene(
                heading=s.get("heading") or s.get("title", ""),
                subheading=s.get("subheading", ""),
                narration=s.get("narration", "").strip(),
                duration=max(1.5, min(10.0, float(s.get("duration", 3)))),
                elements=[_resolve_element(e) for e in s.get("elements", []) if isinstance(e, dict)][:2],
                speaker=s.get("speaker", ""),
                emoji=s.get("emoji", ""),
                character=generated_images.get(s.get("character", "")),
                stickers=[_image_ref(x) for x in s.get("stickers", [])][:3],
                variant=s.get("variant", ""),
                accent=s.get("accent", ""),
                bg_style=style if style in BG_STYLES else "gradient",
                bg_colors=tuple(colors) if len(colors) == 2 else ("#1e1b4b", "#7c3aed"),
                bg_image=generated_images.get(s.get("bg_image", "")),
            ))
        theme = args.get("theme", "terminal")
        spec = VideoSpec(
            theme=theme if theme in THEME_NAMES else "terminal",
            handle=args.get("handle", ""),
            label=args.get("label", ""),
            captions=args.get("captions", True),
        )
        audio, note = None, ""
        if any(s.narration for s in scenes):
            try:
                audio, provider = await _narrate(scenes, args.get("voice_id", ""), args.get("language", "uz"))
                note = f" Ovoz: {provider}."
            except (tts.TTSError, httpx.HTTPError, OSError) as e:
                log.exception("TTS xatosi")
                note = f" Ovoz qo'shilmadi ({e})."
        path = await asyncio.to_thread(render_motion_video, scenes, spec, audio, args.get("music", "auto"))
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
        tools=[ask_user, ask_voice, send_voice_samples, avatar_sticker, generate_image,
               create_motion_video, request_publish, get_instagram_account],
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
            max_turns=30,
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
