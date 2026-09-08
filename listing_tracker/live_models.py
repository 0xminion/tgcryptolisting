"""Data contracts for the low-latency listing/delisting monitor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class ChangeKind(str, Enum):
    LISTED = "listed"
    DELISTED = "delisted"


@dataclass(frozen=True, slots=True)
class Asset:
    """One token-level product state from one exchange/product source."""

    instrument_id: str
    ticker: str
    name: str
    active: bool
    terminal: bool = False
    inactive_is_removal: bool = True
    status: str = ""
    contract_address: str | None = None
    network: str | None = None
    market_cap: Decimal | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A complete point-in-time token set for one exchange product."""

    source_id: str
    source_label: str
    assets: dict[str, Asset]


@dataclass(frozen=True, slots=True)
class Change:
    """A persisted active/inactive transition."""

    source_id: str
    source_label: str
    kind: ChangeKind
    asset: Asset
    detected_at: datetime
    event_id: int | None = None
    lease_token: str | None = None
