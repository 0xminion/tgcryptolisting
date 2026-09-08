from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from listing_tracker.live import apply_snapshots, deliver_pending
from listing_tracker.live_models import Asset, Snapshot
from listing_tracker.live_state import StateStore

NOW = datetime(2026, 9, 8, 19, 1, 45, tzinfo=ZoneInfo("Australia/Perth"))


def snapshot(*tickers: str) -> Snapshot:
    return Snapshot(
        source_id="binance_alpha",
        source_label="Binance Alpha",
        assets={
            ticker: Asset(
                instrument_id=ticker,
                ticker=ticker,
                name=ticker.title(),
                active=True,
                status="ONLINE",
            )
            for ticker in tickers
        },
    )


def test_apply_snapshots_isolates_rejected_source(tmp_path):
    store = StateStore(tmp_path / "state.db")
    changes, errors = apply_snapshots(
        store,
        [snapshot("BTC"), Snapshot("bad", "Bad", {})],
        {"network": "timeout"},
        NOW,
    )
    assert changes == []
    assert set(errors) == {"bad", "network"}
    assert "binance_alpha" in store.source_status()


def test_delivery_acknowledges_only_after_sender_returns(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot("BTC"), NOW)
    store.apply(snapshot("BTC", "PONS"), NOW)
    sent = []

    def sender(message: str, target: str):
        sent.append((message, target))
        return {"success": True}

    delivered, error = deliver_pending(store, "telegram:123456789", NOW, sender=sender)
    assert delivered == 1
    assert error is None
    assert sent[0][0].startswith("Binance Alpha lists new tokens:")
    assert sent[0][1] == "telegram:123456789"
    assert store.pending_changes() == []


def test_delivery_failure_keeps_outbox_pending_and_stops_in_order(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot("BTC"), NOW)
    store.apply(snapshot("BTC", "PONS", "WWW"), NOW)

    def sender(message: str, target: str):
        raise RuntimeError("backend down")

    delivered, error = deliver_pending(store, "telegram:123456789", NOW, sender=sender)
    assert delivered == 0
    assert "backend down" in (error or "")
    assert [item.asset.ticker for item in store.pending_changes()] == ["PONS", "WWW"]


def test_extreme_market_cap_cannot_poison_fifo_delivery(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot("BTC"), NOW)
    current = snapshot("BTC", "BAD", "PONS")
    current.assets["BAD"] = Asset(
        instrument_id="BAD",
        ticker="BAD",
        name="Bad",
        active=True,
        status="ONLINE",
        market_cap=Decimal("1e100000"),
    )
    store.apply(current, NOW)
    sent: list[str] = []

    def sender(message: str, target: str):
        sent.append(message)
        return {"success": True}

    delivered, error = deliver_pending(store, "telegram:123456789", NOW, sender=sender)
    assert delivered == 2
    assert error is None
    assert "MarketCap: n/a" in sent[0]
    assert store.pending_changes() == []
