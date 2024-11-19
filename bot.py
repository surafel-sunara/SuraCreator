import os
import tempfile
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.error import RetryAfter, NetworkError
from dotenv import load_dotenv
import logging
import aiofiles
import redis

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
REDIS_URL = os.getenv("REDIS_URL")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "ytdownloaderbot1")

# Redis client for caching
redis_client = redis.StrictRedis.from_url(REDIS_URL)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Start Command
async def start(update: Update, context):
    await update.message.reply_text("Send me the YouTube video URL you want to download.")

# Handle URL Message
async def handle_url(update: Update, context):
    user_id = update.message.chat.id
    url = update.message.text.strip()

    # Save the URL for later
    context.user_data["video_url"] = url

    # Check if user is a member of the channel
    try:
        chat_member = await context.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        if chat_member.status not in ["member", "administrator", "creator"]:
            # Ask the user to join the channel
            await update.message.reply_text(
                f"To continue, please join our channel: @{CHANNEL_USERNAME}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
                     [InlineKeyboardButton("I've Joined", callback_data="joined_channel")]]
                )
            )
        else:
            # User is already a member; proceed to download options
            await show_download_options(update, context)
    except RetryAfter as e:
        logger.warning(f"Rate limit hit: {e}")
    except NetworkError as e:
        logger.error(f"Network error: {e}")

# Show Download Options
async def show_download_options(update: Update, context):
    url = context.user_data.get("video_url")
    if not url:
        await update.message.reply_text("No video URL found. Please send it again.")
        return

    await update.message.reply_text(
        f"Requested URL: {url}\nChoose options below:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Video", callback_data="video")],
            [InlineKeyboardButton("MP3", callback_data="mp3")]
        ])
    )

# Handle "I've Joined" Button
async def joined_channel(update: Update, context):
    query = update.callback_query
    user_id = query.message.chat.id

    # Check if the user has actually joined the channel
    try:
        chat_member = await context.bot.get_chat_member(chat_id=f"@{CHANNEL_USERNAME}", user_id=user_id)
        if chat_member.status not in ["member", "administrator", "creator"]:
            await query.answer("Please join the channel first.", show_alert=True)
        else:
            await query.answer("Thank you for joining!")
            # Proceed to show download options
            await show_download_options(query, context)
    except Exception as e:
        logger.error(f"Error checking membership: {e}")

# Download and Send File
async def download_file(update: Update, context):
    query = update.callback_query
    await query.answer()
    choice = query.data  # "video" or "mp3"
    url = context.user_data.get("video_url")

    if not url:
        await query.edit_message_text("No video URL found. Please send it again.")
        return

    await query.edit_message_text("Wait a sec... Preparing your file!")

    # Define download options
    file_key = f"{url}_{choice}"
    cached_file = redis_client.get(file_key)
    if cached_file:
        await query.edit_message_text("File ready from cache. Sending now!")
        await send_file_from_cache(query, choice, cached_file)
        return

    ydl_opts = {
        "format": "bestvideo+bestaudio/best" if choice == "video" else "bestaudio[ext=m4a]",
        "quiet": True,
        "merge_output_format": "mp4" if choice == "video" else "mp3",
        "outtmpl": os.path.join(tempfile.gettempdir(), "%(title)s.%(ext)s"),
        "geo_bypass": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

            # Cache file path in Redis
            redis_client.setex(file_key, 3600 * 24, file_path)

            await send_file_from_cache(query, choice, file_path)

    except Exception as e:
        logger.error(f"Error during download: {e}")
        await query.edit_message_text(f"Error: {str(e)}")

# Send File from Cache
async def send_file_from_cache(query, choice, file_path):
    try:
        if not os.path.exists(file_path):
            await query.edit_message_text("Error: File not found on server.")
            return

        async with aiofiles.open(file_path, "rb") as f:
            if choice == "mp3":
                await query.message.bot.send_audio(chat_id=query.message.chat.id, audio=f)
            else:
                await query.message.bot.send_video(chat_id=query.message.chat.id, video=f)

        await query.edit_message_text("Download complete! File sent.")
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        await query.edit_message_text("Error sending the file.")

# Main Function
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(joined_channel, pattern="joined_channel"))
    application.add_handler(CallbackQueryHandler(download_file, pattern="^(video|mp3)$"))

    # Webhook Configuration
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8443)),
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
