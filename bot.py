import os
import logging
import json
from datetime import datetime, timedelta, timezone
import aiohttp
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import Forbidden, BadRequest

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Get bot token and admin usernames from environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_ADMINS = os.getenv('BOT_ADMINS', '').split(',')
REFERRALS_REST_URL = os.getenv('REFERRALS_REST_URL')

USER_DB = os.getenv('USER_DB', 'user_ids.json')

# Change the user_ids set to a global variable
user_ids = set()

# Add these new functions
def load_user_ids():
    global user_ids
    try:
        with open(USER_DB, 'r') as f:
            user_ids = set(json.load(f))
    except FileNotFoundError:
        user_ids = set()

def save_user_ids():
    with open(USER_DB, 'w') as f:
        json.dump(list(user_ids), f)

async def start(update: Update, context):
    user = update.effective_user
    if user.id not in user_ids:
        user_ids.add(user.id)
        save_user_ids()  # Save the updated user_ids
        
        # Notify admins about the new user
        for admin in BOT_ADMINS:
            await context.bot.send_message(
                chat_id=admin,
                text=f"New user connected: @{user.username} (ID: {user.id})"
            )

async def handle_message(update: Update, context):
    user = update.effective_user
    if user.id not in user_ids:    
        user_ids.add(user.id)
        save_user_ids()  # Save the updated user_ids
        # Notify admins about the new user
        for admin in BOT_ADMINS:
            await context.bot.send_message(
                chat_id=admin,
                text=f"New user connected: @{user.username} (ID: {user.id})"
            )

async def error_handler(update: Update, context):
    """Log Errors caused by Updates."""
    logging.error(f"Update {update} caused error {context.error}")

# Assume this function gets user IDs from your external API
async def get_user_ids_from_external_api():
    # Implementation details...
    return [123456789, 987654321, ...]  # Example user IDs

async def check_user_status(bot: Bot, user_id: int):
    try:
        chat = await bot.get_chat(user_id)
        return True  # User is available
    except Forbidden:
        print(f"Bot was blocked by user {user_id}")
        return False
    except BadRequest as e:
        if "chat not found" in str(e).lower():
            print(f"Chat not found for user {user_id}")
            return False
        raise  # Re-raise other BadRequest errors

async def send_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) not in BOT_ADMINS:
        await update.message.reply_text("Sorry, you don't have permission to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Please provide a news message after the /news command.")
        return

    news_message = " ".join(context.args)
    
    # Fetch user IDs from the external API
    referrals_url = f"{REFERRALS_REST_URL}/info/getId"
    async with aiohttp.ClientSession() as session:
        async with session.get(referrals_url) as response:
            if response.status == 200:
                user_data = await response.json()
                user_ids = set(int(user['telegramUserId']) for user in user_data)
            else:
                await update.message.reply_text(f"Failed to fetch user IDs. Status code: {response.status}")
                return

    sent_count = 0
    for user_id in user_ids:
        if str(user_id) not in BOT_ADMINS:
            if await check_user_status(context.bot, user_id):
                try:
                    await context.bot.send_message(chat_id=user_id, text=f"📢 News: {news_message}")
                    logging.info(f"Sent news to user {user_id}")
                    sent_count += 1
                except Forbidden:
                    logging.error(f"Failed to send message. User {user_id} has blocked the bot.")
                except BadRequest as e:
                    logging.error(f"Failed to send message to user {user_id}. Error: {str(e)}")
            else:
                logging.info(f"Skipping user {user_id} as they are not available or have blocked the bot")

    await update.message.reply_text(f"News broadcast sent to {sent_count} users.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) not in BOT_ADMINS:
        await update.message.reply_text("Sorry, you don't have permission to use this command.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Please provide the number of minutes after the /stats command.")
        return

    minutes = int(context.args[0])
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=minutes)

    start = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{REFERRALS_REST_URL}/partners?start={start}&end={end}"
    logging.info(url)

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                partners = await response.json()
                stats_message = f"Stats for the last {minutes} minutes:\n\n"
                
                for partner_id in partners:
                    try:
                        partner_user = await context.bot.get_chat(partner_id)
                        partner_username = partner_user.username or f"user{partner_id}"
                        stats_message += f"Partner @{partner_username} (ID: {partner_id}):\n"
                    except Exception as e:
                        stats_message += f"Partner {partner_id} (Unable to fetch username):\n"

                    referrals_url = f"{REFERRALS_REST_URL}/partners/{partner_id}/referrals?start={start}&end={end}"
                    logging.info(referrals_url)
                    async with session.get(referrals_url) as referrals_response:
                        if referrals_response.status == 200:
                            referrals = await referrals_response.json()
                            for referral in referrals:
                                try:
                                    user = await context.bot.get_chat(referral)
                                    username = user.username or f"user{referral}"
                                    stats_message += f"  - Referral @{username} (ID: {referral})\n"
                                except Exception as e:
                                    stats_message += f"  - Referral {referral} (Unable to fetch username)\n"
                        else:
                            stats_message += f"  Failed to fetch referrals. Status code: {referrals_response.status}\n"
                
                await update.message.reply_text(stats_message)
            else:
                await update.message.reply_text(f"Failed to fetch partners. Status code: {response.status}")

def main():
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logger = logging.getLogger(__name__)

    # Silence specific loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Load user IDs at the start of the main function
    load_user_ids()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CommandHandler("news", send_news))
    application.add_handler(CommandHandler("stats", stats))  # Add this line

    # Add error handler
    application.add_error_handler(error_handler)

    print("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()