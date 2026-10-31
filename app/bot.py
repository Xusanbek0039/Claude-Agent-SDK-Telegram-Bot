"""Telegram bot entrypoint.

Render'da: aiohttp server (Telegram webhook + /media/* fayllar Instagram uchun).
Lokal (PUBLIC_URL yo'q): polling rejimi.
"""

import asyncio
import contextlib
import html
import logging
import uuid
from pathlib import Path

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import agent, config, instagram, templates

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")


def _allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and (not config.ALLOWED_USER_IDS or user.id in config.ALLOWED_USER_IDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text(f"⛔ Ruxsat yo'q. Sizning ID: {update.effective_user.id}")
        return
    await update.message.reply_text(
        "Salom! Men Instagram yordamchingizman 🤖\n\n"
        "Topshiriq yozing, masalan:\n"
        "• «Kofe do'konim uchun 15 soniyali motion video qil»\n"
        "• «Akkaunt statistikasi qanday?»\n\n"
        "/shablon — tayyor video shablonlari (18 ta)\n/new — yangi suhbat\n/id — Telegram ID'ingiz"
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Sizning ID: {update.effective_user.id}")


async def new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    agent.reset(update.effective_chat.id)
    await update.message.reply_text("🆕 Yangi suhbat boshlandi.")


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    while True:
        with contextlib.suppress(Exception):
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(4)


# Agent bergan savollar: qid -> (savol, tugma matnlari, agentga yuboriladigan qiymatlar)
questions: dict[str, tuple[str, list[str], list[str]]] = {}


async def _run_agent(chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    bot = context.bot

    async def send_video(path: Path, caption: str) -> None:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
        with path.open("rb") as f:
            await bot.send_video(chat_id, f, caption=caption, supports_streaming=True,
                                 read_timeout=120, write_timeout=120)

    async def send_photo(path: Path, caption: str) -> None:
        with path.open("rb") as f:
            await bot.send_photo(chat_id, f, caption=caption, read_timeout=60, write_timeout=60)

    async def send_publish_request(post_id: str, path: Path, caption: str) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Chiqarish", callback_data=f"pub:{post_id}"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data=f"cancel:{post_id}"),
        ]])
        await bot.send_message(
            chat_id, f"📤 Instagram'ga chiqaraymi?\n\n{caption}", reply_markup=keyboard
        )

    async def send_audio(data: bytes, title: str) -> None:
        await bot.send_audio(chat_id, data, title=title, filename="namuna.mp3",
                             read_timeout=60, write_timeout=60)

    async def ask(question: str, options: list[str], values: list[str] | None, cols: int) -> None:
        qid = uuid.uuid4().hex[:8]
        questions[qid] = (question, options, values or options)
        buttons = [InlineKeyboardButton(o, callback_data=f"ask:{qid}:{i}") for i, o in enumerate(options)]
        rows = [buttons[i : i + cols] for i in range(0, len(buttons), cols)]
        rows.append([InlineKeyboardButton("🎲 O'zing tanla", callback_data=f"ask:{qid}:auto")])
        await bot.send_message(
            chat_id, f"❓ {question}\n\n(Tugmani bosing yoki o'z variantingizni yozing)",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    typing = asyncio.create_task(_keep_typing(context, chat_id))
    try:
        reply = await agent.run(
            chat_id, text, agent.ChatIO(send_video, send_photo, send_audio, send_publish_request, ask)
        )
    except Exception as e:
        log.exception("Agent xatosi")
        reply = f"⚠️ Xatolik: {e}"
    finally:
        typing.cancel()
    for i in range(0, len(reply), 4000):
        await bot.send_message(chat_id, reply[i : i + 4000])


# Shablon tanlagan, lekin hali mavzu yozmagan chatlar: chat_id -> shablon id
pending_templates: dict[int, str] = {}
PREVIEWS = Path(__file__).parent / "previews"


def _templates_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(t.name, callback_data=f"tpl:{t.id}") for t in templates.TEMPLATES]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


async def shablon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text(f"⛔ Ruxsat yo'q. Sizning ID: {update.effective_user.id}")
        return
    await update.message.reply_text(
        "🎬 Video shablonini tanlang.\nHar biri namunalardagi videolar uslubida tayyorlanadi:",
        reply_markup=_templates_keyboard(),
    )


