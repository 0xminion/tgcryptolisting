"""Shared HTTP client with automatic 429 backoff for all exchange adapters."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from listing_tracker.config import ADAPTER_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


def _retry_after_delay(headers: httpx.Headers, attempt: int) -> float:
    """Parse Retry-After header and return backoff delay in seconds.

    Honours Retry-After if present (seconds or HTTP-date format),
    otherwise uses exponential backoff starting at 1s.
    """
    retry_after = headers.get("retry-after", "")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
        # Try HTTP-date format (e.g. "Wed, 21 Oct 2026 07:28:00 GMT")
        try:
            from datetime import datetime, timezone

            target = parsedate_to_datetime(retry_after)
            delta = (target - datetime.now(timezone.utc)).total_seconds()
            return max(delta, 1.0)
        except Exception:
            pass
    # Exponential backoff: 1s, 2s, 4s
    return min(2**attempt, 32)


async def with_429_retry(
    request_factory: Callable[[], Coroutine[Any, Any, httpx.Response]],
    max_attempts: int = 4,
) -> httpx.Response:
    """Execute an async HTTP call with automatic retry on HTTP 429.

    Args:
        request_factory: A zero-arg callable that returns a fresh coroutine
            on each call. Example: ``lambda: client.get(url)``
        max_attempts: Maximum number of attempts before giving up.

    On 429, reads Retry-After header if present, otherwise uses exponential
    backoff (1s, 2s, 4s). Raises after max_attempts exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = await request_factory()
        except (httpx.HTTPError, asyncio.TimeoutError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise

        if response.status_code != 429:
            return response

        if attempt < max_attempts - 1:
            delay = _retry_after_delay(response.headers, attempt)
            logger.warning("Rate limited (429) — retrying in %.1fs (attempt %d/%d)",
                           delay, attempt + 1, max_attempts)
            await asyncio.sleep(delay)

    raise last_exc or RuntimeError("429 retry loop exhausted")


def make_client() -> httpx.AsyncClient:
    """Create a shared HTTP client with connection pooling and soft 429 handling.

    The per-request with_429_retry() wrapper handles 429 backoff;
    the transport retries on connection errors (not HTTP errors).
    """
    return httpx.AsyncClient(
        timeout=ADAPTER_TIMEOUT_SECONDS,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        transport=httpx.AsyncHTTPTransport(retries=3),
    )
