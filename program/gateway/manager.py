from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from program.gateway.service import Gateway
from program.hooks.types import GatewayStartupEvent, GatewayStopEvent

if TYPE_CHECKING:
    from program.runtime.service import Runtime
    from program.agent.profile import AgentProfile

logger = logging.getLogger(__name__)


class GatewayManager:
    """
    Owns the Gateway and all channel lifecycles.

    At startup, loads all named agent profiles that are not disabled in global
    settings. Each profile that has channels configured (in its settings.json)
    and channel tokens (in its auth/channels.json) gets its own dedicated agent
    and its own channel instances. All of a profile's channels share ONE session.

    Non-profile channels (if any remain in global settings) are also started and
    use the runtime's existing unified/per-session logic.

    Usage:
        gm = GatewayManager(runtime)
        gm.start()
        ...
        gm.stop()
    """

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._settings = runtime.settings_manager
        self._channel_auth = runtime.auth_channel_manager
        self.gateway = Gateway(runtime)
        self._tasks: list[asyncio.Task] = []
        self._gateway_task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start gateway processing loops and all enabled channels as background asyncio tasks."""
        if self._gateway_task is None or self._gateway_task.done():
            self._gateway_task = asyncio.create_task(
                self.gateway.start(), name='gateway:main'
            )

        # Start profile-based channels first
        self._start_profile_channels()

        # Start any non-profile (global) channels that are still configured
        self._start_global_channels()

        asyncio.get_event_loop().create_task(
            self.gateway.hooks.emit(GatewayStartupEvent(
                channel_ids=list(self.gateway._channels.keys()),
            ))
        )

    def _start_profile_channels(self) -> None:
        """Load all non-disabled profiles, create their agents, and start their channels."""
        if self._settings is None:
            return

        disabled = set(self._settings.settings.disabled_profiles or [])
        profiles = self._runtime.get_agent_profiles()

        for profile_name, profile in profiles.items():
            if profile_name in disabled:
                logger.info('Profile %r is disabled — skipping.', profile_name)
                continue

            profile_cfg = self._load_profile_channel_settings(profile)
            if profile_cfg is None:
                continue  # no channels section in profile settings

            profile_auth = self._load_profile_channel_auth(profile)

            # Create a dedicated persistent agent for this profile
            try:
                agent = self._runtime.create_profile_agent(profile)
                self.gateway.register_profile_agent(profile_name, agent)
            except Exception as exc:
                logger.error('Failed to create agent for profile %r: %s', profile_name, exc)
                continue

            self._start_profile_channel_tasks(profile_name, profile_cfg, profile_auth)

    def _start_profile_channel_tasks(
        self,
        profile_name: str,
        cfg,
        auth,
    ) -> None:
        """Start individual channel tasks for a single profile."""
        from program.gateway.channels.types import ChannelsSettings

        if cfg.websocket.enabled:
            channel_name = f'{profile_name}:websocket'
            self._start_task(channel_name, self._run_websocket(cfg.websocket, name=channel_name))

        if cfg.telegram.enabled:
            token = getattr(auth, 'telegram_token', '') or ''
            if not token:
                logger.warning('Profile %r: Telegram enabled but bot_token not set.', profile_name)
            else:
                channel_name = f'{profile_name}:telegram'
                self._start_task(channel_name, self._run_telegram(token, cfg.telegram, name=channel_name))

        if cfg.discord.enabled:
            token = getattr(auth, 'discord_token', '') or ''
            if not token:
                logger.warning('Profile %r: Discord enabled but bot_token not set.', profile_name)
            else:
                channel_name = f'{profile_name}:discord'
                self._start_task(channel_name, self._run_discord(token, cfg.discord, name=channel_name))

        if cfg.slack.enabled:
            bot_token = getattr(auth, 'slack_bot_token', '') or ''
            app_token = getattr(auth, 'slack_app_token', '') or ''
            if not bot_token or not app_token:
                logger.warning('Profile %r: Slack enabled but bot_token/app_token not set.', profile_name)
            else:
                channel_name = f'{profile_name}:slack'
                self._start_task(channel_name, self._run_slack(bot_token, app_token, cfg.slack, name=channel_name))

        if cfg.twitch.enabled:
            token = getattr(auth, 'twitch_token', '') or ''
            if not token:
                logger.warning('Profile %r: Twitch enabled but token not set.', profile_name)
            elif not cfg.twitch.channel_name or not cfg.twitch.nick:
                logger.warning('Profile %r: Twitch enabled but channel_name/nick not set.', profile_name)
            else:
                channel_name = f'{profile_name}:twitch'
                self._start_task(channel_name, self._run_twitch(cfg.twitch, token))

        if cfg.email.enabled:
            username = getattr(auth, 'email_username', '') or ''
            password = getattr(auth, 'email_password', '') or ''
            if not username or not password:
                logger.warning('Profile %r: Email enabled but credentials not set.', profile_name)
            elif not cfg.email.imap_host or not cfg.email.smtp_host:
                logger.warning('Profile %r: Email enabled but imap_host/smtp_host not set.', profile_name)
            else:
                channel_name = f'{profile_name}:email'
                self._start_task(channel_name, self._run_email(cfg.email, username, password))

    def _start_global_channels(self) -> None:
        """Start non-profile (global config) channels — retained for backward compatibility."""
        if self._settings is None or self._channel_auth is None:
            return

        cfg = self._settings.get_channels_settings()
        auth = self._channel_auth

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
        if self._gateway_task is not None:
            self._gateway_task.cancel()
            self._gateway_task = None

    async def astop(self) -> None:
        """Await full channel teardown before the event loop closes."""
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
        if self._gateway_task is not None:
            try:
                await self._gateway_task
            except (asyncio.CancelledError, Exception):
                pass
            self._gateway_task = None

    # ── Profile settings/auth loading ─────────────────────────────────────────

    def _load_profile_channel_settings(self, profile: AgentProfile):
        """Read channels section from profile's settings.json. Returns None if not present."""
        settings_path = profile.settings_path
        if not settings_path.exists():
            return None

        try:
            data = json.loads(settings_path.read_text(encoding='utf-8'))
        except Exception as exc:
            logger.warning('Profile %r: could not parse settings.json: %s', profile.name, exc)
            return None

        channels_data = data.get('channels')
        if not channels_data:
            return None

        try:
            from program.gateway.channels.types import ChannelsSettings
            return ChannelsSettings.model_validate(channels_data)
        except Exception as exc:
            logger.warning('Profile %r: invalid channels config: %s', profile.name, exc)
            return None

    def _load_profile_channel_auth(self, profile: AgentProfile) -> _ProfileChannelAuth:
        """Read auth/channels.json from the profile directory."""
        auth = _ProfileChannelAuth()
        auth_path = profile.auth_channels_path

        if not auth_path.exists():
            return auth

        try:
            data = json.loads(auth_path.read_text(encoding='utf-8'))
        except Exception as exc:
            logger.warning('Profile %r: could not parse auth/channels.json: %s', profile.name, exc)
            return auth

        auth.telegram_token = data.get('telegram', {}).get('bot_token', '')
        auth.discord_token = data.get('discord', {}).get('bot_token', '')
        auth.slack_bot_token = data.get('slack', {}).get('bot_token', '')
        auth.slack_app_token = data.get('slack', {}).get('app_token', '')
        auth.twitch_token = data.get('twitch', {}).get('token', '')
        auth.email_username = data.get('email', {}).get('username', '')
        auth.email_password = data.get('email', {}).get('password', '')
        return auth

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

    async def _run_websocket(self, cfg, name: str = 'websocket') -> None:
        from program.gateway.channels.websocket import WebSocketServer
        await WebSocketServer(self.gateway, host=cfg.host, port=cfg.port).start()

    async def _run_telegram(self, bot_token: str, tcfg, name: str = 'telegram') -> None:
        from program.gateway.channels.telegram import TelegramChannel
        cmds = [(c.name, c.description) for c in self._runtime.commands.list()]
        ch = TelegramChannel(
            token=bot_token,
            name=name,
            commands=cmds,
            allow_from=tcfg.allow_from,
            group_policy=tcfg.group_policy,
            show_tool_calls=tcfg.show_tool_calls,
            show_thinking=tcfg.show_thinking,
            streaming=tcfg.streaming,
            streaming_latency=tcfg.streaming_latency,
        )
        self.gateway.register(ch)
        await ch.connect()

    async def _run_discord(self, bot_token: str, dcfg, name: str = 'discord') -> None:
        from program.gateway.channels.discord import DiscordChannel
        cmds = [(c.name, c.description) for c in self._runtime.commands.list()]
        ch = DiscordChannel(
            token=bot_token,
            name=name,
            commands=cmds,
            command_handler=self.gateway.dispatch_command,
            allow_from=dcfg.allow_from,
            group_policy=dcfg.group_policy,
            show_tool_calls=dcfg.show_tool_calls,
            show_thinking=dcfg.show_thinking,
            streaming=dcfg.streaming,
            streaming_latency=dcfg.streaming_latency,
        )
        self.gateway.register(ch)
        await ch.connect()

    async def _run_slack(self, bot_token: str, app_token: str, scfg, name: str = 'slack') -> None:
        from program.gateway.channels.slack import SlackChannel
        cmds = [(c.name, c.description) for c in self._runtime.commands.list()]
        ch = SlackChannel(
            bot_token=bot_token,
            app_token=app_token,
            name=name,
            commands=cmds,
            command_handler=self.gateway.dispatch_command,
            allow_from=scfg.allow_from,
            show_tool_calls=scfg.show_tool_calls,
            show_thinking=scfg.show_thinking,
            streaming=scfg.streaming,
            streaming_latency=scfg.streaming_latency,
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


class _ProfileChannelAuth:
    """Simple holder for channel tokens loaded from a profile's auth/channels.json."""

    def __init__(self) -> None:
        self.telegram_token: str = ''
        self.discord_token: str = ''
        self.slack_bot_token: str = ''
        self.slack_app_token: str = ''
        self.twitch_token: str = ''
        self.email_username: str = ''
        self.email_password: str = ''
