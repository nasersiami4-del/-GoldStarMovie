import os
import json
import asyncio
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

# ───── Load Environment ─────
load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PRIVATE_GROUP_ID = int(os.environ.get("PRIVATE_GROUP_ID"))
PUBLIC_GROUP_ID = int(os.environ.get("PUBLIC_GROUP_ID"))
BOT_LINK = os.environ.get("BOT_LINK")
PUBLIC_GROUP_LINK = os.environ.get("PUBLIC_GROUP_LINK")
PORT = int(os.environ.get("PORT", 8080))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
DRAFTS = {}

# ───── Flask ─────
app = Flask("GoldStarMovieBot")

@app.route("/")
def home():
    return "✅ GoldStarMovieBot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

# ───── Supabase Functions ─────
def add_movie_supabase(movie_id, poster_file_ids, description, is_series=0, season=0, episode=0, files_json=None):
    supabase.table("movies").upsert({
        "movie_id": movie_id,
        "poster_file_ids": json.dumps(poster_file_ids),
        "description": description,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "files_json": files_json
    }).execute()

def get_movie_supabase(movie_id):
    response = supabase.table("movies").select("*").eq("movie_id", movie_id).execute()
    if response.data:
        row = response.data[0]
        return {
            "poster_file_ids": json.loads(row.get("poster_file_ids", "[]")),
            "description": row.get("description", ""),
            "is_series": row.get("is_series", 0),
            "season": row.get("season", 0),
            "episode": row.get("episode", 0),
            "files": json.loads(row.get("files_json", "[]"))
        }
    return None

def save_user_supabase(user_id):
    supabase.table("users").upsert({"user_id": str(user_id)}).execute()

# ───── Membership Check ─────
async def is_member_public_group(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(PUBLIC_GROUP_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

# ───── Send Posters ─────
async def send_poster_to_public(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie_supabase(movie_id)
    if not movie:
        return
    caption = movie['description'] or "🎬 GoldStarMovie"
    deep_link = f"{BOT_LINK}?start={movie_id}"
    caption += f'\n\n📥 <a href="{deep_link}">📥 Download | دانلـــود</a>'
    for i, pid in enumerate(movie['poster_file_ids']):
        try:
            await context.bot.send_photo(chat_id=PUBLIC_GROUP_ID, photo=pid, caption=caption if i==0 else None, parse_mode=ParseMode.HTML)
        except: pass

# ───── Deliver Movie Files ─────
async def deliver_movie_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id
    if not await is_member_public_group(context, user_id):
        await context.bot.send_message(chat_id=user_id, text=f"برای دانلود، لطفاً عضو گروه شوید:\n{PUBLIC_GROUP_LINK}", disable_web_page_preview=True)
        return
    movie = get_movie_supabase(movie_id)
    if not movie or not movie.get('files'):
        await context.bot.send_message(chat_id=user_id, text="❌ فایل یافت نشد.")
        return
    messages = []
    for f in movie['files']:
        try:
            if f['type'] == 'photo':
                msg = await context.bot.send_photo(chat_id=user_id, photo=f['file_id'], caption=f.get('caption',''))
            elif f['type'] == 'video':
                msg = await context.bot.send_video(chat_id=user_id, video=f['file_id'], caption=f.get('caption',''))
            else:
                msg = await context.bot.send_document(chat_id=user_id, document=f['file_id'], caption=f.get('caption',''))
            messages.append(msg)
        except: pass
    warning = await context.bot.send_message(chat_id=user_id, text="🛑⚠️ توجه: مدیای ارسال شده پس از 2 دقیقه حذف خواهد شد. ⚠️🛑")
    messages.append(warning)
    async def delete_after(chat_id, msgs, delay=120):
        await asyncio.sleep(delay)
        for m in msgs:
            try: await context.bot.delete_message(chat_id=chat_id, message_id=m.message_id)
            except: pass
    asyncio.create_task(delete_after(user_id, messages))

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_supabase(update.effective_user.id)
    if context.args:
        await deliver_movie_files(update, context, context.args[0])
    else:
        await update.message.reply_text(f"سلام 👋\nفیلم‌ها رو از گروه عمومی انتخاب کنید.\n{PUBLIC_GROUP_LINK}")

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await deliver_movie_files(update, context, context.args[0])
    else:
        await update.message.reply_text("❌ فیلم یا سریال پیدا نشد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id)
        await update.message.reply_text("✅ Draft لغو شد.")
    else:
        await update.message.reply_text("❌ Draft فعالی وجود ندارد.")

# ───── Private Group Monitor ─────
async def private_group_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    chat_id = msg.chat_id
    if msg.photo:
        poster_id = msg.photo[-1].file_id
        DRAFTS[chat_id] = {"start_message_id": msg.message_id, "poster_file_ids": [poster_id], "description": msg.caption or "", "files": [], "is_series": 1, "season": 1, "episode":0}
        asyncio.create_task(draft_timeout(chat_id))
    elif (msg.video or msg.document) and chat_id in DRAFTS:
        draft = DRAFTS[chat_id]
        if msg.video: draft['files'].append({'type':'video','file_id':msg.video.file_id,'caption':msg.caption or ''})
        if msg.document: draft['files'].append({'type':'document','file_id':msg.document.file_id,'caption':msg.caption or ''})
        draft['episode'] += 1
    elif msg.sticker and chat_id in DRAFTS:
        draft = DRAFTS.pop(chat_id)
        movie_id = str(draft['start_message_id'])
        add_movie_supabase(movie_id, draft['poster_file_ids'], draft['description'], draft.get('is_series',0), draft.get('season',1), draft.get('episode',0), json.dumps(draft.get('files',[]), ensure_ascii=False))
        await send_poster_to_public(context, movie_id)

# ───── Draft Timeout ─────
async def draft_timeout(chat_id: int, delay: int = 600):
    await asyncio.sleep(delay)
    DRAFTS.pop(chat_id, None)

# ───── Heartbeat Loop ─────
async def heartbeat_loop():
    while True:
        logging.info("💓 Heartbeat: bot is alive")
        await asyncio.sleep(300)

# ───── Main ─────
def main():
    Thread(target=run_flask, daemon=True).start()

    app_builder = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app_builder.add_handler(CommandHandler("start", start))
    app_builder.add_handler(CommandHandler("download", download))
    app_builder.add_handler(CommandHandler("cancel", cancel))
    app_builder.add_handler(MessageHandler(filters.Chat(PRIVATE_GROUP_ID) & (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL), private_group_monitor))

    # Start heartbeat loop
    asyncio.create_task(heartbeat_loop())

    app_builder.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
