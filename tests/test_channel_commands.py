from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from operator_use.commands.types import CommandParseResult


class TestDiscordCommands:
    def _channel(self):
        from operator_use.channels.discord.service import DiscordChannel
        from operator_use.gateway.types import BaseChannel

        ch = DiscordChannel.__new__(DiscordChannel)
        BaseChannel.__init__(ch)
        ch._commands = [
            ("help", "Show help"),
            ("bad name", "Invalid"),
            ("UPPER", "Invalid"),
            ("compact", "x" * 150),
        ]
        ch._command_handler = AsyncMock(return_value="done")
        ch._commands_synced = False
        ch._allow_from = set()
        ch._show_thinking = False
        return ch

    def test_valid_commands_filters_for_discord_application_command_names(self):
        ch = self._channel()

        assert ch._valid_commands() == [
            ("help", "Show help"),
            ("compact", "x" * 100),
        ]

    @pytest.mark.asyncio
    async def test_application_command_callback_dispatches_existing_registry_command(self, monkeypatch):
        from operator_use.channels.discord import service as discord_service

        ch = self._channel()

        class FakeCommand:
            def __init__(self, *, name, description, callback):
                self.name = name
                self.description = description
                self.callback = callback

        fake_app_commands = SimpleNamespace(
            Command=FakeCommand,
            describe=lambda **_kwargs: (lambda callback: callback),
        )
        monkeypatch.setattr(
            discord_service,
            "discord",
            SimpleNamespace(app_commands=fake_app_commands, Interaction=object),
            raising=False,
        )

        tree = MagicMock()
        tree.add_command = MagicMock()
        ch._register_application_commands(tree)

        command = tree.add_command.call_args_list[0].args[0]
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            delete_original_response=AsyncMock(),
        )

        await command.callback(interaction, "recent changes")

        handler = ch._command_handler
        assert handler is not None
        handler.assert_awaited_once_with(CommandParseResult(
            name="help",
            args=["recent", "changes"],
            raw="/help recent changes",
        ))
        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once_with("done")


class TestSlackCommands:
    def _channel(self):
        from operator_use.channels.slack.service import SlackChannel
        from operator_use.gateway.types import BaseChannel

        ch = SlackChannel.__new__(SlackChannel)
        BaseChannel.__init__(ch)
        ch._commands = [
            ("help", "Show help"),
            ("bad name", "Invalid"),
            ("UPPER", "Invalid"),
            ("new", "New session"),
        ]
        ch._command_handler = AsyncMock(return_value="ok")
        ch._allow_from = set()
        ch._show_thinking = False
        return ch

    def test_valid_commands_filters_for_slack_command_names(self):
        ch = self._channel()

        assert ch._valid_commands() == ["help", "new"]

    @pytest.mark.asyncio
    async def test_slash_command_listener_acks_and_dispatches_existing_registry_command(self):
        ch = self._channel()
        registered = {}

        class FakeApp:
            def command(self, name):
                def _decorator(handler):
                    registered[name] = handler
                    return handler
                return _decorator

        ch._register_slash_commands(FakeApp())

        ack = AsyncMock()
        respond = AsyncMock()
        await registered["/help"](
            ack=ack,
            respond=respond,
            command={"user_id": "U1", "text": "all commands"},
        )

        ack.assert_awaited_once()
        handler = ch._command_handler
        assert handler is not None
        handler.assert_awaited_once_with(CommandParseResult(
            name="help",
            args=["all", "commands"],
            raw="/help all commands",
        ))
        respond.assert_awaited_once_with("ok")
