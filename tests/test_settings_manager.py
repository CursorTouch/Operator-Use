"""Tests for settings/manager.py: SettingsManager — merge, get/set, persistence, errors."""
import pytest
from program.settings.manager import SettingsManager
from program.settings.storage import InMemorySettingsStorage
from program.settings.types import (
    Settings, CompactionSettings, BranchSummarySettings,
    RetrySettings, ProviderRetrySettings, SettingsError,
)
from program.engine.types import SteeringMode, FollowupMode
from program.inference.types import Transport, ThinkingLevel


def make_manager(global_data: dict | None = None, project_data: dict | None = None) -> SettingsManager:
    storage = InMemorySettingsStorage()
    if global_data is not None:
        import json
        storage.global_data = json.dumps(global_data)
    if project_data is not None:
        import json
        storage.project_data = json.dumps(project_data)
    return SettingsManager.from_storage(storage)


# ── in_memory factory ─────────────────────────────────────────────────────────

class TestInMemoryFactory:
    def test_creates_manager(self):
        sm = SettingsManager.in_memory()
        assert sm is not None

    def test_empty_seed_has_defaults(self):
        sm = SettingsManager.in_memory()
        assert sm.get_compaction_enabled() is True

    def test_seeded_data_applied(self):
        sm = SettingsManager.in_memory({"default_provider": "anthropic"})
        assert sm.get_default_provider() == "anthropic"


# ── merge precedence ──────────────────────────────────────────────────────────

class TestMergePrecedence:
    def test_project_overrides_global(self):
        sm = make_manager(
            global_data={"default_provider": "openai"},
            project_data={"default_provider": "anthropic"},
        )
        assert sm.get_default_provider() == "anthropic"

    def test_global_used_when_no_project(self):
        sm = make_manager(global_data={"default_provider": "openai"})
        assert sm.get_default_provider() == "openai"

    def test_nested_merge_field_by_field(self):
        sm = make_manager(
            global_data={"compaction": {"enabled": False, "reserve_tokens": 8000}},
            project_data={"compaction": {"enabled": True}},
        )
        # project.enabled wins; global.reserve_tokens preserved
        assert sm.get_compaction_enabled() is True
        assert sm.get_compaction_reserve_tokens() == 8000

    def test_none_project_fields_dont_override_global(self):
        sm = make_manager(
            global_data={"default_model": "gpt-4"},
            project_data={},
        )
        assert sm.get_default_model() == "gpt-4"


# ── defaults ──────────────────────────────────────────────────────────────────

class TestDefaults:
    def test_compaction_enabled_default_true(self):
        sm = SettingsManager.in_memory()
        assert sm.get_compaction_enabled() is True

    def test_compaction_reserve_tokens_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_compaction_reserve_tokens() == 16384

    def test_compaction_keep_recent_tokens_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_compaction_keep_recent_tokens() == 20000

    def test_retry_enabled_default_true(self):
        sm = SettingsManager.in_memory()
        assert sm.get_retry_enabled() is True

    def test_retry_max_retries_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_retry_max_retries() == 3

    def test_retry_base_delay_ms_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_retry_base_delay_ms() == 2000

    def test_branch_summary_reserve_tokens_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_branch_summary_reserve_tokens() == 16384

    def test_branch_summary_skip_prompt_default_false(self):
        sm = SettingsManager.in_memory()
        assert sm.get_branch_summary_skip_prompt() is False

    def test_enable_skill_commands_default_true(self):
        sm = SettingsManager.in_memory()
        assert sm.get_enable_skill_commands() is True

    def test_transport_default_auto(self):
        sm = SettingsManager.in_memory()
        assert sm.get_transport() == Transport.Auto

    def test_steering_mode_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_steering_mode() == SteeringMode.OneAtATime

    def test_follow_up_mode_default(self):
        sm = SettingsManager.in_memory()
        assert sm.get_follow_up_mode() == FollowupMode.OneAtATime


# ── setters ───────────────────────────────────────────────────────────────────

class TestSetters:
    @pytest.mark.asyncio
    async def test_set_default_provider(self):
        sm = SettingsManager.in_memory()
        sm.set_default_provider("anthropic")
        assert sm.get_default_provider() == "anthropic"

    @pytest.mark.asyncio
    async def test_set_default_model(self):
        sm = SettingsManager.in_memory()
        sm.set_default_model("claude-opus-4-7")
        assert sm.get_default_model() == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_set_default_model_and_provider(self):
        sm = SettingsManager.in_memory()
        sm.set_default_model_and_provider("anthropic", "claude-sonnet-4-6")
        assert sm.get_default_provider() == "anthropic"
        assert sm.get_default_model() == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_set_compaction_enabled(self):
        sm = SettingsManager.in_memory()
        sm.set_compaction_enabled(False)
        assert sm.get_compaction_enabled() is False

    @pytest.mark.asyncio
    async def test_set_retry_enabled(self):
        sm = SettingsManager.in_memory()
        sm.set_retry_enabled(False)
        assert sm.get_retry_enabled() is False

    @pytest.mark.asyncio
    async def test_set_transport(self):
        sm = SettingsManager.in_memory()
        sm.set_transport(Transport.SSE)
        assert sm.get_transport() == Transport.SSE

    @pytest.mark.asyncio
    async def test_set_enable_skill_commands(self):
        sm = SettingsManager.in_memory()
        sm.set_enable_skill_commands(False)
        assert sm.get_enable_skill_commands() is False

    @pytest.mark.asyncio
    async def test_set_packages(self):
        sm = SettingsManager.in_memory()
        sm.set_packages(["pkg-a", "pkg-b"])
        assert sm.get_packages() == ["pkg-a", "pkg-b"]

    @pytest.mark.asyncio
    async def test_set_extension_list(self):
        from program.settings.types import ExtensionEntry
        sm = SettingsManager.in_memory()
        entry = ExtensionEntry(path="/ext/a.py", name="a", enabled=True, author="jeomon")
        sm.set_extension_list([entry])
        assert sm.get_extension_list()[0].path == "/ext/a.py"

    @pytest.mark.asyncio
    async def test_extensions_enabled_toggle(self):
        sm = SettingsManager.in_memory()
        assert sm.is_extensions_enabled() is True
        sm.set_extensions_enabled(False)
        assert sm.is_extensions_enabled() is False

    @pytest.mark.asyncio
    async def test_set_skill_paths(self):
        sm = SettingsManager.in_memory()
        sm.set_skill_paths(["/skills/my.md"])
        assert "/skills/my.md" in sm.get_skill_paths()

    @pytest.mark.asyncio
    async def test_set_steering_mode(self):
        sm = SettingsManager.in_memory()
        sm.set_steering_mode(SteeringMode.All)
        assert sm.get_steering_mode() == SteeringMode.All

    @pytest.mark.asyncio
    async def test_set_enabled_models(self):
        sm = SettingsManager.in_memory()
        sm.set_enabled_models(["claude-*", "gpt-4"])
        assert sm.get_enabled_models() == ["claude-*", "gpt-4"]

    @pytest.mark.asyncio
    async def test_set_enabled_models_to_none(self):
        sm = SettingsManager.in_memory()
        sm.set_enabled_models(None)
        assert sm.get_enabled_models() is None


