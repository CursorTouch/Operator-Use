"""Tests for commands/builtins.py: _handle_compact, _handle_new, _handle_help."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from program.commands.builtins import _handle_compact, _handle_new, _handle_help, BUILTIN_COMMANDS
from program.commands.registry import CommandRegistry
from program.commands.types import SlashCommandInfo


# ── helpers ───────────────────────────────────────────────────────────────────

def make_registry(session=None, runtime=None):
    rt = MagicMock()
    rt.current_session = session
    if runtime is not None:
        rt = runtime
    reg = CommandRegistry.__new__(CommandRegistry)
    reg.runtime = rt
    reg._commands = {}
    reg._aliases = {}
    for cmd in BUILTIN_COMMANDS:
        reg._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            reg._aliases[alias] = cmd.name
    return reg


# ── _handle_compact ───────────────────────────────────────────────────────────

class TestHandleCompact:
    @pytest.mark.asyncio
    async def test_calls_session_compact(self):
        session = MagicMock()
        reg = make_registry(session=session)
        await _handle_compact(reg, [])
        session.compact.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_session_is_noop(self):
        rt = MagicMock()
        rt.current_session = None
        reg = make_registry(runtime=rt)
        await _handle_compact(reg, [])  # should not raise

    @pytest.mark.asyncio
    async def test_no_runtime_is_noop(self):
        reg = CommandRegistry.__new__(CommandRegistry)
        reg.runtime = None
        reg._commands = {}
        reg._aliases = {}
        await _handle_compact(reg, [])  # should not raise

    @pytest.mark.asyncio
    async def test_args_become_custom_instructions(self):
        from program.extension.types import CompactOptions
        session = MagicMock()
        reg = make_registry(session=session)
        await _handle_compact(reg, ["focus", "on", "errors"])
        call_args = session.compact.call_args[0][0]
        assert isinstance(call_args, CompactOptions)
        assert call_args.custom_instructions == "focus on errors"

    @pytest.mark.asyncio
    async def test_no_args_passes_none_instructions(self):
        from program.extension.types import CompactOptions
        session = MagicMock()
        reg = make_registry(session=session)
        await _handle_compact(reg, [])
        call_args = session.compact.call_args[0][0]
        assert isinstance(call_args, CompactOptions)
        assert call_args.custom_instructions is None


# ── _handle_new ───────────────────────────────────────────────────────────────

class TestHandleNew:
    @pytest.mark.asyncio
    async def test_calls_new_session(self):
        rt = MagicMock()
        rt.new_session = AsyncMock()
        reg = make_registry(runtime=rt)
        await _handle_new(reg, [])
        rt.new_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_runtime_is_noop(self):
        reg = CommandRegistry.__new__(CommandRegistry)
        reg.runtime = None
        reg._commands = {}
        reg._aliases = {}
        await _handle_new(reg, [])  # should not raise

    @pytest.mark.asyncio
    async def test_args_ignored(self):
        rt = MagicMock()
        rt.new_session = AsyncMock()
        reg = make_registry(runtime=rt)
        await _handle_new(reg, ["extra", "args"])
        rt.new_session.assert_awaited_once()


# ── _handle_help ──────────────────────────────────────────────────────────────

class TestHandleHelp:
    @pytest.mark.asyncio
    async def test_prints_available_commands(self, capsys):
        reg = make_registry()
        await _handle_help(reg, [])
        out = capsys.readouterr().out
        assert "compact" in out
        assert "new" in out
        assert "help" in out

    @pytest.mark.asyncio
    async def test_includes_descriptions(self, capsys):
        reg = make_registry()
        await _handle_help(reg, [])
        out = capsys.readouterr().out
        assert "summarise" in out.lower() or "compact" in out.lower()

    @pytest.mark.asyncio
    async def test_includes_aliases(self, capsys):
        reg = make_registry()
        await _handle_help(reg, [])
        out = capsys.readouterr().out
        assert "clear" in out or "?" in out

    @pytest.mark.asyncio
    async def test_custom_command_listed(self, capsys):
        reg = make_registry()

        async def handler(r, a): pass
        reg._commands["mytest"] = SlashCommandInfo(name="mytest", description="my test cmd", handler=handler)

        await _handle_help(reg, [])
        out = capsys.readouterr().out
        assert "mytest" in out
        assert "my test cmd" in out

    @pytest.mark.asyncio
    async def test_no_commands_prints_header(self, capsys):
        reg = CommandRegistry.__new__(CommandRegistry)
        reg.runtime = None
        reg._commands = {}
        reg._aliases = {}
        await _handle_help(reg, [])
        out = capsys.readouterr().out
        assert "available" in out.lower() or "commands" in out.lower()


# ── BUILTIN_COMMANDS list ─────────────────────────────────────────────────────

class TestBuiltinCommandsList:
    def test_has_three_builtins(self):
        assert len(BUILTIN_COMMANDS) == 3

    def test_names_present(self):
        names = {c.name for c in BUILTIN_COMMANDS}
        assert names == {"compact", "new", "help"}

    def test_new_has_clear_alias(self):
        new_cmd = next(c for c in BUILTIN_COMMANDS if c.name == "new")
        assert "clear" in new_cmd.aliases

    def test_help_has_question_mark_alias(self):
        help_cmd = next(c for c in BUILTIN_COMMANDS if c.name == "help")
        assert "?" in help_cmd.aliases

    def test_all_have_handlers(self):
        for cmd in BUILTIN_COMMANDS:
            assert cmd.handler is not None
