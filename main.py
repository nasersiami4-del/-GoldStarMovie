import os
import json
import asyncio
from flask import Flask, request
from telegram import Update, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import logging
from dotenv import load_dotenv
from supabase import create_client

# ───── Load environment ─────
load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PRIVATE_GROUP_ID = int(os.environ.get("PRIVATE_GROUP_ID"))
PUBLIC_GROUP_ID = int(os.environ.get("PUBLIC_GROUP_ID"))
BOT_LINK = os.environ.get("BOT_LINK")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
PUBLIC_GROUP_LINK = os.environ.get("PUBLIC_GROUP_LINK")
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # URL on Render or similar
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
DRAFTS = {}

# ───── Flask ─────
app = Flask(__name__)

@app.route("/health")
def health():
    return "OK"

@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok"

# ───── Supabase functions ─────
def add_movie(movie_id, poster_file_ids, description, is_series=0, season=0, episode=0, files_json=None):
    supabase.table("movies").upsert({
        "movie_id": movie_id,
        "poster_file_ids": json.dumps(poster_file_ids),
        "description": description,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "files_json": files_json
    }).execute()

def get_movie(movie_id):
    res = supabase.table("movies").select("*").eq("movie_id", movie_id).execute()
    if res.data:
        row = res.data[0]
        return {
            "poster_file_ids": json.loads(row.get("poster_file_ids", "[]")),
            "description": row.get("description", ""),
            "is_series": row.get("is_series", 0),
            "season": row.get("season", 0),
            "episode": row.get("episode", 0),
            "files": json.loads(row.get("files_json", "[]"))
        }
    return None

def save_user(user_id):
    supabase.table("users").upsert({"user_id": str(user_id)}).execute()

# ───── Membership check ─────
async def is_member_public(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(PUBLIC_GROUP_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

# ───── Send posters ─────
async def send_poster(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie(movie_id)
    if not movie: return
    caption = movie['description'].strip() or "🎬 GoldStarMovie"
    caption += f'\n\n📥 <a href="{BOT_LINK}?start={movie_id}">📥 Download | دانلـــود</a>'
    for i, poster_id in enumerate(movie['poster_file_ids']):
        await context.bot.send_photo(chat_id=PUBLIC_GROUP_ID, photo=poster_id,
                                     caption=caption if i==0 else None, parse_mode=ParseMode.HTML)

# ───── Deliver files ─────
async def deliver_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id
    if not await is_member_public(context, user_id):
        await context.bot.send_message(user_id, f"برای دانلود عضو گروه شوید:\n{PUBLIC_GROUP_LINK}")
        return
    movie = get_movie(movie_id)
    if not movie or not movie.get("files"):
        await update.message.reply_text("❌ فایل یافت نشد.")
        return
    msgs = []
    for f in movie["files"]:
        if f["type"]=="photo":
            sent = await context.bot.send_photo(user_id, f["file_id"], caption=f.get("caption",""))
        elif f["type"]=="video":
            sent = await context.bot.send_video(user_id, f["file_id"], caption=f.get("caption",""))
        else:
            sent = await context.bot.send_document(user_id, f["file_id"], caption=f.get("caption",""))
        msgs.append(sent)
    warn = await context.bot.send_message(user_id, "🛑⚠️ مدیا بعد 2 دقیقه حذف می‌شود ⚠️🛑")
    msgs.append(warn)
    async def delete_after_delay(chat_id, messages, delay=120):
        await asyncio.sleep(delay)
        for m in messages:
            try: await context.bot.delete_message(chat_id, m.message_id)
            except: continue
    asyncio.create_task(delete_after_delay(user_id, msgs))

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user.id)
    if context.args:
        await deliver_files(update, context, context.args[0])
        return
    await update.message.reply_text(f"سلام 👋\nفیلم‌ها رو از گروه عمومی انتخاب کنید.\n{PUBLIC_GROUP_LINK}")

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args: await deliver_files(update, context, context.args[0])
    else: await update.message.reply_text("❌ فیلم یا سریال پیدا نشد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id)
        await update.message.reply_text("✅ Draft لغو شد.")
    else:
        await update.message.reply_text("❌ Draft فعالی وجود ندارد.")

# ───── Private group monitor ─────
async def private_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    chat_id = msg.chat_id
    if msg.photo:
        DRAFTS[chat_id] = {
            "start_message_id": msg.message_id,
            "poster_file_ids": [msg.photo[-1].file_id],
            "description": msg.caption or "",
            "files": [],
            "is_series": 1,
            "season": 1,
            "episode": 0
        }
        asyncio.create_task(asyncio.sleep(600))  # draft timeout
        return
    if (msg.video or msg.document) and chat_id in DRAFTS:
        draft = DRAFTS[chat_id]
        if msg.video: draft["files"].append({"type":"video","file_id":msg.video.file_id,"caption":msg.caption or ""})
        if msg.document: draft["files"].append({"type":"document","file_id":msg.document.file_id,"caption":msg.caption or ""})
        draft["episode"] += 1
        return
    if msg.sticker and chat_id in DRAFTS:
        draft = DRAFTS.pop(chat_id)
        movie_id = str(draft["start_message_id"])
        add_movie(movie_id, draft.get("poster_file_ids", []), draft.get("description",""),
                  draft.get("is_series",0), draft.get("season",1), draft.get("episode",0),
                  json.dumps(draft.get("files", []), ensure_ascii=False))
        await send_poster(context, movie_id)

# ───── Main ─────
application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("download", download))
application.add_handler(CommandHandler("cancel", cancel))
private_filter = filters.Chat(PRIVATE_GROUP_ID) & (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL)
application.add_handler(MessageHandler(private_filter, private_monitor))

if __name__ == "__main__":
    from threading import Thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"webhook/{TOKEN}",
        webhook_url=f"{WEBHOOK_URL}/webhook/{TOKEN}"
    )
