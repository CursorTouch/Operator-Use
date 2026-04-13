"""Tests for configuration models and load_config."""

import json

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
    a = AgentDefinition(id="my-agent", description="General purpose manager")
    assert a.id == "my-agent"
    assert a.description == "General purpose manager"
    assert a.plugins == []


def test_agent_definition_with_browser_plugin():
    from operator_use.config.service import PluginConfig

    a = AgentDefinition(id="web", plugins=[PluginConfig(id="browser_use", enabled=True)])
    assert any(p.id == "browser_use" and p.enabled for p in a.plugins)


def test_agent_definition_with_computer_plugin():
    from operator_use.config.service import PluginConfig

    a = AgentDefinition(id="desk", plugins=[PluginConfig(id="computer_use", enabled=True)])
    assert any(p.id == "computer_use" and p.enabled for p in a.plugins)


def test_agent_definition_plugin_disabled():
    from operator_use.config.service import PluginConfig

    a = AgentDefinition(id="op", plugins=[PluginConfig(id="browser_use", enabled=False)])
    assert a.plugins[0].enabled is False


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
    c = Config(
        agents=AgentsConfig(list=[AgentDefinition(id="first"), AgentDefinition(id="second")])
    )
    assert c.default_agent.id == "first"


# --- load_config ---


def test_load_config_no_file(tmp_path):
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config)


def test_load_config_from_json(tmp_path):
    data = {"agents": {"list": [{"id": "json-agent"}]}}
    (tmp_path / "config.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert any(a.id == "json-agent" for a in cfg.agents.list)


def test_load_config_invalid_json_uses_defaults(tmp_path):
    (tmp_path / "config.json").write_text("{ invalid json }", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config)


# --- ACP id auto-generation ---


def test_load_config_generates_id_when_missing(tmp_path):
    """id is auto-generated and persisted on first load."""
    data = {"agents": {"list": [{"id": "op"}]}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.acp_server.id != ""

    # Must be persisted back to disk
    saved = json.loads(path.read_text())
    assert saved["acp_server"]["id"] == cfg.acp_server.id


def test_load_config_id_stable_across_reloads(tmp_path):
    """Same id is returned on every subsequent load."""
    data = {"agents": {"list": [{"id": "op"}]}}
    (tmp_path / "config.json").write_text(json.dumps(data), encoding="utf-8")

    first = load_config(tmp_path).acp_server.id
    second = load_config(tmp_path).acp_server.id
    assert first == second


def test_load_config_respects_existing_id(tmp_path):
    """If id already exists in config it is not overwritten."""
    existing_id = "my-custom-server-id"
    data = {"acp_server": {"id": existing_id}}
    (tmp_path / "config.json").write_text(json.dumps(data), encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.acp_server.id == existing_id
