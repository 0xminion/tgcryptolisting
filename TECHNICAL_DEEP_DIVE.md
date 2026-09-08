# Technical Deep Dive

## Runtime chain

```text
Hermes one-minute ticker
  -> ~/.hermes/scripts/token-listing-monitor.sh
  -> $HOME/services/token-listing-tracker/deploy/run-live-monitor.sh
  -> listing_tracker.live poll
  -> concurrent public API fetches
  -> strict per-source parsing
  -> atomic SQLite transitions and outbox
  -> one alert per hermes send invocation
  -> destination-bound JSON receipt
  -> delivered_at acknowledgement
```

The production cron is `no_agent: true`. Polling and delivery use no LLM calls.

## Source isolation

`fetch_snapshots()` owns one `httpx.AsyncClient` with environment proxies disabled, bounded timeouts, fixed HTTPS hosts, no automatic redirects, and a 50 MB response ceiling. All source coroutines run concurrently with `asyncio.gather(..., return_exceptions=True)`.

Each parser checks the venue's success envelope and required structural fields before it returns a `Snapshot`. A failed parser is treated like a failed network request: no state for that source changes.

Robinhood pagination is restricted to HTTPS URLs on `nummus.robinhood.com`. Bybit pagination is capped at ten 1,000-row pages for both linear and inverse derivatives. Binance merges USDⓈ-M and COIN-M perpetuals, while Bitget merges USDT-, USDC-, and coin-margined futures. Hyperliquid discovers the core and HIP-3 perp DEX set through `perpDexs`, then requires every selected metadata response before updating the combined futures snapshot.

## Token-level normalization

Every source returns `dict[instrument_id, Asset]`:

- centralized-exchange spot and perpetual sources group all quote pairs by base asset;
- Binance Alpha uses `alphaId`, not the reusable display ticker;
- Robinhood uses the asset UUID;
- Hyperliquid Spot uses `tokenId`;
- Hyperliquid Futures uses `dex:name` so HIP-3 universes cannot collide;
- active state is merged with logical OR, so one delisted pair cannot override another live pair for the same token.

This normalization intentionally ignores new quote-pair combinations for an already listed base token.

## State machine

SQLite runs in WAL mode with foreign keys and a busy timeout. `StateStore.apply()` uses `BEGIN IMMEDIATE`, validates the complete snapshot, and commits source metadata, asset rows, and event rows in one transaction.

For each source:

1. No previous source row: insert a silent baseline.
2. New active asset: create `listed` event.
3. Existing inactive asset becomes active: create another `listed` event.
4. Existing active asset receives a terminal inactive state: create `delisted` immediately.
5. Existing active asset receives a source-authenticated but non-terminal removal candidate: require two consecutive responses.
6. Temporary or ambiguous unavailability (`BREAK`, `suspend`, `offline`, `halt`, or Robinhood `untradable`) never becomes a delisting while the product remains present.
7. Existing active asset disappears: require two consecutive complete responses.
8. Missing or removal-candidate state recovers before confirmation: clear the streak without an event.
9. Snapshot count drops to 50% or less of that source's historical maximum: roll back and reject the response. The maximum never ratchets down after a partial response.

The state machine preserves the last confirmed listed state across ambiguous unavailability, but a product that first appears unavailable remains unlisted until it becomes active. Robinhood `display_only=true` is treated as an explicit move to view-only; plain `untradable` is not.

The event journal allows repeated list/delist/relist lifecycles. The outbox is the subset where `delivered_at IS NULL`. Delivery workers atomically claim an event using a random token and expiring lease. Compare-and-set acknowledgement prevents two workers from acknowledging or intentionally delivering the same live lease.

## Delivery contract

Each event is formatted independently and sent with:

```bash
hermes send --to "$LISTING_TRACKER_TARGET" --file - --json
```

The subprocess never uses a shell. The receipt validator requires:

- `success` is the Boolean `true`;
- receipt platform exactly matches the requested platform;
- receipt chat ID exactly matches the requested chat;
- a valid external message ID exists;
- if a thread was requested, the receipt explicitly reports that same effective thread.

Only then is `delivered_at` persisted. A timeout, nonzero exit, malformed JSON, wrong destination, or thread-unbound receipt leaves the event pending for the next minute. Events are sent oldest-first and delivery stops on the first failure, preserving order.

Because message APIs cannot make “send and local acknowledgement” atomic, a process crash after external acceptance but before the SQLite acknowledgement can produce a duplicate. This is the unavoidable at-least-once boundary; silent loss is treated as worse than a duplicate.

## Alert data provenance

Ticker and name come from the monitored venue. A contract address and network are included only where the same venue payload supplies them:

- Binance Alpha: `contractAddress` and `chainName`;
- Aster Spot: `baseAssetAddress`; no chain label is invented because the endpoint does not supply one;
- Hyperliquid Spot: `evmContract.address`, otherwise the exact HyperCore `tokenId` with an explicit label.

Market capitalization is emitted only when the same payload provides it, currently Binance Alpha. No ticker-based cross-provider lookup is used because ticker collisions can attach a plausible-looking but wrong contract or market cap. Missing values are explicit rather than synthesized.

## Operational behavior

`deploy/run-live-monitor.sh`:

- requires a readable runtime config;
- creates the state directory under a restrictive umask;
- takes a non-blocking `flock`;
- unsets `HTTPS_PROXY`, `HTTP_PROXY`, and `ALL_PROXY`;
- enforces a 50-second hard deadline;
- exits silently on lock contention;
- leaves source and delivery errors on stderr for cron run diagnostics.

The poll command exits nonzero when delivery fails or any selected source fails to produce an accepted snapshot. Source isolation still allows healthy venues to persist and self-deliver valid events before the run reports degraded coverage.

## Primary documentation

- [Binance Spot API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-endpoints#exchange-information)
- [Binance USDⓈ-M Futures API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
- [Binance Alpha API](https://developers.binance.com/docs/alpha/market-data/rest-api/get-token-list)
- [OKX public instruments](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)
- [Coinbase Exchange products](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-all-known-trading-pairs)
- [Upbit trading pairs](https://global-docs.upbit.com/reference/list-trading-pairs)
- [Bithumb trading pairs](https://apidocs.bithumb.com/reference/마켓코드-조회)
- [Bybit instrument info](https://bybit-exchange.github.io/docs/v5/market/instrument)
- [Bitget spot symbols](https://www.bitget.com/api-doc/spot/market/Get-Symbols)
- [Bitget futures contracts](https://www.bitget.com/api-doc/contract/market/Get-All-Symbols-Contracts)
- [Kraken asset pairs](https://docs.kraken.com/api/docs/rest-api/get-tradable-asset-pairs/)
- [Robinhood Crypto Trading API](https://docs.robinhood.com/crypto/trading/)
- [Aster API documentation](https://docs.asterdex.com/for-developers/aster-api/api-documentation)
- [Hyperliquid Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
