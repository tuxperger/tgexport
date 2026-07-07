"""Centralised rate limiting for all Telegram API access.

Constitution (Rate Limiting Policy):
- FloodWait: sleep error.seconds + 1, log WARNING, retry.
- Other transient errors: exponential back-off min(2^attempt, 60)s, max 5 attempts.
- At most 3 concurrent downloads; global throttle 20 req/s.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from telethon.errors import FloodWaitError, RPCError

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = 3
MAX_REQUESTS_PER_SECOND = 20.0
MAX_RETRY_ATTEMPTS = 5

T = TypeVar("T")


class RateLimiter:
    """Token-bucket request throttle plus a download concurrency semaphore."""

    def __init__(
        self,
        requests_per_second: float = MAX_REQUESTS_PER_SECOND,
        max_downloads: int = MAX_CONCURRENT_DOWNLOADS,
    ) -> None:
        self._min_interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._lock = asyncio.Lock()
        self._download_semaphore = asyncio.Semaphore(max_downloads)

    async def acquire(self) -> None:
        """Block until a request slot is available under the global throttle."""
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    @asynccontextmanager
    async def acquire_download(self) -> AsyncIterator[None]:
        """Bound concurrent media downloads."""
        async with self._download_semaphore:
            yield


async def call_with_retry[T](
    func: Callable[[], Awaitable[T]],
    max_attempts: int = MAX_RETRY_ATTEMPTS,
    description: str | Callable[[], str] = "telegram call",
) -> T:
    """Invoke an async callable, honouring FloodWait and backing off on transient errors.

    description may be a callable: it is evaluated at log time, so it can
    include volatile context such as the server currently connected to.
    """

    def _desc() -> str:
        return description() if callable(description) else description

    attempt = 0
    while True:
        try:
            return await func()
        except FloodWaitError as exc:
            wait = exc.seconds + 1
            logger.warning("FloodWait on %s: sleeping %ds", _desc(), wait)
            await asyncio.sleep(wait)
        except (TimeoutError, RPCError, ConnectionError) as exc:
            attempt += 1
            if attempt >= max_attempts:
                logger.error("%s failed after %d attempts: %s", _desc(), attempt, exc)
                raise
            delay = min(2**attempt, 60)
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %ds",
                _desc(),
                attempt,
                max_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
