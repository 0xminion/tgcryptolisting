"""Tests for Telegram message formatting."""

from datetime import datetime, timezone

from listing_tracker.formatter import format_daily_report, format_realtime_alert


def test_basic_report():
    """Format a report with one exchange having listings, others n/a."""
    listings = {
        "binance": [
            {"symbol": "BTCUSDT", "listing_type": "S"},
            {"symbol": "ENAUSDT", "listing_type": "F"},
        ],
    }
    date = datetime(2026, 3, 26, tzinfo=timezone.utc)
    messages = format_daily_report(listings, {}, {}, date)
    assert len(messages) >= 1
    msg = messages[0]
    assert "26/03/2026" in msg
    assert "BTCUSDT (S)" in msg
    assert "ENAUSDT (F)" in msg
    assert "n/a" in msg  # other exchanges show n/a


def test_exchange_ordering():
    """Exchanges with listings should appear before n/a exchanges."""
    listings = {
        "kraken": [{"symbol": "XBTUSDT", "listing_type": "S"}],
    }
    messages = format_daily_report(listings, {}, {})
    msg = messages[0]
    # Kraken has listings so should appear before the n/a separator
    kraken_pos = msg.find("Kraken")
    # Any n/a exchange that's higher priority but empty should be after separator
    binance_pos = msg.find("Binance")
    assert binance_pos > kraken_pos  # Binance (n/a) after Kraken (has listings)


def test_error_display():
    """Errors should show ERROR, not n/a."""
    messages = format_daily_report({}, {"binance": "timeout"}, {})
    msg = messages[0]
    assert "ERROR" in msg


def test_staleness_warning():
    """Exchanges stale for 7+ polls should show (stale) marker."""
    messages = format_daily_report({}, {}, {"okx": 10})
    msg = messages[0]
    assert "stale" in msg


def test_html_escape():
    """Dynamic content should be HTML-escaped."""
    listings = {
        "binance": [{"symbol": "<script>alert(1)</script>", "listing_type": "S"}],
    }
    messages = format_daily_report(listings, {}, {})
    msg = messages[0]
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg


def test_message_splitting():
    """Messages over 4096 chars should be split."""
    # Create a very long listing
    listings = {
        "binance": [
            {"symbol": f"TOKEN{i}USDT", "listing_type": "S"}
            for i in range(200)
        ],
    }
    messages = format_daily_report(listings, {}, {})
    total_len = sum(len(m) for m in messages)
    for msg in messages:
        assert len(msg) <= 4096


def test_realtime_alert_format():
    """Real-time alert should include exchange, symbol, and time."""
    listings = [
        {"exchange": "binance", "symbol": "NEWUSDT", "listing_type": "S"},
    ]
    msg = format_realtime_alert(listings)
    assert "New Listing Detected" in msg
    assert "Binance" in msg
    assert "NEWUSDT" in msg
    assert "(S)" in msg


def test_realtime_alert_empty():
    """Empty listings should return empty string."""
    assert format_realtime_alert([]) == ""


def test_all_na_report():
    """Report with all n/a should still be valid."""
    messages = format_daily_report({}, {}, {})
    assert len(messages) >= 1
    msg = messages[0]
    assert "n/a" in msg


def test_coinbase_roadmap_tag():
    """Coinbase roadmap listings should show (R)."""
    listings = {
        "coinbase": [{"symbol": "MEGA", "listing_type": "R"}],
    }
    messages = format_daily_report(listings, {}, {})
    msg = messages[0]
    assert "MEGA (R)" in msg
