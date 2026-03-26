---
name: listing-tracker
description: Track new cryptocurrency exchange listings across 8 major exchanges (Binance, OKX, Coinbase, Upbit, Bithumb, Bybit, Bitget, Kraken). Supports spot, futures, roadmap, and alpha/pre-listing detection with Telegram alerts.
tools: [web_search, web_extract, send_message, execute_code]
---

# Listing Tracker

Track new cryptocurrency listings across 8 exchanges and deliver reports via Telegram.

## Usage

```bash
# Poll for new listings (run every 15 minutes via cron)
python -m listing_tracker.main poll

# Generate and send daily digest report
python -m listing_tracker.main report

# One-shot check (prints to stdout)
python -m listing_tracker.main check
```

## Cron Setup

```bash
# Real-time polling (every 15 minutes)
hermes cron create "*/15 * * * *" "Run listing tracker poll: python -m listing_tracker.main poll"

# Daily digest (9 AM UTC)
hermes cron create "0 9 * * *" "Run listing tracker report: python -m listing_tracker.main report"
```

## Classification Tags

- `(S)` — Spot listing
- `(F)` — Perpetual/futures listing
- `(R)` — Coinbase roadmap addition
- `(A/O)` — Alpha/pre-listing
- `n/a` — No new listings
- `ERROR` — Adapter failure
