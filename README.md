# tgcryptolisting

Track new cryptocurrency exchange listings across 8 major exchanges and deliver alerts via Telegram.

## Features

- **8 Exchanges**: Binance, OKX, Coinbase, Upbit, Bithumb, Bybit, Bitget, Kraken
- **Listing Classification**:
  - `(S)` — Spot listing
  - `(F)` — Perpetual/futures listing
  - `(R)` — Coinbase roadmap addition (not yet live)
  - `(A/O)` — Alpha/pre-listing (e.g., Binance Alpha)
- **Dual Output**: Real-time push alerts + daily Telegram digest
- **Snapshot Diffing**: Compares current exchange state against stored snapshots to detect new pairs
- **Hybrid Architecture**: [ccxt](https://github.com/ccxt/ccxt) for commodity exchanges + custom HTTP adapters for exchanges requiring metadata (Binance Alpha, OKX listTime, Coinbase roadmap)
- **Resilient Storage**: Atomic JSON writes, append-only daily journal, backup rotation, staleness detection

## Sample Output

```
--------- Listing Daily Report 26/03/2026 ---------
Exchange  | Listing
----------+---------------------------
Binance   | NEWUSDT (S), ENAUSDT (F)
Coinbase  | MEGA (R)
Bybit     | XRPUSDT (F)
----------+---------------------------
OKX       | n/a
Upbit     | n/a
Bithumb   | n/a
Bitget    | n/a
Kraken    | n/a

S=Spot F=Futures R=Roadmap A/O=Alpha/Other
```

Exchanges with new listings appear first (in priority order). Exchanges with no activity show `n/a` below the separator.

## Installation

```bash
git clone https://github.com/0xminion/tgcryptolisting.git
cd tgcryptolisting
pip install -e .
```

## Usage

```bash
# One-shot check — prints results to stdout
python -m listing_tracker.main check

# Poll mode — fetch snapshots, diff, push real-time alerts to Telegram
python -m listing_tracker.main poll

# Report mode — generate and send daily digest to Telegram
python -m listing_tracker.main report
```

## Hermes Agent Integration

This project is designed to run as a [hermes-agent](https://hermes-agent.nousresearch.com/docs) skill with cron scheduling.

### Setup

1. Copy the project to your hermes skills directory:
   ```bash
   cp -r . ~/.hermes/skills/listing-tracker/
   ```

2. Create cron jobs:
   ```bash
   # Real-time polling every 15 minutes
   hermes cron create "*/15 * * * *" "python -m listing_tracker.main poll"

   # Daily digest at 9 AM UTC
   hermes cron create "0 9 * * *" "python -m listing_tracker.main report"
   ```

3. Ensure Telegram is configured as a delivery channel in hermes (`~/.hermes/config.yaml`).

## Architecture

```
main.py (asyncio orchestrator)
  │
  ├── asyncio.gather() with 30s per-adapter timeout
  │
  ├── ccxt adapters ──── Upbit, Bithumb, Kraken
  ├── custom adapters ── Binance (spot+futures+alpha), OKX, Bybit, Bitget
  └── coinbase adapter ─ Products API (S) + web_search roadmap (R)
  │
  ├── storage.py ─── Atomic JSON snapshots + append-only journal
  ├── differ.py ──── Snapshot comparison + classification tagging
  ├── formatter.py ─ Telegram HTML formatting
  └── alerter.py ─── Real-time push + daily digest delivery
```

## Exchange API Endpoints

| Exchange | Type | Endpoint | Auth |
|----------|------|----------|------|
| Binance | Custom | `/api/v3/exchangeInfo` + `/fapi/v1/exchangeInfo` | Public |
| OKX | Custom | `/api/v5/public/instruments` (SPOT + SWAP) | Public |
| Coinbase | Custom | `/products` | Public |
| Upbit | ccxt | `load_markets()` | Public |
| Bithumb | ccxt | `load_markets()` | Public |
| Bybit | Custom | `/v5/market/instruments-info` (spot + linear) | Public |
| Bitget | Custom | `/api/v2/spot/public/symbols` + `/api/v2/mix/market/tickers` | Public |
| Kraken | ccxt | `load_markets()` | Public |

All endpoints are public and free — no API keys required.

## Data Storage

Snapshots and journals are stored in `~/.hermes/skills/listing-tracker/data/`:

```
data/
├── snapshots/
│   ├── binance_all.json      # Latest exchange state
│   ├── binance_all.bak       # Previous snapshot (backup)
│   ├── okx_all.json
│   └── ...
└── journal/
    ├── journal_2026-03-25.json  # All listings detected that day
    └── journal_2026-03-26.json
```

- **Atomic writes**: Write to `.tmp` then `os.rename()` — no corruption on crash
- **Backup rotation**: Previous snapshot kept as `.bak`
- **Staleness detection**: Warning if any exchange shows n/a for 7+ consecutive polls

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

26 tests covering snapshot diffing, storage atomicity, Telegram formatting, HTML escaping, message splitting, staleness tracking, and classification tagging.

## Dependencies

- `httpx` — Async HTTP client
- `ccxt` — Unified exchange library
- `python-dateutil` — Date parsing

## License

MIT
