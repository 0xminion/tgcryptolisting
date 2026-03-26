"""Bitget adapter — spot + futures."""

from __future__ import annotations

import logging

import httpx

from listing_tracker.config import ADAPTER_TIMEOUT_SECONDS, ExchangeConfig
from listing_tracker.exchanges.base import (
    AdapterError,
    BaseAdapter,
    InstrumentInfo,
    ListingType,
)

logger = logging.getLogger(__name__)

SPOT_URL = "https://api.bitget.com/api/v2/spot/public/symbols"
FUTURES_URL = "https://api.bitget.com/api/v2/mix/market/tickers"


class BitgetAdapter(BaseAdapter):
    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(
            timeout=ADAPTER_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            retries=3,
        )

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments: dict[str, InstrumentInfo] = {}

        # Spot
        spot_items = await self._fetch_spot()
        for item in spot_items:
            symbol = item.get("symbol", "")
            if not symbol:
                continue
            instruments[f"bitget:spot:{symbol}"] = InstrumentInfo(
                symbol=symbol,
                base=item.get("baseCoin", ""),
                quote=item.get("quoteCoin", ""),
                listing_type=ListingType.SPOT,
                status=item.get("status", ""),
            )

        # Futures
        if self.config.supports_futures:
            futures_items = await self._fetch_futures()
            for item in futures_items:
                symbol = item.get("symbol", "")
                if not symbol:
                    continue
                instruments[f"bitget:futures:{symbol}"] = InstrumentInfo(
                    symbol=symbol,
                    base=item.get("baseCoin", ""),
                    quote=item.get("quoteCoin", "USDT"),
                    listing_type=ListingType.FUTURES,
                    status=item.get("status", "active"),
                )

        return instruments

    async def _fetch_spot(self) -> list[dict]:
        try:
            resp = await self._client.get(SPOT_URL)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AdapterError(f"bitget spot: {e}") from e

        if data.get("code") != "00000":
            raise AdapterError(f"bitget spot: API error: {data.get('msg')}")
        return data.get("data", [])

    async def _fetch_futures(self) -> list[dict]:
        try:
            resp = await self._client.get(
                FUTURES_URL, params={"productType": "USDT-FUTURES"}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AdapterError(f"bitget futures: {e}") from e

        if data.get("code") != "00000":
            raise AdapterError(f"bitget futures: API error: {data.get('msg')}")
        return data.get("data", [])

    async def close(self) -> None:
        await self._client.aclose()
