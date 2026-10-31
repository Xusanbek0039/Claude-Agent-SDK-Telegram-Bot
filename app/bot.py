"""Telegram bot entrypoint.

Render'da: aiohttp server (Telegram webhook + /media/* fayllar Instagram uchun).
Lokal (PUBLIC_URL yo'q): polling rejimi.
"""

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import agent, config, instagram

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
        "/new — yangi suhbat\n/id — Telegram ID'ingiz"
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


# Agent bergan savollar: qid -> (savol, variantlar)
questions: dict[str, tuple[str, list[str]]] = {}


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
            chat_id, f"📤 Instagram'ga chiqaraymi?

{caption}", reply_markup=keyboard
        )

    async def ask(question: str, options: list[str]) -> None:
        qid = uuid.uuid4().hex[:8]
        questions[qid] = (question, options)
        rows = [[InlineKeyboardButton(o, callback_data=f"ask:{qid}:{i}")] for i, o in enumerate(options)]
        rows.append([InlineKeyboardButton("🎲 O'zing tanla", callback_data=f"ask:{qid}:auto")])
        await bot.send_message(
            chat_id, f"❓ {question}

(Tugmani bosing yoki o'z variantingizni yozing)",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    typing = asyncio.create_task(_keep_typing(context, chat_id))
    try:
        reply = await agent.run(
            chat_id, text, agent.ChatIO(send_video, send_photo, send_publish_request, ask)
        )
    except Exception as e:
        log.exception("Agent xatosi")
        reply = f"⚠️ Xatolik: {e}"
    finally:
        typing.cancel()
    for i in range(0, len(reply), 4000):
        await bot.send_message(chat_id, reply[i : i + 4000])


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text(f"⛔ Ruxsat yo'q. Sizning ID: {update.effective_user.id}")
        return
    await _run_agent(update.effective_chat.id, update.message.text, context)


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
    question, options = entry
    answer = "O'zing eng yaxshisini tanla" if choice == "auto" else options[int(choice)]
    await query.edit_message_text(f"❓ {question}

✅ {answer}")
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


def build_application(webhook: bool) -> Application:
    builder = Application.builder().token(config.TELEGRAM_BOT_TOKEN).concurrent_updates(True)
    if webhook:
        builder = builder.updater(None)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("new", new_chat))
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
        build_application(webhook=False).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
