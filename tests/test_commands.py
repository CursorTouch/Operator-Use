"""Command registry — parse, dispatch, builtins, aliases, custom registration."""
from __future__ import annotations

import pytest

from operator_use.commands.registry import CommandRegistry
from operator_use.commands.types import SlashCommandInfo, CommandParseResult, parse_command


class _FakeRuntime:
    current_session = None


class TestParseCommand:
    def test_parses_slash_command(self):
        result = parse_command("/compact focus on recent changes")
        assert result is not None
        assert result.name == "compact"
        assert "focus" in result.args
        assert "recent" in result.args

    def test_no_slash_returns_none(self):
        assert parse_command("hello world") is None
        assert parse_command("") is None

    def test_bare_slash_returns_none(self):
        assert parse_command("/") is None

    def test_command_only_no_args(self):
        result = parse_command("/new")
        assert result is not None
        assert result.name == "new"
        assert result.args == []

    def test_args_is_list_of_strings(self):
        result = parse_command("/compact a b c")
        assert isinstance(result.args, list)
        assert result.args == ["a", "b", "c"]


class TestCommandRegistryBuiltins:
    def test_builtins_registered(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        names = [c.name for c in reg.list()]
        assert 'compact' in names
        assert 'new' in names
        assert 'help' in names

    def test_get_by_name(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        cmd = reg.get("compact")
        assert cmd is not None
        assert cmd.name == "compact"

    def test_alias_resolves(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        cmd = reg.get("?")
        assert cmd is not None
        assert cmd.name == "help"

    def test_unknown_command_returns_none(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        assert reg.get("definitely-not-a-command") is None


class TestCustomRegistration:
    def test_register_custom_command(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        executed = []
        reg.register(SlashCommandInfo(
            name="my-cmd",
            description="test",
            handler=lambda reg, args: executed.append(args),
        ))
        cmd = reg.get("my-cmd")
        assert cmd is not None

    def test_custom_command_in_list(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        reg.register(SlashCommandInfo(name="listed", description="x", handler=lambda r, a: None))
        assert any(c.name == "listed" for c in reg.list())

    def test_register_with_aliases(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        reg.register(SlashCommandInfo(name="myc", description="x",
                                       handler=lambda r, a: None, aliases=["mc", "m"]))
        assert reg.get("mc") is not None
        assert reg.get("m") is not None


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_unknown_is_noop(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        # Should not raise
        result = await reg.dispatch(CommandParseResult(name="no-such-cmd", args=[], raw="/no-such-cmd"))
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_known_command_returns_true(self):
        reg = CommandRegistry(runtime=_FakeRuntime())
        executed = []
        reg.register(SlashCommandInfo(
            name="exec-test",
            description="x",
            handler=lambda r, a: executed.append(True),
        ))
        result = await reg.dispatch(CommandParseResult(name="exec-test", args=[], raw="/exec-test"))
        assert result is True
        assert executed
