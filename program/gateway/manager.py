from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from program.bus.service import Bus
from program.gateway.service import Gateway
from program.hooks.types import GatewayStartupEvent, GatewayStopEvent

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
        settings_manager: SettingsManager | None,
        auth_manager: AuthManager | None,
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

        if self._settings is None or self._auth is None:
            return

        cfg = self._settings.get_channels_settings()
        auth = self._auth

        if cfg.websocket.enabled:
            self._start_task('websocket', self._run_websocket(cfg.websocket))

        if cfg.telegram.enabled:
            if not auth.telegram.bot_token:
                logger.warning('Telegram channel enabled but bot_token is not set in auth/channels.json (or TELEGRAM_BOT_TOKEN env var).')
            else:
                self._start_task('telegram', self._run_telegram(auth.telegram.bot_token, cfg.telegram))

        if cfg.discord.enabled:
            if not auth.discord.bot_token:
                logger.warning('Discord channel enabled but bot_token is not set in auth/channels.json (or DISCORD_BOT_TOKEN env var).')
            else:
                self._start_task('discord', self._run_discord(auth.discord.bot_token, cfg.discord))

        if cfg.slack.enabled:
            if not auth.slack.bot_token or not auth.slack.app_token:
                logger.warning('Slack channel enabled but bot_token/app_token not set in auth/channels.json (or SLACK_BOT_TOKEN/SLACK_APP_TOKEN env vars).')
            else:
                self._start_task('slack', self._run_slack(auth.slack.bot_token, auth.slack.app_token, cfg.slack))

        if cfg.twitch.enabled:
            if not auth.twitch.token:
                logger.warning('Twitch channel enabled but token is not set in auth/channels.json (or TWITCH_TOKEN env var).')
            elif not cfg.twitch.channel_name or not cfg.twitch.nick:
                logger.warning('Twitch channel enabled but channel_name/nick not set in settings.')
            else:
                self._start_task('twitch', self._run_twitch(cfg.twitch, auth.twitch.token))

        if cfg.email.enabled:
            if not auth.email.username or not auth.email.password:
                logger.warning('Email channel enabled but username/password not set in auth/channels.json (or EMAIL_USERNAME/EMAIL_PASSWORD env vars).')
            elif not cfg.email.imap_host or not cfg.email.smtp_host:
                logger.warning('Email channel enabled but imap_host/smtp_host not set in settings.')
            else:
                self._start_task('email', self._run_email(cfg.email, auth.email.username, auth.email.password))

        asyncio.get_event_loop().create_task(
            self.gateway.hooks.emit(GatewayStartupEvent(
                channel_ids=list(self.gateway._channels.keys()),
            ))
        )

    def stop(self) -> None:
        """Cancel all running channel tasks and stop the gateway."""
        asyncio.get_event_loop().create_task(
            self.gateway.hooks.emit(GatewayStopEvent(
                channel_ids=list(self.gateway._channels.keys()),
            ))
        )
        asyncio.get_event_loop().create_task(self.gateway.stop())
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def astop(self) -> None:
        """Await full channel teardown before the event loop closes.

        Cancels each channel task and then awaits it so the channel's
        ``finally: await disconnect()`` runs to completion inside the still-live
        loop. Library backends (python-telegram-bot, discord.py, …) log noisy
        CancelledError tracebacks if the loop dies mid-teardown; awaiting here
        lets them shut down gracefully under the channels' log-muting context.
        """
        try:
            await self.gateway.hooks.emit(GatewayStopEvent(
                channel_ids=list(self.gateway._channels.keys()),
            ))
        except Exception:
            pass
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        await self.gateway.stop()

    # ── Internal task helpers ─────────────────────────────────────────────────

    def _start_task(self, name: str, coro) -> None:
        task = asyncio.create_task(coro, name=f'gateway:{name}')
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._on_task_done(name, t))
        logger.info('Gateway channel started: %s', name)

    def _on_task_done(self, name: str, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error('Gateway channel %r crashed: %s', name, exc, exc_info=exc)

    async def _run_websocket(self, cfg) -> None:
        from program.gateway.channels.websocket import WebSocketServer
        await WebSocketServer(self.gateway, host=cfg.host, port=cfg.port).start()

    async def _run_telegram(self, bot_token: str, tcfg) -> None:
        from program.gateway.channels.telegram import TelegramChannel
        cmds = [(c.name, c.description) for c in self._runtime.commands.list()]
        ch = TelegramChannel(
            token=bot_token,
            commands=cmds,
            allow_from=tcfg.allow_from,
            reply_to_message=tcfg.reply_to_message,
            group_policy=tcfg.group_policy,
            show_tool_notifications=tcfg.show_tool_notifications,
        )
        self.gateway.register(ch)
        await ch.connect()

    async def _run_discord(self, bot_token: str, dcfg) -> None:
        from program.gateway.channels.discord import DiscordChannel
        ch = DiscordChannel(
            token=bot_token,
            allow_from=dcfg.allow_from,
            reply_to_message=dcfg.reply_to_message,
            group_policy=dcfg.group_policy,
            show_tool_notifications=dcfg.show_tool_notifications,
        )
        self.gateway.register(ch)
        await ch.connect()

    async def _run_slack(self, bot_token: str, app_token: str, scfg) -> None:
        from program.gateway.channels.slack import SlackChannel
        ch = SlackChannel(
            bot_token=bot_token,
            app_token=app_token,
            allow_from=scfg.allow_from,
            reply_to_message=scfg.reply_to_message,
            show_tool_notifications=scfg.show_tool_notifications,
        )
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

    async def _run_email(self, cfg, username: str, password: str) -> None:
        from program.gateway.channels.email import EmailChannel
        ch = EmailChannel(
            username=username,
            password=password,
            imap_host=cfg.imap_host,
            smtp_host=cfg.smtp_host,
            imap_port=cfg.imap_port,
            smtp_port=cfg.smtp_port,
            poll_interval=cfg.poll_interval,
            allow_from=cfg.allow_from if cfg.allow_from else None,
        )
        self.gateway.register(ch)
        await ch.connect()
