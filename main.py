import os
import json
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

# ───── بارگذاری متغیرهای محیطی ─────
load_dotenv()

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PRIVATE_GROUP_ID = int(os.environ.get("PRIVATE_GROUP_ID"))
PUBLIC_GROUP_LINKS = os.environ.get("PUBLIC_GROUP_LINKS", "").split(",")  # چند لینک با کاما
BOT_LINK = os.environ.get("BOT_LINK")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
PORT = int(os.environ.get("PORT", 8080))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
DRAFTS = {}

app = Flask("GoldStarMovieBot")

# ───── Webhook مسیر سلامت ─────
@app.route("/health")
def health():
    return "OK", 200

@app.route(f"/{TOKEN}", methods=["POST"])
async def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, bot)
    await application.update_queue.put(update)
    return "OK"

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
    data = response.data
    if data:
        row = data[0]
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
        for link in PUBLIC_GROUP_LINKS:
            chat_id = int(link.split(":")[0]) if ":" in link else None
            if not chat_id:
                continue
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ("member", "administrator", "creator"):
                return True
        return False
    except Exception:
        return False

# ───── Send Posters ─────
async def send_poster_to_public(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie_supabase(movie_id)
    if not movie:
        print(f"Movie {movie_id} not found!")
        return

    caption_text = movie['description'].strip() or "🎬 GoldStarMovie"
    deep_link = f"{BOT_LINK}?start={movie_id}"
    caption_text += f'\n\n📥 <a href="{deep_link}">📥 Download | دانلـــود</a>'

    for i, poster_id in enumerate(movie['poster_file_ids']):
        try:
            for link in PUBLIC_GROUP_LINKS:
                chat_id = int(link.split(":")[0]) if ":" in link else None
                if not chat_id:
                    continue
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=poster_id,
                    caption=caption_text if i == 0 else None,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            print("Error sending poster:", e)

# ───── Draft Timeout ─────
async def draft_timeout(chat_id: int, delay: int = 600):
    await asyncio.sleep(delay)
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id, None)
        print(f"Draft in chat {chat_id} expired due to timeout.")

# ───── Private Group Monitor ─────
async def private_group_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    chat_id = message.chat_id

    if message.photo:
        poster_id = message.photo[-1].file_id
        DRAFTS[chat_id] = {
            "start_message_id": message.message_id,
            "poster_file_ids": [poster_id],
            "description": message.caption or "",
            "files": [],
            "is_series": 1,
            "season": 1,
            "episode": 0,
            "join_links": PUBLIC_GROUP_LINKS
        }
        asyncio.create_task(draft_timeout(chat_id))
        return

    if (message.video or message.document) and chat_id in DRAFTS:
        draft = DRAFTS[chat_id]
        if message.video:
            draft['files'].append({'type': 'video', 'file_id': message.video.file_id, 'caption': message.caption or ''})
        if message.document:
            draft['files'].append({'type': 'document', 'file_id': message.document.file_id, 'caption': message.caption or ''})
        draft['episode'] += 1
        return

    if message.sticker and chat_id in DRAFTS:
        draft = DRAFTS.pop(chat_id)
        movie_id = str(draft['start_message_id'])
        add_movie_supabase(
            movie_id,
            poster_file_ids=draft.get('poster_file_ids', []),
            description=draft.get('description', ''),
            is_series=draft.get('is_series', 0),
            season=draft.get('season', 1),
            episode=draft.get('episode', 0),
            files_json=json.dumps(draft.get('files', []), ensure_ascii=False)
        )
        await send_poster_to_public(context, movie_id)

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_supabase(update.effective_user.id)
    await update.message.reply_text(f"سلام 👋\nفیلم‌ها رو از گروه عمومی انتخاب کنید.\nلینک‌های گروه: {', '.join(PUBLIC_GROUP_LINKS)}")

async def manage_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != int(ADMIN_ID):
        await update.message.reply_text("❌ شما ادمین نیستید.")
        return
    text = "لینک‌های فعلی:\n" + "\n".join(PUBLIC_GROUP_LINKS)
    await update.message.reply_text(text)

# ───── Main ─────
bot = Bot(token=TOKEN)
application = ApplicationBuilder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("manage_links", manage_links))
application.add_handler(MessageHandler(
    filters.Chat(PRIVATE_GROUP_ID) & (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL),
    private_group_monitor
))

if __name__ == "__main__":
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # مثال: https://yourdomain.com
    bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")
    app.run(host="0.0.0.0", port=PORT)