async def handle_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _allowed(update):
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    await query.answer()
    chat_id = update.effective_chat.id
    tid = query.data.split(":", 1)[1]
    if tid == "list":
        await context.bot.send_message(chat_id, "🎬 Shablonni tanlang:", reply_markup=_templates_keyboard())
        return
    if tid == "cancel":
        pending_templates.pop(chat_id, None)
        await query.edit_message_reply_markup(None)
        await context.bot.send_message(chat_id, "❌ Shablon bekor qilindi.")
        return
    t = templates.BY_ID.get(tid)
    if t is None:
        return
    pending_templates[chat_id] = t.id
    about = f"{t.name}  ·  {t.sample}-namuna uslubi\n\n🎞 Video qanday chiqadi:\n{t.description}"
    preview = PREVIEWS / f"{t.id}.jpg"
    if preview.exists() and len(about) <= 1024:
        with preview.open("rb") as f:
            await context.bot.send_photo(chat_id, f, caption=about)
    else:
        await context.bot.send_message(chat_id, about)

    # Tayyor promptlar: matn ustiga bosilsa nusxa olinadi, tugma bosilsa darhol video yasaladi
    prompts = templates.examples(t)
    numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    lines = [f"{numbers[i]} <code>{html.escape(p)}</code>" for i, p in enumerate(prompts)]
    text = ("📝 <b>Tayyor promptlar</b> (ustiga bosing — nusxa olinadi, o'zgartirib yuborishingiz mumkin):\n\n"
            + "\n\n".join(lines)
            + "\n\n▶️ Tugmani bossangiz, prompt darhol ishga tushadi.\n"
              "✍️ Yoki o'z mavzuingizni yozing.")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"▶️ {numbers[i]}", callback_data=f"tpr:{t.id}:{i}") for i in range(len(prompts))],
        [InlineKeyboardButton("↩️ Boshqa shablon", callback_data="tpl:list"),
         InlineKeyboardButton("❌ Bekor qilish", callback_data="tpl:cancel")],
    ])
    await context.bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")


