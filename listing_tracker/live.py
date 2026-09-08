"""Deterministic cron entry point for live exchange listing monitoring."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from listing_tracker.live_delivery import send_message
from listing_tracker.live_format import format_change
from listing_tracker.live_models import Change, Snapshot
from listing_tracker.live_sources import fetch_snapshots, source_ids
from listing_tracker.live_state import SnapshotRejected, StateStore

DEFAULT_DB = Path.home() / ".local/state/token-listing-tracker/listings.db"
DEFAULT_TIMEZONE = "Australia/Perth"
Sender = Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class PollResult:
    snapshots: int
    source_errors: dict[str, str]
    new_changes: int
    pending_before_delivery: int
    delivered: int
    delivery_error: str | None


def _now(timezone_name: str) -> datetime:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid IANA timezone: {timezone_name}") from exc
    return datetime.now(timezone)


def apply_snapshots(
    store: StateStore,
    snapshots: list[Snapshot],
    source_errors: dict[str, str],
    detected_at: datetime,
) -> tuple[list[Change], dict[str, str]]:
    """Persist independent sources while rejecting malformed partial snapshots."""
    changes: list[Change] = []
    errors = dict(source_errors)
    for snapshot in snapshots:
        try:
            changes.extend(store.apply(snapshot, detected_at))
        except SnapshotRejected as exc:
            errors[snapshot.source_id] = str(exc)
            store.record_failure(snapshot.source_id)
    for source_id in source_errors:
        store.record_failure(source_id)
    return changes, errors


def deliver_pending(
    store: StateStore,
    target: str,
    detected_at: datetime,
    *,
    sender: Sender = send_message,
    limit: int = 20,
) -> tuple[int, str | None]:
    """Deliver one event per message and acknowledge only a verified receipt."""
    delivered = 0
    for _ in range(limit):
        claimed = store.claim_pending(detected_at, limit=1)
        if not claimed:
            break
        event = claimed[0]
        if event.lease_token is None:
            raise RuntimeError("claimed event has no lease token")
        try:
            sender(format_change(event), target)
        except Exception as exc:  # noqa: BLE001 - preserve any sender failure in outbox
            error = f"{type(exc).__name__}: {exc}"
            store.mark_delivery_failure(event.event_id, event.lease_token, error)
            return delivered, error
        store.mark_delivered(event.event_id, event.lease_token, detected_at)
        delivered += 1
    return delivered, None


async def run_poll(
    *,
    db_path: Path,
    timezone_name: str,
    selected: set[str] | None,
    timeout_seconds: float,
    target: str | None,
    stdout_delivery: bool,
    min_success_ratio: float,
    sender: Sender = send_message,
) -> PollResult:
    detected_at = _now(timezone_name)
    snapshots, fetch_errors = await fetch_snapshots(
        selected=selected, timeout_seconds=timeout_seconds
    )
    store = StateStore(db_path)
    changes, source_errors = apply_snapshots(
        store, snapshots, fetch_errors, detected_at
    )
    pending = store.pending_changes(limit=20)
    delivered = 0
    delivery_error = None

    if target:
        delivered, delivery_error = deliver_pending(
            store, target, detected_at, sender=sender
        )
    elif stdout_delivery and pending:
        delivered, delivery_error = deliver_pending(
            store,
            "stdout",
            detected_at,
            sender=lambda message, _target: print(message, flush=True),
        )

    selected_count = len(selected) if selected is not None else len(source_ids())
    accepted_snapshots = sum(
        snapshot.source_id not in source_errors for snapshot in snapshots
    )
    success_ratio = accepted_snapshots / selected_count if selected_count else 0.0
    if source_errors:
        for source_id, error in sorted(source_errors.items()):
            print(f"source_error {source_id}: {error}", file=sys.stderr)
    if delivery_error:
        print(f"delivery_error: {delivery_error}", file=sys.stderr)
    if success_ratio < min_success_ratio:
        print(
            f"health_error: only {len(snapshots)}/{selected_count} sources succeeded",
            file=sys.stderr,
        )

    return PollResult(
        snapshots=accepted_snapshots,
        source_errors=source_errors,
        new_changes=len(changes),
        pending_before_delivery=len(pending),
        delivered=delivered,
        delivery_error=delivery_error,
    )


async def run_probe(
    *, selected: set[str] | None, timeout_seconds: float
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    snapshots, errors = await fetch_snapshots(
        selected=selected, timeout_seconds=timeout_seconds
    )
    report = [
        {
            "source_id": snapshot.source_id,
            "source_label": snapshot.source_label,
            "asset_count": len(snapshot.assets),
            "active_count": sum(item.active for item in snapshot.assets.values()),
        }
        for snapshot in snapshots
    ]
    return report, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Poll exchange token universes for listing and delisting transitions"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_fetch_options(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--source",
            action="append",
            choices=source_ids(),
            help="Limit to a source; repeat to select multiple",
        )
        command.add_argument("--timeout", type=float, default=20.0)

    poll = subparsers.add_parser("poll", help="Poll, persist, and deliver changes")
    add_fetch_options(poll)
    poll.add_argument("--db", type=Path, default=DEFAULT_DB)
    poll.add_argument(
        "--timezone",
        default=os.getenv("LISTING_TRACKER_TIMEZONE", DEFAULT_TIMEZONE),
    )
    poll.add_argument(
        "--target",
        default=os.getenv("LISTING_TRACKER_TARGET"),
        help="Receipt-bindable Hermes target such as telegram:1234",
    )
    poll.add_argument(
        "--stdout-delivery",
        action="store_true",
        help="Print and acknowledge pending alerts instead of self-delivery",
    )
    poll.add_argument("--min-success-ratio", type=float, default=1.0)

    probe = subparsers.add_parser("probe", help="Fetch all sources without state")
    add_fetch_options(probe)

    status = subparsers.add_parser("status", help="Inspect source and outbox state")
    status.add_argument("--db", type=Path, default=DEFAULT_DB)
    status.add_argument("--pending-limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected = set(args.source) if getattr(args, "source", None) else None

    if args.command == "probe":
        report, errors = asyncio.run(
            run_probe(selected=selected, timeout_seconds=args.timeout)
        )
        print(
            json.dumps(
                {"sources": report, "errors": errors},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        expected = len(selected) if selected is not None else len(source_ids())
        return 0 if len(report) == expected else 2

    if args.command == "status":
        store = StateStore(args.db)
        print(
            json.dumps(
                {
                    "sources": store.source_status(),
                    "pending": [
                        {
                            "event_id": item.event_id,
                            "source_id": item.source_id,
                            "kind": item.kind.value,
                            "ticker": item.asset.ticker,
                            "detected_at": item.detected_at.isoformat(),
                        }
                        for item in store.pending_changes(limit=args.pending_limit)
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    if not args.target and not args.stdout_delivery:
        print(
            "configuration_error: set LISTING_TRACKER_TARGET/--target or "
            "--stdout-delivery",
            file=sys.stderr,
        )
        return 2
    if not 0 < args.min_success_ratio <= 1:
        print(
            "configuration_error: min success ratio must be in (0, 1]", file=sys.stderr
        )
        return 2

    result = asyncio.run(
        run_poll(
            db_path=args.db,
            timezone_name=args.timezone,
            selected=selected,
            timeout_seconds=args.timeout,
            target=args.target,
            stdout_delivery=args.stdout_delivery,
            min_success_ratio=args.min_success_ratio,
        )
    )
    expected = len(selected) if selected is not None else len(source_ids())
    success_ratio = result.snapshots / expected if expected else 0.0
    if result.delivery_error:
        return 1
    if success_ratio < args.min_success_ratio:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
