"""Base adapter classes for exchange listing tracking."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import ccxt

from listing_tracker.config import ExchangeConfig

logger = logging.getLogger(__name__)


class ListingType(str, Enum):
    SPOT = "S"
    FUTURES = "F"
    ROADMAP = "R"
    ALPHA = "A/O"


@dataclass
class InstrumentInfo:
    symbol: str
    base: str
    quote: str
    listing_type: ListingType
    status: str = "active"
    list_time: str | None = None  # ISO timestamp if available
    raw: dict | None = None  # Original exchange data


class AdapterError(Exception):
    """Raised when an adapter fails to fetch data."""


class BaseAdapter(ABC):
    """Abstract base class for exchange adapters."""

    def __init__(self, config: ExchangeConfig):
        self.config = config

    @property
    def exchange_name(self) -> str:
        return self.config.name

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @abstractmethod
    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        """Fetch all current instruments from the exchange.

        Returns a dict mapping symbol string to InstrumentInfo.
        Raises AdapterError on failure.
        """
        ...

    async def close(self) -> None:
        """Clean up resources. Override if needed."""
        pass


class CcxtAdapter(BaseAdapter):
    """Adapter that uses ccxt for exchanges where basic symbol listing suffices."""

    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        if not config.ccxt_id:
            raise ValueError(f"ccxt_id required for CcxtAdapter: {config.name}")
        exchange_class = getattr(ccxt, config.ccxt_id)
        self._exchange: ccxt.Exchange = exchange_class({"enableRateLimit": True})

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        try:
            markets = self._exchange.load_markets(reload=True)
        except ccxt.BaseError as e:
            raise AdapterError(f"{self.exchange_name}: ccxt error: {e}") from e

        instruments: dict[str, InstrumentInfo] = {}
        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            listing_type = (
                ListingType.FUTURES
                if market.get("type") in ("swap", "future")
                else ListingType.SPOT
            )
            instruments[symbol] = InstrumentInfo(
                symbol=symbol,
                base=market.get("base", ""),
                quote=market.get("quote", ""),
                listing_type=listing_type,
                status="active",
            )
        return instruments

    async def close(self) -> None:
        self._exchange.close()
