"""Tests for snapshot diffing and classification."""

from listing_tracker.differ import compare_snapshots, deduplicate_listings, NewListing
from listing_tracker.exchanges.base import ListingType


def test_first_run_returns_empty(sample_snapshot_with_new):
    """First run (no previous snapshot) should return empty list."""
    result = compare_snapshots("test", None, sample_snapshot_with_new)
    assert result == []


def test_no_new_symbols(sample_snapshot):
    """Identical snapshots should return empty list."""
    result = compare_snapshots("test", sample_snapshot, sample_snapshot)
    assert result == []


def test_detects_new_symbol(sample_snapshot, sample_snapshot_with_new):
    """Should detect newly added symbols."""
    result = compare_snapshots("test", sample_snapshot, sample_snapshot_with_new)
    assert len(result) == 1
    assert result[0].symbol == "NEWUSDT"
    assert result[0].listing_type == ListingType.SPOT
    assert result[0].exchange == "test"


def test_classification_futures():
    """Should correctly classify futures listings."""
    prev = {"symbols": {}}
    curr = {
        "symbols": {
            "ex:futures:BTCUSDT": {
                "symbol": "BTCUSDT",
                "base": "BTC",
                "quote": "USDT",
                "listing_type": "F",
                "status": "active",
                "list_time": None,
            }
        }
    }
    result = compare_snapshots("test", prev, curr)
    assert len(result) == 1
    assert result[0].listing_type == ListingType.FUTURES


def test_classification_alpha():
    """Should correctly classify alpha/pre-listing."""
    prev = {"symbols": {}}
    curr = {
        "symbols": {
            "ex:spot:ALPHA": {
                "symbol": "ALPHA",
                "base": "ALPHA",
                "quote": "USDT",
                "listing_type": "A/O",
                "status": "pre",
                "list_time": None,
            }
        }
    }
    result = compare_snapshots("test", prev, curr)
    assert len(result) == 1
    assert result[0].listing_type == ListingType.ALPHA


def test_deduplication():
    """Should remove duplicates based on exchange + symbol + type."""
    listings = [
        NewListing("binance", "BTCUSDT", "BTC", "USDT", ListingType.SPOT, "k1"),
        NewListing("binance", "BTCUSDT", "BTC", "USDT", ListingType.SPOT, "k2"),
        NewListing("binance", "BTCUSDT", "BTC", "USDT", ListingType.FUTURES, "k3"),
    ]
    result = deduplicate_listings(listings)
    assert len(result) == 2  # spot + futures are different types


def test_multiple_new_symbols():
    """Should detect all new symbols at once."""
    prev = {"symbols": {"a": {"symbol": "A", "listing_type": "S"}}}
    curr = {
        "symbols": {
            "a": {"symbol": "A", "listing_type": "S"},
            "b": {"symbol": "B", "listing_type": "S", "base": "", "quote": "", "status": "", "list_time": None},
            "c": {"symbol": "C", "listing_type": "F", "base": "", "quote": "", "status": "", "list_time": None},
        }
    }
    result = compare_snapshots("test", prev, curr)
    assert len(result) == 2
    symbols = {r.symbol for r in result}
    assert symbols == {"B", "C"}
