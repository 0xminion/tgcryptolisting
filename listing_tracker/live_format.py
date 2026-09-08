"""Exact plain-text alert formatting for Telegram cron delivery."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from listing_tracker.live_models import Change, ChangeKind

_SEPARATOR = "————————————"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_WHITESPACE = re.compile(r"\s+")


def _safe_line(value: object, *, limit: int = 160) -> str:
    """Flatten hostile exchange metadata to one bounded printable line."""
    text = _CONTROL.sub(" ", str(value or ""))
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:limit]


def format_market_cap(value: Decimal | float | str | None) -> str:
    """Render market capitalization using the requested K/M/B notation."""
    if value is None:
        return "n/a"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "n/a"
    if not number.is_finite() or number < 0 or number.adjusted() > 30:
        return "n/a"

    for suffix, divisor in (
        ("T", Decimal(1000000000000)),
        ("B", Decimal(1000000000)),
        ("M", Decimal(1000000)),
        ("K", Decimal(1000)),
    ):
        if number >= divisor:
            rendered = (number / divisor).quantize(Decimal("0.1"))
            return f"{rendered.normalize():f}{suffix}"
    return f"{number.quantize(Decimal(1)).normalize():f}"


def format_change(change: Change) -> str:
    """Format one transition using the user's required six-line template."""
    verb = "lists new tokens" if change.kind is ChangeKind.LISTED else "delists tokens"
    label = _safe_line(change.source_label)
    name = _safe_line(change.asset.name or change.asset.ticker)
    ticker = _safe_line(change.asset.ticker)

    address = _safe_line(change.asset.contract_address)
    network = _safe_line(change.asset.network)
    if address:
        contract_line = f"Contract Address: {address}"
        if network:
            contract_line += f" ({network})"
    else:
        contract_line = "Contract Address: Not available"

    market_cap = format_market_cap(change.asset.market_cap)
    timestamp = change.detected_at.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"{label} {verb}:\n\n"
        f"{name} ({ticker})\n"
        f"{contract_line}\n\n"
        f"{ticker}  MarketCap: {market_cap}\n"
        f"{_SEPARATOR}\n"
        f"{timestamp}"
    )


def format_changes(changes: list[Change]) -> str:
    """Render all transitions. Empty input intentionally produces no stdout."""
    return "\n\n".join(format_change(change) for change in changes)
