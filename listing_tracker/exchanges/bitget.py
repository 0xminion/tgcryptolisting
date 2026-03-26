"""Bitget adapter — spot + futures."""

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

SPOT_URL = "https://api.bitget.com/api/v2/spot/public/symbols"
FUTURES_URL = "https://api.bitget.com/api/v2/mix/market/contracts"

# Bitget status values that indicate a tradeable instrument
TRADING_STATUSES = {"online", "open"}


class BitgetAdapter(BaseAdapter):
    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = make_client()

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments: dict[str, InstrumentInfo] = {}

        # Spot
        spot_items = await self._fetch_spot()
        for item in spot_items:
            symbol = item.get("symbol", "")
            if not symbol:
                continue
            status = item.get("status", "")
            if status not in TRADING_STATUSES:
                continue
            instruments[f"bitget:spot:{symbol}"] = InstrumentInfo(
                symbol=symbol,
                base=item.get("baseCoin", ""),
                quote=item.get("quoteCoin", ""),
                listing_type=ListingType.SPOT,
                status=status,
            )

        # Futures (contracts endpoint)
        if self.config.supports_futures:
            futures_items = await self._fetch_futures()
            for item in futures_items:
                symbol = item.get("symbol", "")
                if not symbol:
                    continue
                status = item.get("symbolStatus", item.get("status", ""))
                if status and status not in TRADING_STATUSES:
                    continue
                instruments[f"bitget:futures:{symbol}"] = InstrumentInfo(
                    symbol=symbol,
                    base=item.get("baseCoin", ""),
                    quote=item.get("quoteCoin", ""),
                    listing_type=ListingType.FUTURES,
                    status=status or "active",
                )

        return instruments

    async def _fetch_spot(self) -> list[dict]:
        try:
            resp = await with_429_retry(lambda: self._client.get(SPOT_URL))
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as e:
            raise AdapterError(f"bitget spot: {e}") from e

        if isinstance(data, dict) and data.get("code") != "00000":
            raise AdapterError(f"bitget spot: API error: {data.get('msg')}")
        return data.get("data", []) if isinstance(data, dict) else []

    async def _fetch_futures(self) -> list[dict]:
        try:
            resp = await with_429_retry(
                lambda: self._client.get(FUTURES_URL, params={"productType": "USDT-FUTURES"})
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as e:
            raise AdapterError(f"bitget futures: {e}") from e

        if isinstance(data, dict) and data.get("code") != "00000":
            raise AdapterError(f"bitget futures: API error: {data.get('msg')}")
        return data.get("data", []) if isinstance(data, dict) else []

    async def close(self) -> None:
        await self._client.aclose()
