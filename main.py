import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from flask import Flask, request
from supabase import create_client

# ───── Config ─────
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # لینک Render شما

PRIVATE_GROUP_ID = int(os.getenv("PRIVATE_GROUP_ID"))
PUBLIC_GROUP_ID = int(os.getenv("PUBLIC_GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PUBLIC_GROUP_LINK = os.getenv("PUBLIC_GROUP_LINK")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ───── Logging ─────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ───── Telegram Bot Setup ─────
telegram_app = Application.builder().token(BOT_TOKEN).build()

# ───── Handlers ─────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! ربات GoldStarMovie آماده است.\nبرای راهنما /help را بزنید."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"لینک گروه عمومی برای دانلود: {PUBLIC_GROUP_LINK}\n\n"
        "دستورات:\n"
        "/start - شروع ربات\n"
        "/help - راهنما\n"
        "/latest - آخرین فیلم‌ها\n"
        "/admin - مدیریت (فقط ادمین)"
    )
    await update.message.reply_text(msg)

async def latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = supabase.table("movies").select("*").order("id", desc=True).limit(5).execute()
    movies = response.data
    if movies:
        msg = "\n".join([f"{m['title']} - {m['link']}" for m in movies])
    else:
        msg = "هیچ فیلمی ثبت نشده است."
    await update.message.reply_text(msg)

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("شما ادمین نیستید!")
        return
    msg = "دستورات ادمین:\n/addmovie - افزودن فیلم\n/listusers - لیست کاربران"
    await update.message.reply_text(msg)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("پیام دریافت شد: " + update.message.text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"انتخاب شد: {query.data}")

# ───── Register Handlers ─────
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("help", help_command))
telegram_app.add_handler(CommandHandler("latest", latest))
telegram_app.add_handler(CommandHandler("admin", admin_command))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
telegram_app.add_handler(CallbackQueryHandler(button_handler))

# ───── Flask App for Render ─────
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    telegram_app.update_queue.put(update)
    return "ok"

@app.route("/health", methods=["GET"])
def health():
    return "OK"

# ───── Main ─────
def main():
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"webhook/{BOT_TOKEN}",
        webhook_url=f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
