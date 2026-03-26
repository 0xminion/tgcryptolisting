# Token Listing Tracker: A Technical Deep Dive

*How we built a real-time exchange monitoring system, the bugs that humbled us, and the engineering lessons buried in the fix commits.*

---

## The Problem: Why This Exists

Imagine you're a crypto trader. A new token gets listed on Binance. Within *seconds*, the price can 10x. By the time you see the announcement tweet, it's already too late — bots and insiders got there first.

This project is our answer to that: a system that watches 8 major exchanges simultaneously, detects the moment a new trading pair appears, and fires a Telegram alert before most humans even know it happened.

Sounds simple, right? Fetch some APIs, compare some lists, send a message. But the devil is *entirely* in the details — and we found every single one of those devils.

---

## The Architecture: A Bird's Eye View

```
                        ┌─────────────────┐
                        │    main.py       │
                        │  (orchestrator)  │
                        └────────┬────────┘
                                 │
                    asyncio.gather() — all at once
                                 │
         ┌───────┬───────┬───────┼───────┬───────┬───────┐
         ▼       ▼       ▼       ▼       ▼       ▼       ▼
      Binance  OKX   Coinbase  Bybit  Bitget  Upbit  Bithumb  Kraken
      (custom) (custom)(custom)(custom)(custom) (ccxt)  (ccxt)  (ccxt)
         │       │       │       │       │       │       │       │
         └───────┴───────┴───────┼───────┴───────┴───────┴───────┘
                                 │
                                 ▼
                        ┌────────────────┐
                        │   differ.py    │
                        │ (spot the diff)│
                        └───────┬────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              storage.py   formatter.py  alerter.py
              (snapshots)  (pretty HTML) (Telegram)
```

The core idea: fetch the current state of every exchange, compare it to what we saw last time, and scream about anything new.

Three modes of operation:
- **`poll`** — the workhorse. Runs every 15 minutes via cron. Fetches, diffs, alerts in real-time.
- **`report`** — the daily newspaper. Compiles the last 24 hours into a formatted digest every morning.
- **`check`** — the debugging buddy. Same as poll but prints to stdout instead of Telegram.

---

## The Hybrid Adapter Pattern: Why One Size Doesn't Fit All

