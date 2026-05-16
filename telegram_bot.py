#!/usr/bin/env python3
"""
Zazy Telegram Bot

Interactive Telegram bot for triggering uzeen playlist updates and checking status.
Runs as a long-running service alongside Flask API and FastAPI in Docker.

Commands:
    /start - Welcome message and command list
    /help - Show all available commands
    /uzeen - Run uzeen playlist updater now
    /uzeen_status - Show current credentials
    /uzeen_history - Show credential change history
    /ping - Health check (bot is alive)

Authorization:
    Only authorized chat IDs (from TELEGRAM_CHAT_ID env var) can execute commands.
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
AUTHORIZED_CHAT_IDS = [int(id.strip()) for id in os.getenv('TELEGRAM_CHAT_ID', '').split(',') if id.strip()]
POLL_INTERVAL = float(os.getenv('TELEGRAM_BOT_POLL_INTERVAL', '1.0'))
POLL_TIMEOUT = int(os.getenv('TELEGRAM_BOT_TIMEOUT', '30'))

# File paths
STATE_FILE = "uzeen_playlist_state.json"
HISTORY_FILE = "uzeen_credential_history.json"
UPDATER_SCRIPT = "uzeen_playlist_updater.py"


def is_authorized(chat_id: int) -> bool:
    """Check if chat_id is authorized to use the bot."""
    return chat_id in AUTHORIZED_CHAT_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start command is issued."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text(
            "⛔ Unauthorized access.\n"
            f"Your chat ID: {chat_id}\n\n"
            "This bot is private and only authorized users can access it."
        )
        logger.warning(f"Unauthorized /start attempt from chat_id: {chat_id}")
        return

    welcome_message = (
        "🤖 **Zazy Telegram Bot**\n\n"
        "Welcome! I can help you manage the Uzeen playlist updater.\n\n"
        "**Available Commands:**\n"
        "/uzeen - Run uzeen playlist updater now\n"
        "/uzeen\\_status - Show current credentials\n"
        "/uzeen\\_history - Show credential change history\n"
        "/help - Show this help message\n"
        "/ping - Check if bot is alive\n\n"
        "The bot also runs automatically via cron every 6 hours."
    )

    await update.message.reply_text(welcome_message, parse_mode='Markdown')
    logger.info(f"Sent welcome message to chat_id: {chat_id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message when /help command is issued."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    help_message = (
        "📚 **Zazy Bot Commands**\n\n"
        "**/uzeen** - Trigger uzeen playlist updater\n"
        "  • Fetches latest M3U file\n"
        "  • Extracts Xtream credentials\n"
        "  • Updates IboPlayer if changed\n\n"
        "**/uzeen\\_status** - Check current status\n"
        "  • Shows current credentials\n"
        "  • Last update timestamp\n"
        "  • Playlist ID\n\n"
        "**/uzeen\\_history** - View change history\n"
        "  • Credential change timeline\n"
        "  • Time between changes\n"
        "  • Change patterns\n\n"
        "**/ping** - Bot health check\n\n"
        "**/help** - Show this message\n\n"
        "**Note:** Automated updates run every 6 hours via cron."
    )

    await update.message.reply_text(help_message, parse_mode='Markdown')


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Respond to /ping command - health check."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    await update.message.reply_text("✅ Bot is alive and running!")
    logger.info(f"Ping from chat_id: {chat_id}")


async def uzeen_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current uzeen credentials and status."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    if not os.path.exists(STATE_FILE):
        await update.message.reply_text(
            "⚠️ No state file found.\n\n"
            "Run /uzeen first to initialize the updater."
        )
        return

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)

        last_updated = datetime.fromisoformat(state.get('last_updated', 'unknown'))
        time_ago = datetime.now() - last_updated
        hours_ago = int(time_ago.total_seconds() / 3600)
        minutes_ago = int((time_ago.total_seconds() % 3600) / 60)

        status_message = (
            f"📋 **Uzeen Playlist Status**\n\n"
            f"**Host:** `{state.get('playlist_url', 'N/A')}`\n"
            f"**Username:** `{state.get('username', 'N/A')}`\n"
            f"**Password:** `{state.get('password', 'N/A')}`\n"
            f"**Playlist ID:** `{state.get('playlist_id', 'N/A')}`\n\n"
            f"**Last Updated:** {last_updated.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"*({hours_ago}h {minutes_ago}m ago)*\n\n"
            f"✅ Playlist is configured"
        )

        await update.message.reply_text(status_message, parse_mode='Markdown')
        logger.info(f"Sent status to chat_id: {chat_id}")

    except Exception as e:
        await update.message.reply_text(f"❌ Error reading state file: {str(e)}")
        logger.error(f"Error in uzeen_status: {e}")


async def uzeen_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show credential change history."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    if not os.path.exists(HISTORY_FILE):
        await update.message.reply_text(
            "⚠️ No history file found.\n\n"
            "Credential changes will be tracked automatically.\n"
            "Run /uzeen to start tracking."
        )
        return

    try:
        with open(HISTORY_FILE, 'r') as f:
            history = json.load(f)

        changes = history.get('changes', [])

        if not changes:
            await update.message.reply_text("📊 No credential changes recorded yet.")
            return

        # Build history message (show last 5 changes)
        history_lines = [f"📊 **Credential Change History**\n"]

        for i, change in enumerate(reversed(changes[-5:]), 1):
            timestamp = datetime.fromisoformat(change['timestamp'])
            history_lines.append(
                f"\n**{len(changes) - i + 1}.** {timestamp.strftime('%Y-%m-%d %H:%M')}\n"
                f"   Host: `{change['playlist_url']}`\n"
                f"   User: `{change['username']}`\n"
                f"   Pass: `{change['password']}`"
            )

        if len(changes) > 5:
            history_lines.append(f"\n\n_Showing last 5 of {len(changes)} total changes_")
        else:
            history_lines.append(f"\n\n_Total changes: {len(changes)}_")

        await update.message.reply_text('\n'.join(history_lines), parse_mode='Markdown')
        logger.info(f"Sent history to chat_id: {chat_id}")

    except Exception as e:
        await update.message.reply_text(f"❌ Error reading history: {str(e)}")
        logger.error(f"Error in uzeen_history: {e}")


async def run_uzeen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the uzeen playlist updater script."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized access.")
        logger.warning(f"Unauthorized /uzeen attempt from chat_id: {chat_id}")
        return

    # Send initial message
    await update.message.reply_text("🔄 **Running Uzeen Playlist Updater...**\n\nPlease wait, this may take 30-60 seconds.")
    logger.info(f"Starting uzeen updater for chat_id: {chat_id}")

    try:
        # Run the updater script
        result = subprocess.run(
            ['python3', UPDATER_SCRIPT],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        # Parse output for key information
        output_lines = result.stdout.split('\n')

        # Extract key details
        username = None
        password = None
        host = None
        changed_fields = []
        no_changes = False
        success = False
        error_msg = None

        for line in output_lines:
            if 'Username:' in line:
                username = line.split('Username:')[1].strip()
            elif 'Password:' in line:
                password = line.split('Password:')[1].strip()
            elif 'Host:' in line or 'Playlist URL:' in line:
                host = line.split(':',1)[1].strip()
            elif 'Changed fields:' in line:
                changed_fields = line.split('Changed fields:')[1].strip().split(', ')
            elif 'No changes detected' in line:
                no_changes = True
            elif 'Playlist updated successfully' in line:
                success = True
            elif '[!]' in line and 'Error' in line:
                error_msg = line

        # Build response message
        if no_changes:
            response = (
                f"✅ **Uzeen Update Complete**\n\n"
                f"ℹ️ No changes detected\n"
                f"Playlist is already up to date!\n\n"
                f"**Current Credentials:**\n"
                f"Host: `{host or 'N/A'}`\n"
                f"Username: `{username or 'N/A'}`\n"
                f"Password: `{password or 'N/A'}`"
            )
        elif success:
            response = (
                f"✅ **Uzeen Update Successful!**\n\n"
                f"**New Credentials:**\n"
                f"Host: `{host or 'N/A'}`\n"
                f"Username: `{username or 'N/A'}`\n"
                f"Password: `{password or 'N/A'}`\n\n"
                f"**Changed:** {', '.join(changed_fields) if changed_fields else 'initial_setup'}\n\n"
                f"IboPlayer playlist updated!"
            )
        elif error_msg:
            response = (
                f"❌ **Update Failed**\n\n"
                f"Error: {error_msg}\n\n"
                f"Check logs for details."
            )
        else:
            # Show abbreviated output
            response = (
                f"⚠️ **Update Completed with Warnings**\n\n"
                f"Please check the details below:\n\n"
                f"```\n{result.stdout[-500:]}\n```"
            )

        await update.message.reply_text(response, parse_mode='Markdown')
        logger.info(f"Uzeen updater completed for chat_id: {chat_id}, success: {success or no_changes}")

    except subprocess.TimeoutExpired:
        await update.message.reply_text(
            "⏱️ **Timeout**\n\n"
            "The updater script took too long (>5 minutes).\n"
            "This might indicate an issue with the M3U server.\n\n"
            "Try again later or check logs."
        )
        logger.error(f"Timeout running uzeen updater for chat_id: {chat_id}")

    except Exception as e:
        await update.message.reply_text(
            f"❌ **Error**\n\n"
            f"Failed to run updater script:\n"
            f"`{str(e)}`\n\n"
            f"Check bot logs for details."
        )
        logger.error(f"Error running uzeen updater for chat_id: {chat_id}: {e}", exc_info=True)


def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        sys.exit(1)

    if not AUTHORIZED_CHAT_IDS:
        logger.error("TELEGRAM_CHAT_ID not found in environment variables!")
        sys.exit(1)

    logger.info("="*70)
    logger.info("Zazy Telegram Bot Starting...")
    logger.info("="*70)
    logger.info(f"Authorized chat IDs: {AUTHORIZED_CHAT_IDS}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")
    logger.info(f"Poll timeout: {POLL_TIMEOUT}s")
    logger.info("Commands: /start, /help, /uzeen, /uzeen_status, /uzeen_history, /ping")
    logger.info("="*70)

    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("uzeen", run_uzeen))
    application.add_handler(CommandHandler("uzeen_status", uzeen_status))
    application.add_handler(CommandHandler("uzeen_history", uzeen_history))

    # Start the bot with long polling
    logger.info("Bot started! Polling for commands...")
    logger.info("Press Ctrl+C to stop")

    application.run_polling(
        poll_interval=POLL_INTERVAL,
        timeout=POLL_TIMEOUT,
        drop_pending_updates=True  # Ignore old messages from before bot started
    )


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n\nBot stopped by user (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
