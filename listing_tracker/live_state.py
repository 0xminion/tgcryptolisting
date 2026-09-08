"""SQLite state machine for verified listing and delisting transitions."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from listing_tracker.live_models import Asset, Change, ChangeKind, Snapshot


class SnapshotRejected(RuntimeError):
    """Raised when a response is too incomplete to update durable state."""


class StateStore:
    """Atomic token-level state with shrink protection and removal confirmation."""

    def __init__(
        self,
        path: str | Path,
        *,
        removal_confirmations: int = 2,
        shrink_ratio: float = 0.5,
    ) -> None:
        if removal_confirmations < 1:
            raise ValueError("removal_confirmations must be at least 1")
        if not 0 < shrink_ratio <= 1:
            raise ValueError("shrink_ratio must be in (0, 1]")
        self.path = Path(path)
        self.removal_confirmations = removal_confirmations
        self.shrink_ratio = shrink_ratio
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    source_label TEXT NOT NULL,
                    initialized_at TEXT NOT NULL,
                    last_success_at TEXT NOT NULL,
                    asset_count INTEGER NOT NULL CHECK(asset_count >= 0),
                    max_asset_count INTEGER NOT NULL CHECK(max_asset_count >= 0),
                    consecutive_failures INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS assets (
                    source_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK(active IN (0, 1)),
                    terminal INTEGER NOT NULL CHECK(terminal IN (0, 1)),
                    status TEXT NOT NULL,
                    contract_address TEXT,
                    network TEXT,
                    market_cap TEXT,
                    reference TEXT,
                    missing_streak INTEGER NOT NULL DEFAULT 0,
                    inactive_streak INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, instrument_id),
                    FOREIGN KEY (source_id) REFERENCES sources(source_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('listed', 'delisted')),
                    detected_at TEXT NOT NULL,
                    asset_json TEXT NOT NULL,
                    delivered_at TEXT,
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    last_delivery_error TEXT,
                    lease_token TEXT,
                    lease_until REAL
                );
                CREATE INDEX IF NOT EXISTS idx_events_detected_at
                    ON events(detected_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_outbox
                    ON events(delivered_at, id);
                """
            )
            self._ensure_column(
                conn,
                "sources",
                "max_asset_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(conn, "events", "lease_token", "TEXT")
            self._ensure_column(conn, "events", "lease_until", "REAL")
            conn.execute(
                """UPDATE sources SET max_asset_count = asset_count
                   WHERE max_asset_count < asset_count"""
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, declaration: str
    ) -> None:
        existing = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _validate_snapshot(snapshot: Snapshot) -> None:
        if not snapshot.source_id or not snapshot.source_label:
            raise SnapshotRejected("source id and label are required")
        if not snapshot.assets:
            raise SnapshotRejected(f"{snapshot.source_id}: empty snapshot")
        for key, item in snapshot.assets.items():
            if key != item.instrument_id:
                raise SnapshotRejected(
                    f"{snapshot.source_id}: key does not match instrument id"
                )
            if not item.instrument_id or not item.ticker:
                raise SnapshotRejected(
                    f"{snapshot.source_id}: empty instrument id or ticker"
                )

    def apply(self, snapshot: Snapshot, detected_at: datetime) -> list[Change]:
        """Apply one complete source snapshot and return committed transitions."""
        self._validate_snapshot(snapshot)
        timestamp = detected_at.isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?", (snapshot.source_id,)
            ).fetchone()

            if source is None:
                conn.execute(
                    """INSERT INTO sources
                       (source_id, source_label, initialized_at, last_success_at,
                        asset_count, max_asset_count)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot.source_id,
                        snapshot.source_label,
                        timestamp,
                        timestamp,
                        len(snapshot.assets),
                        len(snapshot.assets),
                    ),
                )
                for item in snapshot.assets.values():
                    self._upsert_asset(conn, snapshot.source_id, item, timestamp)
                conn.commit()
                return []

            previous_count = int(source["asset_count"])
            max_asset_count = max(previous_count, int(source["max_asset_count"]))
            current_count = len(snapshot.assets)
            if max_asset_count and current_count <= max_asset_count * self.shrink_ratio:
                raise SnapshotRejected(
                    f"{snapshot.source_id}: snapshot shrank from "
                    f"historical max {max_asset_count} to {current_count}"
                )

            previous_rows = {
                row["instrument_id"]: row
                for row in conn.execute(
                    "SELECT * FROM assets WHERE source_id = ?",
                    (snapshot.source_id,),
                )
            }
            changes: list[Change] = []

            for instrument_id, item in snapshot.assets.items():
                previous = previous_rows.pop(instrument_id, None)
                if previous is None:
                    self._upsert_asset(conn, snapshot.source_id, item, timestamp)
                    if item.active:
                        changes.append(
                            self._record_event(
                                conn, snapshot, item, ChangeKind.LISTED, detected_at
                            )
                        )
                    continue

                was_active = bool(previous["active"])
                missing_streak = 0
                inactive_streak = 0
                effective_active = item.active

                if item.active:
                    if not was_active:
                        changes.append(
                            self._record_event(
                                conn, snapshot, item, ChangeKind.LISTED, detected_at
                            )
                        )
                elif was_active:
                    if not item.inactive_is_removal:
                        # A present-but-unavailable product (maintenance, view-only,
                        # BREAK, offline) is not a verified delisting. Preserve the
                        # last confirmed listed state until an explicit terminal
                        # status or confirmed inventory removal arrives.
                        effective_active = True
                    else:
                        inactive_streak = int(previous["inactive_streak"]) + 1
                        confirmed = item.terminal or (
                            inactive_streak >= self.removal_confirmations
                        )
                        if confirmed:
                            effective_active = False
                            changes.append(
                                self._record_event(
                                    conn,
                                    snapshot,
                                    item,
                                    ChangeKind.DELISTED,
                                    detected_at,
                                )
                            )
                        else:
                            effective_active = True
                else:
                    effective_active = False

                stored = replace(item, active=effective_active)
                self._upsert_asset(
                    conn,
                    snapshot.source_id,
                    stored,
                    timestamp,
                    missing_streak=missing_streak,
                    inactive_streak=inactive_streak,
                )

            for instrument_id, previous in previous_rows.items():
                missing_streak = int(previous["missing_streak"]) + 1
                was_active = bool(previous["active"])
                if was_active and missing_streak >= self.removal_confirmations:
                    missing_asset = replace(
                        self._asset_from_row(previous),
                        active=False,
                        terminal=True,
                        status="MISSING",
                    )
                    changes.append(
                        self._record_event(
                            conn,
                            snapshot,
                            missing_asset,
                            ChangeKind.DELISTED,
                            detected_at,
                        )
                    )
                    self._upsert_asset(
                        conn,
                        snapshot.source_id,
                        missing_asset,
                        timestamp,
                        missing_streak=missing_streak,
                    )
                else:
                    conn.execute(
                        """UPDATE assets
                           SET missing_streak = ?, updated_at = ?
                           WHERE source_id = ? AND instrument_id = ?""",
                        (
                            missing_streak,
                            timestamp,
                            snapshot.source_id,
                            instrument_id,
                        ),
                    )

            conn.execute(
                """UPDATE sources
                   SET source_label = ?, last_success_at = ?, asset_count = ?,
                       max_asset_count = ?, consecutive_failures = 0
                   WHERE source_id = ?""",
                (
                    snapshot.source_label,
                    timestamp,
                    current_count,
                    max(max_asset_count, current_count),
                    snapshot.source_id,
                ),
            )
            conn.commit()
            return changes
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_failure(self, source_id: str) -> None:
        """Increment health state without touching instrument state."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE sources SET consecutive_failures = consecutive_failures + 1
                   WHERE source_id = ?""",
                (source_id,),
            )

    def source_status(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        return {row["source_id"]: dict(row) for row in rows}

    def recent_changes(self, *, limit: int = 100) -> list[Change]:
        if limit < 1:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._change_from_row(row) for row in reversed(rows)]

    def pending_changes(self, *, limit: int = 20) -> list[Change]:
        """Return undelivered events in creation order for durable retry."""
        if limit < 1:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE delivered_at IS NULL
                   ORDER BY id ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._change_from_row(row) for row in rows]

    def claim_pending(
        self,
        claimed_at: datetime,
        *,
        limit: int = 1,
        lease_seconds: int = 120,
    ) -> list[Change]:
        """Atomically lease oldest undelivered events to one delivery worker."""
        if limit < 1:
            return []
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        token = uuid.uuid4().hex
        now_epoch = claimed_at.timestamp()
        lease_until = (claimed_at + timedelta(seconds=lease_seconds)).timestamp()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE delivered_at IS NULL
                     AND (lease_until IS NULL OR lease_until <= ?)
                   ORDER BY id ASC LIMIT ?""",
                (now_epoch, limit),
            ).fetchall()
            if not rows:
                conn.commit()
                return []
            ids = [int(row["id"]) for row in rows]
            for event_id in ids:
                cursor = conn.execute(
                    """UPDATE events SET lease_token = ?, lease_until = ?
                       WHERE id = ? AND delivered_at IS NULL
                         AND (lease_until IS NULL OR lease_until <= ?)""",
                    (token, lease_until, event_id, now_epoch),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("delivery lease compare-and-set failed")
            conn.commit()
            return [
                replace(self._change_from_row(row), lease_token=token) for row in rows
            ]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_delivered(
        self, event_id: int | None, lease_token: str, delivered_at: datetime
    ) -> None:
        if event_id is None:
            raise ValueError("event_id is required")
        if not lease_token:
            raise ValueError("lease_token is required")
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE events
                   SET delivered_at = ?, delivery_attempts = delivery_attempts + 1,
                       last_delivery_error = NULL, lease_token = NULL,
                       lease_until = NULL
                   WHERE id = ? AND delivered_at IS NULL AND lease_token = ?""",
                (delivered_at.isoformat(), event_id, lease_token),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"event {event_id} is absent, delivered, or owned by another lease"
                )

    def mark_delivery_failure(
        self, event_id: int | None, lease_token: str, error: str
    ) -> None:
        if event_id is None:
            raise ValueError("event_id is required")
        if not lease_token:
            raise ValueError("lease_token is required")
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE events
                   SET delivery_attempts = delivery_attempts + 1,
                       last_delivery_error = ?, lease_token = NULL,
                       lease_until = NULL
                   WHERE id = ? AND delivered_at IS NULL AND lease_token = ?""",
                (str(error)[:1000], event_id, lease_token),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"event {event_id} is absent, delivered, or owned by another lease"
                )

    @staticmethod
    def _asset_json(item: Asset) -> str:
        payload = asdict(item)
        if payload["market_cap"] is not None:
            payload["market_cap"] = str(payload["market_cap"])
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _asset_from_json(value: str) -> Asset:
        payload = json.loads(value)
        market_cap = payload.get("market_cap")
        if market_cap is not None:
            try:
                payload["market_cap"] = Decimal(str(market_cap))
            except InvalidOperation:
                payload["market_cap"] = None
        return Asset(**payload)

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> Asset:
        market_cap = row["market_cap"]
        return Asset(
            instrument_id=row["instrument_id"],
            ticker=row["ticker"],
            name=row["name"],
            active=bool(row["active"]),
            terminal=bool(row["terminal"]),
            status=row["status"],
            contract_address=row["contract_address"],
            network=row["network"],
            market_cap=Decimal(market_cap) if market_cap is not None else None,
            reference=row["reference"],
        )

    def _upsert_asset(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        item: Asset,
        timestamp: str,
        *,
        missing_streak: int = 0,
        inactive_streak: int = 0,
    ) -> None:
        conn.execute(
            """INSERT INTO assets
               (source_id, instrument_id, ticker, name, active, terminal, status,
                contract_address, network, market_cap, reference, missing_streak,
                inactive_streak, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id, instrument_id) DO UPDATE SET
                 ticker=excluded.ticker,
                 name=excluded.name,
                 active=excluded.active,
                 terminal=excluded.terminal,
                 status=excluded.status,
                 contract_address=excluded.contract_address,
                 network=excluded.network,
                 market_cap=excluded.market_cap,
                 reference=excluded.reference,
                 missing_streak=excluded.missing_streak,
                 inactive_streak=excluded.inactive_streak,
                 updated_at=excluded.updated_at""",
            (
                source_id,
                item.instrument_id,
                item.ticker,
                item.name,
                int(item.active),
                int(item.terminal),
                item.status,
                item.contract_address,
                item.network,
                str(item.market_cap) if item.market_cap is not None else None,
                item.reference,
                missing_streak,
                inactive_streak,
                timestamp,
            ),
        )

    def _record_event(
        self,
        conn: sqlite3.Connection,
        snapshot: Snapshot,
        item: Asset,
        kind: ChangeKind,
        detected_at: datetime,
    ) -> Change:
        cursor = conn.execute(
            """INSERT INTO events
               (source_id, source_label, instrument_id, kind, detected_at, asset_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                snapshot.source_id,
                snapshot.source_label,
                item.instrument_id,
                kind.value,
                detected_at.isoformat(),
                self._asset_json(item),
            ),
        )
        event_id = cursor.lastrowid
        if event_id is None:
            raise RuntimeError("SQLite did not return an event id")
        return Change(
            source_id=snapshot.source_id,
            source_label=snapshot.source_label,
            kind=kind,
            asset=item,
            detected_at=detected_at,
            event_id=int(event_id),
        )

    def _change_from_row(self, row: sqlite3.Row) -> Change:
        return Change(
            source_id=row["source_id"],
            source_label=row["source_label"],
            kind=ChangeKind(row["kind"]),
            asset=self._asset_from_json(row["asset_json"]),
            detected_at=datetime.fromisoformat(row["detected_at"]),
            event_id=int(row["id"]),
            lease_token=row["lease_token"],
        )