Here's the first real engineering decision. There's a popular library called [ccxt](https://github.com/ccxt/ccxt) that provides a unified API across hundreds of exchanges. So why not just use it everywhere?

Because **every exchange is a special snowflake**.

Upbit, Bithumb, and Kraken? They're well-behaved. `load_markets()` gives you everything you need. Four lines of code, done:

```python
# upbit.py — the entire file
from listing_tracker.exchanges.base import CcxtAdapter
```

That's it. The `CcxtAdapter` base class handles everything. These exchanges are the "good students" of the crypto world.

But then there's Binance. Binance has "Alpha" listings — tokens in a pre-launch phase that show up with a special permission flag (`TRD_GRP_BINANCE_ALPHA`) buried inside nested arrays. ccxt doesn't know about this. If we used ccxt for Binance, we'd miss early-stage listings — which are arguably the *most valuable* ones to detect.

OKX gives you a `listTime` field — the exact moment a pair went live. Coinbase has a "roadmap" — tokens announced but not yet tradeable. Bybit paginates with cursors. Bitget can't decide whether the field is called `status` or `symbolStatus`.

So we built a **hybrid architecture**:

```python
class AdapterRegistry:
    """Plugin registry for custom adapters."""
    _adapters: ClassVar[dict[str, type[BaseAdapter]]] = {}

    @classmethod
    def register(cls, name, adapter_cls):
        cls._adapters[name] = adapter_cls

    @classmethod
    def get(cls, name, config):
        return cls._adapters[name](config)
```

Custom adapters register themselves. The orchestrator doesn't care *how* each exchange gets its data — it just calls `adapter.fetch_instruments()` and gets back a uniform `dict[str, InstrumentInfo]`.

**The lesson:** Don't force uniformity where it doesn't exist. Use a unified interface, but let the implementations be as weird as they need to be. The adapter pattern exists precisely for this — same contract, different guts.

---

## The Concurrency Model: asyncio and the Thread Pool Escape Hatch

All 8 exchanges are fetched simultaneously:

```python
tasks = [fetch_exchange(adapter) for adapter in adapters]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

Each adapter gets 30 seconds. If Bithumb is having a bad day, it doesn't hold up the other seven.

But here's a subtlety that bit us early: **ccxt is synchronous**. It uses `requests` under the hood, which blocks the event loop. Call `load_markets()` directly in an async function and you've just frozen *everything* for however long that HTTP call takes.

The fix:

```python
markets = await asyncio.to_thread(self._exchange.load_markets, reload=True)
```

`asyncio.to_thread()` punts the blocking call to a thread pool, keeping the event loop free for the custom async adapters.

**The lesson:** Mixing sync and async code is a landmine. `asyncio.to_thread()` is your bomb disposal kit. Every time you call a library that does I/O without `async`, ask yourself: "Is this going to block my event loop?" If yes, wrap it.

---

## The 429 Retry Bug: A Coroutine You Can Only Await Once

This was our most educational bug. Here's the original code:

```python
# THE BUG
resp = await with_429_retry(
    self._client.get(url)  # This creates a coroutine ONCE
)
```

And inside `with_429_retry`:

```python
async def with_429_retry(coro, max_attempts=4):
    for attempt in range(max_attempts):
        response = await coro  # Second iteration: BOOM
```

See it? `self._client.get(url)` creates a coroutine *object*. You can `await` it exactly once. On the first 429, we'd sleep, loop back, and try to `await` the same spent coroutine. Python would silently return `None` or raise a cryptic error.

Think of a coroutine like a match. You can strike it once. After that, it's just a burnt stick — `await`-ing it again doesn't re-ignite it.

The fix is to pass a **factory** — a function that creates a fresh coroutine each time:

```python
# THE FIX
resp = await with_429_retry(
    lambda: self._client.get(url)  # Fresh coroutine on every call
)
```

```python
async def with_429_retry(request_factory, max_attempts=4):
    for attempt in range(max_attempts):
        response = await request_factory()  # New coroutine each time
```

**The lesson:** Coroutines are single-use. If you're building retry logic around async calls, always pass a callable that *produces* the coroutine, never the coroutine itself. This is such a common pitfall that it deserves to be tattooed on every async programmer's forearm.

---

## The Rate Limit Dance: Respecting Retry-After

Exchanges rate-limit aggressively. When you get a 429, the response often includes a `Retry-After` header telling you exactly how long to wait. Ignoring it is rude (and gets you banned faster).

But `Retry-After` comes in two flavors:

```
Retry-After: 30                              ← seconds
Retry-After: Wed, 21 Oct 2026 07:28:00 GMT  ← HTTP-date
```

Most implementations only handle the first. We handle both:

```python
def _retry_after_delay(headers, attempt):
    retry_after = headers.get("retry-after", "")
    if retry_after:
        try:
            return float(retry_after)  # Seconds
        except ValueError:
            pass
        try:
            target = parsedate_to_datetime(retry_after)  # HTTP-date
            delta = (target - datetime.now(timezone.utc)).total_seconds()
            return max(delta, 1.0)
        except Exception:
            pass
    return min(2**attempt, 32)  # Fallback: exponential backoff
```

If neither format works, we fall back to exponential backoff: 1s, 2s, 4s, up to 32s max.

**The lesson:** Always respect rate limits. They're not suggestions — they're the API telling you "slow down or get cut off." Parse the headers properly, and have a reasonable fallback. The few seconds you "save" by ignoring them cost you hours of debugging when your IP gets temporarily banned.

---

## The Snapshot Shrink Problem: When Less Data Means More Danger

This one was subtle and potentially catastrophic.

Imagine this sequence:
1. Normal poll: Exchange returns 2,000 symbols. We save the snapshot.
2. Exchange has an outage. API returns 50 symbols (partial response).
3. We diff: 2,000 - 50 = 1,950 "removed" symbols.
4. Exchange recovers. API returns 2,000 symbols again.
5. We diff: 2,000 - 50 = 1,950 "new" symbols.
6. We fire **1,950 false alerts**.

Your Telegram explodes. Your credibility evaporates.

The fix is a circuit breaker:

```python
SNAPSHOT_SHRINK_THRESHOLD = 0.5

if prev_keys and len(curr_keys) < len(prev_keys) * SNAPSHOT_SHRINK_THRESHOLD:
    logger.warning(
        "%s: Snapshot shrank from %d to %d symbols — "
        "possible API outage, skipping diff",
        exchange, len(prev_keys), len(curr_keys),
    )
    return []
```

If the current snapshot has less than 50% of the previous snapshot's symbols, something is clearly wrong. We skip the diff entirely and wait for the next poll.

**The lesson:** In monitoring systems, **what you don't alert on** is just as important as what you do. False positives erode trust faster than missed detections. Build circuit breakers for impossible-looking state transitions. If your exchange suddenly "lost" half its symbols, the exchange didn't actually delist 1,000 tokens overnight — your data source is broken.

---

## The Lock File Race: A Concurrency Bug That Hides in Plain Sight

We use `fcntl` file locks to prevent concurrent processes from corrupting snapshots. The original code cleaned up lock files after releasing the lock:

```python
# THE BUG
finally:
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
    lock_fd.close()
    lock_path.unlink(missing_ok=True)  # Delete the lock file
```

Looks tidy, right? But here's the race condition:

1. Process A acquires lock on `data.json.lock` (inode 100)
2. Process B opens `data.json.lock` (inode 100), blocks on `flock()`
3. Process A releases lock, **deletes** `data.json.lock`
4. Process B acquires lock on inode 100 (the now-deleted file)
5. Process C creates new `data.json.lock` (inode 200)
6. Process C acquires lock on inode 200

Now B and C both think they have exclusive access. They're locking different inodes. Data corruption follows.

The fix is embarassingly simple: **don't delete lock files**.

```python
finally:
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
    lock_fd.close()
    # Do NOT delete the lock file — removing it creates a race where
    # concurrent processes can acquire locks on different inodes.
```

A few zero-byte `.lock` files sitting on disk forever is a tiny price for correctness.

**The lesson:** File locks and file *deletion* don't mix. If you're using `flock()` for coordination, the lock file must be permanent. This is one of those bugs that works perfectly in testing (single process), works 99.9% of the time in production (low concurrency), and corrupts your data on the one day it matters most.

---

## Atomic Writes: The Art of Not Losing Data

Every snapshot write follows this pattern:

```
1. Write data to snapshot.tmp
2. Acquire exclusive lock
3. Copy snapshot.json → snapshot.bak (backup)
4. os.rename(snapshot.tmp → snapshot.json)  ← atomic on POSIX
5. Release lock
```

`os.rename()` is atomic on POSIX systems. It either happens completely or not at all. If the process crashes between steps 1 and 4, you have a stale `.tmp` file (harmless) and the old `.json` is still intact. If it crashes during step 4... it can't. That's the beauty of atomic rename.

The `.bak` file is insurance. If the new snapshot is somehow corrupt (shouldn't happen, but paranoia is a virtue in production), the previous version is one rename away.

**The lesson:** Never write directly to a file you're also reading. Write to a temp file, then rename. This is the single most important pattern for any system that persists state to disk. It's the difference between "the process crashed and we lost nothing" and "the process crashed and our data is a half-written JSON blob."

---

## The Coinbase Roadmap Problem: Scraping the Unknowable

Most exchanges give you a clean API: "here are our trading pairs." Coinbase is special. They publish a "roadmap" — a list of tokens they *plan* to list but haven't yet. This information is incredibly valuable (imagine knowing a token will be on Coinbase before it actually is), but it's not available via API.

So we scrape it. Sort of.

The adapter uses DuckDuckGo news search to find articles mentioning "Coinbase roadmap new token listing":

```python
results = ddgs.news(
    f"Coinbase roadmap new token listing added {year}",
    max_results=5,
)
```

Then it extracts ticker-like patterns (3-6 uppercase letters) from article titles and bodies, filtering through a gauntlet of 150+ skip words:

```python
SKIP_WORDS = {
    "THE", "AND", "FOR", "NEW", "ALSO",  # English
    "USDT", "USDC", "BTC", "ETH",        # Crypto
    "SEC", "CEO", "NFT", "DEX",          # Finance
    "BASE", "PRIME", "CLOUD", "EARN",    # Coinbase products
    ...
}
```

Is this fragile? Absolutely. Is it better than nothing? By a mile.

**The lesson:** Sometimes the "right" data source doesn't exist. The choice isn't between a clean solution and a messy one — it's between a messy solution and no solution. The key is to be *honest* about the mess: tag these as `(R)` for roadmap, make the dependency optional (`ddgs` is a soft requirement), and fail gracefully when the scraping breaks.

---

## HTML Message Splitting: When Telegram Fights Back

Telegram messages cap at 4,096 characters. Our daily digest can easily exceed that with 8 exchanges. Simple fix: split the message, right?

Not when you're using HTML formatting.

The first version just split at line boundaries. But if the split happened inside a `<pre>` block, Telegram would reject the malformed HTML. You'd get a silent failure and no alert.

The fix tracks open HTML tags like a stack:

```python
open_tags: list[str] = []  # stack of currently open tags

for line in lines:
    # When we need to split...
    closing = "".join(f"</{tag}>" for tag in reversed(open_tags))
    chunks.append("\n".join(current_chunk) + closing)
    # Reopen tags for next chunk
    current_chunk = [f"<{tag}>" for tag in open_tags]

    # Track tag opens/closes
    for match in re.finditer(r"<(/?)(\w+)>", line):
        is_close, tag = match.group(1), match.group(2).lower()
        if is_close:
            open_tags.pop()
        else:
            open_tags.append(tag)
```

Chunk 1 ends with `</pre>`. Chunk 2 begins with `<pre>`. Every chunk is valid HTML.

**The lesson:** When splitting formatted text, you can't just cut at arbitrary points. Any format that has opening/closing semantics (HTML, XML, Markdown code blocks) requires tracking state across the split. Think of it like cutting a story mid-sentence — you need to close the thought and restart it cleanly.

---

## Staleness Detection: Knowing When You're Blind

Here's a philosophical question: if your exchange adapter returns nothing, does that mean there are no new listings, or does it mean the API is down?

Originally, staleness tracked "no new listings detected." But that's the *normal* state for most polls — exchanges don't list new tokens every 15 minutes. So the staleness counter was always incrementing, and the warnings were meaningless noise.

The fix: track whether the adapter returned **any symbols at all**:

```python
current_symbol_count = len(current_snapshot.get("symbols", {}))
stale_count = storage.update_staleness(
    exchange_name,
    has_new_listings=current_symbol_count > 0,  # Not "new listings found"
)
```

Zero symbols means the API is probably broken. 2,000 symbols with nothing new means everything is working fine.

**The lesson:** Your health checks should measure *capability*, not *activity*. "Did the system do the thing?" is a different question from "Can the system do the thing?" The first gives you false alarms during quiet periods. The second tells you when you're actually blind.

---

## The Prompt Injection Fix: Trust No Data

Alert messages contain exchange-sourced data — symbol names, trading pairs. This data gets passed to the `hermes` CLI tool, which uses an LLM to format and send Telegram messages.

The original code put the message directly in the CLI argument:

```python
# DANGEROUS
subprocess.run(["hermes", "chat", "--quiet", f"Send this: {message}"])
```

If an exchange returned a symbol named `"; rm -rf /; echo "`, that string would end up in the command. Worse, since hermes uses an LLM, a cleverly crafted symbol name could hijack the prompt.

The fix: write messages to temp files and tell hermes to read the file:

```python
# SAFE
with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
    f.write(message)
    msg_path = f.name

subprocess.run([
    "hermes", "chat", "--quiet",
    f"Read file at {msg_path} and send via send_message with parse_mode=HTML"
])
```

The message content never touches the command string. It's isolated in a file that hermes reads as data, not instructions.

**The lesson:** Treat all external data as hostile. Exchange APIs are external systems — you don't control what they return. Any time external data flows into a command, a prompt, or a query, it's an injection vector. The fix is always the same: separate data from instructions. Files, parameterized queries, template variables — anything that keeps the data out of the control plane.

---

## The Technology Stack: Why These Choices

| Technology | Why |
|---|---|
| **Python 3.11+** | asyncio maturity, type hints, dataclass slots. The ecosystem for crypto tooling is unmatched. |
| **httpx** | Modern async HTTP client. Unlike `aiohttp`, it has a requests-compatible API and built-in connection pooling. Unlike `requests`, it's async-native. |
| **ccxt** | 100+ exchanges with a unified API. Perfect for the "boring" exchanges where we don't need custom logic. |
| **asyncio** | Native Python concurrency. No framework overhead, no external event loop. Fetching 8 exchanges sequentially would take 4 minutes; concurrently takes 30 seconds max. |
| **fcntl** | OS-level file locking. No external dependencies (Redis, etc.) for a single-machine deployment. |
| **dataclasses with slots** | Memory-efficient data containers. `slots=True` prevents accidental attribute creation and reduces memory footprint — meaningful when processing thousands of instruments. |

What we *didn't* use is just as revealing:
- **No database.** JSON files + atomic writes are simpler, easier to debug (you can `cat` them), and sufficient for our data volume.
- **No message queue.** Direct subprocess call to hermes is simpler than setting up RabbitMQ for a system that sends maybe 5 messages a day.
- **No web framework.** This is a CLI tool that runs on cron. Adding Flask or FastAPI would be pure architecture astronautics.

**The lesson:** Choose the simplest technology that solves the problem. A JSON file is a database. A cron job is a scheduler. A subprocess call is a message queue. Reach for the heavyweight tool when the lightweight one actually breaks — not before.

---

## How The Pieces Connect: A Complete Poll Cycle

Let's trace a single poll from start to finish:

```
1. main.py: poll() called
   │
2. Create 8 adapters (5 custom + 3 ccxt)
   │
3. asyncio.gather() → fetch all 8 simultaneously
   │  ├── BinanceAdapter._fetch_spot()  → httpx GET /api/v3/exchangeInfo
   │  ├── BinanceAdapter._fetch_futures() → httpx GET /fapi/v1/exchangeInfo
   │  ├── OkxAdapter._fetch_type("SPOT") → httpx GET /api/v5/public/instruments
   │  ├── OkxAdapter._fetch_type("SWAP") → httpx GET /api/v5/public/instruments
   │  ├── CoinbaseAdapter._fetch_spot_products() → httpx GET /products
   │  ├── BybitAdapter._fetch_category("spot") → httpx GET /v5/market/instruments-info
   │  ├── BybitAdapter._fetch_category("linear") → paginated fetches
   │  ├── BitgetAdapter._fetch_spot() → httpx GET /api/v2/spot/public/symbols
   │  ├── BitgetAdapter._fetch_futures() → httpx GET /api/v2/mix/market/contracts
   │  ├── CcxtAdapter (Upbit) → asyncio.to_thread(load_markets)
   │  ├── CcxtAdapter (Bithumb) → asyncio.to_thread(load_markets)
   │  └── CcxtAdapter (Kraken) → asyncio.to_thread(load_markets)
   │
4. Each adapter returns dict[str, InstrumentInfo]
   │  (keyed like "binance:spot:BTCUSDT")
   │
5. storage.build_snapshot() → convert to JSON-serializable format
   │
6. For each exchange:
   │  ├── Load previous snapshot from disk
   │  ├── differ.compare_snapshots(previous, current)
   │  │   ├── Check snapshot shrink (< 50% → skip)
   │  │   ├── Set difference: new_keys = curr_keys - prev_keys
   │  │   └── Classify each new key (S/F/R/A)
   │  ├── storage.save_snapshot() → atomic write
   │  ├── storage.update_staleness()
   │  └── If new listings: storage.append_journal()
   │
7. Deduplicate across exchanges
   │
8. If any new listings found:
   │  ├── formatter.format_realtime_alert(listings)
   │  └── alerter.push_realtime_alerts(formatted_html)
   │      └── Write to temp file → hermes → Telegram
   │
9. Return list of NewListing objects
```

Total wall-clock time: ~5-15 seconds typically, 30 seconds max (timeout).

---

## The Test Suite: What We Actually Verify

26 tests across three files. Not a huge number, but they're targeted:

- **Diffing tests (7):** First run baseline, no changes, single new symbol, futures classification, alpha/A/O normalization, deduplication, batch detection.
- **Formatter tests (10):** Report structure, exchange ordering, error display, staleness warnings, HTML escaping, message splitting, real-time alerts, empty states, roadmap tags.
- **Storage tests (9):** Atomic save/load round-trip, missing files, corrupt JSON, backup creation, journal append/accumulate, empty entry no-op, staleness tracking, snapshot building.

What's *not* tested: the actual HTTP calls to exchanges. Those are integration tests that require live APIs, and they'd be flaky by nature. Instead, we test the *logic* — given this API response, does the adapter produce the right output? Given this snapshot pair, does the differ find the right new listings?

**The lesson:** Test the logic, not the I/O. Mock the boundaries, verify the brains. A test that calls the real Binance API and asserts "more than 100 symbols" isn't testing *your* code — it's testing Binance's uptime.

---

## Patterns Worth Stealing

### 1. The Registry Pattern
Register adapters at import time, look them up at runtime. Adding a new exchange means writing one file and one `register()` call. Zero changes to the orchestrator.

### 2. Callable Factories for Retries
Pass `lambda: do_thing()` instead of `do_thing()`. Ensures fresh execution on each retry. Works for coroutines, database connections, file handles — anything that can't be reused.

### 3. Circuit Breakers on Data Quality
Don't blindly trust your data source. If the numbers look wrong (snapshot shrunk by half), stop processing and wait. Better to miss one poll cycle than to spam false alerts.

### 4. Atomic File Operations
Write → Rename, never Write-in-Place. Three lines of code that prevent an entire class of data corruption bugs.

### 5. Separate Data from Instructions
Whether it's SQL parameters, shell arguments, or LLM prompts — external data goes in a data channel, never in the control channel.

---

## What Good Engineers Actually Do

Looking at the git history tells a story. The initial commit was 1,842 lines — a working system. Then came six fix commits totaling 500+ lines of changes. That's not failure. That's the process.

**Good engineers ship, then harden.** The first version worked. The second version handled edge cases. The third version survived production. Each commit in the history represents a lesson learned:

- *"The event loop is blocking"* → wrap sync calls in `to_thread()`
- *"We're missing alpha listings"* → flatten nested permission arrays
- *"Retry isn't retrying"* → coroutines are single-use, pass factories
- *"We got 2,000 false alerts"* → add snapshot shrink detection
- *"The lock file disappears between processes"* → stop deleting lock files
- *"Messages render broken in Telegram"* → track HTML tags across splits

None of these were obvious in advance. They emerged from running the system against real exchanges with real data. The skill isn't avoiding bugs — it's building systems where bugs are *findable* and *fixable* without rewriting everything.

That's what the adapter pattern, the atomic writes, and the separation of concerns buy you. Not perfection on day one. **Debuggability on day two.**

---

## File Map (For Reference)

```
listing_tracker/
├── main.py          ← Start here. The orchestrator.
├── config.py        ← All constants and exchange definitions.
├── http_client.py   ← 429 retry logic. Small but critical.
├── storage.py       ← Snapshots, journals, locks. The persistence layer.
├── differ.py        ← Snapshot comparison. Where new listings are born.
├── formatter.py     ← Telegram HTML. Prettier than it looks.
├── alerter.py       ← Message delivery via hermes.
└── exchanges/
    ├── base.py      ← BaseAdapter, CcxtAdapter, InstrumentInfo, registry.
    ├── binance.py   ← Spot + futures + alpha detection.
    ├── okx.py       ← SPOT + SWAP with listTime tracking.
    ├── coinbase.py  ← Products API + DuckDuckGo roadmap scraping.
    ├── bybit.py     ← Paginated instrument fetching.
    ├── bitget.py    ← Dual endpoint (spot symbols + futures contracts).
    ├── upbit.py     ← 4 lines. ccxt does the work.
    ├── bithumb.py   ← 4 lines. Same.
    └── kraken.py    ← 4 lines. Same.
```

~2,200 lines of production code. 26 tests. 8 exchanges. One Telegram bot that never sleeps.
