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
from supabase import create_client, Client
import logging
from dotenv import load_dotenv

# ───── Load Env ─────
load_dotenv()
TOKEN = os.environ.get("BOT_TOKEN")
PRIVATE_GROUP_ID = int(os.environ.get("PRIVATE_GROUP_ID", 0))
PUBLIC_GROUP_ID = int(os.environ.get("PUBLIC_GROUP_ID", 0))
PUBLIC_GROUP_LINK = os.environ.get("PUBLIC_GROUP_LINK", "")
BOT_LINK = os.environ.get("BOT_LINK", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY")

# ───── Logging ─────
logging.basicConfig(level=logging.INFO)

# ───── Supabase Client ─────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ───── Draft Storage ─────
DRAFTS = {}

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

# ───── Supabase Functions ─────
def add_movie(movie_id, poster_file_ids, description, is_series=0, season=0, episode=0, files_json=None):
    supabase.table("movies").upsert({
        "movie_id": movie_id,
        "poster_file_ids": json.dumps(poster_file_ids),
        "description": description,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "files_json": json.dumps(files_json or [])
    }).execute()

def get_movie(movie_id):
    resp = supabase.table("movies").select("*").eq("movie_id", movie_id).execute()
    if resp.data:
        row = resp.data[0]
        return {
            "poster_file_ids": json.loads(row["poster_file_ids"] or "[]"),
            "description": row.get("description", ""),
            "is_series": row.get("is_series", 0),
            "season": row.get("season", 0),
            "episode": row.get("episode", 0),
            "files": json.loads(row.get("files_json") or "[]")
        }
    return None

def save_user(user_id):
    exists = supabase.table("users").select("*").eq("user_id", user_id).execute()
    if not exists.data:
        supabase.table("users").insert({"user_id": user_id}).execute()

# ───── Telegram Helpers ─────
async def is_member_public_group(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(PUBLIC_GROUP_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

async def send_poster_to_public(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie(movie_id)
    if not movie:
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

async def _deliver_movie_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id
    if not await is_member_public_group(context, user_id):
        await context.bot.send_message(
            chat_id=user_id,
            text=f"برای دانلود، لطفاً عضو گروه شوید:\n{PUBLIC_GROUP_LINK}",
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

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user.id)
    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
        return
    await update.message.reply_text(f"سلام 👋\nفیلم‌ها رو از گروه عمومی انتخاب کنید.\n{PUBLIC_GROUP_LINK}")

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
        add_movie(
            movie_id,
            poster_file_ids=draft.get('poster_file_ids', []),
            description=draft.get('description', ''),
            is_series=draft.get('is_series', 0),
            season=draft.get('season', 1),
            episode=draft.get('episode', 0),
            files_json=draft.get('files', [])
        )
        await send_poster_to_public(context, movie_id)

# ───── Draft Timeout ─────
async def draft_timeout(chat_id: int, delay: int = 600):
    await asyncio.sleep(delay)
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id, None)
        print(f"Draft in chat {chat_id} expired due to timeout.")

# ───── Main ─────
def main():
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
