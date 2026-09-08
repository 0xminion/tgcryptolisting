from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from listing_tracker.live_models import Asset, ChangeKind, Snapshot
from listing_tracker.live_state import SnapshotRejected, StateStore

TZ = ZoneInfo("Australia/Perth")
NOW = datetime(2026, 9, 8, 19, 1, 45, tzinfo=TZ)


def asset(
    ticker: str = "PONS",
    *,
    active: bool = True,
    terminal: bool = False,
    status: str = "TRADING",
) -> Asset:
    return Asset(
        instrument_id=ticker,
        ticker=ticker,
        name="Pons",
        active=active,
        terminal=terminal,
        status=status,
        contract_address="0x39dbed3a2bd333467115de45665cc57f813c4571",
        network="Robinhood",
        market_cap=Decimal(519100000),
    )


def snapshot(*assets: Asset, source_id: str = "binance_alpha") -> Snapshot:
    return Snapshot(
        source_id=source_id,
        source_label="Binance Alpha",
        assets={item.instrument_id: item for item in assets},
    )


def test_first_successful_snapshot_is_a_silent_baseline(tmp_path):
    store = StateStore(tmp_path / "state.db")
    assert store.apply(snapshot(asset()), NOW) == []
    assert store.source_status()["binance_alpha"]["asset_count"] == 1


def test_new_active_asset_emits_listing_after_baseline(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot(asset("BTC")), NOW)

    changes = store.apply(snapshot(asset("BTC"), asset()), NOW)

    assert [(event.kind, event.asset.ticker) for event in changes] == [
        (ChangeKind.LISTED, "PONS")
    ]


def test_new_inactive_asset_is_tracked_but_not_alerted_until_active(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot(asset("BTC")), NOW)
    assert store.apply(snapshot(asset("BTC"), asset(active=False)), NOW) == []

    changes = store.apply(snapshot(asset("BTC"), asset(active=True)), NOW)

    assert [event.kind for event in changes] == [ChangeKind.LISTED]


def test_terminal_status_change_delists_immediately(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot(asset()), NOW)

    changes = store.apply(
        snapshot(asset(active=False, terminal=True, status="DELISTED")), NOW
    )

    assert [event.kind for event in changes] == [ChangeKind.DELISTED]


def test_nonterminal_inactive_state_requires_two_confirmations(tmp_path):
    store = StateStore(tmp_path / "state.db", removal_confirmations=2)
    store.apply(snapshot(asset()), NOW)
    assert store.apply(snapshot(asset(active=False, status="offline")), NOW) == []

    changes = store.apply(snapshot(asset(active=False, status="offline")), NOW)

    assert [event.kind for event in changes] == [ChangeKind.DELISTED]


def test_missing_asset_requires_two_complete_snapshots(tmp_path):
    store = StateStore(tmp_path / "state.db", removal_confirmations=2, shrink_ratio=0.4)
    store.apply(snapshot(asset(), asset("BTC")), NOW)
    assert store.apply(snapshot(asset("BTC")), NOW) == []

    changes = store.apply(snapshot(asset("BTC")), NOW)

    assert [event.kind for event in changes] == [ChangeKind.DELISTED]
    assert changes[0].asset.status == "MISSING"


def test_one_poll_disappearance_that_recovers_never_delists(tmp_path):
    store = StateStore(tmp_path / "state.db", removal_confirmations=2, shrink_ratio=0.4)
    store.apply(snapshot(asset(), asset("BTC")), NOW)
    store.apply(snapshot(asset("BTC")), NOW)

    assert store.apply(snapshot(asset(), asset("BTC")), NOW) == []


def test_relisting_after_confirmed_delisting_emits_again(tmp_path):
    store = StateStore(tmp_path / "state.db", removal_confirmations=1, shrink_ratio=0.4)
    other = asset("OTHER")
    store.apply(snapshot(asset(), other), NOW)
    assert store.apply(snapshot(other), NOW)[0].kind is ChangeKind.DELISTED

    changes = store.apply(snapshot(asset(), other), NOW)

    assert [event.kind for event in changes] == [ChangeKind.LISTED]


def test_catastrophic_snapshot_shrink_is_rejected_without_poisoning_state(tmp_path):
    store = StateStore(tmp_path / "state.db", shrink_ratio=0.5)
    baseline = [asset(f"T{i}") for i in range(10)]
    store.apply(snapshot(*baseline), NOW)

    with pytest.raises(SnapshotRejected):
        store.apply(snapshot(*baseline[:2]), NOW)

    assert store.source_status()["binance_alpha"]["asset_count"] == 10


def test_rolling_partial_snapshot_cannot_ratchet_the_shrink_floor_down(tmp_path):
    store = StateStore(tmp_path / "state.db", shrink_ratio=0.5)
    baseline = [asset(f"T{i}") for i in range(100)]
    store.apply(snapshot(*baseline), NOW)

    assert store.apply(snapshot(*baseline[:60]), NOW) == []
    with pytest.raises(SnapshotRejected):
        store.apply(snapshot(*baseline[:40]), NOW)

    status = store.source_status()["binance_alpha"]
    assert status["asset_count"] == 60
    assert status["max_asset_count"] == 100


def test_events_are_journaled_once(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot(asset("BTC")), NOW)
    store.apply(snapshot(asset("BTC"), asset()), NOW)
    store.apply(snapshot(asset("BTC"), asset()), NOW)

    journal = store.recent_changes(limit=10)
    assert len(journal) == 1
    assert journal[0].asset.ticker == "PONS"
    assert journal[0].detected_at == NOW


def test_outbox_retries_until_exact_delivery_is_acknowledged(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot(asset("BTC")), NOW)
    event = store.apply(snapshot(asset("BTC"), asset()), NOW)[0]

    claimed = store.claim_pending(NOW, limit=1)
    assert [item.event_id for item in claimed] == [event.event_id]
    assert store.claim_pending(NOW, limit=1) == []
    token = claimed[0].lease_token
    assert token is not None

    store.mark_delivery_failure(event.event_id, token, "network timeout")
    assert [item.event_id for item in store.pending_changes()] == [event.event_id]

    retry = store.claim_pending(NOW, limit=1)[0]
    assert retry.lease_token is not None
    with pytest.raises(RuntimeError):
        store.mark_delivered(event.event_id, "wrong-token", NOW)
    store.mark_delivered(event.event_id, retry.lease_token, NOW)
    assert store.pending_changes() == []


def test_expired_delivery_lease_is_recoverable_after_worker_crash(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.apply(snapshot(asset("BTC")), NOW)
    event = store.apply(snapshot(asset("BTC"), asset()), NOW)[0]
    first = store.claim_pending(NOW, limit=1, lease_seconds=120)[0]

    second = store.claim_pending(NOW + timedelta(seconds=121), limit=1)[0]

    assert second.event_id == event.event_id
    assert second.lease_token != first.lease_token
