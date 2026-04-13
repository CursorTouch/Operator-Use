"""Centralized configuration schema for Operator, inspired by nanobot."""

from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TelegramConfig(Base):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)
    allow_from: List[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    use_webhook: bool = False
    webhook_url: str = ""
    webhook_path: str = "/telegram"
    webhook_port: int = 8080
    proxy: Optional[str] = None  # HTTP/SOCKS5 proxy URL
    reply_to_message: bool = True  # If true, bot replies quote the original message
    media_dir: Optional[str] = None


class DiscordConfig(Base):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)
    allow_from: List[str] = Field(default_factory=list)  # Allowed user IDs
    reply_to_message: bool = True  # If true, bot replies quote the original message
    use_webhook: bool = False  # Use webhook mode instead of WebSocket
    webhook_url: str = ""  # Webhook URL for receiving events
    webhook_path: str = "/discord"  # Webhook path
    webhook_port: int = 8080  # Webhook server port
    media_dir: Optional[str] = None


class SlackConfig(Base):
    """Slack channel configuration (DM mode)."""

    enabled: bool = False
    bot_token: str = ""  # Bot User OAuth Token (xoxb-...)
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)
    app_token: str = ""  # App-Level Token (xapp-..., for Socket Mode)
    use_webhook: bool = False  # Use webhook mode instead of Socket Mode
    webhook_url: str = ""  # Public URL for Slack Request URL
    webhook_path: str = "/slack"  # Webhook path
    webhook_port: int = 8080  # Webhook server port
    signing_secret: str = ""  # Slack signing secret for request verification
    reply_to_message: bool = True  # If true, bot replies to the original message
    allow_from: List[str] = Field(default_factory=list)  # Allowed user IDs


class TwitchConfig(Base):
    """Twitch channel configuration."""

    enabled: bool = False
    token: str = ""  # OAuth token (oauth:xxxx or raw token)
    nick: str = ""  # Bot's Twitch username
    channel_name: str = ""  # Channel to join (without #)
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)
    allow_from: List[str] = Field(default_factory=list)  # Allowed Twitch usernames
    prefix: str = "!"  # Command prefix for twitchio


class ChannelsConfig(Base):
    """Configuration for chat channels."""

    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    twitch: TwitchConfig = Field(default_factory=TwitchConfig)


class LLMConfig(Base):
    """LLM configuration."""

    provider: str = "openai"
    model: str = "gpt-4o"


class STTConfig(Base):
    """Speech-to-Text configuration."""

    enabled: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None


class TTSConfig(Base):
    """Text-to-Speech configuration."""

    enabled: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    voice: Optional[str] = None


class ImageConfig(Base):
    """Image generation configuration."""

    enabled: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    size: Optional[str] = None
    quality: Optional[str] = None
    style: Optional[str] = None


class SearchConfig(Base):
    """Web search provider configuration. Search is always enabled — users pick the provider."""

    provider: Optional[str] = None  # "ddgs", "exa", "tavily" — defaults to ddgs if unset
    api_key: Optional[str] = None  # required for exa and tavily


class MCPServerConfig(Base):
    """Configuration for a single MCP server connection."""

    name: str
    transport: str = "stdio"  # "stdio" | "http" | "sse"
    command: Optional[str] = None  # For stdio: executable path (e.g. "npx", "uvx", "python")
    args: List[str] = Field(default_factory=list)  # CLI args for stdio subprocess
    url: Optional[str] = None  # For http/sse transport
    env: Dict[str, str] = Field(default_factory=dict)  # Extra env vars for subprocess
    auth_token: Optional[str] = None  # Bearer token for HTTP auth header


class PeerMatch(Base):
    """Match a specific chat/channel/group within a platform."""

    kind: str = "channel"  # "channel", "group", "direct", "thread"
    id: str = ""


class BindingMatch(Base):
    """Criteria for matching an incoming message to an agent."""

    channel: str = ""  # Platform name: "telegram", "discord", "slack", etc.
    peer: Optional[PeerMatch] = None  # Specific chat/channel ID within the platform
    account_id: Optional[str] = None  # Bot account (for multi-account setups)


class AgentRouteBinding(Base):
    """Routes messages matching `match` to `agent_id`.

    Bindings are evaluated top-down; first match wins.
    Peer matches are more specific than channel-only matches.
    """

    agent_id: str = "operator"
    match: BindingMatch = Field(default_factory=BindingMatch)


class ToolsConfig(Base):
    """Per-agent tool configuration."""

    profile: str = "full"  # Base preset: "minimal", "coding", or "full"
    also_allow: List[str] = Field(default_factory=list)  # Tool names to add on top of profile
    deny: List[str] = Field(default_factory=list)  # Tool names to remove from resolved list


class RetryConfig(Base):
    """Retry configuration for subagents — exponential backoff."""

    max_retries: int = 3
    base_delay: float = 1.0  # seconds before first retry
    max_delay: float = 60.0  # cap on delay between retries
    backoff_factor: float = 2.0  # multiplier per attempt


class SubagentConfig(Base):
    """Global subagent configuration — applies to all subagents spawned by any agent."""

    max_iterations: int = 20
    system_prompt: str = ""  # If empty, subagent uses its built-in default system prompt
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    retry: Optional[RetryConfig] = None  # If None, no retry (backward-compatible)
    max_concurrent: int = 10  # Max concurrent subagent tasks (0 = unlimited)


