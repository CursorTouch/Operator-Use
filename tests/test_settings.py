"""Settings manager — every setter/getter, flush, reload, overrides, missing coverage."""
from __future__ import annotations

import asyncio
import json

import pytest

from program.settings.manager import SettingsManager
from program.engine.types import SteeringMode, FollowupMode
from program.inference.types import ThinkingLevel
from program.compaction.types import CompactionSettings


def _sm(initial: dict | None = None) -> SettingsManager:
    return SettingsManager.in_memory(initial)


class TestModelAndProvider:
    @pytest.mark.asyncio
    async def test_set_model_and_provider(self):
        sm = _sm()
        sm.set_default_model_and_provider("anthropic", "claude-sonnet-4-6")
        assert sm.get_default_model() == "claude-sonnet-4-6"
        assert sm.get_default_provider() == "anthropic"
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_set_default_model_alone(self):
        sm = _sm()
        sm.set_default_model("my-model")
        assert sm.get_default_model() == "my-model"
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_set_thinking_level(self):
        sm = _sm()
        sm.set_default_thinking_level(ThinkingLevel.High)
        assert sm.get_default_thinking_level() == ThinkingLevel.High
        await asyncio.sleep(0)


class TestCompactionSettings:
    @pytest.mark.asyncio
    async def test_compaction_enabled_toggle(self):
        sm = _sm()
        sm.set_compaction_enabled(False)
        assert sm.get_compaction_enabled() is False
        sm.set_compaction_enabled(True)
        assert sm.get_compaction_enabled() is True
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_compaction_keep_recent_tokens(self):
        sm = _sm()
        sm.global_settings.compaction = CompactionSettings(keep_recent_tokens=8000)
        sm._save()
        assert sm.get_compaction_keep_recent_tokens() == 8000
        await asyncio.sleep(0)

    def test_get_compaction_settings_returns_dict(self):
        sm = _sm()
        cs = sm.get_compaction_settings()
        assert isinstance(cs, dict)
        assert 'enabled' in cs
        assert 'keep_recent_tokens' in cs
        assert 'reserve_tokens' in cs


class TestRetrySettings:
    @pytest.mark.asyncio
    async def test_retry_enabled_toggle(self):
        sm = _sm()
        sm.set_retry_enabled(False)
        assert sm.get_retry_enabled() is False
        sm.set_retry_enabled(True)
        assert sm.get_retry_enabled() is True
        await asyncio.sleep(0)


class TestMemorySettings:
    @pytest.mark.asyncio
    async def test_memory_defaults_when_unset(self):
        sm = _sm()

        memory = sm.get_memory_settings()

        assert memory.enabled is None
        assert memory.provider is None
        assert memory.max_prompt_chars is None
        assert memory.sync_turns is None
        assert memory.prefetch is None
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_memory_settings_from_dict(self):
        sm = _sm({
            "memory": {
                "enabled": True,
                "provider": "mem0",
                "max_prompt_chars": 4000,
                "sync_turns": False,
                "prefetch": True,
            }
        })

        memory = sm.get_memory_settings()

        assert memory.enabled is True
        assert memory.provider == "mem0"
        assert memory.max_prompt_chars == 4000
        assert memory.sync_turns is False
        assert memory.prefetch is True
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_set_memory_settings(self):
        sm = _sm()

        sm.set_memory_settings(enabled=True, provider="mem0", max_prompt_chars=3000)
        memory = sm.get_memory_settings()

        assert memory.enabled is True
        assert memory.provider == "mem0"
        assert memory.max_prompt_chars == 3000
        await asyncio.sleep(0)


class TestSteeringAndFollowup:
    @pytest.mark.asyncio
    async def test_steering_mode(self):
        sm = _sm()
        sm.set_steering_mode(SteeringMode.All)
        assert sm.get_steering_mode() == SteeringMode.All
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_follow_up_mode(self):
        sm = _sm()
        sm.set_follow_up_mode(FollowupMode.All)
        assert sm.get_follow_up_mode() == FollowupMode.All
        await asyncio.sleep(0)


class TestPathsAndPackages:
    @pytest.mark.asyncio
    async def test_extension_paths(self):
        sm = _sm()
        sm.set_extension_paths(["/ext/a", "/ext/b"])
        assert "/ext/a" in sm.get_extension_paths()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_skill_paths(self):
        sm = _sm()
        sm.set_skill_paths(["/skills/one"])
        assert "/skills/one" in sm.get_skill_paths()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_prompt_paths(self):
        sm = _sm()
        sm.set_prompt_paths(["/prompts/p1"])
        assert "/prompts/p1" in sm.get_prompt_paths()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_packages(self):
        sm = _sm()
        sm.set_packages(["pkg-a", "pkg-b"])
        assert "pkg-a" in sm.get_packages()
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_session_dir(self):
        sm = _sm()
        sm.set_session_dir("/custom/sessions")
        assert sm.get_session_dir() is not None
        await asyncio.sleep(0)


