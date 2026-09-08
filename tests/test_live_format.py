from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from listing_tracker.live_format import format_change, format_changes, format_market_cap
from listing_tracker.live_models import Asset, Change, ChangeKind

NOW = datetime(2026, 9, 8, 19, 1, 45, tzinfo=ZoneInfo("Australia/Perth"))


def event(kind: ChangeKind = ChangeKind.LISTED, **overrides) -> Change:
    values = {
        "instrument_id": "ALPHA_1155",
        "ticker": "PONS",
        "name": "Pons",
        "active": kind is ChangeKind.LISTED,
        "terminal": kind is ChangeKind.DELISTED,
        "status": "TRADING" if kind is ChangeKind.LISTED else "DELISTED",
        "contract_address": "0x39dbed3a2bd333467115de45665cc57f813c4571",
        "network": "Robinhood",
        "market_cap": Decimal(519100000),
    }
    values.update(overrides)
    return Change(
        source_id="binance_alpha",
        source_label="Binance Alpha",
        kind=kind,
        asset=Asset(**values),
        detected_at=NOW,
    )


def test_listing_format_exactly_matches_requested_template():
    assert format_change(event()) == (
        "Binance Alpha lists new tokens:\n\n"
        "Pons (PONS)\n"
        "Contract Address: 0x39dbed3a2bd333467115de45665cc57f813c4571 (Robinhood)\n\n"
        "PONS  MarketCap: 519.1M\n"
        "————————————\n"
        "2026-09-08 19:01:45"
    )


def test_delisting_uses_same_template_shape():
    message = format_change(event(ChangeKind.DELISTED))
    assert message.startswith("Binance Alpha delists tokens:\n\nPons (PONS)")
    assert message.endswith("2026-09-08 19:01:45")


def test_missing_contract_and_market_cap_are_explicit():
    message = format_change(event(contract_address=None, network=None, market_cap=None))
    assert "Contract Address: Not available" in message
    assert "PONS  MarketCap: n/a" in message


def test_market_cap_units_are_compact_and_deterministic():
    assert format_market_cap(Decimal(999)) == "999"
    assert format_market_cap(Decimal(1200)) == "1.2K"
    assert format_market_cap(Decimal(519100000)) == "519.1M"
    assert format_market_cap(Decimal(2500000000)) == "2.5B"
    assert format_market_cap(None) == "n/a"
    assert format_market_cap(Decimal("1e100000")) == "n/a"


def test_untrusted_names_are_flattened_and_bounded():
    message = format_change(event(name="Bad\nName\x00" + "X" * 300, ticker="P<ON&S"))
    assert "Bad Name" in message
    assert "\x00" not in message
    assert "P<ON&S" in message
    assert len(message) < 800


def test_multiple_changes_are_separated_without_extra_prose():
    output = format_changes([event(), event(ChangeKind.DELISTED)])
    assert output.count("————————————") == 2
    assert "\n\n" in output