class AgentDefaults(Base):
    """Default settings shared across all agents (can be overridden per agent)."""

    max_tool_iterations: int = 40
    streaming: bool = True
    subagent: SubagentConfig = Field(default_factory=SubagentConfig)


class PluginConfig(Base):
    """A plugin entry on an agent — id maps to a registered plugin class."""

    id: str
    enabled: bool = True


class AgentDefinition(Base):
    """Individual agent definition."""

    id: str
    description: str = ""  # Short role/capability summary used for delegation and routing hints
    workspace: Optional[str] = None  # Defaults to ~/.operator-use/workspaces/<id>
    llm_config: Optional[LLMConfig] = None  # Overrides agents.defaults.llm_config
    max_tool_iterations: Optional[int] = None  # Overrides agents.defaults
    channels: Optional["ChannelsConfig"] = None  # Per-agent dedicated channel bots
    plugins: List[PluginConfig] = Field(default_factory=list)  # Ordered list of plugins
    tools: ToolsConfig = Field(default_factory=ToolsConfig)  # Tool profile + allow/deny
    prompt_mode: str = "full"  # Prompt mode: "full", "minimal", or "none"
    system_prompt: str = ""  # Freeform instructions appended to every system prompt call
    acp_token: str = ""  # If set, this agent is only accessible via ACP with this token


class AgentsConfig(Base):
    """Multi-agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    list: List[AgentDefinition] = Field(default_factory=list)


class ACPAgentEntry(Base):
    """A pre-approved remote ACP agent the LLM is allowed to call."""

    base_url: str  # e.g. "http://localhost:9000"
    agent_id: str = ""  # Remote agent ID on the server; empty = auto-discover
    auth_token: str = ""  # Bearer token for the remote server
    timeout: float = 120.0
    description: str = ""  # Human-readable hint shown to the LLM


class ACPServerSettings(Base):
    """Config for exposing Operator itself as an ACP server on the local network."""

    enabled: bool = False
    host: str = "0.0.0.0"  # "0.0.0.0" = reachable by other machines on the LAN
    port: int = 8765
    id: str = ""  # Stable UUID identifying this server instance (auto-generated on first run)
    auth_token: str = ""  # Optional bearer token to protect the endpoint (all agents)
    public_url: str = ""  # Advertised URL for agent discovery (e.g. http://192.168.1.10:8765)


class ProviderConfig(Base):
    """LLM provider configuration (keys, bases)."""

    api_key: str = ""
    api_base: Optional[str] = None


class ProvidersConfig(Base):
    """Configuration for all LLM providers (to store keys centrally)."""

    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    google: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    mistral: ProviderConfig = Field(default_factory=ProviderConfig)
    nvidia: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)
    cerebras: ProviderConfig = Field(default_factory=ProviderConfig)
    open_router: ProviderConfig = Field(default_factory=ProviderConfig)
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    xai: ProviderConfig = Field(default_factory=ProviderConfig)
    sarvam: ProviderConfig = Field(default_factory=ProviderConfig)
    together: ProviderConfig = Field(default_factory=ProviderConfig)
    fal: ProviderConfig = Field(default_factory=ProviderConfig)
    codex: ProviderConfig = Field(default_factory=ProviderConfig)
    claude_code: ProviderConfig = Field(default_factory=ProviderConfig)
    antigravity: ProviderConfig = Field(default_factory=ProviderConfig)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig)


class HeartbeatConfig(Base):
    """Heartbeat configuration."""

    enabled: bool = False
    llm_config: Optional[LLMConfig] = None  # Dedicated LLM for heartbeat tasks


class Config(BaseSettings):
    """Root configuration for Operator."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    bindings: List[AgentRouteBinding] = Field(default_factory=list)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    # Named registry of pre-approved remote ACP agents.
    # The LLM can only call agents listed here — it never supplies raw URLs.
    acp_agents: Dict[str, ACPAgentEntry] = Field(default_factory=dict)
    # ACP server — exposes this Operator instance as an ACP agent on the network.
    acp_server: ACPServerSettings = Field(default_factory=ACPServerSettings)
    # MCP (Model Context Protocol) server configurations for runtime connection.
    mcp_servers: Dict[str, MCPServerConfig] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_prefix="OPERATOR_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def default_agent(self) -> AgentDefinition | None:
        """Return the first agent definition, or None if none are configured."""
        return self.agents.list[0] if self.agents.list else None


def load_config(user_data_dir: Path) -> Config:
    """Load configuration from .operator_use/config.json and environment."""
    import json
    import uuid

    path = user_data_dir / "config.json"
    data = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load config from {path}: {e}")

    # Initialize Config (Pydantic merges JSON data + actual environment variables)
    config = Config(**data)

    # Auto-generate a stable id on first run (new installs and upgrades).
    # Written back immediately so the same ID is reused on every subsequent start.
    if not config.acp_server.id:
        config.acp_server.id = str(uuid.uuid4())
        if path.exists():
            try:
                acp_block = data.get("acp_server", {})
                acp_block["id"] = config.acp_server.id
                data["acp_server"] = acp_block
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
            except Exception as e:
                print(f"Warning: Could not persist acp_server.id: {e}")

    return config
