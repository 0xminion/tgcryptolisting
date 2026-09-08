# Token Listing Tracker

A deterministic, one-minute monitor for exchange token listings and delistings. It polls public market-universe endpoints concurrently, stores token-level state in SQLite, and delivers each transition through Hermes only after validating a destination-bound delivery receipt.

## Coverage

- Binance Spot
- Binance Perpetual Futures
- Binance Alpha
- OKX Spot and Perpetual Futures
- Coinbase Spot
- Upbit Spot
- Bithumb Spot
- Bybit Spot and Perpetual Futures
- Bitget Spot and Perpetual Futures
- Kraken Spot
- Robinhood Spot
- Aster Spot and Perpetual Futures
- Hyperliquid Spot and Perpetual Futures, including HIP-3 perp DEX universes

The monitor tracks **token-level product presence**, not every quote pair. Adding `TOKEN/BTC` for a token already trading as `TOKEN/USDT` does not create a false “new token” alert.

## Alert format

```text
Binance Alpha lists new tokens:

Pons (PONS)
Contract Address: 0x39dbed3a2bd333467115de45665cc57f813c4571 (Robinhood)

PONS  MarketCap: 519.1M
————————————
2026-09-08 19:01:45
```

Delistings use the same structure with `delists tokens`. Contract address, network, and market capitalization are printed only when the monitored exchange endpoint provides a verifiable value. Otherwise the fixed lines remain present as `Not available` and `n/a`; the monitor does not guess identity from a ticker.

## Detection semantics

- The first successful response from each source creates a **silent baseline**.
- A newly active token or an inactive-to-active transition emits a listing.
- Explicit terminal states such as `fullyDelisted`, `delisted`, or a scheduled `offTime` emit a delisting immediately.
- Temporary or ambiguous unavailable states do not produce fake delisting/relisting cycles while the product remains in the venue inventory.
- A token missing from an otherwise healthy response must be absent on two consecutive polls before a delisting is emitted.
- A snapshot that loses more than half its prior token set is rejected without changing state. This prevents API degradation from becoming hundreds of fake delistings.
- Source failures are isolated. A failed venue cannot mutate or delete its last known state.
- Events enter a durable SQLite outbox. They remain pending until `hermes send --json` proves the requested platform, chat, and message ID. A thread-scoped target is rejected if the installed Hermes receipt cannot prove the effective thread.
- A filesystem lock plus a 50-second process deadline prevents overlapping one-minute polls.

## Run locally

Python 3.11 or later is required.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'

# Fetch every live dependency without changing state
.venv/bin/python -m listing_tracker.live probe

# Build a silent baseline in a disposable database
.venv/bin/python -m listing_tracker.live poll \
  --db /tmp/listing-tracker.db \
  --stdout-delivery

# Inspect source health and pending events
.venv/bin/python -m listing_tracker.live status \
  --db /tmp/listing-tracker.db

# Test suite
.venv/bin/python -m pytest -q
```

## Production layout

The deployed no-agent job uses:

- checkout: `~/services/token-listing-tracker`
- wrapper: `deploy/run-live-monitor.sh`
- Hermes adapter: `~/.hermes/scripts/token-listing-monitor.sh`
- runtime config: `~/.config/token-listing-tracker.env` with mode `0600`
- state: `~/.local/state/token-listing-tracker/listings.db`
- schedule: `*/1 * * * *`
- inference: none (`no_agent: true`)

Example runtime config:

```bash
LISTING_TRACKER_TARGET=telegram:<chat_id>
LISTING_TRACKER_TIMEZONE=Australia/Perth
```

For Hermes `0.21.0`, use a parent Telegram chat unless a live `hermes send --json` receipt proves the effective thread ID. A successful process exit without a target-bound receipt is not an acknowledgement.

## Source contracts

The production path uses fixed HTTPS endpoints and strict response-envelope checks:

- Binance: Spot `exchangeInfo`, USDⓈ-M and COIN-M Futures `exchangeInfo`, and the documented Alpha token list
- OKX: public `instruments` for `SPOT` and `SWAP`
- Coinbase Exchange: public products
- Upbit and Bithumb: public market lists
- Bybit: V5 spot, linear, and inverse instrument info with bounded cursor pagination
- Bitget: V2 spot symbols plus USDT-, USDC-, and coin-margined futures contracts
- Kraken: public asset pairs
- Robinhood: public `nummus.robinhood.com/currency_pairs/` app endpoint
- Aster: documented SAPI/FAPI exchange information
- Hyperliquid: documented `spotMeta`, `perpDexs`, and per-DEX `meta` calls

Robinhood is the only adapter using an undocumented public app endpoint because Robinhood's supported Crypto Trading API requires account credentials. The adapter fails closed if that endpoint changes. It should be replaced with the authenticated supported API if dedicated Robinhood API credentials are provisioned.

## Important boundary

This system detects exchange product metadata and live/pre-open state changes. It does not scrape announcement articles, infer token contracts from ambiguous symbols, or claim that an event is a first-ever global token launch. A venue can publish an announcement before its market API changes; the monitor will alert when the venue exposes the product or a pre-listing state through the monitored endpoint.

See [TECHNICAL_DEEP_DIVE.md](TECHNICAL_DEEP_DIVE.md) for state, parsing, and delivery details.
