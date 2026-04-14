"""Gateway: unified channel management and outbound dispatch."""

import asyncio
import logging
from typing import Callable, Optional

from operator_use.bus import Bus
from operator_use.gateway.channels.base import BaseChannel


logger = logging.getLogger(__name__)


class Gateway:
    """
    Unified gateway that bridges channels and outbound dispatch.

    - Channels push incoming messages to the bus.
    - Gateway dispatches outgoing messages from the bus to the correct channel.
    """

    def __init__(
        self,
        bus: Bus,
        on_ready: Optional[Callable] = None,
        on_stop: Optional[Callable] = None,
        on_restart: Optional[Callable] = None,
    ) -> None:
        self._bus = bus
        self._channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task[None] | None = None
        self._running = False
        self.on_ready = on_ready  # fired once all channels are live
        self.on_stop = on_stop  # fired after all channels have stopped
        self.on_restart = on_restart  # fired when a restart is requested

    def add_channel(self, channel: BaseChannel) -> None:
        """Register a channel. Uses account_id as suffix when present to support multiple bots of the same type."""
        channel.bus = self._bus
        account_id = getattr(channel.config, "account_id", "") if hasattr(channel, "config") else ""
        key = f"{channel.name}:{account_id}" if account_id else channel.name
        self._channels[key] = channel

    async def start(self) -> None:
        """Start all channels and the outbound dispatcher.

        Fires ``self.on_ready`` (if set) once all channel tasks are scheduled
        and the dispatcher is running — the exact moment the system is live.
        """
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        for channel in self._channels.values():
            channel.running = True
        tasks = [asyncio.create_task(channel.start()) for channel in self._channels.values()]
        from rich.console import Console as _C

        _C().print(f"└ [#abb2bf]{'Gateway':<10}[/#abb2bf] [#61afef]started[/#61afef]")
        if self.on_ready is not None:
            try:
                self.on_ready()
            except Exception:
                pass
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        """Stop all channels and the dispatcher."""
        if not self._running:
            return
        self._running = False

        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None

        tasks = [asyncio.create_task(channel.stop()) for channel in self._channels.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._channels.clear()
        from rich.console import Console as _C

        _C().print(f"└ [#abb2bf]{'Gateway':<10}[/#abb2bf] [#61afef]stopped[/#61afef]")
        if self.on_stop is not None:
            try:
                self.on_stop()
            except Exception:
                pass

    async def _dispatch_loop(self) -> None:
        """Dispatch outgoing messages from bus to channels."""
        while self._running:
            try:
                message = await asyncio.wait_for(self._bus.consume_outgoing(), timeout=5.0)
                key = (
                    f"{message.channel}:{message.account_id}"
                    if message.account_id
                    else message.channel
                )
                channel = self._channels.get(key) or self._channels.get(message.channel)
                if channel:
                    try:
                        msg_id = await channel.send(message)
                        # Automatically resolve the future if the channel returns an ID
                        if message.sent_id_future and not message.sent_id_future.done():
                            message.sent_id_future.set_result(msg_id)
                    except Exception as e:
                        logger.error(f"Error sending to {message.channel}: {e}")
                elif message.channel not in ("cli", "direct"):
                    logger.warning(f"Unknown channel: {message.channel}")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self._channels.get(name)

    def list_channels(self) -> list[BaseChannel]:
        """List all registered channels."""
        return list(self._channels.values())

    async def enable_channel(self, name: str) -> bool:
        """Start a stopped channel. Returns True if started, False if not found or already running."""
        channel = self._channels.get(name)
        if not channel:
            return False
        if channel.running:
            return False
        channel.running = True
        asyncio.create_task(channel.start())
        return True

    async def disable_channel(self, name: str) -> bool:
        """Stop a running channel. Returns True if stopped, False if not found or already stopped."""
        channel = self._channels.get(name)
        if not channel:
            return False
        if not channel.running:
            return False
        channel.running = False
        await channel.stop()
        return True
