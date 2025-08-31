import os
import json
import asyncio
from flask import Flask, request
from telegram import Update
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
PUBLIC_GROUP_ID = int(os.environ.get("PUBLIC_GROUP_ID"))
BOT_LINK = os.environ.get("BOT_LINK")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # آدرس عمومی وب‌هاک
PORT = int(os.environ.get("PORT", 8080))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
DRAFTS = {}
JOIN_LINKS = []  # لینک‌های جوین گروه‌ها

app = Flask("GoldStarMovieBot")

@app.route("/")
def home():
    return "✅ GoldStarMovieBot is running!"

@app.route("/health")
def health():
    return "OK", 200

# مسیر Webhook برای تلگرام
@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    asyncio.create_task(app_telegram.update_queue.put(update))
    return "OK"

# ───── Supabase Functions ─────
def add_movie_supabase(movie_id, poster_file_ids, description, files_json=None):
    supabase.table("movies").upsert({
        "movie_id": movie_id,
        "poster_file_ids": json.dumps(poster_file_ids),
        "description": description,
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
            "files": json.loads(row.get("files_json", "[]"))
        }
    return None

def save_user_supabase(user_id):
    supabase.table("users").upsert({"user_id": str(user_id)}).execute()

# ───── Telegram Helper Functions ─────
async def is_member_public_group(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(PUBLIC_GROUP_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

async def send_poster_to_public(context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    movie = get_movie_supabase(movie_id)
    if not movie:
        print(f"Movie {movie_id} not found!")
        return

    caption_text = movie['description'].strip() or "🎬 GoldStarMovie"
    # اضافه کردن لینک‌های جوین قبل لینک دانلود
    if JOIN_LINKS:
        join_text = "\n".join([f"📌 <a href='{link}'>Join Group</a>" for link in JOIN_LINKS])
        caption_text = join_text + "\n\n" + caption_text

    deep_link = f"{BOT_LINK}?start={movie_id}"
    caption_text += f'\n\n📥 <a href="{deep_link}">📥 Download</a>'

    for i, poster_id in enumerate(movie['poster_file_ids']):
        try:
            await context.bot.send_photo(
                chat_id=PUBLIC_GROUP_ID,
                photo=poster_id,
                caption=caption_text if i == 0 else None,
                parse_mode="HTML"
            )
        except Exception as e:
            print("Error sending poster:", e)

async def _deliver_movie_files(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_id: str):
    user_id = update.effective_user.id
    if JOIN_LINKS and not await is_member_public_group(context, user_id):
        await context.bot.send_message(
            chat_id=user_id,
            text="برای دانلود، لطفاً عضو گروه‌ها شوید:\n" + "\n".join(JOIN_LINKS),
            disable_web_page_preview=True
        )
        return

    movie = get_movie_supabase(movie_id)
    if not movie or not movie.get('files'):
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

# ───── Draft Handling ─────
async def draft_timeout(chat_id: int, delay: int = 600):
    await asyncio.sleep(delay)
    if chat_id in DRAFTS:
        DRAFTS.pop(chat_id, None)
        print(f"Draft in chat {chat_id} expired due to timeout.")

# ───── Commands ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_supabase(update.effective_user.id)
    if context.args:
        await _deliver_movie_files(update, context, context.args[0])
        return
    await update.message.reply_text("سلام 👋\nفیلم‌ها را از گروه عمومی انتخاب کنید.")

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

# ───── Admin Commands ─────
async def add_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if context.args:
        link = context.args[0]
        if link not in JOIN_LINKS:
            JOIN_LINKS.append(link)
            await update.message.reply_text(f"✅ لینک اضافه شد: {link}")

async def remove_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if context.args:
        link = context.args[0]
        if link in JOIN_LINKS:
            JOIN_LINKS.remove(link)
            await update.message.reply_text(f"✅ لینک حذف شد: {link}")

async def list_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not JOIN_LINKS:
        await update.message.reply_text("هیچ لینک جوینی ثبت نشده است.")
    else:
        await update.message.reply_text("لیست لینک‌های جوین:\n" + "\n".join(JOIN_LINKS))

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
            "files": []
        }
        asyncio.create_task(draft_timeout(chat_id))
        return

    if (message.video or message.document) and chat_id in DRAFTS:
        draft = DRAFTS[chat_id]
        if message.video:
            draft['files'].append({'type': 'video', 'file_id': message.video.file_id, 'caption': message.caption or ''})
        if message.document:
            draft['files'].append({'type': 'document', 'file_id': message.document.file_id, 'caption': message.caption or ''})
        return

    if message.sticker and chat_id in DRAFTS:
        draft = DRAFTS.pop(chat_id)
        movie_id = str(draft['start_message_id'])
        add_movie_supabase(
            movie_id,
            poster_file_ids=draft.get('poster_file_ids', []),
            description=draft.get('description', ''),
            files_json=json.dumps(draft.get('files', []), ensure_ascii=False)
        )
        await send_poster_to_public(context, movie_id)

# ───── Main ─────
async def main():
    global bot, app_telegram
    app_telegram = ApplicationBuilder().token(TOKEN).build()
    bot = app_telegram.bot

    # Handlers
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(CommandHandler("download", download))
    app_telegram.add_handler(CommandHandler("cancel", cancel))
    app_telegram.add_handler(CommandHandler("addjoin", add_join))
    app_telegram.add_handler(CommandHandler("removejoin", remove_join))
    app_telegram.add_handler(CommandHandler("listjoin", list_join))

    private_group_filter = filters.Chat(PRIVATE_GROUP_ID) & (
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.Sticker.ALL
    )
    app_telegram.add_handler(MessageHandler(private_group_filter, private_group_monitor))

    # Set Webhook
    await bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")
    logging.info("Webhook set successfully.")

    # Run Flask app in background
    loop = asyncio.get_event_loop()
    loop.create_task(asyncio.to_thread(app.run, host="0.0.0.0", port=PORT))

    # Start Telegram application
    await app_telegram.initialize()
    await app_telegram.start()
    await app_telegram.updater.start_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN)
    await app_telegram.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
