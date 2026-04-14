from dataclasses import dataclass, field


@dataclass
class Config:
    enabled: bool = False
    allow_from: list[str] = field(default_factory=list)


@dataclass
class TelegramConfig(Config):
    """Telegram API."""

    token: str = ""
    use_webhook: bool = False
    webhook_url: str = ""
    webhook_path: str = "/telegram"
    webhook_port: int = 8080
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL
    reply_to_message: bool = True  # If true, bot replies quote the original message
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)


@dataclass
class DiscordConfig(Config):
    """Discord API."""

    token: str = ""
    use_webhook: bool = False
    webhook_url: str = ""
    webhook_path: str = "/discord"
    webhook_port: int = 8080
    reply_to_message: bool = True  # If true, bot replies quote the original message
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)


@dataclass
class SlackConfig(Config):
    """Slack API (DM mode)."""

    bot_token: str = ""  # xoxb-... token
    app_token: str = ""  # xapp-... token (for Socket Mode)
    use_webhook: bool = False
    webhook_url: str = ""  # Public URL for Slack Request URL
    webhook_path: str = "/slack"
    webhook_port: int = 8080
    signing_secret: str = ""  # Slack signing secret for request verification
    reply_to_message: bool = True  # If true, bot replies to the original message
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)


@dataclass
class TwitchConfig(Config):
    """Twitch IRC channel configuration."""

    token: str = ""  # OAuth token (oauth:xxxx or raw token)
    nick: str = ""  # Bot's Twitch username
    channel_name: str = ""  # Channel to join (without #)
    account_id: str = ""  # Internal routing ID (set automatically for per-agent bots)
    prefix: str = "!"  # Command prefix for twitchio


@dataclass
class MQTTConfig(Config):
    """MQTT broker configuration for IoT/hardware device connectivity."""

    broker_host: str = ""  # Broker hostname or IP (required to enable)
    broker_port: int = 1883  # 1883 = plain, 8883 = TLS
    username: str = ""
    password: str = ""
    topic_prefix: str = (
        "operator"  # Subscribes to {prefix}/in/#, publishes to {prefix}/out/{device}
    )
    client_id: str = "operator-agent"
    tls: bool = False
    keepalive: int = 60
