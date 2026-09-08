from decimal import Decimal

import pytest

from listing_tracker.live_sources import (
    SourcePayloadError,
    parse_aster_perp,
    parse_aster_spot,
    parse_binance_alpha,
    parse_binance_perp,
    parse_binance_spot,
    parse_bitget_perp,
    parse_bitget_spot,
    parse_bithumb,
    parse_bybit_perp,
    parse_bybit_spot,
    parse_coinbase,
    parse_hyperliquid_perps,
    parse_hyperliquid_spot,
    parse_kraken,
    parse_okx,
    parse_robinhood,
    parse_upbit,
    source_ids,
)


def test_binance_spot_groups_quote_pairs_by_base_and_detects_pretrading():
    snap = parse_binance_spot(
        {
            "symbols": [
                {
                    "symbol": "PONSUSDT",
                    "baseAsset": "PONS",
                    "quoteAsset": "USDT",
                    "status": "PRE_TRADING",
                },
                {
                    "symbol": "PONSBTC",
                    "baseAsset": "PONS",
                    "quoteAsset": "BTC",
                    "status": "BREAK",
                },
            ]
        }
    )
    assert list(snap.assets) == ["PONS"]
    assert snap.assets["PONS"].active is True


def test_binance_spot_break_is_not_mislabeled_as_delisting():
    snap = parse_binance_spot(
        {
            "symbols": [
                {
                    "symbol": "PONSUSDT",
                    "baseAsset": "PONS",
                    "quoteAsset": "USDT",
                    "status": "BREAK",
                }
            ]
        }
    )
    assert snap.assets["PONS"].active is False
    assert snap.assets["PONS"].terminal is False
    assert snap.assets["PONS"].inactive_is_removal is False


def test_binance_perp_keeps_only_perpetual_contracts_and_pending_is_active():
    snap = parse_binance_perp(
        {
            "symbols": [
                {
                    "symbol": "PONSUSDT",
                    "baseAsset": "PONS",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "PENDING_TRADING",
                },
                {
                    "symbol": "PONS250101",
                    "baseAsset": "PONS",
                    "quoteAsset": "USDT",
                    "contractType": "CURRENT_QUARTER",
                    "status": "TRADING",
                },
                {
                    "symbol": "PONSUSD_PERP",
                    "baseAsset": "PONS",
                    "quoteAsset": "USD",
                    "contractType": "PERPETUAL",
                    "contractStatus": "TRADING",
                },
            ]
        }
    )
    assert list(snap.assets) == ["PONS"]
    assert snap.assets["PONS"].active is True


def test_binance_alpha_preserves_verified_identity_and_market_cap():
    snap = parse_binance_alpha(
        {
            "code": "000000",
            "success": True,
            "data": [
                {
                    "alphaId": "ALPHA_1155",
                    "symbol": "PONS",
                    "name": "Pons",
                    "contractAddress": "0x39dbed3a2bd333467115de45665cc57f813c4571",
                    "chainName": "Robinhood",
                    "marketCap": "519100000",
                    "fullyDelisted": False,
                    "offline": False,
                }
            ],
        }
    )
    item = snap.assets["ALPHA_1155"]
    assert item.ticker == "PONS"
    assert item.name == "Pons"
    assert item.network == "Robinhood"
    assert item.market_cap == Decimal(519100000)
    assert item.active is True


def test_binance_alpha_fully_delisted_is_terminal():
    payload = {
        "code": "000000",
        "success": True,
        "data": [
            {"alphaId": "A", "symbol": "X", "fullyDelisted": True, "offline": True}
        ],
    }
    item = parse_binance_alpha(payload).assets["A"]
    assert item.active is False
    assert item.terminal is True


def test_binance_alpha_offline_is_not_a_delisting_without_terminal_flag():
    payload = {
        "code": "000000",
        "success": True,
        "data": [
            {"alphaId": "A", "symbol": "X", "fullyDelisted": False, "offline": True}
        ],
    }
    item = parse_binance_alpha(payload).assets["A"]
    assert item.active is False
    assert item.inactive_is_removal is False
    assert item.status == "OFFLINE"


