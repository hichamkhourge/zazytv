"""
Telegram Notification Module for Zazy Playlist Automation

Sends notifications to Telegram when the automation script completes or encounters errors.
"""

import os
import requests
from typing import Optional, Dict, Any
from datetime import datetime


class TelegramNotifier:
    """Handle Telegram notifications with robust error handling."""

    def __init__(self):
        """Initialize Telegram notifier with environment variables."""
        self.enabled = os.getenv('TELEGRAM_ENABLED', 'False').lower() == 'true'
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        # Validate configuration
        if self.enabled and (not self.bot_token or not self.chat_id):
            print("[!] Warning: Telegram notifications enabled but credentials missing")
            self.enabled = False

    def send_notification(
        self,
        status: str,
        message: str,
        details: Optional[str] = None,
        silent: bool = False
    ) -> bool:
        """
        Send a notification to Telegram.

        Args:
            status: Status emoji/prefix (e.g., "✓ SUCCESS", "✗ ERROR", "⚠ WARNING")
            message: Main message text
            details: Optional additional details (credentials, error trace, etc.)
            silent: If True, send notification without sound

        Returns:
            bool: True if notification sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            # Build message
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            full_message = f"🤖 <b>Zazy Automation</b>\n\n"
            full_message += f"<b>{status}</b>\n"
            full_message += f"<i>{timestamp}</i>\n\n"
            full_message += f"{message}"

            if details:
                full_message += f"\n\n<b>Details:</b>\n<code>{details}</code>"

            # Prepare payload
            payload = {
                'chat_id': self.chat_id,
                'text': full_message,
                'parse_mode': 'HTML',
                'disable_notification': silent
            }

            # Send request with timeout
            response = requests.post(
                self.api_url,
                json=payload,
                timeout=10
            )

            if response.status_code == 200:
                return True
            else:
                print(f"[!] Telegram notification failed: {response.status_code} - {response.text}")
                return False

        except requests.exceptions.Timeout:
            print("[!] Telegram notification timeout (network issue)")
            return False
        except requests.exceptions.RequestException as e:
            print(f"[!] Telegram notification error: {e}")
            return False
        except Exception as e:
            print(f"[!] Unexpected error sending Telegram notification: {e}")
            return False

    def notify_start(self) -> bool:
        """Send notification when automation starts."""
        return self.send_notification(
            status="🚀 STARTED",
            message="Automation script has begun execution",
            silent=True
        )

    def notify_success(self, m3u_url: Optional[str] = None, username: Optional[str] = None, balance: Optional[str] = None) -> bool:
        """Send notification when automation completes successfully."""
        details_text = ""
        if username:
            details_text += f"Username: {username}\n"
        if m3u_url:
            details_text += f"M3U URL: {m3u_url[:50]}...\n"
        if balance:
            details_text += f"\n💰 2captcha Balance: ${balance}"

        return self.send_notification(
            status="✅ SUCCESS",
            message="Automation completed successfully!\nM3U playlist extracted and saved to IBO Player.",
            details=details_text.strip() if details_text else None
        )

    def notify_error(self, error_message: str, traceback_str: Optional[str] = None, balance: Optional[str] = None) -> bool:
        """Send notification when automation encounters an error."""
        # Limit traceback to last 500 characters to avoid message limits
        details = traceback_str[-500:] if traceback_str and len(traceback_str) > 500 else traceback_str

        # Add balance if available
        if balance:
            balance_info = f"\n\n💰 2captcha Balance: ${balance}"
            if details:
                details += balance_info
            else:
                details = balance_info.strip()

        return self.send_notification(
            status="❌ ERROR",
            message=f"Automation failed with error:\n{error_message}",
            details=details
        )

    def notify_warning(self, warning_message: str, details: Optional[str] = None) -> bool:
        """Send notification for warnings (e.g., CAPTCHA failure, retry needed)."""
        return self.send_notification(
            status="⚠️ WARNING",
            message=warning_message,
            details=details,
            silent=True
        )

    def notify_m3u_extracted(self, m3u_url: str, username: str, password: str) -> bool:
        """Send notification when M3U credentials are extracted."""
        details = f"URL: {m3u_url}\nUsername: {username}\nPassword: {password}"
        return self.send_notification(
            status="📥 M3U EXTRACTED",
            message="Successfully extracted M3U playlist credentials",
            details=details,
            silent=True
        )

    def notify_ibo_saved(self) -> bool:
        """Send notification when playlist is saved to IBO Player."""
        return self.send_notification(
            status="💾 IBO SAVED",
            message="Playlist successfully uploaded to IBO Player",
            silent=True
        )

    def notify_ibo_failed(self) -> bool:
        """Send notification when IBO Player save fails."""
        return self.send_notification(
            status="⚠️ IBO SAVE FAILED",
            message="Failed to save playlist to IBO Player. Check logs for details.",
            silent=False
        )


# Global instance for easy import
notifier = TelegramNotifier()


def send_notification(status: str, message: str, details: Optional[str] = None) -> bool:
    """
    Convenience function for sending notifications.

    Usage:
        from telegram_notifier import send_notification
        send_notification("✓ SUCCESS", "Task completed", "Additional details")
    """
    return notifier.send_notification(status, message, details)
