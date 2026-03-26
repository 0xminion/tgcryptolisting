"""Configuration constants for listing tracker."""

from pathlib import Path
from dataclasses import dataclass
import os

# Storage paths — allow override via env var
_DATA_DIR = os.environ.get(
    "LISTING_TRACKER_DATA_DIR",
    str(Path.home() / ".hermes" / "skills" / "listing-tracker" / "data"),
)
DATA_DIR = Path(_DATA_DIR)
SNAPSHOT_DIR = DATA_DIR / "snapshots"
JOURNAL_DIR = DATA_DIR / "journal"

# Adapter timeout
ADAPTER_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3

# Polling
STALENESS_THRESHOLD_POLLS = 7  # 7 consecutive n/a = warning

# Journal retention
JOURNAL_RETENTION_DAYS = 30

# Telegram
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Exchange priority order (for display sorting in reports)
EXCHANGE_PRIORITY = [
    "binance",
    "okx",
    "coinbase",
    "bybit",
    "bitget",
    "upbit",
    "bithumb",
    "kraken",
]


@dataclass(frozen=True)
class ExchangeConfig:
    name: str
    display_name: str
    adapter_type: str  # "custom" or "ccxt"
    ccxt_id: str | None = None  # ccxt exchange id
    spot_url: str | None = None
    futures_url: str | None = None
    supports_futures: bool = False
    supports_alpha: bool = False


EXCHANGES: dict[str, ExchangeConfig] = {
    "binance": ExchangeConfig(
        name="binance",
        display_name="Binance",
        adapter_type="custom",
        spot_url="https://api.binance.com/api/v3/exchangeInfo",
        futures_url="https://fapi.binance.com/fapi/v1/exchangeInfo",
        supports_futures=True,
        supports_alpha=True,
    ),
    "okx": ExchangeConfig(
        name="okx",
        display_name="OKX",
        adapter_type="custom",
        spot_url="https://www.okx.com/api/v5/public/instruments",
        futures_url="https://www.okx.com/api/v5/public/instruments",
        supports_futures=True,
    ),
    "coinbase": ExchangeConfig(
        name="coinbase",
        display_name="Coinbase",
        adapter_type="custom",
        spot_url="https://api.exchange.coinbase.com/products",
    ),
    "upbit": ExchangeConfig(
        name="upbit",
        display_name="Upbit",
        adapter_type="ccxt",
        ccxt_id="upbit",
    ),
    "bithumb": ExchangeConfig(
        name="bithumb",
        display_name="Bithumb",
        adapter_type="ccxt",
        ccxt_id="bithumb",
    ),
    "bybit": ExchangeConfig(
        name="bybit",
        display_name="Bybit",
        adapter_type="custom",
        spot_url="https://api.bybit.com/v5/market/instruments-info",
        futures_url="https://api.bybit.com/v5/market/instruments-info",
        supports_futures=True,
    ),
    "bitget": ExchangeConfig(
        name="bitget",
        display_name="Bitget",
        adapter_type="custom",
        spot_url="https://api.bitget.com/api/v2/spot/public/symbols",
        futures_url="https://api.bitget.com/api/v2/mix/market/contracts",
        supports_futures=True,
    ),
    "kraken": ExchangeConfig(
        name="kraken",
        display_name="Kraken",
        adapter_type="ccxt",
        ccxt_id="kraken",
    ),
}
