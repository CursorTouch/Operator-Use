from __future__ import annotations

import asyncio
import os
import select
import signal
import sys
import termios
import threading
import tty
from pathlib import Path

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.patch_stdout import patch_stdout

from program.runtime import Runtime, RuntimeConfig
from program.subagent.manager import _session_channel, _session_chat_id
from program.hooks.types import (
    MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent, AgentErrorEvent,
    AgentStartEvent, SessionBeforeCompactEvent, SessionCompactEvent,
)
from program.message.types import Role


# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _cyan(s: str) -> str:   return f"\033[1;36m{s}\033[0m"
def _blue(s: str) -> str:   return f"\033[1;34m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[1;33m{s}\033[0m"
def _green(s: str) -> str:  return f"\033[1;32m{s}\033[0m"
def _grey(s: str) -> str:   return f"\033[1;30m{s}\033[0m"
def _red(s: str) -> str:    return f"\033[1;31m{s}\033[0m"


# ── Event renderer ────────────────────────────────────────────────────────────

_streaming_role: str | None = None
_attempt: int = 0


def _render_event(event) -> None:
    global _streaming_role, _attempt
    match event:
        case AgentStartEvent():
            if _attempt > 0:
                print(f"{_yellow(f'[Retry {_attempt}]')} Retrying...", file=sys.stderr)
            _attempt += 1
            _streaming_role = None

        case MessageUpdateEvent(message=msg) if msg.role == Role.ASSISTANT:
            for c in msg.contents:
                content = getattr(c, 'content', '')
                kind = getattr(c, 'type', '')
                if not content:
                    continue
                if kind == 'thinking':
                    if _streaming_role != 'thinking':
                        print(f"\n{_grey('[Thinking]')} ", end='', flush=True)
                        _streaming_role = 'thinking'
                    sys.stdout.write(_grey(content))
                    sys.stdout.flush()
                elif kind == 'text':
                    if _streaming_role != 'assistant':
                        print(f"\n{_blue('[Assistant]')} ", end='', flush=True)
                        _streaming_role = 'assistant'
                    sys.stdout.write(content)
                    sys.stdout.flush()

        case MessageEndEvent(message=msg) if msg is not None and msg.role == Role.ASSISTANT:
            if _streaming_role is not None:
                print()
            _streaming_role = None

        case ToolExecutionStartEvent(tool_call=tc):
            args_str = ', '.join(f'{k}={v!r}' for k, v in tc.args.items())
            print(f"\n{_yellow(f'[Tool] {tc.name}({args_str})')}")

        case ToolExecutionUpdateEvent(partial_tool_result=part):
            text = getattr(part, 'content', '') if part is not None else ''
            if not text:
                return
            if _streaming_role != 'tool_stream':
                print(f"{_grey('[Tool ⋯]')} ", end='', flush=True)
                _streaming_role = 'tool_stream'
            sys.stdout.write(text)
            sys.stdout.flush()

        case ToolExecutionEndEvent(tool_result=res):
            if _streaming_role == 'tool_stream':
                print()
                _streaming_role = None
            content = str(res.content)
            if len(content) > 500:
                content = content[:500] + _grey(' … [truncated]')
            print(f"{_green('[Result]')} {content}")

        case SessionBeforeCompactEvent():
            print(f"\n{_grey('[Compact]')} Compacting conversation history...")

        case SessionCompactEvent():
            print(f"{_grey('[Compact]')} Done.")

        case AgentErrorEvent(error=err):
            print(f"{_red('[Error]')} {err}", file=sys.stderr)


# ── Session ───────────────────────────────────────────────────────────────────

def _bind_renderer(runtime: Runtime, current_session, unsubscribe):
    next_session = runtime.current_session
    if next_session is current_session:
        return current_session, unsubscribe
    if unsubscribe is not None:
        unsubscribe()
    next_unsubscribe = None
    if next_session is not None:
        next_unsubscribe = next_session.hooks.subscribe(_render_event)
    return next_session, next_unsubscribe


# ── Esc key cancel (during agent execution) ───────────────────────────────────

async def _wait_for_esc() -> None:
    """Block until ESC is pressed by reading raw terminal input in a thread.

    Runs between prompt_async() calls (agent execution phase), where the
    terminal is in cooked mode and safe to switch to raw mode temporarily.
    """
    stop = threading.Event()

    def _reader() -> None:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while not stop.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch = os.read(fd, 1)
                    if ch == b'\x1b':
                        return
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    try:
        await asyncio.to_thread(_reader)
    finally:
        stop.set()


