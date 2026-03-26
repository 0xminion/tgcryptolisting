"""Snapshot comparison and listing classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from listing_tracker.exchanges.base import ListingType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class NewListing:
    exchange: str
    symbol: str
    base: str
    quote: str
    listing_type: ListingType
    key: str  # unique identifier like "binance:spot:BTCUSDT"


def compare_snapshots(
    exchange: str,
    previous: dict | None,
    current: dict,
) -> list[NewListing]:
    """Compare two snapshots and return newly added instruments.

    Args:
        exchange: Exchange name for tagging
        previous: Previous snapshot dict (None on first run)
        current: Current snapshot dict

    Returns:
        List of NewListing objects for newly detected instruments
    """
    if previous is None:
        # First run — no previous state to compare against
        logger.info("%s: First run, saving baseline (%d symbols)", exchange, len(current.get("symbols", {})))
        return []

    prev_keys = set(previous.get("symbols", {}).keys())
    curr_keys = set(current.get("symbols", {}).keys())

    new_keys = curr_keys - prev_keys

    if not new_keys:
        return []

    new_listings: list[NewListing] = []
    for key in sorted(new_keys):
        info = current["symbols"][key]
        listing_type_raw = info.get("listing_type", "S")
        if isinstance(listing_type_raw, str):
            # Normalise compound values: "A/O" -> "A" (Alpha/Other -> Alpha)
            normalized = listing_type_raw.split("/")[0]
            try:
                listing_type = ListingType(normalized)
            except ValueError:
                listing_type = ListingType.SPOT
        else:
            listing_type = listing_type_raw

        new_listings.append(
            NewListing(
                exchange=exchange,
                symbol=info.get("symbol", key),
                base=info.get("base", ""),
                quote=info.get("quote", ""),
                listing_type=listing_type,
                key=key,
            )
        )

    logger.info("%s: Found %d new listings: %s", exchange, len(new_listings),
                ", ".join(nl.symbol for nl in new_listings))
    return new_listings


def deduplicate_listings(listings: list[NewListing]) -> list[NewListing]:
    """Remove duplicate listings (same exchange + symbol + type)."""
    seen: set[str] = set()
    unique: list[NewListing] = []
    for listing in listings:
        dedup_key = f"{listing.exchange}:{listing.symbol}:{listing.listing_type.value}"
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique.append(listing)
    return unique
