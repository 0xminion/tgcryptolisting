"""Atomic JSON snapshot storage with daily journal and staleness tracking."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from listing_tracker import config as _config

logger = logging.getLogger(__name__)


def ensure_dirs() -> None:
    """Create storage directories if they don't exist."""
    _config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


def snapshot_path(exchange: str, market_type: str = "spot") -> Path:
    return _config.SNAPSHOT_DIR / f"{exchange}_{market_type}.json"


def load_snapshot(path: Path) -> dict | None:
    """Load a snapshot file. Returns None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupt snapshot %s: %s — treating as first run", path, e)
        return None


def save_snapshot(path: Path, data: dict) -> None:
    """Atomically save a snapshot with backup rotation.

    1. Write to temp file
    2. Copy current to .bak
    3. os.rename() temp -> target (atomic on POSIX)
    """
    ensure_dirs()
    tmp = path.with_suffix(".tmp")
    backup = path.with_suffix(".bak")

    tmp.write_text(json.dumps(data, indent=2, default=str))

    if path.exists():
        shutil.copy2(path, backup)

    os.rename(str(tmp), str(path))


def build_snapshot(instruments: dict) -> dict:
    """Build a snapshot dict from instruments."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": {
            key: {
                "symbol": info.symbol if hasattr(info, "symbol") else str(info),
                "base": getattr(info, "base", ""),
                "quote": getattr(info, "quote", ""),
                "listing_type": getattr(info, "listing_type", "S"),
                "status": getattr(info, "status", ""),
                "list_time": getattr(info, "list_time", None),
            }
            for key, info in instruments.items()
        },
    }


# --- Daily Journal ---


def journal_path(date: datetime | None = None) -> Path:
    """Path to today's journal file."""
    if date is None:
        date = datetime.now(timezone.utc)
    return _config.JOURNAL_DIR / f"journal_{date.strftime('%Y-%m-%d')}.json"


def load_journal(date: datetime | None = None) -> list[dict]:
    """Load journal entries for a given date."""
    path = journal_path(date)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt journal %s — returning empty", path)
        return []


def append_journal(entries: list[dict], date: datetime | None = None) -> None:
    """Append new listing entries to today's journal (append-only)."""
    if not entries:
        return
    ensure_dirs()
    existing = load_journal(date)
    existing.extend(entries)
    path = journal_path(date)
    # Atomic write for journal too
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=2, default=str))
    os.rename(str(tmp), str(path))


# --- Staleness Tracking ---


def staleness_path() -> Path:
    return _config.SNAPSHOT_DIR / "_staleness.json"


def load_staleness() -> dict[str, int]:
    """Load staleness counters: exchange -> consecutive_empty_polls."""
    path = staleness_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def update_staleness(exchange: str, has_new_listings: bool) -> int:
    """Update staleness counter for an exchange. Returns current count."""
    counters = load_staleness()
    if has_new_listings:
        counters[exchange] = 0
    else:
        counters[exchange] = counters.get(exchange, 0) + 1

    path = staleness_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(counters, indent=2))
    os.rename(str(tmp), str(path))

    return counters[exchange]
