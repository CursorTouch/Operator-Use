"""Tests for session TTL and auto-expiry.

Validates that Session tracks last_activity, expires after its
configurable TTL, and that touch() extends the session lifetime.
"""

from __future__ import annotations

import time

from operator_use.session.views import Session, DEFAULT_SESSION_TTL


class TestSessionTTL:
    def test_new_session_not_expired(self) -> None:
        session = Session(id="test-1")
        assert not session.is_expired()

    def test_default_ttl_is_one_hour(self) -> None:
        session = Session(id="test-2")
        assert session.ttl == DEFAULT_SESSION_TTL
        assert session.ttl == 3600.0

    def test_custom_ttl(self) -> None:
        session = Session(id="test-3", ttl=120.0)
        assert session.ttl == 120.0

    def test_session_expires_after_ttl(self) -> None:
        session = Session(id="test-4", ttl=0.05)  # 50ms TTL
        assert not session.is_expired()
        time.sleep(0.1)
        assert session.is_expired()

    def test_touch_resets_expiry_clock(self) -> None:
        session = Session(id="test-5", ttl=0.1)  # 100ms TTL
        time.sleep(0.06)   # 60ms elapsed — not expired yet
        session.touch()    # reset the clock
        time.sleep(0.06)   # 60ms since touch — still within TTL
        assert not session.is_expired()

    def test_session_expires_after_touch_if_ttl_passes(self) -> None:
        session = Session(id="test-6", ttl=0.05)
        session.touch()
        time.sleep(0.1)    # past TTL since last touch
        assert session.is_expired()

    def test_zero_ttl_immediately_expired(self) -> None:
        session = Session(id="test-7", ttl=0.0)
        time.sleep(0.001)  # any elapsed time exceeds 0s TTL
        assert session.is_expired()

    def test_negative_ttl_immediately_expired(self) -> None:
        session = Session(id="test-8", ttl=-1.0)
        assert session.is_expired()

    def test_very_large_ttl_does_not_expire(self) -> None:
        session = Session(id="test-9", ttl=1e9)
        assert not session.is_expired()

    def test_multiple_touches_keep_session_alive(self) -> None:
        session = Session(id="test-10", ttl=0.05)
        for _ in range(5):
            time.sleep(0.02)
            session.touch()
            assert not session.is_expired()
