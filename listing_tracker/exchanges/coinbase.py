"""Coinbase adapter — products API for spot + DuckDuckGo search for roadmap."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

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

# Coinbase roadmap search phrases — must appear near a token mention
_ROADMAP_KEYWORDS = {
    "roadmap", "listing roadmap", "added to roadmap", "new asset",
    "adding", "will list", "plans to list", "listing plan",
}


class CoinbaseAdapter(BaseAdapter):
    """Coinbase adapter with dual tracking: API products + roadmap via DuckDuckGo.

    The roadmap check searches for Coinbase roadmap additions via DuckDuckGo news
    search and cross-references against the products API to avoid false positives.
    Only tokens that are NOT already in the products API are tagged as (R).
    """

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(
            timeout=ADAPTER_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments = await self._fetch_spot_products()

        # Roadmap search — cross-reference against spot products to avoid duplicates
        spot_bases = {
            info.base.upper() for info in instruments.values()
        }
        roadmap = await self._fetch_roadmap(existing_bases=spot_bases)
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

    async def _fetch_roadmap(
        self, existing_bases: set[str]
    ) -> dict[str, InstrumentInfo]:
        """Search for Coinbase roadmap token additions via DuckDuckGo news search.

        Cross-references results against:
        1. The skip list of common English/crypto words
        2. Existing spot products (already listed = not roadmap)
        3. Requires the token mention to appear in an article that also
           contains a roadmap-related keyword.
        """
        instruments: dict[str, InstrumentInfo] = {}

        try:
            from ddgs import DDGS

            year = datetime.now(timezone.utc).year
            query = f"Coinbase roadmap new token listing added {year}"
            results = await asyncio.to_thread(
                lambda: list(DDGS().news(query, max_results=10))
            )

            seen_tokens: set[str] = set()
            for r in results:
                body = (r.get("body", "") or "").upper()
                title = (r.get("title", "") or "").upper()
                combined = f"{title} {body}"

                # Only consider articles that mention roadmap-related keywords
                has_roadmap_keyword = any(
                    kw.upper() in combined for kw in _ROADMAP_KEYWORDS
                )
                if not has_roadmap_keyword:
                    continue

                # Extract ticker-like patterns: 3-6 uppercase letters
                # (skip 2-letter words — too many false positives: IS, OF, TO, etc.)
                matches = re.findall(r"\b([A-Z]{3,6})\b", combined)
                for m in matches:
                    if m in _SKIP_WORDS:
                        continue
                    if m in existing_bases:
                        # Already listed on Coinbase — not a roadmap addition
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
                    len(seen_tokens),
                    sorted(seen_tokens),
                )

        except ImportError:
            logger.debug("ddgs not installed — skipping Coinbase roadmap search")
        except Exception as e:
            logger.warning("Coinbase roadmap search failed: %s", e)

        return instruments

    async def close(self) -> None:
        await self._client.aclose()


# Large skip list of common English words, crypto terms, and acronyms
# that match the 2-6 uppercase letter pattern but are not token tickers
_SKIP_WORDS = frozenset({
    # Common English 3-6 letter words
    "THE", "AND", "FOR", "NEW", "ADD", "HAS", "ARE", "WAS", "BUT", "NOT",
    "ALL", "NOW", "CAN", "FROM", "THIS", "THAT", "WITH", "WILL", "YOUR",
    "ITS", "MAY", "HAD", "HIS", "HER", "OUR", "BEEN", "HAVE", "WERE",
    "THEY", "SAID", "EACH", "WHICH", "THEIR", "WOULD", "ABOUT", "COULD",
    "OTHER", "INTO", "THAN", "SOME", "WHEN", "WHAT", "THERE",
    "ALSO", "AFTER", "OVER", "JUST", "MORE", "MOST", "ONLY", "VERY",
    "LIKE", "BACK", "YEAR", "LAST", "NEXT", "MUCH", "TAKE", "COME",
    "MADE", "FIND", "HERE", "KNOW", "MANY", "WELL", "PART", "STILL",
    "EVEN", "SUCH", "LONG", "SAME", "MAKE", "BOTH", "GOOD", "FIRST",
    "BEING", "UNDER", "THOSE", "SINCE", "DOES", "GOING", "WHERE",
    "SAYS", "ADDS", "GETS", "PUTS", "RUNS", "LETS", "ASKS", "TOLD",
    "LOOK", "NEED", "WANT", "KEEP", "HELP", "SHOW", "GIVE", "CALL",
    "WORK", "MOVE", "LIVE", "REAL", "OPEN", "CLOSE", "HIGH", "LOW",
    "FULL", "HALF", "TURN", "ABLE", "GIANT", "KNOWN", "LITTLE", "NATIVE",
    "SMALL", "LARGE", "MAJOR", "MINOR", "EARLY", "LATE",
    # Crypto/finance terms
    "USD", "USDC", "USDT", "BTC", "ETH", "EUR", "GBP", "JPY", "AUD",
    "COIN", "TOKEN", "LIST", "TRADE", "PRICE", "ADDED", "SWAP", "SPOT",
    "SEC", "CEO", "CFO", "CTO", "API", "DEX", "CEX", "DEFI", "NFT",
    "IPO", "ETF", "NYSE", "CBOE", "FED",
    "CRYPTO", "BASED", "BLOCK", "CHAIN", "ASSET", "FUND",
    "MARKET", "SHARE", "STOCK", "BOND", "YIELD", "RATE",
    "PERP", "MARGIN", "LEVER", "LONG", "SHORT",
    # Coinbase-specific
    "BASE", "PRIME", "CLOUD", "PRO", "EARN", "VAULT",
    # News/media
    "NEWS", "BLOG", "POST", "READ", "PRESS", "TODAY",
    "REPORT", "UPDATE", "ALERT", "WATCH",
    # Common false-positive noise from news articles
    "USERS", "COULD", "WOULD", "PLANS", "BEGAN",
    "AMONG", "BELOW", "ABOVE", "UNTIL", "WHILE",
    "AFTER", "EVERY", "NEVER", "OFTEN", "SINCE",
})