# ── apply_overrides ───────────────────────────────────────────────────────────

class TestApplyOverrides:
    def test_override_not_persisted(self):
        sm = SettingsManager.in_memory()
        sm.apply_overrides({"default_provider": "override-provider"})
        assert sm.get_default_provider() == "override-provider"
        # global_settings unchanged
        assert sm.get_global_settings().default_provider is None

    def test_override_merges_with_existing(self):
        sm = SettingsManager.in_memory({"default_model": "gpt-4"})
        sm.apply_overrides({"default_provider": "openai"})
        assert sm.get_default_model() == "gpt-4"
        assert sm.get_default_provider() == "openai"


# ── get_global_settings / get_project_settings ───────────────────────────────

class TestScopedGetters:
    def test_get_global_settings_returns_copy(self):
        sm = SettingsManager.in_memory({"default_provider": "openai"})
        g = sm.get_global_settings()
        assert g.default_provider == "openai"
        g.default_provider = "mutated"
        assert sm.get_default_provider() == "openai"

    def test_get_project_settings_returns_copy(self):
        sm = make_manager(project_data={"default_model": "gpt-4"})
        p = sm.get_project_settings()
        assert p.default_model == "gpt-4"
        p.default_model = "mutated"
        assert sm.get_project_settings().default_model == "gpt-4"


# ── error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_load_error_surfaces_in_errors(self):
        from program.settings.types import LockResult

        class BrokenStorage(InMemorySettingsStorage):
            def with_lock(self, scope, fn):
                if scope == "global":
                    raise ValueError("disk read failed")
                return super().with_lock(scope, fn)

        storage = BrokenStorage()
        sm = SettingsManager.from_storage(storage)
        assert len(sm.errors) == 1
        assert sm.errors[0].scope == "global"

    def test_drain_errors_clears_queue(self):
        from program.settings.types import LockResult

        class BrokenStorage(InMemorySettingsStorage):
            def with_lock(self, scope, fn):
                if scope == "global":
                    raise ValueError("fail")
                return super().with_lock(scope, fn)

        sm = SettingsManager.from_storage(BrokenStorage())
        errors = sm.drain_errors()
        assert len(errors) >= 1
        assert sm.drain_errors() == []


# ── settings_from_dict ────────────────────────────────────────────────────────

class TestSettingsFromDict:
    def test_flat_fields_parsed(self):
        s = SettingsManager._settings_from_dict({"default_provider": "openai", "default_model": "gpt-4"})
        assert s.default_provider == "openai"
        assert s.default_model == "gpt-4"

    def test_unknown_keys_ignored(self):
        s = SettingsManager._settings_from_dict({"unknown_key": "value"})
        assert s.default_provider is None

    def test_compaction_dict_parsed(self):
        s = SettingsManager._settings_from_dict({"compaction": {"enabled": False, "reserve_tokens": 5000}})
        assert s.compaction is not None
        assert s.compaction.enabled is False
        assert s.compaction.reserve_tokens == 5000

    def test_retry_with_provider_parsed(self):
        s = SettingsManager._settings_from_dict({
            "retry": {
                "enabled": True,
                "max_retries": 5,
                "provider": {"timeout_ms": 30000, "max_retries": 2},
            }
        })
        assert s.retry is not None
        assert s.retry.max_retries == 5
        assert s.retry.provider is not None
        assert s.retry.provider.timeout_ms == 30000


# ── compaction_settings dict ──────────────────────────────────────────────────

class TestGetCompactionSettings:
    def test_returns_all_keys(self):
        sm = SettingsManager.in_memory()
        d = sm.get_compaction_settings()
        assert "enabled" in d
        assert "reserve_tokens" in d
        assert "keep_recent_tokens" in d

    def test_values_match_individual_getters(self):
        sm = SettingsManager.in_memory({"compaction": {"enabled": False, "reserve_tokens": 1000, "keep_recent_tokens": 2000}})
        d = sm.get_compaction_settings()
        assert d["enabled"] == sm.get_compaction_enabled()
        assert d["reserve_tokens"] == sm.get_compaction_reserve_tokens()
        assert d["keep_recent_tokens"] == sm.get_compaction_keep_recent_tokens()
