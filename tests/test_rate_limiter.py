"""Tests for token bucket rate limiter."""

import time

import pytest

from operator_use.utils.rate_limiter import RateLimiter, TokenBucket


class TestTokenBucket:
    def test_initial_bucket_is_full(self):
        bucket = TokenBucket(rate=10.0, capacity=5.0)
        assert bucket.available_tokens == pytest.approx(5.0, abs=0.1)

    def test_consume_allows_within_capacity(self):
        bucket = TokenBucket(rate=10.0, capacity=5.0)
        for _ in range(5):
            assert bucket.consume() is True

    def test_consume_denies_when_empty(self):
        bucket = TokenBucket(rate=0.01, capacity=1.0)  # very slow refill
        bucket.consume()  # drain the one token
        assert bucket.consume() is False

    def test_tokens_refill_over_time(self):
        bucket = TokenBucket(rate=100.0, capacity=5.0)
        # drain all tokens
        for _ in range(5):
            bucket.consume()
        time.sleep(0.05)  # 50ms at 100/s = ~5 tokens refilled
        assert bucket.consume() is True

    def test_capacity_is_ceiling(self):
        bucket = TokenBucket(rate=1000.0, capacity=3.0)
        time.sleep(0.01)  # would add 10 tokens at 1000/s, but capacity is 3
        assert bucket.available_tokens == pytest.approx(3.0, abs=0.1)

    def test_consume_multiple_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=5.0)
        assert bucket.consume(3.0) is True
        assert bucket.available_tokens == pytest.approx(2.0, abs=0.1)
        assert bucket.consume(3.0) is False  # only ~2 left


class TestRateLimiter:
    def test_allows_requests_within_limit(self):
        limiter = RateLimiter(rate=100.0, capacity=10.0)
        for _ in range(10):
            assert limiter.is_allowed("channel-1") is True

    def test_denies_requests_over_limit(self):
        limiter = RateLimiter(rate=0.01, capacity=1.0)
        limiter.is_allowed("channel-1")  # consume the one token
        assert limiter.is_allowed("channel-1") is False

    def test_independent_buckets_per_channel(self):
        limiter = RateLimiter(rate=0.01, capacity=1.0)
        limiter.is_allowed("ch-A")  # drain ch-A
        assert limiter.is_allowed("ch-A") is False
        assert limiter.is_allowed("ch-B") is True  # ch-B still full

    def test_reset_clears_bucket(self):
        limiter = RateLimiter(rate=0.01, capacity=1.0)
        limiter.is_allowed("ch-X")  # drain
        assert limiter.is_allowed("ch-X") is False
        limiter.reset("ch-X")
        assert limiter.is_allowed("ch-X") is True  # fresh bucket after reset

    def test_reset_nonexistent_key_is_noop(self):
        limiter = RateLimiter(rate=10.0, capacity=5.0)
        limiter.reset("does-not-exist")  # should not raise
