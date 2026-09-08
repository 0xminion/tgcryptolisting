---
name: listing-tracker
description: Monitor exchange token listings and delistings live.
version: 0.2.0
author: 0xminion, Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [crypto, exchanges, listings, cron, telegram]
    related_skills: []
---

# Listing Tracker Skill

Operate a deterministic one-minute exchange listing and delisting monitor. The runtime polls public market-universe APIs, persists transitions in SQLite, and sends receipt-verified alerts without an LLM.

## When to Use

- Set up or audit live exchange listing and delisting alerts.
- Probe supported venue APIs and inspect token-universe health.
- Verify SQLite transition, retry, and delivery behavior.
- Do not use for exchange announcement summaries or ticker-based token attribution.

## Prerequisites

- Linux with Python 3.11+, `uv`, `flock`, and `timeout`.
- Hermes with a configured Telegram target.
- A production checkout and a user-owned runtime environment file.

## How to Run

Use `terminal` in the repository root:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m listing_tracker.live probe
.venv/bin/python -m pytest -q
```

Run a disposable silent baseline:

```bash
.venv/bin/python -m listing_tracker.live poll \
  --db /tmp/listing-tracker.db \
  --stdout-delivery
```

## Procedure

1. Run `listing_tracker.live probe`; require every configured source and zero errors.
2. Run the complete test suite; require all tests to pass.
3. Install a clean production checkout and its Python 3.11 virtual environment.
4. Create the runtime environment from `deploy/token-listing-tracker.env.example`, replace the placeholder with an explicit receipt-bindable target, and set mode `0600`.
5. Install a real copy of `deploy/hermes-scripts/token-listing-monitor.sh` beneath the Hermes scripts directory.
6. Run the installed adapter once; require exit code zero, no alert on baseline, every source initialized, and an empty outbox.
7. Create a `no_agent: true` Hermes cron on `*/1 * * * *`; use local normal delivery because the script self-delivers, and route scheduler failures to the alert chat.
8. Read the persisted cron record and several consecutive run records; require correct cadence, script, enabled state, and no delivery error.

## Pitfalls

- First poll is intentionally silent; it is a baseline, not proof that no venues exist.
- Track token bases, not quote pairs. A new quote pair for an old token is not a token listing.
- Never infer a contract or market cap from a ticker. Missing verified data remains explicit.
- Missing instruments require two consecutive healthy snapshots. Explicit terminal states are immediate.
- `origin` is not a durable cron target. Use an explicit platform/chat target.
- Hermes `0.21.0` does not prove an effective Telegram thread in `hermes send --json`; use a parent chat unless a live receipt binds the thread.
- A successful `hermes` exit code is insufficient. Acknowledge only a platform/chat/message-bound JSON receipt.
- A genuine future listing cannot be exercised safely during deployment; use deterministic transition fixtures for the pipeline and a clearly labeled routing canary for delivery.

## Verification

- [ ] All deterministic tests pass.
- [ ] All configured live sources probe successfully.
- [ ] Two consecutive live baseline polls create no event.
- [ ] Synthetic listing, delisting, missing-confirmation, relisting, malformed-response, and delivery-retry tests pass.
- [ ] Installed script resolves to the production checkout and is executable.
- [ ] Runtime environment is mode `0600`.
- [ ] Cron is script-only, enabled, scheduled every minute, and has a fresh ticker heartbeat.
- [ ] Delivery canary receipt binds the exact platform and parent chat.
- [ ] SQLite reports all sources initialized and no stuck outbox rows.
