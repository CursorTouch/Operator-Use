from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


# ── Per-channel auth models ───────────────────────────────────────────────────

class TelegramAuth(BaseModel):
    model_config = ConfigDict(extra='ignore')
    bot_token: str = ''


class DiscordAuth(BaseModel):
    model_config = ConfigDict(extra='ignore')
    bot_token: str = ''


class SlackAuth(BaseModel):
    model_config = ConfigDict(extra='ignore')
    bot_token: str = ''
    app_token: str = ''


class TwitchAuth(BaseModel):
    model_config = ConfigDict(extra='ignore')
    token: str = ''


class ChannelTokens(BaseModel):
    """Root model for ~/.program/channels_auth.json."""
    model_config = ConfigDict(extra='ignore')
    telegram: TelegramAuth = TelegramAuth()
    discord: DiscordAuth = DiscordAuth()
    slack: SlackAuth = SlackAuth()
    twitch: TwitchAuth = TwitchAuth()


# ── ChannelAuthManager ────────────────────────────────────────────────────────

class ChannelAuthManager:
    """
    Loads and persists channel bot tokens from ~/.program/channels_auth.json.

    Kept separate from provider auth (OAuth / API keys) so channel credentials
    are never mixed with LLM provider credentials. The file is global-only and
    should be added to .gitignore.

    Usage:
        auth = ChannelAuthManager(get_channels_auth_path())
        token = auth.telegram.bot_token
        auth.set_slack(bot_token="xoxb-...", app_token="xapp-...")
    """

    def __init__(self, auth_path: Path) -> None:
        self._path = auth_path
        self._tokens = self._load()

    # ── Load / save ───────────────────────────────────────────────────────────

    def _load(self) -> ChannelTokens:
        if not self._path.exists():
            return ChannelTokens()
        try:
            return ChannelTokens.model_validate(json.loads(self._path.read_text()))
        except Exception:
            logger.warning('channels_auth.json could not be parsed, using empty tokens.')
            return ChannelTokens()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._tokens.model_dump_json(indent=2))
        self._path.chmod(0o600)

    # ── Typed read access ─────────────────────────────────────────────────────

    @property
    def telegram(self) -> TelegramAuth:
        return self._tokens.telegram

    @property
    def discord(self) -> DiscordAuth:
        return self._tokens.discord

    @property
    def slack(self) -> SlackAuth:
        return self._tokens.slack

    @property
    def twitch(self) -> TwitchAuth:
        return self._tokens.twitch

    # ── Setters (persist immediately) ─────────────────────────────────────────

    def set_telegram(self, *, bot_token: str) -> None:
        self._tokens.telegram = TelegramAuth(bot_token=bot_token)
        self._save()

    def set_discord(self, *, bot_token: str) -> None:
        self._tokens.discord = DiscordAuth(bot_token=bot_token)
        self._save()

    def set_slack(self, *, bot_token: str, app_token: str) -> None:
        self._tokens.slack = SlackAuth(bot_token=bot_token, app_token=app_token)
        self._save()

    def set_twitch(self, *, token: str) -> None:
        self._tokens.twitch = TwitchAuth(token=token)
        self._save()