def test_okx_spot_and_swap_state_semantics():
    payload = {
        "code": "0",
        "data": [
            {
                "instId": "PONS-USDT",
                "baseCcy": "PONS",
                "quoteCcy": "USDT",
                "state": "preopen",
            }
        ],
    }
    assert parse_okx(payload, "SPOT").assets["PONS"].active is True
    assert parse_okx(payload, "SWAP").assets["PONS"].active is True

    payload["data"][0]["state"] = "suspend"
    suspended = parse_okx(payload, "SPOT").assets["PONS"]
    assert suspended.active is False
    assert suspended.inactive_is_removal is False


def test_coinbase_delisted_pair_does_not_override_live_pair_for_same_base():
    snap = parse_coinbase(
        [
            {
                "id": "PONS-USD",
                "base_currency": "PONS",
                "quote_currency": "USD",
                "status": "online",
                "trading_disabled": False,
            },
            {
                "id": "PONS-USDT",
                "base_currency": "PONS",
                "quote_currency": "USDT",
                "status": "delisted",
                "trading_disabled": True,
            },
        ]
    )
    assert snap.assets["PONS"].active is True


def test_coinbase_offline_is_still_listed_until_explicitly_delisted():
    snap = parse_coinbase(
        [
            {
                "id": "PONS-USD",
                "base_currency": "PONS",
                "quote_currency": "USD",
                "status": "offline",
                "trading_disabled": True,
            }
        ]
    )
    assert snap.assets["PONS"].active is False
    assert snap.assets["PONS"].inactive_is_removal is False


def test_token_merge_requires_every_inactive_pair_to_assert_removal():
    snap = parse_coinbase(
        [
            {
                "id": "PONS-USD",
                "base_currency": "PONS",
                "quote_currency": "USD",
                "status": "delisted",
            },
            {
                "id": "PONS-USDT",
                "base_currency": "PONS",
                "quote_currency": "USDT",
                "status": "offline",
            },
        ]
    )
    item = snap.assets["PONS"]
    assert item.active is False
    assert item.terminal is False
    assert item.inactive_is_removal is False


def test_presence_only_korean_exchange_parsers_keep_names():
    upbit = parse_upbit(
        [
            {
                "market": "KRW-PONS",
                "english_name": "Pons",
                "korean_name": "폰스",
                "market_event": {"warning": False},
            }
        ]
    )
    bithumb = parse_bithumb(
        [
            {
                "market": "KRW-PONS",
                "english_name": "Pons",
                "korean_name": "폰스",
                "market_warning": "NONE",
            }
        ]
    )
    assert upbit.assets["PONS"].name == "Pons"
    assert bithumb.assets["PONS"].name == "Pons"


def test_bybit_spot_and_perp_parsers():
    spot = parse_bybit_spot(
        {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "PONSUSDT",
                        "baseCoin": "PONS",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                    }
                ]
            },
        }
    )
    perp = parse_bybit_perp(
        {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "symbol": "PONSUSDT",
                        "baseCoin": "PONS",
                        "quoteCoin": "USDT",
                        "contractType": "LinearPerpetual",
                        "status": "PendingOpen",
                    },
                    {
                        "symbol": "PONSUSDC",
                        "baseCoin": "PONS",
                        "quoteCoin": "USDC",
                        "contractType": "LinearPerpetual",
                        "status": "Closed",
                    },
                ]
            },
        }
    )
    assert spot.assets["PONS"].active is True
    assert perp.assets["PONS"].active is True


def test_bitget_scheduled_offtime_is_terminal_delisting_signal():
    spot = parse_bitget_spot(
        {
            "code": "00000",
            "data": [
                {
                    "symbol": "PONSUSDT",
                    "baseCoin": "PONS",
                    "quoteCoin": "USDT",
                    "status": "online",
                    "offTime": "1789120800000",
                }
            ],
        }
    )
    perp = parse_bitget_perp(
        {
            "code": "00000",
            "data": [
                {
                    "symbol": "PONSUSDT",
                    "baseCoin": "PONS",
                    "quoteCoin": "USDT",
                    "symbolStatus": "normal",
                    "offTime": "1789120800000",
                }
            ],
        }
    )
    assert spot.assets["PONS"].terminal is True
    assert perp.assets["PONS"].terminal is True


def test_kraken_groups_online_pairs_and_keeps_restricted_pair_present():
    snap = parse_kraken(
        {
            "error": [],
            "result": {
                "PONSUSD": {
                    "base": "PONS",
                    "quote": "USD",
                    "wsname": "PONS/USD",
                    "status": "post_only",
                }
            },
        }
    )
    assert snap.assets["PONS"].active is True


