"""Main orchestrator for listing tracker — poll, report, and check modes."""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from listing_tracker.config import (
    EXCHANGES,
    STALENESS_THRESHOLD_POLLS,
    ADAPTER_TIMEOUT_SECONDS,
)
from listing_tracker.exchanges.base import (
    AdapterError,
    AdapterRegistry,
    BaseAdapter,
    CcxtAdapter,
    ListingType,
)
from listing_tracker import storage
from listing_tracker.differ import NewListing, compare_snapshots, deduplicate_listings
from listing_tracker.formatter import format_daily_report, format_realtime_alert
from listing_tracker.alerter import push_realtime_alerts, send_daily_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_adapter(exchange_name: str) -> BaseAdapter:
    """Create the appropriate adapter for an exchange using the registry."""
    config = EXCHANGES[exchange_name]
    if config.adapter_type == "ccxt":
        return CcxtAdapter(config)
    # Custom adapters registered in AdapterRegistry
    return AdapterRegistry.get(exchange_name, config)


async def fetch_exchange(adapter: BaseAdapter) -> tuple[str, dict | AdapterError]:
    """Fetch instruments from a single exchange with timeout."""
    try:
        instruments = await asyncio.wait_for(
            adapter.fetch_instruments(),
            timeout=ADAPTER_TIMEOUT_SECONDS,
        )
        snapshot = storage.build_snapshot(instruments)
        return adapter.exchange_name, snapshot
    except asyncio.TimeoutError:
        return adapter.exchange_name, AdapterError(f"{adapter.exchange_name}: timeout after {ADAPTER_TIMEOUT_SECONDS}s")
    except AdapterError as e:
        return adapter.exchange_name, e
    except Exception as e:
        return adapter.exchange_name, AdapterError(f"{adapter.exchange_name}: unexpected error: {e}")


async def poll() -> list[NewListing]:
    """Poll all exchanges, diff against stored snapshots, return new listings."""
    storage.ensure_dirs()

    # Clean up old journals on every poll (fast on empty dir, prevents accumulation)
    storage.cleanup_old_journals()

    adapters = []
    for name in EXCHANGES:
        try:
            adapters.append(create_adapter(name))
        except Exception as e:
            logger.error("Failed to create adapter for %s: %s", name, e)

    # Fetch all exchanges in parallel
    tasks = [fetch_exchange(adapter) for adapter in adapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_new_listings: list[NewListing] = []

    for result in results:
        if isinstance(result, Exception):
            logger.error("Unexpected gather error: %s", result)
            continue

        exchange_name, snapshot_or_error = result

        if isinstance(snapshot_or_error, AdapterError):
            logger.error("Adapter error: %s", snapshot_or_error)
            # Don't update staleness on adapter errors — staleness tracks
            # "no new listings" not "adapter broken"
            continue

        current_snapshot = snapshot_or_error

        snap_path = storage.snapshot_path(exchange_name, "all")
        previous = storage.load_snapshot(snap_path)

        new_listings = compare_snapshots(exchange_name, previous, current_snapshot)
        storage.save_snapshot(snap_path, current_snapshot)

        # Track staleness based on whether the adapter returned any symbols
        # at all (empty = possible API issue), not whether new listings were
        # found (which is the normal state for most polls).
        current_symbol_count = len(current_snapshot.get("symbols", {}))
        stale_count = storage.update_staleness(
            exchange_name, has_new_listings=current_symbol_count > 0,
        )

        if stale_count >= STALENESS_THRESHOLD_POLLS:
            logger.warning("%s: returned 0 symbols for %d consecutive polls", exchange_name, stale_count)

        all_new_listings.extend(new_listings)

    # Close all adapters
    for adapter in adapters:
        try:
            await adapter.close()
        except Exception:
            pass

    # Deduplicate
    all_new_listings = deduplicate_listings(all_new_listings)

    # Append to journal
    if all_new_listings:
        journal_entries = [
            {
                "exchange": nl.exchange,
                "symbol": nl.symbol,
                "base": nl.base,
                "quote": nl.quote,
                "listing_type": nl.listing_type.value,
                "key": nl.key,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
            for nl in all_new_listings
        ]
        storage.append_journal(journal_entries)
        logger.info("Appended %d new listings to journal", len(all_new_listings))

    return all_new_listings


async def report() -> list[str]:
    """Generate the daily digest report from the last 24 hours of journal entries."""
    now = datetime.now(timezone.utc)
    today_entries = storage.load_journal(now)
    yesterday_entries = storage.load_journal(now - timedelta(days=1))

    # Clean up old journals AFTER reading to avoid deleting data we need
    deleted = storage.cleanup_old_journals()
    if deleted:
        logger.info("Cleaned up %d stale journal file(s)", deleted)

    # Filter to last 24 hours using proper datetime comparison
    cutoff = now - timedelta(hours=24)
    all_entries = yesterday_entries + today_entries
    recent = []
    for e in all_entries:
        detected_str = e.get("detected_at", "")
        if not detected_str:
            continue
        try:
            detected = datetime.fromisoformat(detected_str)
            if detected >= cutoff:
                recent.append(e)
        except (ValueError, TypeError):
            # If we can't parse the timestamp, include it to avoid data loss
            recent.append(e)

    # Group by exchange
    listings_by_exchange: dict[str, list[dict]] = {}
    for entry in recent:
        ex = entry.get("exchange", "unknown")
        listings_by_exchange.setdefault(ex, []).append(entry)

    # Get staleness
    staleness = storage.load_staleness()
    # Build errors dict from staleness for formatter
    errors: dict[str, str] = {}
    for ex, count in staleness.items():
        if count >= STALENESS_THRESHOLD_POLLS:
            errors[ex] = f"stale ({count} consecutive polls)"

    messages = format_daily_report(listings_by_exchange, errors, staleness, now)
    return messages


async def check() -> None:
    """One-shot check: poll and print results to stdout."""
    new_listings = await poll()

    if not new_listings:
        print("No new listings detected.")
        return

    print(f"Found {len(new_listings)} new listing(s):")
    for nl in new_listings:
        print(f"  {nl.exchange}: {nl.symbol} ({nl.listing_type.value})")


async def run_poll() -> None:
    """Poll mode: fetch, diff, alert on new listings."""
    logger.info("Starting poll cycle...")
    new_listings = await poll()

    if new_listings:
        # Convert to dicts for alerter
        listing_dicts = [
            {
                "exchange": nl.exchange,
                "symbol": nl.symbol,
                "listing_type": nl.listing_type.value,
            }
            for nl in new_listings
        ]
        push_realtime_alerts(listing_dicts)
    else:
        logger.info("No new listings detected this cycle.")


async def run_report() -> None:
    """Report mode: generate and send daily digest."""
    logger.info("Generating daily report...")
    messages = await report()
    if messages:
        send_daily_report(messages)
        logger.info("Daily report sent (%d message(s))", len(messages))
    else:
        logger.warning("No messages generated for daily report")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes Listing Tracker — track new crypto exchange listings"
    )
    parser.add_argument(
        "mode",
        choices=["poll", "report", "check"],
        help="poll: fetch & alert | report: daily digest | check: one-shot stdout",
    )
    args = parser.parse_args()

    if args.mode == "poll":
        asyncio.run(run_poll())
    elif args.mode == "report":
        asyncio.run(run_report())
    elif args.mode == "check":
        asyncio.run(check())


if __name__ == "__main__":
    main()
