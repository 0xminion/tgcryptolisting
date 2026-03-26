"""Coinbase adapter — products API for spot + DuckDuckGo search for roadmap."""

from __future__ import annotations

import logging
import re

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

# Only include products that are actually live (open)
LIVE_STATUS = {"open"}


class CoinbaseAdapter(BaseAdapter):
    """Coinbase adapter with dual tracking: API products + roadmap via DuckDuckGo.

    The roadmap check searches for Coinbase roadmap additions via DuckDuckGo news
    search and marks tokens as ROADMAP (R).
    """

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(
            timeout=ADAPTER_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            retries=3,
        )

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments = await self._fetch_spot_products()
        # Also search for roadmap listings
        roadmap = await self._fetch_roadmap()
        instruments.update(roadmap)
        return instruments

    async def _fetch_spot_products(self) -> dict[str, InstrumentInfo]:
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
            if status not in LIVE_STATUS:
                continue
            instruments[f"coinbase:spot:{product_id}"] = InstrumentInfo(
                symbol=product_id,
                base=product.get("base_currency", ""),
                quote=product.get("quote_currency", ""),
                listing_type=ListingType.SPOT,
                status=status,
            )
        return instruments

    async def _fetch_roadmap(self) -> dict[str, InstrumentInfo]:
        """Search for Coinbase roadmap token additions via DuckDuckGo news search."""
        instruments: dict[str, InstrumentInfo] = {}

        try:
            from ddgs import DDGS

            query = "Coinbase roadmap new token listing added 2026"
            with DDGS() as ddgs:
                results = list(ddgs.news(query, max_results=10))

            seen_tokens: set[str] = set()
            for r in results:
                body = r.get("body", "") or ""
                title = r.get("title", "") or ""
                combined = f"{title} {body}".upper()

                # Look for ticker-like patterns (3-5 uppercase letters, possibly with -)
                matches = re.findall(r"\b([A-Z]{2,6}[-]?[A-Z]{0,4})\b", combined)
                for m in matches:
                    # Filter out generic words that match the pattern
                    skip = {
                        "USD", "USDC", "USDT", "BTC", "ETH", "EUR", "GBP",
                        "COIN", "THE", "AND", "FOR", "NEW", "ADD", "HAS",
                        "ARE", "WAS", "BUT", "NOT", "ALL", "NOW", "CAN",
                        "FROM", "THIS", "THAT", "WITH", "WILL", "YOUR",
                    }
                    if m in skip or len(m) > 6:
                        continue
                    if m in seen_tokens:
                        continue
                    seen_tokens.add(m)

            for token in seen_tokens:
                instruments[f"coinbase:roadmap:{token}"] = InstrumentInfo(
                    symbol=token,
                    base=token,
                    quote="USD",
                    listing_type=ListingType.ROADMAP,
                    status="roadmap",
                )

            if seen_tokens:
                logger.info(
                    "Coinbase roadmap: detected %d potential tokens: %s",
                    len(seen_tokens), sorted(seen_tokens),
                )

        except ImportError:
            logger.debug("ddgs not installed — skipping Coinbase roadmap search")
        except Exception as e:
            logger.warning("Coinbase roadmap search failed: %s", e)

        return instruments

    async def close(self) -> None:
        await self._client.aclose()
