"""In-process token bucket rate limiter for gateway channels.

Provides per-channel (or per-agent) rate limiting with no external dependencies.
Thread-safe via threading.Lock; async-safe because consume() is non-blocking.
"""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Thread-safe token bucket rate limiter.

    Args:
        rate: Number of tokens added per second.
        capacity: Maximum number of tokens the bucket can hold.
    """

    rate: float
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """Add tokens based on elapsed time. Must be called with lock held."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def available_tokens(self) -> float:
        """Current available tokens (approximate, for diagnostics)."""
        with self._lock:
            self._refill()
            return self._tokens


class RateLimiter:
    """Per-key rate limiter registry.

    Maintains one TokenBucket per key (channel_id, agent_id, etc.). Thread-safe.
    """

    def __init__(self, rate: float = 10.0, capacity: float = 20.0) -> None:
        """
        Args:
            rate: Requests per second allowed per key (default: 10/s).
            capacity: Burst capacity per key (default: 20 requests).
        """
        self._rate = rate
        self._capacity = capacity
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        """Check if a request for the given key is allowed under the rate limit."""
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = TokenBucket(rate=self._rate, capacity=self._capacity)
        return self._buckets[key].consume()

    def reset(self, key: str) -> None:
        """Remove the rate limit bucket for a key (e.g. for tests or channel teardown)."""
        with self._lock:
            self._buckets.pop(key, None)
