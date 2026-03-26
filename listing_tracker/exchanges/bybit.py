"""Bybit adapter — spot + linear futures with pagination."""

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

BASE_URL = "https://api.bybit.com/v5/market/instruments-info"


class BybitAdapter(BaseAdapter):
    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(timeout=ADAPTER_TIMEOUT_SECONDS)

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments: dict[str, InstrumentInfo] = {}

        for category, listing_type in [
            ("spot", ListingType.SPOT),
            ("linear", ListingType.FUTURES),
        ]:
            items = await self._fetch_category(category)
            for item in items:
                symbol = item.get("symbol", "")
                if not symbol:
                    continue
                instruments[f"bybit:{category}:{symbol}"] = InstrumentInfo(
                    symbol=symbol,
                    base=item.get("baseCoin", ""),
                    quote=item.get("quoteCoin", item.get("settleCoin", "")),
                    listing_type=listing_type,
                    status=item.get("status", ""),
                )
        return instruments

    async def _fetch_category(self, category: str) -> list[dict]:
        """Fetch instruments for a category, handling pagination for linear."""
        all_items: list[dict] = []
        cursor = None

        while True:
            params: dict = {"category": category, "limit": "1000"}
            if cursor:
                params["cursor"] = cursor

            try:
                resp = await self._client.get(BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                raise AdapterError(f"bybit {category}: {e}") from e

            result = data.get("result", {})
            items = result.get("list", [])
            all_items.extend(items)

            cursor = result.get("nextPageCursor", "")
            # Spot doesn't paginate; linear may
            if not cursor or category == "spot":
                break

        return all_items

    async def close(self) -> None:
        await self._client.aclose()
