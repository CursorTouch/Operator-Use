"""Tests for CommandRegistry and slash command parsing."""
import pytest

from operator_use.commands.registry import CommandRegistry
from operator_use.commands.types import SlashCommandInfo, CommandParseResult, parse_command


# ── parse_command ─────────────────────────────────────────────────────────────

class TestParseCommand:
    def test_parses_slash_command(self):
        result = parse_command("/compact")
        assert result is not None
        assert result.name == "compact"
        assert result.args == []

    def test_parses_command_with_args(self):
        result = parse_command("/compact some extra instructions")
        assert result.name == "compact"
        assert result.args == ["some", "extra", "instructions"]

    def test_non_slash_returns_none(self):
        assert parse_command("hello there") is None

    def test_just_slash_returns_none(self):
        assert parse_command("/") is None

    def test_whitespace_around_command(self):
        result = parse_command("  /help  ")
        assert result is not None
        assert result.name == "help"

    def test_raw_preserved(self):
        result = parse_command("/new arg1")
        assert result.raw == "/new arg1"

    def test_command_lowercased(self):
        result = parse_command("/HELP")
        assert result.name == "help"

    def test_empty_string_returns_none(self):
        assert parse_command("") is None


# ── CommandRegistry ───────────────────────────────────────────────────────────

class TestCommandRegistry:
    def test_builtins_registered_on_init(self):
        reg = CommandRegistry()
        names = {c.name for c in reg.list()}
        assert 'compact' in names
        assert 'new' in names
        assert 'help' in names

    def test_register_custom_command(self):
        reg = CommandRegistry()
        async def handler(r, args): pass
        cmd = SlashCommandInfo(name='foo', description='Foo', handler=handler)
        reg.register(cmd)
        assert reg.get('foo') is not None

    def test_get_returns_none_for_unknown(self):
        reg = CommandRegistry()
        assert reg.get('nonexistent') is None

    def test_alias_resolves_to_same_command(self):
        reg = CommandRegistry()
        # 'clear' is an alias for 'new'
        new_cmd = reg.get('new')
        clear_cmd = reg.get('clear')
        assert new_cmd is not None
        assert clear_cmd is not None
        assert new_cmd.name == clear_cmd.name

    def test_question_mark_alias_for_help(self):
        reg = CommandRegistry()
        assert reg.get('?') is not None
        assert reg.get('?').name == 'help'

    def test_list_deduplicates_aliases(self):
        reg = CommandRegistry()
        names = [c.name for c in reg.list()]
        # no duplicates
        assert len(names) == len(set(names))

    def test_register_from_extensions(self):
        from operator_use.extension.types import RegisteredCommand
        from operator_use.skill.types import SourceInfo
        si = SourceInfo(path="x.py", source='local')
        async def h(r, a): pass
        ext_cmd = RegisteredCommand(name='ext_cmd', source_info=si, description='Ext', handler=h)
        reg = CommandRegistry()
        reg.register_from_extensions({'ext_cmd': ext_cmd})
        assert reg.get('ext_cmd') is not None

    @pytest.mark.asyncio
    async def test_dispatch_returns_true_for_known(self):
        reg = CommandRegistry()
        parsed = CommandParseResult(name='help', args=[], raw='/help')
        result = await reg.dispatch(parsed)
        assert result is True

    @pytest.mark.asyncio
    async def test_dispatch_returns_false_for_unknown(self, capsys):
        reg = CommandRegistry()
        parsed = CommandParseResult(name='ghost', args=[], raw='/ghost')
        result = await reg.dispatch(parsed)
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_calls_handler(self):
        called = []
        reg = CommandRegistry()
        async def handler(r, args): called.append(args)
        cmd = SlashCommandInfo(name='mycmd', description='', handler=handler)
        reg.register(cmd)
        parsed = CommandParseResult(name='mycmd', args=['a', 'b'], raw='/mycmd a b')
        await reg.dispatch(parsed)
        assert called == [['a', 'b']]

    @pytest.mark.asyncio
    async def test_dispatch_unknown_prints_message(self, capsys):
        reg = CommandRegistry()
        parsed = CommandParseResult(name='nope', args=[], raw='/nope')
        await reg.dispatch(parsed)
        out = capsys.readouterr().out
        assert 'Unknown command' in out