def test_robinhood_uses_asset_uuid_and_tradability():
    snap = parse_robinhood(
        {
            "results": [
                {
                    "id": "pair-1",
                    "symbol": "PONS-USD",
                    "tradability": "tradable",
                    "asset_currency": {"id": "asset-1", "code": "PONS", "name": "Pons"},
                    "quote_currency": {"code": "USD"},
                }
            ]
        }
    )
    assert list(snap.assets) == ["asset-1"]
    assert snap.assets["asset-1"].name == "Pons"

    untradable = parse_robinhood(
        {
            "results": [
                {
                    "id": "pair-1",
                    "symbol": "PONS-USD",
                    "tradability": "untradable",
                    "asset_currency": {
                        "id": "asset-1",
                        "code": "PONS",
                        "name": "Pons",
                    },
                }
            ]
        }
    )
    assert untradable.assets["asset-1"].active is False
    assert untradable.assets["asset-1"].inactive_is_removal is False

    display_only = parse_robinhood(
        {
            "results": [
                {
                    "id": "pair-1",
                    "symbol": "PONS-USD",
                    "tradability": "untradable",
                    "display_only": True,
                    "asset_currency": {
                        "id": "asset-1",
                        "code": "PONS",
                        "name": "Pons",
                    },
                }
            ]
        }
    ).assets["asset-1"]
    assert display_only.active is False
    assert display_only.terminal is True


def test_aster_spot_excludes_documented_test_symbols_and_keeps_address():
    snap = parse_aster_spot(
        {
            "symbols": [
                {
                    "symbol": "TESTUSDT",
                    "baseAsset": "TEST",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                },
                {
                    "symbol": "PONSUSDT",
                    "baseAsset": "PONS",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "baseAssetAddress": "0x123",
                },
            ]
        }
    )
    assert list(snap.assets) == ["PONS"]
    assert snap.assets["PONS"].contract_address == "0x123"
    assert snap.assets["PONS"].network is None


def test_aster_perp_keeps_only_perpetual_and_pending():
    snap = parse_aster_perp(
        {
            "symbols": [
                {
                    "symbol": "PONSUSDT",
                    "baseAsset": "PONS",
                    "quoteAsset": "USDT",
                    "status": "PENDING_TRADING",
                    "contractType": "PERPETUAL",
                }
            ]
        }
    )
    assert snap.assets["PONS"].active is True


def test_hyperliquid_spot_uses_token_id_and_exact_evm_contract():
    snap = parse_hyperliquid_spot(
        {
            "tokens": [
                {
                    "index": 0,
                    "name": "USDC",
                    "tokenId": "0x0",
                    "fullName": None,
                    "evmContract": None,
                },
                {
                    "index": 1,
                    "name": "PONS",
                    "tokenId": "0xabc",
                    "fullName": "Pons",
                    "evmContract": {"address": "0x123"},
                },
            ],
            "universe": [
                {"index": 0, "name": "PONS/USDC", "tokens": [1, 0], "isCanonical": True}
            ],
        }
    )
    item = snap.assets["0xabc"]
    assert item.contract_address == "0x123"
    assert item.network == "HyperEVM"


def test_hyperliquid_perp_delisted_flag_is_terminal():
    snap = parse_hyperliquid_perps(
        [
            ("", {"universe": [{"name": "PONS", "isDelisted": True}]}),
            ("xyz", {"universe": [{"name": "xyz:TSLA"}]}),
        ]
    )
    assert snap.assets["core:PONS"].terminal is True
    assert snap.assets["xyz:TSLA"].active is True


def test_malformed_success_envelope_fails_closed():
    with pytest.raises(SourcePayloadError):
        parse_okx({"code": "500", "data": []}, "SPOT")
    with pytest.raises(SourcePayloadError):
        parse_coinbase({"message": "error"})


def test_source_registry_covers_every_requested_product():
    assert set(source_ids()) == {
        "binance_spot",
        "binance_perp",
        "binance_alpha",
        "okx_spot",
        "okx_perp",
        "coinbase_spot",
        "upbit_spot",
        "bithumb_spot",
        "bybit_spot",
        "bybit_perp",
        "bitget_spot",
        "bitget_perp",
        "kraken_spot",
        "robinhood_spot",
        "aster_spot",
        "aster_perp",
        "hyperliquid_spot",
        "hyperliquid_perp",
    }
