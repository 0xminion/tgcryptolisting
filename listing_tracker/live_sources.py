"""Authoritative public exchange market sources and strict parsers.

The monitor tracks token-level product presence, not individual quote pairs. This
avoids announcing an old token merely because an exchange added another quote.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx

from listing_tracker.live_models import Asset, Snapshot


class SourcePayloadError(RuntimeError):
    """A nominal HTTP success did not contain the documented payload shape."""


Fetch = Callable[[httpx.AsyncClient], Awaitable[Snapshot]]


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    source_label: str
    fetch: Fetch


def _dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourcePayloadError(f"{where}: expected object")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise SourcePayloadError(f"{where}: expected array")
    return value


def _text(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SourcePayloadError(f"{where}: expected string")
    value = value.strip()
    if not value and not allow_empty:
        raise SourcePayloadError(f"{where}: empty string")
    return value


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _merge(existing: Asset | None, item: Asset) -> Asset:
    """Merge quote-pair rows into one token-level state."""
    if existing is None:
        return item
    active = existing.active or item.active
    terminal = existing.terminal and item.terminal and not active
    statuses = sorted(set(filter(None, (existing.status, item.status))))
    name = existing.name
    if name == existing.ticker and item.name != item.ticker:
        name = item.name
    return Asset(
        instrument_id=existing.instrument_id,
        ticker=existing.ticker,
        name=name,
        active=active,
        terminal=terminal,
        inactive_is_removal=(existing.inactive_is_removal and item.inactive_is_removal)
        and not active,
        status=",".join(statuses),
        contract_address=existing.contract_address or item.contract_address,
        network=existing.network or item.network,
        market_cap=existing.market_cap or item.market_cap,
        reference=existing.reference or item.reference,
    )


def _snapshot(source_id: str, source_label: str, rows: Iterable[Asset]) -> Snapshot:
    assets: dict[str, Asset] = {}
    for item in rows:
        assets[item.instrument_id] = _merge(assets.get(item.instrument_id), item)
    return Snapshot(source_id=source_id, source_label=source_label, assets=assets)


def parse_binance_spot(payload: Any) -> Snapshot:
    rows = _list(_dict(payload, "binance spot").get("symbols"), "symbols")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"symbols[{index}]")
        base = _text(item.get("baseAsset"), "baseAsset")
        status = _text(item.get("status"), "status")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=status in {"TRADING", "PRE_TRADING"},
                terminal=status == "DELISTED",
                inactive_is_removal=status == "DELISTED",
                status=status,
            )
        )
    return _snapshot("binance_spot", "Binance Spot", assets)


def parse_binance_perp(payload: Any) -> Snapshot:
    rows = _list(_dict(payload, "binance perpetual").get("symbols"), "symbols")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"symbols[{index}]")
        if item.get("contractType") != "PERPETUAL":
            continue
        base = _text(item.get("baseAsset"), "baseAsset")
        status = _text(item.get("status") or item.get("contractStatus"), "status")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=status in {"TRADING", "PENDING_TRADING", "PRE_TRADING"},
                terminal=status in {"SETTLING", "DELISTED"},
                inactive_is_removal=status in {"SETTLING", "DELISTED"},
                status=status,
            )
        )
    return _snapshot("binance_perp", "Binance Perpetual Futures", assets)


def parse_binance_alpha(payload: Any) -> Snapshot:
    root = _dict(payload, "binance alpha")
    if root.get("code") != "000000" or root.get("success") is not True:
        raise SourcePayloadError("binance alpha: unsuccessful response")
    rows = _list(root.get("data"), "data")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"data[{index}]")
        instrument_id = _text(item.get("alphaId"), "alphaId")
        ticker = _text(item.get("symbol"), "symbol")
        fully_delisted = item.get("fullyDelisted")
        offline = item.get("offline")
        if not isinstance(fully_delisted, bool) or not isinstance(offline, bool):
            raise SourcePayloadError("binance alpha: invalid listing flags")
        assets.append(
            Asset(
                instrument_id=instrument_id,
                ticker=ticker,
                name=_optional_text(item.get("name")) or ticker,
                active=not fully_delisted and not offline,
                terminal=fully_delisted,
                inactive_is_removal=fully_delisted,
                status=(
                    "FULLY_DELISTED"
                    if fully_delisted
                    else "OFFLINE"
                    if offline
                    else "ONLINE"
                ),
                contract_address=_optional_text(item.get("contractAddress")),
                network=_optional_text(item.get("chainName")),
                market_cap=_decimal(item.get("marketCap")),
            )
        )
    return _snapshot("binance_alpha", "Binance Alpha", assets)


def parse_okx(payload: Any, product: str) -> Snapshot:
    root = _dict(payload, "okx")
    if root.get("code") != "0":
        raise SourcePayloadError("okx: unsuccessful response")
    rows = _list(root.get("data"), "data")
    product = product.upper()
    if product not in {"SPOT", "SWAP"}:
        raise ValueError("OKX product must be SPOT or SWAP")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"data[{index}]")
        inst_id = _text(item.get("instId"), "instId")
        base = (
            _optional_text(item.get("baseCcy"))
            or _optional_text(item.get("ctValCcy"))
            or inst_id.split("-", 1)[0]
        )
        status = _text(item.get("state"), "state")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=status in {"live", "preopen"},
                terminal=False,
                inactive_is_removal=False,
                status=status,
            )
        )
    source_id = "okx_spot" if product == "SPOT" else "okx_perp"
    label = "OKX Spot" if product == "SPOT" else "OKX Perpetual Futures"
    return _snapshot(source_id, label, assets)


def parse_coinbase(payload: Any) -> Snapshot:
    rows = _list(payload, "coinbase products")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"products[{index}]")
        if item.get("product_type") not in (None, "SPOT"):
            continue
        base = _text(item.get("base_currency"), "base_currency")
        status = _text(item.get("status"), "status")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=status == "online",
                terminal=status == "delisted",
                inactive_is_removal=status == "delisted",
                status=status,
            )
        )
    return _snapshot("coinbase_spot", "Coinbase Spot", assets)


def parse_upbit(payload: Any) -> Snapshot:
    rows = _list(payload, "upbit markets")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"markets[{index}]")
        market = _text(item.get("market"), "market")
        parts = market.split("-", 1)
        if len(parts) != 2:
            raise SourcePayloadError("upbit: malformed market code")
        ticker = parts[1]
        assets.append(
            Asset(
                instrument_id=ticker,
                ticker=ticker,
                name=_optional_text(item.get("english_name")) or ticker,
                active=True,
                status="LISTED",
            )
        )
    return _snapshot("upbit_spot", "Upbit Spot", assets)


def parse_bithumb(payload: Any) -> Snapshot:
    rows = _list(payload, "bithumb markets")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"markets[{index}]")
        market = _text(item.get("market"), "market")
        parts = market.split("-", 1)
        if len(parts) != 2:
            raise SourcePayloadError("bithumb: malformed market code")
        ticker = parts[1]
        assets.append(
            Asset(
                instrument_id=ticker,
                ticker=ticker,
                name=_optional_text(item.get("english_name")) or ticker,
                active=True,
                status="LISTED",
            )
        )
    return _snapshot("bithumb_spot", "Bithumb Spot", assets)


def _bybit_rows(payload: Any) -> list[Any]:
    root = _dict(payload, "bybit")
    if root.get("retCode") != 0:
        raise SourcePayloadError("bybit: unsuccessful response")
    return _list(_dict(root.get("result"), "result").get("list"), "result.list")


def parse_bybit_spot(payload: Any) -> Snapshot:
    assets: list[Asset] = []
    for index, raw in enumerate(_bybit_rows(payload)):
        item = _dict(raw, f"result.list[{index}]")
        base = _text(item.get("baseCoin"), "baseCoin")
        status = _text(item.get("status"), "status")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=_optional_text(item.get("fullName")) or base,
                active=status in {"Trading", "PreLaunch", "PendingOpen"},
                terminal=status in {"Settling", "Delivering", "Closed", "Delisted"},
                inactive_is_removal=status
                in {"Settling", "Delivering", "Closed", "Delisted"},
                status=status,
            )
        )
    return _snapshot("bybit_spot", "Bybit Spot", assets)


def parse_bybit_perp(payload: Any) -> Snapshot:
    assets: list[Asset] = []
    for index, raw in enumerate(_bybit_rows(payload)):
        item = _dict(raw, f"result.list[{index}]")
        if not str(item.get("contractType", "")).endswith("Perpetual"):
            continue
        base = _text(item.get("baseCoin"), "baseCoin")
        status = _text(item.get("status"), "status")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=status in {"Trading", "PreLaunch", "PendingOpen"},
                terminal=status in {"Settling", "Delivering", "Closed", "Delisted"},
                inactive_is_removal=status
                in {"Settling", "Delivering", "Closed", "Delisted"},
                status=status,
            )
        )
    return _snapshot("bybit_perp", "Bybit Perpetual Futures", assets)


def _bitget_rows(payload: Any) -> list[Any]:
    root = _dict(payload, "bitget")
    if root.get("code") != "00000":
        raise SourcePayloadError("bitget: unsuccessful response")
    return _list(root.get("data"), "data")


def _positive_millis(value: Any) -> bool:
    try:
        return int(str(value or "-1")) > 0
    except ValueError:
        return False


def parse_bitget_spot(payload: Any) -> Snapshot:
    assets: list[Asset] = []
    for index, raw in enumerate(_bitget_rows(payload)):
        item = _dict(raw, f"data[{index}]")
        base = _text(item.get("baseCoin"), "baseCoin")
        status = _text(item.get("status"), "status")
        scheduled_off = _positive_millis(item.get("offTime"))
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=not scheduled_off and status == "online",
                terminal=scheduled_off,
                inactive_is_removal=scheduled_off,
                status="SCHEDULED_OFF" if scheduled_off else status,
            )
        )
    return _snapshot("bitget_spot", "Bitget Spot", assets)


def parse_bitget_perp(payload: Any) -> Snapshot:
    assets: list[Asset] = []
    for index, raw in enumerate(_bitget_rows(payload)):
        item = _dict(raw, f"data[{index}]")
        base = _text(item.get("baseCoin"), "baseCoin")
        status = _text(item.get("symbolStatus"), "symbolStatus")
        scheduled_off = _positive_millis(item.get("offTime"))
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=not scheduled_off and status == "normal",
                terminal=scheduled_off,
                inactive_is_removal=scheduled_off,
                status="SCHEDULED_OFF" if scheduled_off else status,
            )
        )
    return _snapshot("bitget_perp", "Bitget Perpetual Futures", assets)


def parse_kraken(payload: Any) -> Snapshot:
    root = _dict(payload, "kraken")
    if root.get("error") != []:
        raise SourcePayloadError("kraken: unsuccessful response")
    result = _dict(root.get("result"), "result")
    assets: list[Asset] = []
    for pair_id, raw in result.items():
        item = _dict(raw, f"result.{pair_id}")
        base_id = _text(item.get("base"), "base")
        wsname = _optional_text(item.get("wsname"))
        ticker = wsname.split("/", 1)[0] if wsname and "/" in wsname else base_id
        status = _optional_text(item.get("status")) or "online"
        terminal = status == "delisted"
        assets.append(
            Asset(
                instrument_id=base_id,
                ticker=ticker,
                name=ticker,
                active=not terminal,
                terminal=terminal,
                status=status,
            )
        )
    return _snapshot("kraken_spot", "Kraken Spot", assets)


def parse_robinhood(payload: Any) -> Snapshot:
    rows = _list(_dict(payload, "robinhood").get("results"), "results")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"results[{index}]")
        asset = _dict(item.get("asset_currency"), "asset_currency")
        instrument_id = _text(asset.get("id"), "asset_currency.id")
        ticker = _text(asset.get("code"), "asset_currency.code")
        status = _text(item.get("tradability"), "tradability")
        display_only = item.get("display_only", False)
        if not isinstance(display_only, bool):
            raise SourcePayloadError("robinhood: invalid display_only flag")
        assets.append(
            Asset(
                instrument_id=instrument_id,
                ticker=ticker,
                name=_optional_text(asset.get("name")) or ticker,
                active=status == "tradable" and not display_only,
                terminal=display_only,
                inactive_is_removal=display_only,
                status="DISPLAY_ONLY" if display_only else status,
            )
        )
    return _snapshot("robinhood_spot", "Robinhood Spot", assets)


def _is_test_symbol(symbol: str, base: str) -> bool:
    upper_symbol = symbol.upper()
    upper_base = base.upper()
    return upper_base.startswith("TEST") or upper_symbol.startswith("TEST")


def parse_aster_spot(payload: Any) -> Snapshot:
    rows = _list(_dict(payload, "aster spot").get("symbols"), "symbols")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"symbols[{index}]")
        symbol = _text(item.get("symbol"), "symbol")
        base = _text(item.get("baseAsset"), "baseAsset")
        if _is_test_symbol(symbol, base):
            continue
        status = _text(item.get("status"), "status")
        address = _optional_text(item.get("baseAssetAddress"))
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                # The spot schema only documents TRADING. Unknown states are
                # unavailable, not authenticated delistings.
                active=status == "TRADING",
                terminal=False,
                inactive_is_removal=False,
                status=status,
                contract_address=address,
                # The endpoint does not authenticate a chain for this address.
                network=None,
            )
        )
    return _snapshot("aster_spot", "Aster Spot", assets)


def parse_aster_perp(payload: Any) -> Snapshot:
    rows = _list(_dict(payload, "aster perpetual").get("symbols"), "symbols")
    assets: list[Asset] = []
    for index, raw in enumerate(rows):
        item = _dict(raw, f"symbols[{index}]")
        if item.get("contractType") != "PERPETUAL":
            continue
        symbol = _text(item.get("symbol"), "symbol")
        base = _text(item.get("baseAsset"), "baseAsset")
        if _is_test_symbol(symbol, base):
            continue
        status = _text(item.get("status"), "status")
        assets.append(
            Asset(
                instrument_id=base,
                ticker=base,
                name=base,
                active=status in {"TRADING", "PRE_TRADING", "PENDING_TRADING"},
                terminal=status in {"PRE_SETTLE", "SETTLING", "CLOSE", "DELISTED"},
                inactive_is_removal=status
                in {"PRE_SETTLE", "SETTLING", "CLOSE", "DELISTED"},
                status=status,
            )
        )
    return _snapshot("aster_perp", "Aster Perpetual Futures", assets)


def parse_hyperliquid_spot(payload: Any) -> Snapshot:
    root = _dict(payload, "hyperliquid spot")
    token_rows = _list(root.get("tokens"), "tokens")
    universe = _list(root.get("universe"), "universe")
    by_index: dict[int, dict[str, Any]] = {}
    for raw in token_rows:
        item = _dict(raw, "tokens[]")
        index = item.get("index")
        if not isinstance(index, int):
            raise SourcePayloadError("hyperliquid spot: invalid token index")
        by_index[index] = item

    base_indexes: set[int] = set()
    for raw in universe:
        item = _dict(raw, "universe[]")
        indexes = _list(item.get("tokens"), "universe.tokens")
        if len(indexes) != 2 or not isinstance(indexes[0], int):
            raise SourcePayloadError("hyperliquid spot: invalid pair tokens")
        base_indexes.add(indexes[0])

    assets: list[Asset] = []
    for index in sorted(base_indexes):
        item = by_index.get(index)
        if item is None:
            raise SourcePayloadError("hyperliquid spot: unknown token index")
        ticker = _text(item.get("name"), "token.name")
        token_id = _text(item.get("tokenId"), "token.tokenId")
        evm = item.get("evmContract")
        address = None
        network = None
        if evm is not None:
            evm_data = _dict(evm, "token.evmContract")
            address = _optional_text(evm_data.get("address"))
            if address:
                network = "HyperEVM"
        if address is None:
            address = token_id
            network = "HyperCore token ID"
        assets.append(
            Asset(
                instrument_id=token_id,
                ticker=ticker,
                name=_optional_text(item.get("fullName")) or ticker,
                active=True,
                status="LISTED",
                contract_address=address,
                network=network,
            )
        )
    return _snapshot("hyperliquid_spot", "Hyperliquid Spot", assets)


def parse_hyperliquid_perps(payloads: list[tuple[str, Any]]) -> Snapshot:
    assets: list[Asset] = []
    for dex, payload in payloads:
        root = _dict(payload, f"hyperliquid meta {dex or 'core'}")
        universe = _list(root.get("universe"), "universe")
        dex_id = dex or "core"
        for raw in universe:
            item = _dict(raw, "universe[]")
            name = _text(item.get("name"), "name")
            is_delisted = item.get("isDelisted", False)
            if not isinstance(is_delisted, bool):
                raise SourcePayloadError("hyperliquid perp: invalid isDelisted")
            ticker = name.rsplit(":", 1)[-1]
            instrument_id = (
                name if dex and name.startswith(f"{dex}:") else f"{dex_id}:{name}"
            )
            assets.append(
                Asset(
                    instrument_id=instrument_id,
                    ticker=ticker,
                    name=name,
                    active=not is_delisted,
                    terminal=is_delisted,
                    status="DELISTED" if is_delisted else "LISTED",
                )
            )
    return _snapshot("hyperliquid_perp", "Hyperliquid Perpetual Futures", assets)


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> Any:
    """Retry one transient transport failure, then fail closed with endpoint context."""
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt < attempts:
                await asyncio.sleep(0.25)
                continue
            raise SourcePayloadError(
                f"{url}: {type(exc).__name__} after {attempts} attempts"
            ) from exc

        response.raise_for_status()
        if len(response.content) > 50_000_000:
            raise SourcePayloadError(f"{url}: response exceeds 50 MB")
        try:
            return response.json()
        except ValueError as exc:
            raise SourcePayloadError(f"{url}: invalid JSON") from exc
    raise AssertionError("unreachable")


async def _json_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return await _request_json(client, "GET", url, params=params, headers=headers)


async def _json_post(client: httpx.AsyncClient, url: str, payload: Any) -> Any:
    return await _request_json(client, "POST", url, json=payload)


async def _fetch_binance_spot(client: httpx.AsyncClient) -> Snapshot:
    return parse_binance_spot(
        await _json_get(client, "https://api.binance.com/api/v3/exchangeInfo")
    )


async def _fetch_binance_perp(client: httpx.AsyncClient) -> Snapshot:
    usd_m, coin_m = await asyncio.gather(
        _json_get(client, "https://fapi.binance.com/fapi/v1/exchangeInfo"),
        _json_get(client, "https://dapi.binance.com/dapi/v1/exchangeInfo"),
    )
    symbols = _list(_dict(usd_m, "binance usd-m").get("symbols"), "symbols")
    symbols.extend(_list(_dict(coin_m, "binance coin-m").get("symbols"), "symbols"))
    return parse_binance_perp({"symbols": symbols})


async def _fetch_binance_alpha(client: httpx.AsyncClient) -> Snapshot:
    return parse_binance_alpha(
        await _json_get(
            client,
            "https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list",
            headers={"clienttype": "web"},
        )
    )


async def _fetch_okx(client: httpx.AsyncClient, product: str) -> Snapshot:
    return parse_okx(
        await _json_get(
            client,
            "https://www.okx.com/api/v5/public/instruments",
            params={"instType": product},
        ),
        product,
    )


async def _fetch_okx_spot(client: httpx.AsyncClient) -> Snapshot:
    return await _fetch_okx(client, "SPOT")


async def _fetch_okx_perp(client: httpx.AsyncClient) -> Snapshot:
    return await _fetch_okx(client, "SWAP")


async def _fetch_coinbase(client: httpx.AsyncClient) -> Snapshot:
    return parse_coinbase(
        await _json_get(client, "https://api.exchange.coinbase.com/products")
    )


async def _fetch_upbit(client: httpx.AsyncClient) -> Snapshot:
    return parse_upbit(
        await _json_get(
            client,
            "https://api.upbit.com/v1/market/all",
            params={"is_details": "true"},
        )
    )


async def _fetch_bithumb(client: httpx.AsyncClient) -> Snapshot:
    return parse_bithumb(
        await _json_get(
            client,
            "https://api.bithumb.com/v1/market/all",
            params={"isDetails": "true"},
        )
    )


async def _fetch_bybit_page(
    client: httpx.AsyncClient, category: str, cursor: str | None = None
) -> dict[str, Any]:
    params = {"category": category, "limit": "1000"}
    if cursor:
        params["cursor"] = cursor
    return _dict(
        await _json_get(
            client, "https://api.bybit.com/v5/market/instruments-info", params=params
        ),
        "bybit",
    )


async def _fetch_bybit(client: httpx.AsyncClient, category: str) -> dict[str, Any]:
    combined: list[Any] = []
    cursor: str | None = None
    for _ in range(10):
        payload = await _fetch_bybit_page(client, category, cursor)
        if payload.get("retCode") != 0:
            raise SourcePayloadError("bybit: unsuccessful response")
        result = _dict(payload.get("result"), "result")
        combined.extend(_list(result.get("list"), "result.list"))
        next_cursor = _optional_text(result.get("nextPageCursor"))
        if not next_cursor or next_cursor == cursor:
            return {"retCode": 0, "result": {"list": combined}}
        cursor = next_cursor
    raise SourcePayloadError("bybit: pagination exceeded 10 pages")


async def _fetch_bybit_spot(client: httpx.AsyncClient) -> Snapshot:
    return parse_bybit_spot(await _fetch_bybit(client, "spot"))


async def _fetch_bybit_perp(client: httpx.AsyncClient) -> Snapshot:
    linear, inverse = await asyncio.gather(
        _fetch_bybit(client, "linear"), _fetch_bybit(client, "inverse")
    )
    rows = _list(_dict(linear.get("result"), "result").get("list"), "result.list")
    rows.extend(
        _list(_dict(inverse.get("result"), "result").get("list"), "result.list")
    )
    return parse_bybit_perp({"retCode": 0, "result": {"list": rows}})


async def _fetch_bitget_spot(client: httpx.AsyncClient) -> Snapshot:
    return parse_bitget_spot(
        await _json_get(client, "https://api.bitget.com/api/v2/spot/public/symbols")
    )


async def _fetch_bitget_perp(client: httpx.AsyncClient) -> Snapshot:
    payloads = await asyncio.gather(
        *(
            _json_get(
                client,
                "https://api.bitget.com/api/v2/mix/market/contracts",
                params={"productType": product_type},
            )
            for product_type in ("USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES")
        )
    )
    rows: list[Any] = []
    for payload in payloads:
        root = _dict(payload, "bitget")
        if root.get("code") != "00000":
            raise SourcePayloadError("bitget: unsuccessful response")
        rows.extend(_list(root.get("data"), "data"))
    return parse_bitget_perp({"code": "00000", "data": rows})


async def _fetch_kraken(client: httpx.AsyncClient) -> Snapshot:
    return parse_kraken(
        await _json_get(client, "https://api.kraken.com/0/public/AssetPairs")
    )


async def _fetch_robinhood(client: httpx.AsyncClient) -> Snapshot:
    url = "https://nummus.robinhood.com/currency_pairs/"
    results: list[Any] = []
    for _ in range(10):
        payload = _dict(
            await _json_get(client, url, params={"page_size": "1000"}), "robinhood"
        )
        results.extend(_list(payload.get("results"), "results"))
        next_url = _optional_text(payload.get("next"))
        if not next_url:
            return parse_robinhood({"results": results})
        parsed = urlparse(next_url)
        if parsed.scheme != "https" or parsed.hostname != "nummus.robinhood.com":
            raise SourcePayloadError("robinhood: unsafe pagination URL")
        url = next_url
    raise SourcePayloadError("robinhood: pagination exceeded 10 pages")


async def _fetch_aster_spot(client: httpx.AsyncClient) -> Snapshot:
    return parse_aster_spot(
        await _json_get(client, "https://sapi.asterdex.com/api/v1/exchangeInfo")
    )


async def _fetch_aster_perp(client: httpx.AsyncClient) -> Snapshot:
    return parse_aster_perp(
        await _json_get(client, "https://fapi.asterdex.com/fapi/v1/exchangeInfo")
    )


_HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"


async def _fetch_hyperliquid_spot(client: httpx.AsyncClient) -> Snapshot:
    return parse_hyperliquid_spot(
        await _json_post(client, _HYPERLIQUID_INFO, {"type": "spotMeta"})
    )


async def _fetch_hyperliquid_perp(client: httpx.AsyncClient) -> Snapshot:
    raw_dexes = _list(
        await _json_post(client, _HYPERLIQUID_INFO, {"type": "perpDexs"}),
        "perpDexs",
    )
    dexes = [""]
    for raw in raw_dexes:
        if raw is None:
            continue
        item = _dict(raw, "perpDexs[]")
        name = _optional_text(item.get("name"))
        if name:
            dexes.append(name)
    payloads = await asyncio.gather(
        *(
            _json_post(
                client,
                _HYPERLIQUID_INFO,
                {"type": "meta", **({"dex": dex} if dex else {})},
            )
            for dex in dexes
        )
    )
    return parse_hyperliquid_perps(list(zip(dexes, payloads, strict=True)))


SOURCES: tuple[Source, ...] = (
    Source("binance_spot", "Binance Spot", _fetch_binance_spot),
    Source("binance_perp", "Binance Perpetual Futures", _fetch_binance_perp),
    Source("binance_alpha", "Binance Alpha", _fetch_binance_alpha),
    Source("okx_spot", "OKX Spot", _fetch_okx_spot),
    Source("okx_perp", "OKX Perpetual Futures", _fetch_okx_perp),
    Source("coinbase_spot", "Coinbase Spot", _fetch_coinbase),
    Source("upbit_spot", "Upbit Spot", _fetch_upbit),
    Source("bithumb_spot", "Bithumb Spot", _fetch_bithumb),
    Source("bybit_spot", "Bybit Spot", _fetch_bybit_spot),
    Source("bybit_perp", "Bybit Perpetual Futures", _fetch_bybit_perp),
    Source("bitget_spot", "Bitget Spot", _fetch_bitget_spot),
    Source("bitget_perp", "Bitget Perpetual Futures", _fetch_bitget_perp),
    Source("kraken_spot", "Kraken Spot", _fetch_kraken),
    Source("robinhood_spot", "Robinhood Spot", _fetch_robinhood),
    Source("aster_spot", "Aster Spot", _fetch_aster_spot),
    Source("aster_perp", "Aster Perpetual Futures", _fetch_aster_perp),
    Source("hyperliquid_spot", "Hyperliquid Spot", _fetch_hyperliquid_spot),
    Source(
        "hyperliquid_perp", "Hyperliquid Perpetual Futures", _fetch_hyperliquid_perp
    ),
)


def source_ids() -> tuple[str, ...]:
    return tuple(source.source_id for source in SOURCES)


async def fetch_snapshots(
    *, selected: set[str] | None = None, timeout_seconds: float = 20.0
) -> tuple[list[Snapshot], dict[str, str]]:
    """Fetch all selected sources concurrently, isolating source failures."""
    chosen = [
        source for source in SOURCES if selected is None or source.source_id in selected
    ]
    if selected is not None:
        unknown = selected - {source.source_id for source in SOURCES}
        if unknown:
            raise ValueError(f"unknown sources: {', '.join(sorted(unknown))}")
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    limits = httpx.Limits(max_connections=30, max_keepalive_connections=20)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "token-listing-tracker/0.2",
        },
        trust_env=False,
    ) as client:
        results = await asyncio.gather(
            *(source.fetch(client) for source in chosen), return_exceptions=True
        )

    snapshots: list[Snapshot] = []
    errors: dict[str, str] = {}
    for source, result in zip(chosen, results, strict=True):
        if isinstance(result, BaseException):
            errors[source.source_id] = f"{type(result).__name__}: {result}"
        else:
            snapshots.append(result)
    return snapshots, errors
