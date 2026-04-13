"""Tests for session TTL and auto-expiry.

Validates that Session tracks last_activity, expires after its
configurable TTL, and that touch() extends the session lifetime.

Covers all qodo findings for PR #32:
- Req Gap 1: TTL must be config-driven (24h default from SessionConfig)
- Req Gap 2: Loaded sessions must expire based on real age (updated_at)
- Req Gap 3: Cleanup and encryption round-trip coverage
- Bug 4: clear() must call touch() to refresh _last_activity
- Bug 5: Timing-sensitive tests replaced with monkeypatch
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import operator_use.session.views as views_module
from operator_use.session.views import Session, DEFAULT_SESSION_TTL
from operator_use.session.service import SessionStore
from operator_use.messages.service import HumanMessage


# ---------------------------------------------------------------------------
# Existing TTL expiry / touch behaviour (Bug 5 fix: use monkeypatch clock)
# ---------------------------------------------------------------------------

class TestSessionTTL:
    def test_new_session_not_expired(self) -> None:
        session = Session(id="test-1")
        assert not session.is_expired()

    def test_default_ttl_is_24_hours(self) -> None:
        """After Req Gap 1 fix: default TTL must be 24 hours (86400s), not 1 hour."""
        session = Session(id="test-2")
        assert session.ttl == DEFAULT_SESSION_TTL
        assert session.ttl == 86400.0

    def test_custom_ttl(self) -> None:
        session = Session(id="test-3", ttl=120.0)
        assert session.ttl == 120.0

    def test_session_expires_after_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Use monkeypatch clock — no real sleep, no CI flakiness."""
        fake_time = [0.0]

        def fake_monotonic() -> float:
            return fake_time[0]

        monkeypatch.setattr(views_module.time, "monotonic", fake_monotonic)

        session = Session(id="test-4", ttl=100.0)
        assert not session.is_expired()

        fake_time[0] = 101.0  # advance past TTL
        assert session.is_expired()

    def test_touch_resets_expiry_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="test-5", ttl=100.0)
        fake_time[0] = 60.0   # 60s elapsed — not expired
        session.touch()
        fake_time[0] = 120.0  # 60s after touch — within TTL
        assert not session.is_expired()

    def test_session_expires_after_touch_if_ttl_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="test-6", ttl=100.0)
        fake_time[0] = 50.0
        session.touch()
        fake_time[0] = 160.0  # 110s since touch — past TTL
        assert session.is_expired()

    def test_zero_ttl_immediately_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="test-7", ttl=0.0)
        fake_time[0] = 0.001  # any elapsed time exceeds 0s TTL
        assert session.is_expired()

    def test_negative_ttl_immediately_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="test-8", ttl=-1.0)
        assert session.is_expired()  # negative TTL: 0 > -1 is always true

    def test_very_large_ttl_does_not_expire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="test-9", ttl=1e9)
        fake_time[0] = 1_000_000.0
        assert not session.is_expired()

    def test_multiple_touches_keep_session_alive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="test-10", ttl=100.0)
        for i in range(1, 6):
            fake_time[0] = i * 50.0  # 50s increments — each would expire without touch
            session.touch()
            assert not session.is_expired()


# ---------------------------------------------------------------------------
# Bug 4: clear() must call touch()
# ---------------------------------------------------------------------------

