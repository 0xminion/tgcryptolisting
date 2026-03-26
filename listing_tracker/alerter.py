"""Real-time push alerts for new listings via hermes send_message."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from listing_tracker.formatter import format_realtime_alert

logger = logging.getLogger(__name__)


def send_telegram_message(message: str) -> bool:
    """Send a message via hermes send_message tool.

    Writes the message to a temp file and passes the file path to hermes
    to avoid prompt injection via exchange-sourced content in the CLI arg.
    Returns True on success.
    """
    if not message:
        return True

    import shutil as _shutil
    if not _shutil.which("hermes"):
        logger.error("hermes binary not found in PATH — cannot send alerts")
        return False

    try:
        # Write message to temp file to isolate content from the prompt
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False
        ) as f:
            f.write(message)
            msg_path = f.name

        result = subprocess.run(
            [
                "hermes", "chat", "--quiet",
                f"Read the file at {msg_path} and send its exact contents "
                f"to Telegram using the send_message tool with parse_mode=HTML. "
                f"Do not modify the content.",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Clean up temp file
        Path(msg_path).unlink(missing_ok=True)

        if result.returncode != 0:
            logger.error("hermes send failed: %s", result.stderr)
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error("hermes send error: %s", e)
        return False


def push_realtime_alerts(new_listings: list[dict], max_retries: int = 3) -> bool:
    """Send real-time Telegram alerts for newly detected listings.

    Args:
        new_listings: List of listing dicts with exchange, symbol, listing_type
        max_retries: Number of retry attempts on failure

    Returns:
        True if sent successfully
    """
    if not new_listings:
        return True

    message = format_realtime_alert(new_listings)
    if not message:
        return True

    for attempt in range(max_retries):
        if send_telegram_message(message):
            logger.info("Real-time alert sent: %d listings", len(new_listings))
            return True
        logger.warning("Alert send attempt %d/%d failed", attempt + 1, max_retries)

    logger.error("Failed to send real-time alert after %d attempts", max_retries)
    return False


def send_daily_report(messages: list[str], max_retries: int = 3) -> bool:
    """Send the daily digest report (may be multiple messages if split).

    Args:
        messages: List of HTML message strings
        max_retries: Number of retry attempts per message

    Returns:
        True if all messages sent successfully
    """
    all_ok = True
    for i, message in enumerate(messages):
        sent = False
        for attempt in range(max_retries):
            if send_telegram_message(message):
                sent = True
                break
            logger.warning("Report message %d send attempt %d/%d failed",
                          i + 1, attempt + 1, max_retries)
        if not sent:
            logger.error("Failed to send report message %d after %d attempts",
                        i + 1, max_retries)
            all_ok = False
    return all_ok
