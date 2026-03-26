"""OKX adapter — spot + swap with listTime support."""

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

BASE_URL = "https://www.okx.com/api/v5/public/instruments"

# OKX instrument states that indicate a live, tradeable instrument
LIVE_STATES = {"live"}


class OkxAdapter(BaseAdapter):
    def __init__(self, config: ExchangeConfig):
        super().__init__(config)
        self._client = httpx.AsyncClient(
            timeout=ADAPTER_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            retries=3,
        )

    async def fetch_instruments(self) -> dict[str, InstrumentInfo]:
        instruments: dict[str, InstrumentInfo] = {}

        for inst_type, listing_type in [
            ("SPOT", ListingType.SPOT),
            ("SWAP", ListingType.FUTURES),
        ]:
            data = await self._fetch_type(inst_type)
            for inst in data:
                inst_id = inst.get("instId", "")
                if not inst_id:
                    continue
                state = inst.get("state", "")
                if state not in LIVE_STATES:
                    continue
                list_time = inst.get("listTime", "")

                instruments[f"okx:{inst_type.lower()}:{inst_id}"] = InstrumentInfo(
                    symbol=inst_id,
                    base=inst.get("baseCcy", inst.get("ctValCcy", "")),
                    quote=inst.get("quoteCcy", inst.get("settleCcy", "")),
                    listing_type=listing_type,
                    status=state,
                    list_time=list_time if list_time else None,
                )
        return instruments

    async def _fetch_type(self, inst_type: str) -> list[dict]:
        try:
            resp = await self._client.get(
                BASE_URL, params={"instType": inst_type}
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AdapterError(f"okx {inst_type}: {e}") from e

        if data.get("code") != "0":
            raise AdapterError(f"okx {inst_type}: API error: {data.get('msg')}")

        return data.get("data", [])

    async def close(self) -> None:
        await self._client.aclose()
