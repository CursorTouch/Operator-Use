"""ACP server and client configuration."""

from dataclasses import dataclass, field


@dataclass
class ACPServerConfig:
    """Configuration for the built-in ACP server (exposes Operator as an ACP agent)."""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8765
    # Stable UUID identifying this server instance across restarts (set by load_config)
    id: str = ""
    # Global bearer token — grants access to ALL agents (fallback when per_agent_tokens is empty)
    auth_token: str = ""
    # Per-agent bearer tokens: {agent_id: token}
    # When populated, each token unlocks only its own agent — callers cannot see or reach other agents.
    # Takes precedence over auth_token when non-empty.
    per_agent_tokens: dict = field(default_factory=dict)
    # --- ACP Provenance (Ed25519 signatures) ---
    # Sign all responses with this agent's private key
    sign_responses: bool = False
    # Require incoming requests to carry valid X-ACP-Signature headers
    verify_signatures: bool = False
    # Path to PEM file for persisting the keypair (generated on first run)
    key_path: str = ""
    # Pre-registered trusted agents: {agent_id: base64url_public_key}
    # Used for signature verification without requiring X-ACP-Agent-URL discovery
    trusted_agents: dict = field(default_factory=dict)
    # Advertised base URL of this server (included in X-ACP-Agent-URL on responses)
    public_url: str = ""


@dataclass
class ACPClientConfig:
    """Configuration for the ACP client channel (Operator calls a remote ACP agent)."""

    enabled: bool = False
    # Base URL of the remote ACP server (e.g. "http://localhost:9000")
    base_url: str = ""
    # Target agent ID on the remote server (e.g. "claude-code")
    agent_id: str = "claude-code"
    # Optional bearer token for authenticating with the remote server
    auth_token: str = ""
    # How long to wait (seconds) for a run to complete in sync mode
    timeout: float = 120.0
    # Channel name used to identify messages from this ACP client
    channel_name: str = "acp"
    # --- ACP Provenance (Ed25519 signatures) ---
    # Sign all outgoing requests with this agent's private key
    sign_requests: bool = False
    # Path to PEM file for persisting the keypair (generated on first run)
    key_path: str = ""
    # This agent's ID, included in X-ACP-Agent-ID header when signing
    agent_id_self: str = "operator"
    # Advertised base URL of this agent, included in X-ACP-Agent-URL for key discovery
    public_url: str = ""


@dataclass
class ACPStdioConfig:
    """Configuration for the JSON-RPC 2.0 over stdio channel.

    When enabled, Operator reads JSON-RPC requests from stdin and writes
    responses to stdout — letting IDEs and CLI tools (Claude Code, Zed,
    Codex, etc.) pipe directly into the agent without an HTTP server.
    """

    enabled: bool = False
    agent_id: str = "operator"
    agent_name: str = "Operator"
    agent_description: str = "Operator AI agent accessible via stdio"
    # Redirect all logging to stderr so stdout stays clean JSON-RPC only
    redirect_logging_to_stderr: bool = True
    # Seconds to wait for an agent response before timing out
    timeout: float = 120.0
