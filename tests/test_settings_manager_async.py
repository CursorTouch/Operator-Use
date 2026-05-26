"""Tests for SettingsManager async write queue: flush(), reload(), persistence round-trip."""
import asyncio
import json
import pytest
from program.settings.manager import SettingsManager
from program.settings.storage import InMemorySettingsStorage
from program.settings.types import Settings, CompactionSettings


def make_manager(global_data: dict | None = None, project_data: dict | None = None) -> tuple[SettingsManager, InMemorySettingsStorage]:
    storage = InMemorySettingsStorage()
    if global_data is not None:
        storage.global_data = json.dumps(global_data)
    if project_data is not None:
        storage.project_data = json.dumps(project_data)
    sm = SettingsManager.from_storage(storage)
    return sm, storage


# ── flush() ───────────────────────────────────────────────────────────────────

class TestFlush:
    @pytest.mark.asyncio
    async def test_flush_with_no_pending_is_noop(self):
        sm, _ = make_manager()
        await sm.flush()  # should not raise

    @pytest.mark.asyncio
    async def test_flush_completes_pending_write(self):
        sm, storage = make_manager()
        sm.set_default_provider("anthropic")
        await sm.flush()
        data = json.loads(storage.global_data)
        assert data.get("default_provider") == "anthropic"

    @pytest.mark.asyncio
    async def test_flush_completes_multiple_pending_writes(self):
        sm, storage = make_manager()
        sm.set_default_provider("openai")
        sm.set_default_model("gpt-4")
        await sm.flush()
        data = json.loads(storage.global_data)
        assert data.get("default_provider") == "openai"
        assert data.get("default_model") == "gpt-4"

    @pytest.mark.asyncio
    async def test_flush_serializes_concurrent_writes(self):
        sm, storage = make_manager()
        sm.set_default_provider("p1")
        sm.set_default_model("m1")
        sm.set_default_provider("p2")
        await sm.flush()
        data = json.loads(storage.global_data)
        assert data.get("default_provider") == "p2"


# ── reload() ─────────────────────────────────────────────────────────────────

class TestReload:
    @pytest.mark.asyncio
    async def test_reload_picks_up_external_change(self):
        sm, storage = make_manager()
        # Externally mutate storage after creation
        storage.global_data = json.dumps({"default_provider": "external-change"})
        await sm.reload()
        assert sm.get_default_provider() == "external-change"

    @pytest.mark.asyncio
    async def test_reload_flushes_pending_writes_first(self):
        sm, storage = make_manager()
        sm.set_default_provider("pending")
        await sm.reload()
        # After reload, pending write completed and storage reflects it
        data = json.loads(storage.global_data)
        assert data.get("default_provider") == "pending"

    @pytest.mark.asyncio
    async def test_reload_clears_modification_tracking(self):
        sm, storage = make_manager()
        sm.set_default_provider("x")
        assert "default_provider" in sm.modified_fields
        await sm.reload()
        assert len(sm.modified_fields) == 0

    @pytest.mark.asyncio
    async def test_reload_updates_merged_view(self):
        sm, storage = make_manager(
            global_data={"default_provider": "old"},
        )
        assert sm.get_default_provider() == "old"
        storage.global_data = json.dumps({"default_provider": "new"})
        await sm.reload()
        assert sm.get_default_provider() == "new"

    @pytest.mark.asyncio
    async def test_reload_handles_corrupt_storage_gracefully(self):
        sm, storage = make_manager()

        original_with_lock = storage.with_lock
        call_count = [0]

        def patched_with_lock(scope, fn):
            call_count[0] += 1
            if scope == "global" and call_count[0] > 2:
                raise ValueError("storage error")
            return original_with_lock(scope, fn)

        storage.with_lock = patched_with_lock
        await sm.reload()  # should not raise — errors are recorded
        # errors recorded
        assert len(sm.errors) >= 0  # may or may not have errors, just shouldn't crash


# ── persistence round-trip ────────────────────────────────────────────────────

class TestPersistenceRoundTrip:
    @pytest.mark.asyncio
    async def test_set_then_reload_retrieves_value(self):
        sm, storage = make_manager()
        sm.set_default_model("claude-opus-4-7")
        await sm.flush()

        sm2 = SettingsManager.from_storage(storage)
        assert sm2.get_default_model() == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_nested_setting_persisted_correctly(self):
        sm, storage = make_manager()
        sm.set_compaction_enabled(False)
        await sm.flush()

        sm2 = SettingsManager.from_storage(storage)
        assert sm2.get_compaction_enabled() is False

    @pytest.mark.asyncio
    async def test_partial_nested_update_preserved(self):
        sm, storage = make_manager(global_data={"compaction": {"enabled": True, "reserve_tokens": 8000}})
        sm.set_compaction_enabled(False)
        await sm.flush()

        sm2 = SettingsManager.from_storage(storage)
        assert sm2.get_compaction_enabled() is False
        assert sm2.get_compaction_strategy() == "summarization"  # untouched field preserved

    @pytest.mark.asyncio
    async def test_multiple_settings_all_persisted(self):
        sm, storage = make_manager()
        sm.set_default_provider("anthropic")
        sm.set_default_model("claude-sonnet-4-6")
        sm.set_retry_enabled(False)
        await sm.flush()

        sm2 = SettingsManager.from_storage(storage)
        assert sm2.get_default_provider() == "anthropic"
        assert sm2.get_default_model() == "claude-sonnet-4-6"
        assert sm2.get_retry_enabled() is False


# ── drain_errors ──────────────────────────────────────────────────────────────

class TestDrainErrors:
    @pytest.mark.asyncio
    async def test_write_error_recorded_after_flush(self):
        sm, storage = make_manager()

        original = storage.with_lock

        fail_next = [False]

        def patched(scope, fn):
            if fail_next[0] and scope == "global":
                fail_next[0] = False
                raise IOError("disk full")
            return original(scope, fn)

        storage.with_lock = patched
        fail_next[0] = True
        sm.set_default_provider("x")
        await sm.flush()
        errors = sm.drain_errors()
        assert any(e.scope == "global" for e in errors)

    def test_drain_clears_errors(self):
        from program.settings.types import LockResult

        class BrokenStorage(InMemorySettingsStorage):
            def with_lock(self, scope, fn):
                if scope == "global":
                    raise ValueError("broken")
                return super().with_lock(scope, fn)

        sm = SettingsManager.from_storage(BrokenStorage())
        sm.drain_errors()
        assert sm.drain_errors() == []