async def handle_template_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """▶️ tugmasi: tanlangan tayyor prompt bilan darhol video yasash."""
    query = update.callback_query
    if not _allowed(update):
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    _, tid, idx = query.data.split(":", 2)
    t = templates.BY_ID.get(tid)
    prompts = templates.examples(t) if t else ()
    if not t or not idx.isdigit() or int(idx) >= len(prompts):
        await query.answer("Bu prompt eskirgan", show_alert=True)
        return
    await query.answer("Video tayyorlanmoqda...")
    chat_id = update.effective_chat.id
    pending_templates.pop(chat_id, None)
    await query.edit_message_reply_markup(None)
    prompt = prompts[int(idx)]
    await context.bot.send_message(chat_id, f"⏳ «{t.name}» shablonida video tayyorlanmoqda (1-3 daqiqa):\n\n{prompt}")
    await _run_agent(chat_id, templates.agent_prompt(t, prompt), context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text(f"⛔ Ruxsat yo'q. Sizning ID: {update.effective_user.id}")
        return
    chat_id = update.effective_chat.id
    tid = pending_templates.pop(chat_id, None)
    if tid and tid in templates.BY_ID:
        t = templates.BY_ID[tid]
        await update.message.reply_text(f"⏳ «{t.name}» shablonida video tayyorlanmoqda (1-3 daqiqa)...")
        await _run_agent(chat_id, templates.agent_prompt(t, update.message.text), context)
        return
    await _run_agent(chat_id, update.message.text, context)


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _allowed(update):
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    _, qid, choice = query.data.split(":", 2)
    await query.answer()
    entry = questions.pop(qid, None)
    if entry is None:
        await query.edit_message_reply_markup(None)
        return
    question, options, values = entry
    if choice == "auto":
        label = answer = "O'zing eng yaxshisini tanla"
    else:
        label, answer = options[int(choice)], values[int(choice)]
    await query.edit_message_text(f"❓ {question}\n\n✅ {label}")
    await _run_agent(update.effective_chat.id, f"[{question}] javobim: {answer}", context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _allowed(update):
        await query.answer("Ruxsat yo'q", show_alert=True)
        return
    action, post_id = query.data.split(":", 1)
    post = agent.pending_posts.pop(post_id, None)
    await query.answer()
    if post is None:
        await query.edit_message_text("Bu so'rov eskirgan yoki allaqachon bajarilgan.")
        return
    if action == "cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return
    if not config.PUBLIC_URL:
        await query.edit_message_text("⚠️ PUBLIC_URL yo'q: Instagram videoni yuklab ololmaydi.")
        return

    await query.edit_message_text("⏳ Instagram'ga yuklanmoqda (1-3 daqiqa)...")
    video_url = f"{config.PUBLIC_URL}/media/{post.video_path.name}"
    try:
        media_id = await instagram.publish_reel(video_url, post.caption)
        await query.edit_message_text(f"✅ Chiqarildi! Media ID: {media_id}")
    except Exception as e:
        log.exception("Instagram publish xatosi")
        agent.pending_posts[post_id] = post  # qayta urinish mumkin
        await query.edit_message_text(f"⚠️ Instagram xatosi: {e}")


async def _set_commands(app: Application) -> None:
    from telegram import BotCommand

    try:
        await app.bot.set_my_commands([
            BotCommand("shablon", "Tayyor video shablonlari"),
            BotCommand("new", "Yangi suhbat"),
            BotCommand("start", "Yo'riqnoma"),
            BotCommand("id", "Telegram ID"),
        ])
    except Exception as e:  # tarmoq yo'q bo'lsa ham bot ishlayversin
        log.warning("Buyruqlar menyusi o'rnatilmadi: %s", e)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, NetworkError):  # TimedOut ham shu turga kiradi
        # Internet bir lahza uzildi: PTB o'zi qayta ulanadi, uzun traceback shart emas
        log.warning("Telegram bilan aloqa uzildi, qayta ulanmoqda: %s", err)
        return
    log.error("Kutilmagan xato", exc_info=err)


def build_application(webhook: bool) -> Application:
    builder = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .connect_timeout(20)
        .read_timeout(30)
        .write_timeout(120)  # katta videolarni yuborish uchun
        .pool_timeout(20)
    )
    if webhook:
        builder = builder.updater(None)
    else:
        builder = builder.get_updates_connect_timeout(20).get_updates_read_timeout(40)
    app = builder.post_init(_set_commands).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("new", new_chat))
    app.add_handler(CommandHandler("shablon", shablon))
    app.add_handler(CallbackQueryHandler(handle_template, pattern=r"^tpl:"))
    app.add_handler(CallbackQueryHandler(handle_template_prompt, pattern=r"^tpr:"))
    app.add_handler(CallbackQueryHandler(handle_answer, pattern=r"^ask:"))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(pub|cancel):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


async def run_webhook() -> None:
    application = build_application(webhook=True)

    async def telegram_webhook(request: web.Request) -> web.Response:
        if config.WEBHOOK_SECRET and (
            request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET
        ):
            return web.Response(status=403)
        data = await request.json()
        await application.update_queue.put(Update.de_json(data, application.bot))
        return web.Response(text="ok")

    async def media(request: web.Request) -> web.StreamResponse:
        name = Path(request.match_info["name"]).name  # path traversal'dan himoya
        path = config.MEDIA_DIR / name
        if not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def health(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    web_app = web.Application()
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_get("/media/{name}", media)
    web_app.router.add_get("/", health)

    async with application:
        await application.bot.set_webhook(
            f"{config.PUBLIC_URL}/telegram",
            secret_token=config.WEBHOOK_SECRET or None,
            allowed_updates=Update.ALL_TYPES,
        )
        await application.start()
        runner = web.AppRunner(web_app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
        log.info("Webhook rejimi: %s (port %s)", config.PUBLIC_URL, config.PORT)
        try:
            await asyncio.Event().wait()
        finally:
            await application.stop()
            await runner.cleanup()


def main() -> None:
    if config.PUBLIC_URL:
        asyncio.run(run_webhook())
    else:
        log.info("Polling rejimi (lokal)")
        # bootstrap_retries=-1: ishga tushishda internet bo'lmasa ham to'xtamay qayta urinadi
        build_application(webhook=False).run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