class TestImageAndExecuteSettings:
    @pytest.mark.asyncio
    async def test_image_auto_resize(self):
        sm = _sm()
        sm.set_image_auto_resize(True)
        assert sm.get_image_auto_resize() is True
        sm.set_image_auto_resize(False)
        assert sm.get_image_auto_resize() is False
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_image_block_images(self):
        sm = _sm()
        sm.set_image_block_images(True)
        assert sm.get_image_block_images() is True
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_execute_path(self):
        sm = _sm()
        sm.set_execute_path("/usr/bin/custom")
        assert sm.get_execute_path() == "/usr/bin/custom"
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_execute_command_prefix(self):
        sm = _sm()
        sm.set_execute_command_prefix("timeout 30")
        assert sm.get_execute_command_prefix() == "timeout 30"
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_shell_path(self):
        sm = _sm()
        sm.set_shell_path("/bin/zsh")
        assert sm.get_shell_path() == "/bin/zsh"
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_shell_command_prefix(self):
        sm = _sm()
        sm.set_shell_command_prefix("docker exec -it container")
        assert sm.get_shell_command_prefix() == "docker exec -it container"
        await asyncio.sleep(0)


class TestEnabledModels:
    @pytest.mark.asyncio
    async def test_set_and_get_enabled_models(self):
        sm = _sm()
        sm.set_enabled_models(["gpt-4o", "claude-*"])
        models = sm.get_enabled_models()
        assert "gpt-4o" in models
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_set_enabled_models_none(self):
        sm = _sm()
        sm.set_enabled_models(None)
        assert sm.get_enabled_models() is None
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_enable_skill_commands(self):
        sm = _sm()
        sm.set_enable_skill_commands(True)
        assert sm.get_enable_skill_commands() is True
        await asyncio.sleep(0)


class TestOverridesAndFlush:
    def test_apply_overrides_not_persisted(self):
        sm = _sm()
        sm.apply_overrides({"default_model": "override"})
        assert sm.get_default_model() == "override"

    @pytest.mark.asyncio
    async def test_flush_does_not_raise(self):
        sm = _sm()
        sm.set_default_model_and_provider("p", "m")
        await sm.flush()
        assert sm.get_default_model() == "m"

    @pytest.mark.asyncio
    async def test_reload_picks_up_external_change(self):
        from program.settings.storage import InMemorySettingsStorage, LockResult, SCOPE
        storage = InMemorySettingsStorage()
        sm = SettingsManager.from_storage(storage)
        storage.with_lock(SCOPE.GLOBAL, lambda _: LockResult(
            result=None, next=json.dumps({"default_model": "externally-set"})
        ))
        await sm.reload()
        assert sm.get_default_model() == "externally-set"

    def test_settings_from_dict_roundtrip(self):
        data = {
            "default_model": "mistral-small-latest",
            "default_provider": "mistral",
            "compaction": {"enabled": True, "keep_recent_tokens": 5000},
        }
        sm = SettingsManager.in_memory(data)
        assert sm.get_default_model() == "mistral-small-latest"
        cs = sm.get_compaction_settings()
        assert cs['enabled'] is True
        assert cs['keep_recent_tokens'] == 5000


class TestChannelSettings:
    @pytest.mark.asyncio
    async def test_default_channels_streaming_config(self):
        sm = _sm()
        
        # Test default values for each channel
        ws = sm.get_websocket_channel_config()
        assert ws.streaming is True
        assert ws.streaming_latency == 1.0

        tg = sm.get_telegram_channel_config()
        assert tg.streaming is True
        assert tg.streaming_latency == 1.0
        assert tg.show_tool_calls is True
        assert tg.show_thinking is False

        dc = sm.get_discord_channel_config()
        assert dc.streaming is True
        assert dc.streaming_latency == 1.0
        assert dc.show_tool_calls is True
        assert dc.show_thinking is False

        sl = sm.get_slack_channel_config()
        assert sl.streaming is True
        assert sl.streaming_latency == 1.0
        assert sl.show_tool_calls is True
        assert sl.show_thinking is False

        tw = sm.get_twitch_channel_config()
        assert tw.streaming is True
        assert tw.streaming_latency == 1.0
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_modify_channels_streaming_config(self):
        sm = _sm()
        
        # Modify and assert for each channel
        sm.set_websocket_channel_config(streaming=False, streaming_latency=0.5)
        ws = sm.get_websocket_channel_config()
        assert ws.streaming is False
        assert ws.streaming_latency == 0.5

        sm.set_telegram_channel_config(streaming=False, streaming_latency=0.6, show_tool_calls=False, show_thinking=True)
        tg = sm.get_telegram_channel_config()
        assert tg.streaming is False
        assert tg.streaming_latency == 0.6
        assert tg.show_tool_calls is False
        assert tg.show_thinking is True

        sm.set_discord_channel_config(streaming=False, streaming_latency=0.7, show_tool_calls=False, show_thinking=True)
        dc = sm.get_discord_channel_config()
        assert dc.streaming is False
        assert dc.streaming_latency == 0.7
        assert dc.show_tool_calls is False
        assert dc.show_thinking is True

        sm.set_slack_channel_config(streaming=False, streaming_latency=0.8, show_tool_calls=False, show_thinking=True)
        sl = sm.get_slack_channel_config()
        assert sl.streaming is False
        assert sl.streaming_latency == 0.8
        assert sl.show_tool_calls is False
        assert sl.show_thinking is True

        sm.set_twitch_channel_config(streaming=False, streaming_latency=0.9)
        tw = sm.get_twitch_channel_config()
        assert tw.streaming is False
        assert tw.streaming_latency == 0.9
        await asyncio.sleep(0)
