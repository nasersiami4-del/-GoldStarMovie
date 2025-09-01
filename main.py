import os
import json
import asyncio
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
BOT_LINK = os.environ.get("BOT_LINK")
PORT = int(os.environ.get("PORT", 8080))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
DRAFTS = {}

app = Flask("GoldStarMovieBot")

@app.route("/")
def home():
    return "✅ GoldStarMovieBot is running!"

@app.route("/health")
def health():
    return "OK", 200

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

def get_group_links():
    response = supabase.table("group_links").select("*").execute()
    return response.data or []

def add_group_link(link: str):
    if not link.startswith("https://t.me/"):
        link = "https://t.me/" + link.lstrip("@")
    supabase.table("group_links").insert({"link": link}).execute()

def remove_group_link(link_id: int):
    supabase.table("group_links").delete().eq("id", link_id).execute()

# ───── Combined Functions ─────
def add_movie_both(movie_id, poster_file_ids, description, is_series=0, season=0, episode=0, files_json=None):
    add_movie_supabase(movie_id, poster_file_ids, description, is_series, season, episode, files_json)

def get_movie_both(movie_id):
    return get_movie_supabase(movie_id)

def save_user_both(user_id):
    save_user_supabase(user_id)

# ───── Membership Check ─────
async def is_member_all_groups(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    links = get_group_links()
    if not links:
        return True
    for row in links:
        try:
            member = await context.bot.get_chat_member(row['link'].split("https://t.me/")[-1], user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            return False
    return True

# ───── Send Posters ─────
async def send_poster_to_public(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie_both(movie_id)
    if not movie:
        print(f"Movie {movie_id} not found!")
        return

    caption_text = movie['description'].strip() or "🎬 GoldStarMovie"
    for i, poster_id in enumerate(movie['poster_file_ids']):
        try:
            await context.bot.send_photo(
                chat_id=PRIVATE_GROUP_ID,
                photo=poster_id,
                caption=caption_text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print("Error sending poster:", e)

# ───── Deliver Movie Files ─────
async def _deliver_movie_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id
    links = get_group_links()

    if links and not await is_member_all_groups(context, user_id):
        buttons = [[InlineKeyboardButton("عضو گروه", url=row['link'])] for row in links]
        keyboard = InlineKeyboardMarkup(buttons)
        await context.bot.send_message(
            chat_id=user_id,
            text="برای دانلود، لطفاً عضو همه گروه‌ها شوید:",
            reply_markup=keyboard
        )
        return

    movie = get_movie_both(movie_id)
    if not movie or not movie.get('files'):
        await update.message.reply_text("❌ فایل یافت نشد.")
        return

    sent_messages = []
    deep_link = f"{BOT_LINK}?start={movie_id}"
    # لینک دانلود به صورت متن داخل پوستر
    caption_text = f"{movie['description'].strip() or '🎬 GoldStarMovie'}\n\n📥 Download | دانلـــود ({deep_link})"

    for poster_id in movie['poster_file_ids']:
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=poster_id,
                caption=caption_text,
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

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_both(update.effective_user.id)

    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
        return

    links = get_group_links()
    buttons = [[InlineKeyboardButton("عضو گروه", url=row['link'])] for row in links]
    keyboard = InlineKeyboardMarkup(buttons) if links else None

    text = (
        "سلام 👋\n"
        "به GoldStarMovieBot خوش آمدید!\n"
        "🎬 اینجا می‌تونید جدیدترین فیلم‌ها و سریال‌ها رو ببینید و دانلود کنید.\n\n"
        "برای دانلود، لطفاً عضو گروه‌های زیر شوید:"
    )

    await update.message.reply_text(
        text=text,
        reply_markup=keyboard
    )

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
    else:
        await update.message.reply_text("❌ فیلم یا سریال پیدا نشد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id)
        await update.message.reply_text("✅ Draft لغو شد.")
    else:
        await update.message.reply_text("❌ Draft فعالی وجود ندارد.")

# ───── مدیریت لینک‌ها توسط ادمین ─────
async def addlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ دسترسی ندارید!")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً لینک گروه را وارد کنید.")
        return
    link = context.args[0]
    add_group_link(link)
    await update.message.reply_text(f"✅ لینک اضافه شد:\n{link}")

async def listlinks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ دسترسی ندارید!")
        return
    links = get_group_links()
    if not links:
        await update.message.reply_text("❌ لینکی ثبت نشده است.")
        return
    msg = "\n".join([f"{row['id']}: {row['link']}" for row in links])
    await update.message.reply_text(f"لینک‌های ثبت شده:\n{msg}")

async def removelink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ دسترسی ندارید!")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً آیدی لینک را وارد کنید.")
        return
    try:
        link_id = int(context.args[0])
        remove_group_link(link_id)
        await update.message.reply_text(f"✅ لینک با آیدی {link_id} حذف شد.")
    except Exception:
        await update.message.reply_text("❌ آیدی نامعتبر است.")

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
            "episode": 0
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
        add_movie_both(
            movie_id,
            poster_file_ids=draft.get('poster_file_ids', []),
            description=draft.get('description', ''),
            is_series=draft.get('is_series', 0),
            season=draft.get('season', 1),
            episode=draft.get('episode', 0),
            files_json=json.dumps(draft.get('files', []), ensure_ascii=False)
        )
        await send_poster_to_public(context, movie_id)

# ───── Main ─────
def main():
    print("✅ Starting GoldStarMovieBot...")
    telegram_app = ApplicationBuilder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("download", download))
    telegram_app.add_handler(CommandHandler("cancel", cancel))
    telegram_app.add_handler(CommandHandler("addlink", addlink))
    telegram_app.add_handler(CommandHandler("listlinks", listlinks))
    telegram_app.add_handler(CommandHandler("removelink", removelink))

    private_group_filter = filters.Chat(PRIVATE_GROUP_ID) & (
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL
    )
    telegram_app.add_handler(MessageHandler(private_group_filter, private_group_monitor))

    Thread(target=run_flask, daemon=True).start()
    telegram_app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
