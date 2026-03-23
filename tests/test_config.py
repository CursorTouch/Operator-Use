"""Tests for configuration models and load_config."""

import json
import pytest
from pydantic import ValidationError

from operator_use.config.service import (
    TelegramConfig,
    DiscordConfig,
    SlackConfig,
    TwitchConfig,
    ChannelsConfig,
    LLMConfig,
    STTConfig,
    TTSConfig,
    AgentDefinition,
    AgentDefaults,
    AgentsConfig,
    BindingMatch,
    AgentRouteBinding,
    Config,
    load_config,
)


# --- Channel configs ---

def test_telegram_config_defaults():
    c = TelegramConfig()
    assert c.enabled is False
    assert c.token == ""
    assert c.allow_from == []
    assert c.use_webhook is False
    assert c.reply_to_message is True


def test_discord_config_defaults():
    c = DiscordConfig()
    assert c.enabled is False
    assert c.token == ""
    assert c.allow_from == []


def test_slack_config_defaults():
    c = SlackConfig()
    assert c.enabled is False
    assert c.bot_token == ""
    assert c.app_token == ""


def test_twitch_config_defaults():
    c = TwitchConfig()
    assert c.enabled is False
    assert c.prefix == "!"


def test_channels_config_defaults():
    c = ChannelsConfig()
    assert isinstance(c.telegram, TelegramConfig)
    assert isinstance(c.discord, DiscordConfig)
    assert isinstance(c.slack, SlackConfig)
    assert isinstance(c.twitch, TwitchConfig)


def test_telegram_config_camel_case():
    c = TelegramConfig.model_validate({"enabled": True, "token": "abc", "allowFrom": ["123"]})
    assert c.enabled is True
    assert c.allow_from == ["123"]


def test_discord_config_snake_case():
    c = DiscordConfig.model_validate({"enabled": True, "token": "tok", "allow_from": ["456"]})
    assert c.allow_from == ["456"]


# --- LLM / STT / TTS ---

def test_llm_config_defaults():
    c = LLMConfig()
    assert c.provider == "openai"
    assert c.model == "gpt-4o"


def test_stt_config_defaults():
    c = STTConfig()
    assert c.enabled is False
    assert c.provider is None


def test_tts_config_defaults():
    c = TTSConfig()
    assert c.enabled is False
    assert c.voice is None


# --- AgentDefinition ---

def test_agent_definition_valid():
    a = AgentDefinition(id="my-agent", computer_use=False, browser_use=False)
    assert a.id == "my-agent"


def test_agent_definition_computer_use_only():
    a = AgentDefinition(id="desk", computer_use=True, browser_use=False)
    assert a.computer_use is True


def test_agent_definition_browser_use_only():
    a = AgentDefinition(id="web", computer_use=False, browser_use=True)
    assert a.browser_use is True


def test_agent_definition_both_raises():
    with pytest.raises(ValidationError, match="cannot both be enabled"):
        AgentDefinition(id="bad", computer_use=True, browser_use=True)


def test_agent_definition_default_workspace_none():
    a = AgentDefinition(id="op")
    assert a.workspace is None


# --- AgentsConfig ---

def test_agents_config_defaults():
    c = AgentsConfig()
    assert c.list == []
    assert isinstance(c.defaults, AgentDefaults)


def test_agent_defaults():
    d = AgentDefaults()
    assert d.max_tool_iterations == 40
    assert d.streaming is True


# --- BindingMatch / AgentRouteBinding ---

def test_binding_match_defaults():
    b = BindingMatch()
    assert b.channel == ""
    assert b.peer is None


def test_binding_match_with_channel():
    b = BindingMatch(channel="telegram")
    assert b.channel == "telegram"


def test_agent_route_binding_defaults():
    r = AgentRouteBinding()
    assert r.agent_id == "operator"
    assert isinstance(r.match, BindingMatch)


# --- Config ---

def test_config_defaults():
    c = Config()
    assert c.bindings == []
    assert c.agents.list == []


def test_config_default_agent_none_when_empty():
    c = Config()
    assert c.default_agent is None


def test_config_default_agent_first():
    c = Config(agents=AgentsConfig(list=[AgentDefinition(id="first"), AgentDefinition(id="second", browser_use=False)]))
    assert c.default_agent.id == "first"


# --- load_config ---

def test_load_config_no_file(tmp_path):
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config)


def test_load_config_from_json(tmp_path):
    data = {
        "agents": {
            "list": [{"id": "json-agent", "browser_use": False}]
        }
    }
    (tmp_path / "config.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert any(a.id == "json-agent" for a in cfg.agents.list)


def test_load_config_invalid_json_uses_defaults(tmp_path):
    (tmp_path / "config.json").write_text("{ invalid json }", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config)
