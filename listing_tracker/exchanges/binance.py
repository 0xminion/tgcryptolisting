"""Binance adapter — spot + USDT-M futures + Alpha detection."""

from __future__ import annotations

import asyncio
import logging

import httpx

from listing_tracker.config import ExchangeConfig
from listing_tracker.exchanges.base import (
    AdapterError,
    BaseAdapter,
    InstrumentInfo,
    ListingType,
)
from listing_tracker.http_client import make_client, with_429_retry

logger = logging.getLogger(__name__)

# Include TRADING (normal listings) and PRE_TRADING (alpha/pre-listing stage)
TRADING_STATUS = {"TRADING", "PRE_TRADING"}


class BinanceAdapter(BaseAdapter):
    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = make_client()

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments: dict[str, InstrumentInfo] = {}

        # Fetch spot
        spot = await self._fetch_spot()
        instruments.update(spot)

        # Fetch futures
        if self.config.supports_futures and self.config.futures_url:
            futures = await self._fetch_futures()
            instruments.update(futures)

        return instruments

    async def _fetch_spot(self) -> dict[str, InstrumentInfo]:
        try:
            resp = await with_429_retry(
                lambda: self._client.get(self.config.spot_url)
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as e:
            raise AdapterError(f"binance spot: {e}") from e

        # Surface application-layer errors that return 2xx with an error body
        if isinstance(data, dict) and "code" in data and data.get("code") != 0:
            raise AdapterError(f"binance spot: API error: {data.get('msg')}")

        instruments: dict[str, InstrumentInfo] = {}
        for sym in data.get("symbols", []):
            status = sym.get("status", "")
            symbol = sym.get("symbol", "")
            if not symbol:
                continue

            # Only include TRADING symbols
            if status not in TRADING_STATUS:
                continue

            # Detect alpha/pre-listing status
            permissions = sym.get("permissions", []) or sym.get("permissionSets", [])
            flat_perms = []
            for p in permissions:
                if isinstance(p, list):
                    flat_perms.extend(p)
                else:
                    flat_perms.append(p)

            if "TRD_GRP_BINANCE_ALPHA" in flat_perms:
                listing_type = ListingType.ALPHA
            else:
                listing_type = ListingType.SPOT

            instruments[f"binance:spot:{symbol}"] = InstrumentInfo(
                symbol=symbol,
                base=sym.get("baseAsset", ""),
                quote=sym.get("quoteAsset", ""),
                listing_type=listing_type,
                status=status,
            )
        return instruments

    async def _fetch_futures(self) -> dict[str, InstrumentInfo]:
        try:
            resp = await with_429_retry(
                lambda: self._client.get(self.config.futures_url)
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as e:
            raise AdapterError(f"binance futures: {e}") from e

        if isinstance(data, dict) and "code" in data and data.get("code") != 0:
            raise AdapterError(f"binance futures: API error: {data.get('msg')}")

        instruments: dict[str, InstrumentInfo] = {}
        for sym in data.get("symbols", []):
            symbol = sym.get("symbol", "")
            status = sym.get("status", "")
            if not symbol:
                continue

            # Only include TRADING symbols
            if status not in TRADING_STATUS:
                continue

            instruments[f"binance:futures:{symbol}"] = InstrumentInfo(
                symbol=symbol,
                base=sym.get("baseAsset", ""),
                quote=sym.get("quoteAsset", ""),
                listing_type=ListingType.FUTURES,
                status=status,
            )
        return instruments

    async def close(self) -> None:
        await self._client.aclose()