class TestClearCallsTouch:
    def test_clear_refreshes_last_activity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """clear() is a mutating operation; it must extend the TTL window."""
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="clear-1", ttl=100.0)
        # Advance to just before expiry
        fake_time[0] = 95.0
        # Clearing the session is activity — it must reset _last_activity
        session.clear()
        # Now advance another 95s (total 190s, but only 95s since clear)
        fake_time[0] = 190.0
        assert not session.is_expired()

    def test_clear_without_touch_would_expire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the test logic: without calling touch() the session expires."""
        fake_time = [0.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        session = Session(id="clear-2", ttl=100.0)
        fake_time[0] = 101.0  # past TTL — would expire if clear doesn't touch
        # clear() must touch, so session should NOT be expired after clear
        session.clear()
        assert not session.is_expired()


# ---------------------------------------------------------------------------
# Req Gap 1: TTL from config (SessionConfig in Config)
# ---------------------------------------------------------------------------

class TestConfigDrivenTTL:
    def test_session_config_has_ttl_hours_field(self) -> None:
        """SessionConfig must exist with a ttl_hours field defaulting to 24.0."""
        from operator_use.config.service import SessionConfig
        sc = SessionConfig()
        assert sc.ttl_hours == 24.0

    def test_config_has_session_block(self) -> None:
        """Root Config must have a session: SessionConfig field."""
        from operator_use.config.service import Config
        c = Config()
        assert hasattr(c, "session")
        assert c.session.ttl_hours == 24.0

    def test_from_config_uses_config_ttl(self) -> None:
        """Session.from_config() must derive ttl from config.session.ttl_hours."""
        from operator_use.config.service import Config, SessionConfig
        config = Config()
        # Patch ttl_hours to a known value
        config.session = SessionConfig(ttl_hours=2.0)
        session = Session.from_config(id="cfg-1", config=config)
        assert session.ttl == 2.0 * 3600  # 7200s

    def test_from_config_default_24h(self) -> None:
        """from_config() with default config must produce 86400s TTL."""
        from operator_use.config.service import Config
        config = Config()
        session = Session.from_config(id="cfg-2", config=config)
        assert session.ttl == 86400.0


# ---------------------------------------------------------------------------
# Req Gap 2: Loaded sessions expire based on real age (updated_at)
# ---------------------------------------------------------------------------

class TestLoadedSessionExpiry:
    def test_loaded_session_last_activity_reflects_updated_at(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session loaded from disk must base _last_activity on updated_at,
        not on the current monotonic time at load time."""
        fake_time = [1000.0]  # monotonic at "load time"
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        store = SessionStore(tmp_path)
        session_id = "loaded-expiry-1"

        # Write a session that was last updated 2 hours ago
        two_hours_ago = datetime.now() - timedelta(hours=2)
        path = store._sessions_path(session_id)
        meta = {
            "type": "metadata",
            "id": session_id,
            "created_at": (datetime.now() - timedelta(hours=4)).isoformat(),
            "updated_at": two_hours_ago.isoformat(),
            "metadata": {},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps(meta) + "\n")

        # Load the session with a 1-hour TTL — it should appear expired
        # because updated_at is 2 hours ago
        loaded = store.load(session_id)
        assert loaded is not None
        loaded.ttl = 3600.0  # 1 hour TTL
        assert loaded.is_expired(), (
            "Session updated 2 hours ago with 1h TTL must appear expired on load"
        )

    def test_loaded_recent_session_not_expired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A session updated 5 minutes ago with 1h TTL must NOT be expired."""
        fake_time = [1000.0]
        monkeypatch.setattr(views_module.time, "monotonic", lambda: fake_time[0])

        store = SessionStore(tmp_path)
        session_id = "loaded-fresh-1"

        five_min_ago = datetime.now() - timedelta(minutes=5)
        path = store._sessions_path(session_id)
        meta = {
            "type": "metadata",
            "id": session_id,
            "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
            "updated_at": five_min_ago.isoformat(),
            "metadata": {},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps(meta) + "\n")

        loaded = store.load(session_id)
        assert loaded is not None
        loaded.ttl = 3600.0  # 1 hour TTL
        assert not loaded.is_expired(), (
            "Session updated 5 minutes ago with 1h TTL must NOT be expired"
        )

    def test_get_or_create_deletes_expired_session(self, tmp_path: Path) -> None:
        """get_or_create() must invalidate/delete expired sessions on access."""
        store = SessionStore(tmp_path)
        session_id = "expired-cleanup-1"

        # Write a session that was last updated 48 hours ago
        old_time = datetime.now() - timedelta(hours=48)
        path = store._sessions_path(session_id)
        meta = {
            "type": "metadata",
            "id": session_id,
            "created_at": old_time.isoformat(),
            "updated_at": old_time.isoformat(),
            "metadata": {},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps(meta) + "\n")

        # get_or_create with 1h TTL must return a fresh session, not the expired one
        session = store.get_or_create(session_id=session_id, ttl=3600.0)
        assert session.messages == [], "Expired session must be replaced with fresh one"
        assert not session.is_expired(), "Newly created replacement session must not be expired"


# ---------------------------------------------------------------------------
# Req Gap 3: Cleanup method
# ---------------------------------------------------------------------------

class TestSessionCleanup:
    def test_cleanup_removes_expired_sessions(self, tmp_path: Path) -> None:
        """SessionStore.cleanup() must delete expired sessions from disk."""
        store = SessionStore(tmp_path)

        # Create an expired session file (48h old)
        old_time = datetime.now() - timedelta(hours=48)
        expired_id = "cleanup-expired-1"
        path = store._sessions_path(expired_id)
        meta = {
            "type": "metadata",
            "id": expired_id,
            "created_at": old_time.isoformat(),
            "updated_at": old_time.isoformat(),
            "metadata": {},
        }
        with open(path, "w") as f:
            f.write(json.dumps(meta) + "\n")

        # Create a fresh session file (5 min old)
        fresh_id = "cleanup-fresh-1"
        fresh_path = store._sessions_path(fresh_id)
        fresh_time = datetime.now() - timedelta(minutes=5)
        fresh_meta = {
            "type": "metadata",
            "id": fresh_id,
            "created_at": fresh_time.isoformat(),
            "updated_at": fresh_time.isoformat(),
            "metadata": {},
        }
        with open(fresh_path, "w") as f:
            f.write(json.dumps(fresh_meta) + "\n")

        removed = store.cleanup(ttl=3600.0)  # 1h TTL
        assert expired_id in removed, "Expired session must be in removed list"
        assert fresh_id not in removed, "Fresh session must NOT be removed"
        assert not path.exists(), "Expired session file must be deleted from disk"
        assert fresh_path.exists(), "Fresh session file must survive cleanup"

    def test_cleanup_returns_empty_list_when_nothing_expired(self, tmp_path: Path) -> None:
        """cleanup() returns an empty list when no sessions are expired."""
        store = SessionStore(tmp_path)
        removed = store.cleanup(ttl=86400.0)
        assert removed == []


# ---------------------------------------------------------------------------
# Req Gap 3: Encryption round-trip
# ---------------------------------------------------------------------------

class TestSessionEncryption:
    def test_save_and_load_with_encryption(self, tmp_path: Path) -> None:
        """Encrypted-at-rest sessions must survive a save→load round-trip."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        store = SessionStore(tmp_path, encryption_key=key)
        session = Session(id="enc-1", ttl=86400.0)
        session.add_message(HumanMessage(content="secret message"))
        store.save(session)

        loaded = store.load("enc-1")
        assert loaded is not None
        assert len(loaded.messages) == 1
        assert loaded.messages[0].content == "secret message"

    def test_encrypted_file_is_not_plaintext(self, tmp_path: Path) -> None:
        """When encryption is enabled, the raw .jsonl file must not contain
        plaintext message content."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        store = SessionStore(tmp_path, encryption_key=key)
        session = Session(id="enc-2", ttl=86400.0)
        session.add_message(HumanMessage(content="top secret"))
        store.save(session)

        raw = store._sessions_path("enc-2").read_bytes()
        assert b"top secret" not in raw, (
            "Plaintext message content must not appear in the encrypted file"
        )

    def test_load_without_key_when_saved_with_key_raises(self, tmp_path: Path) -> None:
        """Loading an encrypted session without a key must raise an error."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()

        store_with_key = SessionStore(tmp_path, encryption_key=key)
        session = Session(id="enc-3", ttl=86400.0)
        session.add_message(HumanMessage(content="confidential"))
        store_with_key.save(session)

        store_no_key = SessionStore(tmp_path)
        with pytest.raises(ValueError):
            store_no_key.load("enc-3")

    def test_unencrypted_save_load_round_trip(self, tmp_path: Path) -> None:
        """Without encryption, save→load must still work correctly (regression guard)."""
        store = SessionStore(tmp_path)
        session = Session(id="plain-1", ttl=86400.0)
        session.add_message(HumanMessage(content="hello"))
        store.save(session)

        loaded = store.load("plain-1")
        assert loaded is not None
        assert loaded.messages[0].content == "hello"
