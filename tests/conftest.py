"""Shared test fixtures."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Override storage paths to use temp directory."""
    snapshot_dir = tmp_path / "snapshots"
    journal_dir = tmp_path / "journal"
    snapshot_dir.mkdir()
    journal_dir.mkdir()

    import listing_tracker.config as config
    monkeypatch.setattr(config, "SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(config, "JOURNAL_DIR", journal_dir)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def sample_binance_spot_response():
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "permissions": ["SPOT"],
            },
            {
                "symbol": "ETHUSDT",
                "status": "TRADING",
                "baseAsset": "ETH",
                "quoteAsset": "USDT",
                "permissions": ["SPOT"],
            },
            {
                "symbol": "NEWTOKEN",
                "status": "PRE_TRADING",
                "baseAsset": "NEW",
                "quoteAsset": "USDT",
                "permissionSets": [["TRD_GRP_BINANCE_ALPHA"]],
            },
        ]
    }


@pytest.fixture
def sample_okx_response():
    return {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT",
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "state": "live",
                "listTime": "1609459200000",
            },
            {
                "instId": "ETH-USDT",
                "baseCcy": "ETH",
                "quoteCcy": "USDT",
                "state": "live",
                "listTime": "1609459200000",
            },
        ],
    }


@pytest.fixture
def sample_snapshot():
    """A snapshot with 2 symbols."""
    return {
        "timestamp": "2026-03-25T09:00:00+00:00",
        "symbols": {
            "exchange:spot:BTCUSDT": {
                "symbol": "BTCUSDT",
                "base": "BTC",
                "quote": "USDT",
                "listing_type": "S",
                "status": "active",
                "list_time": None,
            },
            "exchange:spot:ETHUSDT": {
                "symbol": "ETHUSDT",
                "base": "ETH",
                "quote": "USDT",
                "listing_type": "S",
                "status": "active",
                "list_time": None,
            },
        },
    }


@pytest.fixture
def sample_snapshot_with_new():
    """A snapshot with 3 symbols (1 new)."""
    return {
        "timestamp": "2026-03-26T09:00:00+00:00",
        "symbols": {
            "exchange:spot:BTCUSDT": {
                "symbol": "BTCUSDT",
                "base": "BTC",
                "quote": "USDT",
                "listing_type": "S",
                "status": "active",
                "list_time": None,
            },
            "exchange:spot:ETHUSDT": {
                "symbol": "ETHUSDT",
                "base": "ETH",
                "quote": "USDT",
                "listing_type": "S",
                "status": "active",
                "list_time": None,
            },
            "exchange:spot:NEWUSDT": {
                "symbol": "NEWUSDT",
                "base": "NEW",
                "quote": "USDT",
                "listing_type": "S",
                "status": "active",
                "list_time": None,
            },
        },
    }
