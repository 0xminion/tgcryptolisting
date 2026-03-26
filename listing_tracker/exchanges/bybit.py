"""Bybit adapter — spot + linear futures with pagination."""

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

BASE_URL = "https://api.bybit.com/v5/market/instruments-info"

# Bybit status values that indicate a tradeable instrument
TRADING_STATUSES = {"Trading", "Open", "Normal"}


class BybitAdapter(BaseAdapter):
    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = make_client()

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
                status = item.get("status", "")
                if status not in TRADING_STATUSES:
                    continue
                instruments[f"bybit:{category}:{symbol}"] = InstrumentInfo(
                    symbol=symbol,
                    base=item.get("baseCoin", ""),
                    quote=item.get("quoteCoin", item.get("settleCoin", "")),
                    listing_type=listing_type,
                    status=status,
                )
        return instruments

    async def _fetch_category(self, category: str, max_pages: int = 20) -> list[dict]:
        """Fetch instruments for a category, handling pagination for linear."""
        all_items: list[dict] = []
        cursor = None

        for _page in range(max_pages):
            params: dict = {"category": category, "limit": "1000"}
            if cursor:
                params["cursor"] = cursor

            try:
                resp = await with_429_retry(
                    lambda params=params: self._client.get(BASE_URL, params=params)
                )
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as e:
                raise AdapterError(f"bybit {category}: {e}") from e

            if isinstance(data, dict) and data.get("retCode") != 0:
                raise AdapterError(
                    f"bybit {category}: API error {data.get('retCode')}: "
                    f"{data.get('retMsg')}"
                )

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
