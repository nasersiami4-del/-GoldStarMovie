import os
import json
import sqlite3
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
    filters,
)
import logging

# ───── لاگ‌گیری ─────
logging.basicConfig(level=logging.INFO)

# ───── تنظیمات امن (از Environment Variables) ─────
TOKEN = os.environ.get("BOT_TOKEN")
PRIVATE_GROUP_ID = int(os.environ.get("PRIVATE_GROUP_ID", "-1001311582958"))
PUBLIC_GROUP_ID = int(os.environ.get("PUBLIC_GROUP_ID", "-1001081524118"))
BOT_LINK = os.environ.get("BOT_LINK", "https://t.me/GoldStarMusicMoviebot")
DB_PATH = "movies.db"
USER_LIST_FILE = "users.txt"
os.makedirs("movie_files", exist_ok=True)

# ───── Draft ─────
DRAFTS = {}

# ───── Flask ─────
app = Flask("")

@app.route("/")
def home():
    return "✅ GoldStarMovieBot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ───── پرینت لینک عمومی ─────
def print_public_url():
    repl_owner = os.environ.get("REPL_OWNER")
    repl_name = os.environ.get("REPL_SLUG")
    if repl_owner and repl_name:
        public_url = f"https://{repl_name}.{repl_owner}.repl.co/"
        print(f"Public URL (for UptimeRobot): {public_url}")
    else:
        print("Could not determine public URL automatically.")

# ───── دیتابیس ─────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS movies (
            movie_id TEXT PRIMARY KEY,
            poster_file_ids TEXT,
            description TEXT,
            is_series INTEGER DEFAULT 0,
            season INTEGER DEFAULT 0,
            episode INTEGER DEFAULT 0,
            files_json TEXT
        )
    ''')
    conn.close()

def add_movie(movie_id, poster_file_ids, description, is_series=0, season=0, episode=0, files_json=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        INSERT OR REPLACE INTO movies 
        (movie_id, poster_file_ids, description, is_series, season, episode, files_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (movie_id, json.dumps(poster_file_ids), description, is_series, season, episode, files_json))
    conn.commit()
    conn.close()

def get_movie(movie_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM movies WHERE movie_id = ?", (movie_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "poster_file_ids": json.loads(row[1]) if row[1] else [],
            "description": row[2] or "",
            "is_series": row[3] or 0,
            "season": row[4] or 0,
            "episode": row[5] or 0,
            "files": json.loads(row[6]) if row[6] else []
        }
    return None

# ───── مدیریت کاربران ─────
def save_user(user_id):
    try:
        if not os.path.exists(USER_LIST_FILE):
            with open(USER_LIST_FILE, "w", encoding="utf-8") as f:
                f.write(f"{user_id}\n")
        else:
            with open(USER_LIST_FILE, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            if str(user_id) not in lines:
                with open(USER_LIST_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{user_id}\n")
    except Exception as e:
        print("Error saving user:", e)

async def is_member_public_group(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(PUBLIC_GROUP_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

# ───── ارسال پوستر و لینک به گروه عمومی ─────
async def send_poster_to_public(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie(movie_id)
    if not movie:
        print(f"Movie {movie_id} not found!")
        return

    caption_text = movie['description'].strip() or "🎬 GoldStarMovie"
    deep_link = f"{BOT_LINK}?start={movie_id}"
    caption_text += f'\n\n📥 <a href="{deep_link}">📥 Download | دانلـــود</a>'

    for i, poster_id in enumerate(movie['poster_file_ids']):
        try:
            await context.bot.send_photo(
                chat_id=PUBLIC_GROUP_ID,
                photo=poster_id,
                caption=caption_text if i == 0 else None,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print("Error sending poster:", e)

# ───── ارسال فایل‌ها به کاربر ─────
async def _deliver_movie_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id

    if not await is_member_public_group(context, user_id):
        await context.bot.send_message(
            chat_id=user_id,
            text="برای دانلود، لطفاً عضو گروه شوید:\nhttps://t.me/GoldStarMusic3",
            disable_web_page_preview=True
        )
        return

    movie = get_movie(movie_id)
    if not movie or not movie.get('files'):
        await context.bot.send_message(chat_id=user_id, text="❌ فایل یافت نشد.")
        return

    sent_messages = []
    for f in movie['files']:
        try:
            if f['type'] == 'photo':
                sent = await context.bot.send_photo(chat_id=user_id, photo=f['file_id'], caption=f.get('caption', ''))
            elif f['type'] == 'video':
                sent = await context.bot.send_video(chat_id=user_id, video=f['file_id'], caption=f.get('caption', ''))
            else:
                sent = await context.bot.send_document(chat_id=user_id, document=f['file_id'], caption=f.get('caption', ''))
            sent_messages.append(sent)
        except Exception as e:
            print("Error sending file:", e)

    warning_msg = await context.bot.send_message(
        chat_id=user_id,
        text="🛑⚠️ توجه: مدیای ارسال شده پس از 2 دقیقه حذف خواهد شد. لطفا پیام را ذخیره کنید. ⚠️🛑"
    )
    sent_messages.append(warning_msg)

    async def delete_after_delay(chat_id, messages, delay=120):
        await asyncio.sleep(delay)
        for msg in messages:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except Exception:
                continue

    asyncio.create_task(delete_after_delay(user_id, sent_messages))

# ───── Timeout Draft ─────
async def draft_timeout(chat_id: int, delay: int = 600):
    await asyncio.sleep(delay)
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id, None)
        print(f"Draft in chat {chat_id} expired due to timeout.")

# ───── دستورات ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user.id)
    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
        return
    await update.message.reply_text("سلام 👋\nفیلم‌ها رو از گروه عمومی انتخاب کنید.\n@GoldStarMusic3")

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

# ───── مانیتور گروه خصوصی ─────
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
        add_movie(
            movie_id,
            poster_file_ids=draft.get('poster_file_ids', []),
            description=draft.get('description', ''),
            is_series=draft.get('is_series', 0),
            season=draft.get('season', 1),
            episode=draft.get('episode', 0),
            files_json=json.dumps(draft.get('files', []), ensure_ascii=False)
        )
        await send_poster_to_public(context, movie_id)

# ───── اجرای همزمان ─────
def main():
    init_db()
    print_public_url()  # لینک عمومی را هنگام ران شدن چاپ کن

    telegram_app = ApplicationBuilder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("download", download))
    telegram_app.add_handler(CommandHandler("cancel", cancel))

    private_group_filter = filters.Chat(PRIVATE_GROUP_ID) & (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL)
    telegram_app.add_handler(MessageHandler(private_group_filter, private_group_monitor))

    Thread(target=run_flask, daemon=True).start()
    telegram_app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
