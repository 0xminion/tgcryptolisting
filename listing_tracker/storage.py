"""Atomic JSON snapshot storage with daily journal, staleness tracking, and retention."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from listing_tracker import config as _config

logger = logging.getLogger(__name__)


# --- File Locking ---


@contextmanager
def _locked_file(path: Path, mode: str = "r"):
    """Context manager for exclusive file locking via fcntl.

    Acquires an exclusive (LOCK_EX) lock on the file and releases on exit.
    For read operations the lock is shared (LOCK_SH) among readers.
    """
    is_write = "w" in mode or "a" in mode
    lock_path = path.with_suffix(path.suffix + ".lock")

    # Ensure parent dir exists for the lock file
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX if is_write else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        lock_path.unlink(missing_ok=True)


# --- Directory Setup ---


def ensure_dirs() -> None:
    """Create storage directories if they don't exist."""
    _config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


# --- Snapshot Storage ---


def snapshot_path(exchange: str, market_type: str = "spot") -> Path:
    return _config.SNAPSHOT_DIR / f"{exchange}_{market_type}.json"


def load_snapshot(path: Path) -> dict | None:
    """Load a snapshot file. Returns None if missing or corrupt."""
    if not path.exists():
        return None
    try:
        with _locked_file(path, "r"):
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupt snapshot %s: %s — treating as first run", path, e)
        return None


def save_snapshot(path: Path, data: dict) -> None:
    """Atomically save a snapshot with backup rotation.

    All operations happen inside the lock to prevent races:
    1. Write to temp file
    2. Copy current to .bak
    3. os.rename() temp -> target (atomic on POSIX)
    """
    ensure_dirs()
    tmp = path.with_suffix(".tmp")
    backup = path.with_suffix(".bak")

    with _locked_file(path, "w"):
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
                "listing_type": getattr(info, "listing_type", "S").value if hasattr(getattr(info, "listing_type", "S"), "value") else "S",
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
        with _locked_file(path, "r"):
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt journal %s — returning empty", path)
        return []


def append_journal(entries: list[dict], date: datetime | None = None) -> None:
    """Append new listing entries to today's journal (append-only, locked)."""
    if not entries:
        return
    ensure_dirs()
    path = journal_path(date)

    with _locked_file(path, "a"):
        # Re-read inside the lock to avoid lost updates
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.extend(entries)

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2, default=str))
        os.rename(str(tmp), str(path))


# --- Journal Retention ---


def cleanup_old_journals() -> int:
    """Delete journal files older than JOURNAL_RETENTION_DAYS.

    Returns the number of files deleted.
    """
    if not _config.JOURNAL_DIR.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=_config.JOURNAL_RETENTION_DAYS)
    deleted = 0

    for path in _config.JOURNAL_DIR.iterdir():
        if not path.name.startswith("journal_") or not path.name.endswith(".json"):
            continue
        try:
            date_str = path.name.replace("journal_", "").replace(".json", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                path.unlink()
                deleted += 1
                logger.info("Deleted stale journal: %s", path.name)
        except (ValueError, OSError) as e:
            logger.warning("Could not process journal file %s: %s", path.name, e)

    return deleted


# --- Staleness Tracking ---


def staleness_path() -> Path:
    return _config.SNAPSHOT_DIR / "_staleness.json"


def load_staleness() -> dict[str, int]:
    """Load staleness counters: exchange -> consecutive_empty_polls."""
    path = staleness_path()
    if not path.exists():
        return {}
    try:
        with _locked_file(path, "r"):
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def update_staleness(exchange: str, has_new_listings: bool) -> int:
    """Update staleness counter for an exchange. Returns current count."""
    path = staleness_path()

    with _locked_file(path, "w"):
        if path.exists():
            try:
                counters = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                counters = {}
        else:
            counters = {}

        if has_new_listings:
            counters[exchange] = 0
        else:
            counters[exchange] = counters.get(exchange, 0) + 1

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(counters, indent=2))
        os.rename(str(tmp), str(path))

    return counters.get(exchange, 0)