async def _run_repl(cwd: Path, model_id: str | None, provider: str | None, sandbox: str = 'off', ephemeral: bool = False, resume: bool = False, system_prompt: str | None = None) -> None:
    config = RuntimeConfig(
        cwd=cwd,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
        sandbox=sandbox if sandbox != 'off' else None,
        persist_session=not ephemeral,
        resume=resume,
        system_prompt=system_prompt,
    )

    print(f"Agent starting in {cwd}  (model: {config.model_id})")
    print("Type /help for commands. Esc = cancel agent, Ctrl-C twice or /quit = exit.\n")

    runtime = await Runtime.create(config)
    subscribed_session, unsubscribe_renderer = _bind_renderer(runtime, None, None)

    # Mark this task as the 'stdio' CLI session so subagents know to route
    # their results back through the gateway.
    _session_channel.set('stdio')
    _session_chat_id.set('cli')

    # Own the stdio subagent result queue and consumer here in the REPL —
    # the gateway delegates 'stdio' channel messages to _on_stdio_message via
    # register_direct_handler(), keeping the gateway free of REPL-specific logic.
    _stdio_queue: asyncio.Queue = asyncio.Queue()

    async def _on_stdio_message(msg) -> None:
        from program.bus.types import text_from_parts
        from program.agent.types import PromptOptions
        agent = runtime.current_session
        if agent is None:
            return
        await _stdio_queue.put((agent, text_from_parts(msg.parts), PromptOptions(source='subagent')))

    async def _stdio_consumer() -> None:
        while True:
            try:
                agent, content, opts = await _stdio_queue.get()
                while True:
                    try:
                        await agent.invoke(content, opts)
                        break
                    except RuntimeError:
                        await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    runtime.gateway_manager.gateway.register_direct_handler('stdio', _on_stdio_message)
    stdio_consumer_task = asyncio.create_task(_stdio_consumer(), name='repl:stdio_consumer')

    cancel: asyncio.Event = asyncio.Event()
    session: PromptSession = PromptSession()
    last_interrupt = False

    # patch_stdout() keeps the event loop running during prompt_async() so
    # background asyncio tasks (subagents) can print above the prompt line
    # without corrupting it, and the stdio consumer can deliver results while
    # the user is idle.
    with patch_stdout(raw=True):
        while True:
            try:
                user_input = (await session.prompt_async(ANSI(_cyan('\n[You] ')))).strip()
                last_interrupt = False
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                if last_interrupt:
                    break
                last_interrupt = True
                print(_grey('(Press Ctrl-C again to exit)'))
                continue

            if not user_input:
                continue
            if user_input in ('/quit', '/exit', '/q'):
                break

            global _attempt
            _attempt = 0
            try:
                cancel.clear()

                def _on_sigint(*_):
                    cancel.set()

                old_sigint = signal.signal(signal.SIGINT, _on_sigint)
                try:
                    agent_task = asyncio.ensure_future(runtime.user_input(user_input))
                    cancel_task = asyncio.ensure_future(cancel.wait())
                    esc_task = asyncio.ensure_future(_wait_for_esc())
                    await asyncio.wait(
                        [agent_task, cancel_task, esc_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if esc_task.done() and not esc_task.cancelled():
                        cancel.set()
                    for t in (agent_task, cancel_task, esc_task):
                        if not t.done():
                            t.cancel()
                            try:
                                await t
                            except (asyncio.CancelledError, Exception):
                                pass
                    if cancel.is_set():
                        print(f"\n{_yellow('[Interrupted]')}")
                    else:
                        exc = agent_task.exception()
                        if exc is not None:
                            raise exc
                finally:
                    signal.signal(signal.SIGINT, old_sigint)

                subscribed_session, unsubscribe_renderer = _bind_renderer(
                    runtime, subscribed_session, unsubscribe_renderer
                )
            except KeyboardInterrupt:
                pass
            except Exception as e:
                err_msg = str(e) or f"{type(e).__name__} (no message)"
                print(f"{_red('[Error]')} {err_msg}", file=sys.stderr)
                subscribed_session, unsubscribe_renderer = _bind_renderer(
                    runtime, subscribed_session, unsubscribe_renderer
                )

    stdio_consumer_task.cancel()
    try:
        await stdio_consumer_task
    except asyncio.CancelledError:
        pass
    if unsubscribe_renderer is not None:
        unsubscribe_renderer()
    await runtime.ashutdown()


# ── Click command ─────────────────────────────────────────────────────────────

@click.command('repl')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
@click.option('--provider', default=None, help='Provider override')
@click.option(
    '--sandbox',
    type=click.Choice(['off', 'warn', 'enforce', 'strict'], case_sensitive=False),
    default='off', show_default=True,
    help='Sandbox mode: strict=write-locked+OS sandbox; enforce=policy only; warn=log; off=disabled',
)
@click.option('--ephemeral', is_flag=True, default=False, help='Run in-memory only — session is not saved to disk.')
@click.option('--resume', is_flag=True, default=False, help='Resume the most recent session instead of starting fresh')
@click.option('--system-prompt', default=None, help='Override the default system prompt')
def repl(cwd: str | None, model: str | None, provider: str | None, sandbox: str, ephemeral: bool, resume: bool, system_prompt: str | None) -> None:
    """Start the interactive agent REPL."""
    from dotenv import load_dotenv
    load_dotenv()

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    try:
        asyncio.run(_run_repl(cwd=cwd_path, model_id=model, provider=provider, sandbox=sandbox, ephemeral=ephemeral, resume=resume, system_prompt=system_prompt))
    except KeyboardInterrupt:
        pass
