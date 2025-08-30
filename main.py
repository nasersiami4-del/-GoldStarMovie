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
    filters,
)
import logging
from dotenv import load_dotenv
import psycopg2

# ───── Load Secrets ─────
env_path = "/etc/secrets/.env"
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    load_dotenv()

TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_GROUP_ID = int(os.environ.get("PUBLIC_GROUP_ID", 0))
SUDO_PERCENT = int(os.environ.get("SUDO_PERCENT", 0))
BOT_LINK = os.environ.get("BOT_LINK", "")
DATABASE_URL = os.environ.get("DATABASE_URL")  # Supabase Postgres

# ───── Logging ─────
logging.basicConfig(level=logging.INFO)

# ───── Flask ─────
app = Flask("GoldStarMovieBot")

@app.route("/")
def home():
    return "✅ GoldStarMovieBot is running!"

@app.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ───── Database ─────
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS movies (
            movie_id TEXT PRIMARY KEY,
            description TEXT,
            files_json JSONB
        );
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

def add_movie(movie_id, description, files):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO movies (movie_id, description, files_json) VALUES (%s, %s, %s) ON CONFLICT (movie_id) DO UPDATE SET description=%s, files_json=%s",
        (movie_id, description, json.dumps(files), description, json.dumps(files))
    )
    conn.commit()
    cur.close()
    conn.close()

def get_movie(movie_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT description, files_json FROM movies WHERE movie_id=%s", (movie_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"description": row[0], "files": row[1]}
    return None

def save_user(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def set_setting(key, value):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=%s", (key, value, value))
    conn.commit()
    cur.close()
    conn.close()

def get_setting(key):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

# ───── Send Files ─────
async def _deliver_movie_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id
    save_user(user_id)
    movie = get_movie(movie_id)
    if not movie:
        await update.message.reply_text("❌ فایل یافت نشد.")
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
    warning_msg = await context.bot.send_message(chat_id=user_id, text="🛑 فایل‌ها پس از 2 دقیقه حذف می‌شوند. ذخیره کنید.")
    sent_messages.append(warning_msg)

    async def delete_after_delay(chat_id, messages, delay=120):
        await asyncio.sleep(delay)
        for msg in messages:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except:
                continue
    asyncio.create_task(delete_after_delay(user_id, sent_messages))

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user.id)
    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
    else:
        group_link = get_setting("PUBLIC_GROUP_LINK") or "پیوند گروه تنظیم نشده"
        await update.message.reply_text(f"سلام 👋\nفیلم‌ها را از گروه عمومی انتخاب کنید:\n{group_link}")

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
    else:
        await update.message.reply_text("❌ فیلم یا سریال پیدا نشد.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ هیچ Draft فعالی وجود ندارد.")

async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ استفاده: /set <کلید> <مقدار>")
        return
    key = context.args[0].upper()
    value = " ".join(context.args[1:])
    set_setting(key, value)
    await update.message.reply_text(f"✅ تنظیم شد: {key} = {value}")

# ───── Main ─────
def main():
    init_db()
    app_thread = Thread(target=run_flask, daemon=True)
    app_thread.start()

    telegram_app = ApplicationBuilder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("download", download))
    telegram_app.add_handler(CommandHandler("cancel", cancel))
    telegram_app.add_handler(CommandHandler("set", set_command))

    telegram_app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
