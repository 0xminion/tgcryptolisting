"""Tests for atomic storage, journal, and staleness tracking."""

import json

from listing_tracker import storage


def test_save_and_load_snapshot(tmp_data_dir):
    """Atomic save + load round-trip."""
    path = storage.snapshot_path("test", "spot")
    data = {"timestamp": "2026-03-26T00:00:00Z", "symbols": {"a": {"symbol": "A"}}}

    storage.save_snapshot(path, data)
    loaded = storage.load_snapshot(path)

    assert loaded is not None
    assert loaded["symbols"]["a"]["symbol"] == "A"


def test_load_missing_snapshot(tmp_data_dir):
    """Missing snapshot returns None."""
    path = storage.snapshot_path("nonexistent", "spot")
    assert storage.load_snapshot(path) is None


def test_load_corrupt_snapshot(tmp_data_dir):
    """Corrupt JSON returns None (treated as first run)."""
    path = storage.snapshot_path("corrupt", "spot")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid json")
    assert storage.load_snapshot(path) is None


def test_backup_created(tmp_data_dir):
    """Saving twice should create a .bak backup."""
    path = storage.snapshot_path("test", "spot")
    data1 = {"timestamp": "t1", "symbols": {}}
    data2 = {"timestamp": "t2", "symbols": {}}

    storage.save_snapshot(path, data1)
    storage.save_snapshot(path, data2)

    backup = path.with_suffix(".bak")
    assert backup.exists()
    bak_data = json.loads(backup.read_text())
    assert bak_data["timestamp"] == "t1"


def test_journal_append_and_load(tmp_data_dir):
    """Append entries and load them back."""
    entries = [
        {"exchange": "binance", "symbol": "BTCUSDT", "listing_type": "S"},
        {"exchange": "okx", "symbol": "ETH-USDT", "listing_type": "S"},
    ]
    storage.append_journal(entries)

    loaded = storage.load_journal()
    assert len(loaded) == 2
    assert loaded[0]["exchange"] == "binance"


def test_journal_append_preserves_existing(tmp_data_dir):
    """Multiple appends accumulate entries."""
    storage.append_journal([{"exchange": "a", "symbol": "X"}])
    storage.append_journal([{"exchange": "b", "symbol": "Y"}])

    loaded = storage.load_journal()
    assert len(loaded) == 2


def test_journal_empty_entries_noop(tmp_data_dir):
    """Appending empty list does nothing."""
    storage.append_journal([])
    path = storage.journal_path()
    assert not path.exists()


def test_staleness_tracking(tmp_data_dir):
    """Staleness counter increments on empty, resets on findings."""
    count = storage.update_staleness("binance", has_new_listings=False)
    assert count == 1

    count = storage.update_staleness("binance", has_new_listings=False)
    assert count == 2

    count = storage.update_staleness("binance", has_new_listings=True)
    assert count == 0


def test_build_snapshot():
    """build_snapshot creates proper structure from InstrumentInfo-like objects."""
    from listing_tracker.exchanges.base import InstrumentInfo, ListingType

    instruments = {
        "k1": InstrumentInfo("BTC", "BTC", "USDT", ListingType.SPOT),
    }
    snap = storage.build_snapshot(instruments)
    assert "timestamp" in snap
    assert "k1" in snap["symbols"]
    assert snap["symbols"]["k1"]["listing_type"] == ListingType.SPOT
