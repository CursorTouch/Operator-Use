from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from program.bus.service import Bus
from program.gateway.service import Gateway

if TYPE_CHECKING:
    from program.runtime.service import Runtime
    from program.settings.manager import SettingsManager
    from program.auth.channels import ChannelAuthManager as AuthManager

logger = logging.getLogger(__name__)


class GatewayManager:
    """
    Owns the Gateway, Bus, and all channel lifecycles.

    Created in Runtime.__init__() after the Runtime itself exists.
    start() spins up the gateway processing loops and each enabled channel as
    asyncio Tasks; stop() cancels them all.

    Channels that are disabled or missing required tokens are silently skipped
    with a log warning so a misconfigured channel never crashes the agent.

    Usage (handled automatically by Runtime):
        gm = GatewayManager(runtime, settings_manager, auth_manager)
        gm.start()
        ...
        gm.stop()
    """

    def __init__(
        self,
        runtime: Runtime,
        settings_manager: SettingsManager,
        auth_manager: AuthManager,
    ) -> None:
        self._runtime = runtime
        self._settings = settings_manager
        self._auth = auth_manager
        self._bus = Bus()
        self.gateway = Gateway(self._bus, runtime)
        self._tasks: list[asyncio.Task] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start gateway processing loops and all enabled channels as background asyncio tasks."""
        # Start the gateway event loops
        asyncio.get_event_loop().create_task(
            self.gateway.start(), name='gateway:main'
        )

        cfg = self._settings.get_channels_settings()
        auth = self._auth

        if cfg.websocket.enabled:
            self._start_task('websocket', self._run_websocket(cfg.websocket))

        if cfg.telegram.enabled:
            if not auth.telegram.bot_token:
                logger.warning('Telegram channel enabled but bot_token is not set in auth.json.')
            else:
                self._start_task('telegram', self._run_telegram(auth.telegram.bot_token))

        if cfg.discord.enabled:
            if not auth.discord.bot_token:
                logger.warning('Discord channel enabled but bot_token is not set in auth.json.')
            else:
                self._start_task('discord', self._run_discord(auth.discord.bot_token))

        if cfg.slack.enabled:
            if not auth.slack.bot_token or not auth.slack.app_token:
                logger.warning('Slack channel enabled but bot_token/app_token not set in auth.json.')
            else:
                self._start_task('slack', self._run_slack(auth.slack.bot_token, auth.slack.app_token))

        if cfg.twitch.enabled:
            if not auth.twitch.token:
                logger.warning('Twitch channel enabled but token is not set in auth.json.')
            elif not cfg.twitch.channel_name or not cfg.twitch.nick:
                logger.warning('Twitch channel enabled but channel_name/nick not set in settings.')
            else:
                self._start_task('twitch', self._run_twitch(cfg.twitch, auth.twitch.token))

    def stop(self) -> None:
        """Cancel all running channel tasks and stop the gateway."""
        asyncio.get_event_loop().create_task(self.gateway.stop())
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    # ── Internal task helpers ─────────────────────────────────────────────────

    def _start_task(self, name: str, coro) -> None:
        task = asyncio.create_task(coro, name=f'gateway:{name}')
        self._tasks.append(task)
        logger.info('Gateway channel started: %s', name)

    async def _run_websocket(self, cfg) -> None:
        from program.gateway.channels.websocket import WebSocketServer
        await WebSocketServer(self.gateway, host=cfg.host, port=cfg.port).start()

    async def _run_telegram(self, bot_token: str) -> None:
        from program.gateway.channels.telegram import TelegramChannel
        ch = TelegramChannel(token=bot_token)
        self.gateway.register(ch)
        await ch.connect()

    async def _run_discord(self, bot_token: str) -> None:
        from program.gateway.channels.discord import DiscordChannel
        ch = DiscordChannel(token=bot_token)
        self.gateway.register(ch)
        await ch.connect()

    async def _run_slack(self, bot_token: str, app_token: str) -> None:
        from program.gateway.channels.slack import SlackChannel
        ch = SlackChannel(bot_token=bot_token, app_token=app_token)
        self.gateway.register(ch)
        await ch.connect()

    async def _run_twitch(self, cfg, token: str) -> None:
        from program.gateway.channels.twitch import TwitchChannel
        ch = TwitchChannel(
            token=token,
            nick=cfg.nick,
            channel_name=cfg.channel_name,
            prefix=cfg.prefix,
            allow_from=cfg.allow_from,
        )
        self.gateway.register(ch)
        await ch.connect()
