"""Coinbase adapter — products API for spot + web_search for roadmap."""

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

PRODUCTS_URL = "https://api.exchange.coinbase.com/products"


class CoinbaseAdapter(BaseAdapter):
    """Coinbase adapter with dual tracking: API products + roadmap via web_search.

    The roadmap check (R) is handled separately by the orchestrator using
    hermes web_search, since it requires LLM-based search. This adapter
    only handles the structured products API for spot listings (S).
    """

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(timeout=ADAPTER_TIMEOUT_SECONDS)

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        try:
            resp = await self._client.get(PRODUCTS_URL)
            resp.raise_for_status()
            products = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AdapterError(f"coinbase: {e}") from e

        instruments: dict[str, InstrumentInfo] = {}
        for product in products:
            product_id = product.get("id", "")
            if not product_id:
                continue
            status = product.get("status", "")
            instruments[f"coinbase:spot:{product_id}"] = InstrumentInfo(
                symbol=product_id,
                base=product.get("base_currency", ""),
                quote=product.get("quote_currency", ""),
                listing_type=ListingType.SPOT,
                status=status,
            )
        return instruments

    async def close(self) -> None:
        await self._client.aclose()
