"""Telegram message formatting for listing reports."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from listing_tracker.config import EXCHANGE_PRIORITY, EXCHANGES, STALENESS_THRESHOLD_POLLS, TELEGRAM_MAX_MESSAGE_LENGTH

logger = logging.getLogger(__name__)


def format_daily_report(
    listings_by_exchange: dict[str, list[dict]],
    errors: dict[str, str],
    staleness: dict[str, int],
    date: datetime | None = None,
) -> list[str]:
    """Format the daily digest as HTML for Telegram.

    Args:
        listings_by_exchange: {exchange_name: [listing_dicts]}
        errors: {exchange_name: error_message} for failed adapters
        staleness: {exchange_name: consecutive_empty_count}
        date: Report date (defaults to now UTC)

    Returns:
        List of HTML message strings (split if > 4096 chars)
    """
    if date is None:
        date = datetime.now(timezone.utc)

    date_str = date.strftime("%d/%m/%Y")

    # Build exchange rows
    has_listings: list[str] = []
    no_listings: list[str] = []

    for exchange_name in EXCHANGE_PRIORITY:
        config = EXCHANGES.get(exchange_name)
        display = config.display_name if config else exchange_name.capitalize()

        if exchange_name in errors:
            row = _format_row(display, "ERROR")
            no_listings.append(row)
            continue

        exchange_listings = listings_by_exchange.get(exchange_name, [])

        if not exchange_listings:
            suffix = ""
            stale_count = staleness.get(exchange_name, 0)
            if stale_count >= STALENESS_THRESHOLD_POLLS:
                suffix = " (stale)"
            row = _format_row(display, f"n/a{suffix}")
            no_listings.append(row)
        else:
            listing_str = _format_listings(exchange_listings)
            row = _format_row(display, listing_str)
            has_listings.append(row)

    # Assemble message
    header = f"<b>--------- Listing Daily Report {date_str} ---------</b>"
    col_header = f"{'Exchange':<10}| Listing"
    separator = "----------+---------------------------"

    lines = [header, "<pre>", col_header, separator]

    if has_listings:
        lines.extend(has_listings)
        lines.append(separator)

    lines.extend(no_listings)
    lines.append("</pre>")
    lines.append("<i>S=Spot F=Futures R=Roadmap A/O=Alpha/Other</i>")

    full_message = "\n".join(lines)

    # Split if too long
    return _split_message(full_message)


def format_realtime_alert(listings: list[dict]) -> str:
    """Format a real-time alert for newly detected listings."""
    if not listings:
        return ""

    lines = ["<b>New Listing Detected!</b>"]
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    for listing in listings:
        exchange = listing.get("exchange", "Unknown")
        config = EXCHANGES.get(exchange)
        display = config.display_name if config else exchange.capitalize()
        symbol = html.escape(listing.get("symbol", ""))
        lt = listing.get("listing_type", "S")
        lines.append(f"<b>{display}:</b> <code>{symbol}</code> ({lt})")

    lines.append(f"<i>Detected at {now}</i>")
    return "\n".join(lines)


def _format_row(exchange: str, listing_text: str) -> str:
    """Format a single exchange row with padding."""
    return f"{exchange:<10}| {listing_text}"


def _format_listings(listings: list[dict]) -> str:
    """Format a list of listings into a comma-separated string."""
    parts = []
    for listing in listings:
        symbol = html.escape(listing.get("symbol", ""))
        lt = listing.get("listing_type", "S")
        parts.append(f"{symbol} ({lt})")
    return ", ".join(parts)


def _split_message(message: str) -> list[str]:
    """Split a message into chunks that fit Telegram's 4096 char limit."""
    if len(message) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return [message]

    # Split at line boundaries
    lines = message.split("\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > TELEGRAM_MAX_MESSAGE_LENGTH and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks
