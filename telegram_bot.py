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
        "🤖 <b>Zazy Telegram Bot</b>\n\n"
        "Welcome! I can help you manage the Uzeen playlist updater.\n\n"
        "<b>Available Commands:</b>\n"
        "/uzeen - Run uzeen playlist updater now\n"
        "/uzeen_status - Show current credentials\n"
        "/uzeen_history - Show credential change history\n"
        "/help - Show this help message\n"
        "/ping - Check if bot is alive\n\n"
        "The bot also runs automatically via cron every 6 hours."
    )

    await update.message.reply_text(welcome_message, parse_mode='HTML')
    logger.info(f"Sent welcome message to chat_id: {chat_id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message when /help command is issued."""
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    help_message = (
        "📚 <b>Zazy Bot Commands</b>\n\n"
        "<b>/uzeen</b> - Trigger uzeen playlist updater\n"
        "  • Fetches latest M3U file\n"
        "  • Extracts Xtream credentials\n"
        "  • Updates IboPlayer if changed\n\n"
        "<b>/uzeen_status</b> - Check current status\n"
        "  • Shows current credentials\n"
        "  • Last update timestamp\n"
        "  • Playlist ID\n\n"
        "<b>/uzeen_history</b> - View change history\n"
        "  • Credential change timeline\n"
        "  • Time between changes\n"
        "  • Change patterns\n\n"
        "<b>/ping</b> - Bot health check\n\n"
        "<b>/help</b> - Show this message\n\n"
        "<b>Note:</b> Automated updates run every 6 hours via cron."
    )

    await update.message.reply_text(help_message, parse_mode='HTML')


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
            f"📋 <b>Uzeen Playlist Status</b>\n\n"
            f"<b>Host:</b> <code>{state.get('playlist_url', 'N/A')}</code>\n"
            f"<b>Username:</b> <code>{state.get('username', 'N/A')}</code>\n"
            f"<b>Password:</b> <code>{state.get('password', 'N/A')}</code>\n"
            f"<b>Playlist ID:</b> <code>{state.get('playlist_id', 'N/A')}</code>\n\n"
            f"<b>Last Updated:</b> {last_updated.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"<i>({hours_ago}h {minutes_ago}m ago)</i>\n\n"
            f"✅ Playlist is configured"
        )

        await update.message.reply_text(status_message, parse_mode='HTML')
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
        history_lines = [f"📊 <b>Credential Change History</b>\n"]

        for i, change in enumerate(reversed(changes[-5:]), 1):
            timestamp = datetime.fromisoformat(change['timestamp'])
            history_lines.append(
                f"\n<b>{len(changes) - i + 1}.</b> {timestamp.strftime('%Y-%m-%d %H:%M')}\n"
                f"   Host: <code>{change['playlist_url']}</code>\n"
                f"   User: <code>{change['username']}</code>\n"
                f"   Pass: <code>{change['password']}</code>"
            )

        if len(changes) > 5:
            history_lines.append(f"\n\n<i>Showing last 5 of {len(changes)} total changes</i>")
        else:
            history_lines.append(f"\n\n<i>Total changes: {len(changes)}</i>")

        await update.message.reply_text('\n'.join(history_lines), parse_mode='HTML')
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
    await update.message.reply_text("🔄 <b>Running Uzeen Playlist Updater...</b>\n\nPlease wait, this may take 30-60 seconds.", parse_mode='HTML')
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
                f"✅ <b>Uzeen Update Complete</b>\n\n"
                f"ℹ️ No changes detected\n"
                f"Playlist is already up to date!\n\n"
                f"<b>Current Credentials:</b>\n"
                f"Host: <code>{host or 'N/A'}</code>\n"
                f"Username: <code>{username or 'N/A'}</code>\n"
                f"Password: <code>{password or 'N/A'}</code>"
            )
        elif success:
            response = (
                f"✅ <b>Uzeen Update Successful!</b>\n\n"
                f"<b>New Credentials:</b>\n"
                f"Host: <code>{host or 'N/A'}</code>\n"
                f"Username: <code>{username or 'N/A'}</code>\n"
                f"Password: <code>{password or 'N/A'}</code>\n\n"
                f"<b>Changed:</b> {', '.join(changed_fields) if changed_fields else 'initial_setup'}\n\n"
                f"IboPlayer playlist updated!"
            )
        elif error_msg:
            response = (
                f"❌ <b>Update Failed</b>\n\n"
                f"Error: {error_msg}\n\n"
                f"Check logs for details."
            )
        else:
            # Show abbreviated output
            response = (
                f"⚠️ <b>Update Completed with Warnings</b>\n\n"
                f"Please check the details below:\n\n"
                f"<pre>{result.stdout[-500:]}</pre>"
            )

        await update.message.reply_text(response, parse_mode='HTML')
        logger.info(f"Uzeen updater completed for chat_id: {chat_id}, success: {success or no_changes}")

    except subprocess.TimeoutExpired:
        await update.message.reply_text(
            "⏱️ <b>Timeout</b>\n\n"
            "The updater script took too long (&gt;5 minutes).\n"
            "This might indicate an issue with the M3U server.\n\n"
            "Try again later or check logs.",
            parse_mode='HTML'
        )
        logger.error(f"Timeout running uzeen updater for chat_id: {chat_id}")

    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Error</b>\n\n"
            f"Failed to run updater script:\n"
            f"<code>{str(e)}</code>\n\n"
            f"Check bot logs for details.",
            parse_mode='HTML'
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
