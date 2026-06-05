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
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.patch_stdout import patch_stdout

from operator_use.runtime import Runtime, RuntimeConfig
from operator_use.subagent.manager import _session_channel, _session_chat_id
from operator_use.hooks.types import (
    MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionUpdateEvent, ToolExecutionEndEvent, AgentErrorEvent,
    AgentStartEvent, SessionBeforeCompactEvent, SessionCompactEvent,
)
from operator_use.agent.types import GoalUpdateEvent
from operator_use.message.types import Role


# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _cyan(s: str) -> str:   return f"\033[1;36m{s}\033[0m"
def _blue(s: str) -> str:   return f"\033[1;34m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[1;33m{s}\033[0m"
def _green(s: str) -> str:  return f"\033[1;32m{s}\033[0m"
def _grey(s: str) -> str:   return f"\033[1;30m{s}\033[0m"
def _red(s: str) -> str:    return f"\033[1;31m{s}\033[0m"


# ── Output helper ────────────────────────────────────────────────────────────
# patch_stdout(raw=True) is required so ANSI escape codes pass through intact.
# In raw mode \n only moves the cursor down without a CR, so we replace \n
# with \r\n in every print so multi-line output stays left-aligned.

def _out(text: str = '', end: str = '\r\n') -> None:
    sys.stdout.write(text.replace('\n', '\r\n') + end)
    sys.stdout.flush()


def _terminal_write(text: str = '', end: str = '') -> None:
    def _write() -> None:
        sys.stdout.write(text.replace('\n', '\r\n') + end)
        sys.stdout.flush()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write()
    else:
        asyncio.ensure_future(run_in_terminal(_write), loop=loop)


# ── Event renderer ────────────────────────────────────────────────────────────

_streaming_role: str | None = None
_attempt: int = 0
_partial_lines: int = 0  # physical lines printed by the current attempt's streaming


def _render_event(event) -> None:
    global _streaming_role, _attempt, _partial_lines
    match event:
        case AgentStartEvent():
            if _attempt > 0:
                # Erase partial streamed text from the failed attempt before
                # showing the retry label — move up to the start of that output
                # and clear everything from there to end of screen.
                if _partial_lines > 0:
                    sys.stdout.write(f"\033[{max(0, _partial_lines - 1)}A\r\033[J")
                    sys.stdout.flush()
                _out(f"\n{_yellow(f'[Retry {_attempt}]')} Retrying...")
            _attempt += 1
            _partial_lines = 0
            _streaming_role = None

        case MessageUpdateEvent(message=msg) if msg.role == Role.ASSISTANT:
            for c in msg.contents:
                content = getattr(c, 'content', '')
                kind = getattr(c, 'type', '')
                if not content:
                    continue
                if kind == 'thinking':
                    if _streaming_role != 'thinking':
                        _terminal_write(f"\n{_grey('[Thinking]')} ")
                        _streaming_role = 'thinking'
                        _partial_lines += 1
                    _terminal_write(_grey(content))
                    _partial_lines += content.count('\n')
                elif kind == 'text':
                    if _streaming_role != 'assistant':
                        _terminal_write(f"\n{_blue('[Assistant]')} ")
                        _streaming_role = 'assistant'
                        _partial_lines += 1
                    _terminal_write(content)
                    _partial_lines += content.count('\n')

        case MessageEndEvent(message=msg) if msg is not None and msg.role == Role.ASSISTANT:
            if _streaming_role is not None:
                _terminal_write(end='\r\n')
            _streaming_role = None
            _partial_lines = 0  # turn completed cleanly — nothing to erase

        case ToolExecutionStartEvent(tool_call=tc):
            args_str = ', '.join(f'{k}={v!r}' for k, v in tc.args.items())
            _out(f"\n{_yellow(f'[Tool] {tc.name}({args_str})')}")

        case ToolExecutionUpdateEvent():
            pass  # suppress live-update lines in the repl; [Result] already shows the outcome

        case ToolExecutionEndEvent(tool_result=res):
            content = res.content
            if len(content) > 500:
                content = content[:500] + _grey(' … [truncated]')
            _out(f"{_green('[Result]')} {content}")

        case SessionBeforeCompactEvent():
            _out(f"\n{_grey('[Compact]')} Compacting conversation history...")

        case SessionCompactEvent():
            _out(f"{_grey('[Compact]')} Done.")

        case AgentErrorEvent(error=err):
            _out(f"{_red('[Error]')} {err}")

        case GoalUpdateEvent(verdict=v, message=msg) if msg:
            if v == "done":
                _out(f"\n{_green('[Goal]')} {msg}")
            elif v == "paused":
                _out(f"\n{_yellow('[Goal]')} {msg}")
            else:
                _out(f"\n{_grey('[Goal]')} {msg}")


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
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    terminal_restored: asyncio.Future = loop.create_future()

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
                        # Real ESC has no bytes within 50ms.
                        # CSI sequences (\x1b[...R for CPR, arrow keys)
                        # have bytes immediately after — consume and ignore.
                        r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if not r2:
                            return  # bare ESC
                        os.read(fd, 64)
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, old)
            except Exception:
                pass
            loop.call_soon_threadsafe(
                lambda: terminal_restored.done() or terminal_restored.set_result(None)
            )

    try:
        await asyncio.to_thread(_reader)
    finally:
        stop.set()
        # Wait for thread to restore terminal before prompt_async() starts,
        # so CPR responses don't leak into the next input line.
        try:
            await asyncio.wait_for(asyncio.shield(terminal_restored), timeout=0.3)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass


async def _run_repl(cwd: Path, model_id: str | None, provider: str | None, sandbox: str = 'off', agent: str | None = None, system_prompt: str | None = None, prompt: str | None = None, session_file: str | None = None) -> None:
    from pathlib import Path as _Path
    from operator_use.agent.profile import load_agent_profiles
    from operator_use.settings.paths import get_profiles_dir

    # Resolve named profile when --agent is given
    profile = None
    if agent:
        profiles = load_agent_profiles([get_profiles_dir()])
        profile = next((p for p in profiles.profiles if p.name == agent), None)
        if profile is None:
            available = ', '.join(p.name for p in profiles.profiles) or 'none'
            print(f"Error: profile '{agent}' not found. Available: {available}")
            return

        # Apply CLI overrides onto the loaded profile (in memory only).
        if model_id:
            profile.model_id = model_id
        if provider:
            profile.provider = provider

    # REPL sessions are always ephemeral — nothing is saved to disk.

    config = RuntimeConfig(
        cwd=cwd,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
        sandbox=sandbox if sandbox != 'off' else None,
        persist_session=False,
        system_prompt=system_prompt,
        session_file=_Path(session_file) if session_file else None,
        profile=profile,
    )

    label = f"agent: {profile.name}" if profile else "ephemeral"
    if profile and profile.tools:
        tools_label = f"tools: {', '.join(profile.tools)}"
    else:
        tools_label = "tools: all"
    print(f"Agent starting in {cwd}  (model: {config.model_id}, {label}, {tools_label})")
    print("Type /help for commands. Esc = cancel agent, Ctrl-C twice or /quit = exit.\n")

    runtime = await Runtime.create(config)
    from operator_use.gateway.manager import GatewayManager
    gateway_manager = GatewayManager(runtime)
    gateway_manager.start()
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
        from operator_use.bus.types import text_from_parts
        from operator_use.agent.types import PromptOptions
        agent = msg.metadata.get('target_agent') or runtime.current_session
        if agent is None:
            return
        await _stdio_queue.put((agent, text_from_parts(msg.parts), PromptOptions(source='subagent')))

    async def _stdio_consumer() -> None:
        global _attempt, _partial_lines
        while True:
            try:
                agent, content, opts = await _stdio_queue.get()
                _attempt = 0
                _partial_lines = 0
                while True:
                    try:
                        await agent.invoke(content, opts)
                        break
                    except RuntimeError:
                        if agent.is_idle():
                            # Agent is idle but invoke still failed — permanent error, give up.
                            break
                        # Agent is busy — wait and retry.
                        await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    gateway_manager.gateway.register_direct_handler('stdio', _on_stdio_message)
    stdio_consumer_task = asyncio.create_task(_stdio_consumer(), name='repl:stdio_consumer')

    cancel: asyncio.Event = asyncio.Event()
    session: PromptSession = PromptSession()
    last_interrupt = False

    # If an initial prompt was provided (e.g. via --prompt or post-reboot resume),
    # inject it before entering the interactive loop.
    if prompt:
        print(_cyan('\n[You] ') + prompt)
        await runtime.user_input(prompt)

    # patch_stdout() keeps the event loop running during prompt_async() so
    # background asyncio tasks (subagents) can print above the prompt line
    # without corrupting it, and the stdio consumer can deliver results while
    # the user is idle.
    with patch_stdout(raw=True):
        while True:
            try:
                try:
                    termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                except Exception:
                    pass
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
    await gateway_manager.astop()
    await runtime.ashutdown()


# ── Click command ─────────────────────────────────────────────────────────────

@click.command('repl')
@click.option('--cwd', default=None, type=click.Path(exists=True, file_okay=False), help='Working directory')
@click.option('--model', default=None, help='Model ID (runtime override)')
@click.option('--provider', default=None, help='Provider override')
@click.option(
    '--sandbox',
    type=click.Choice(['off', 'warn', 'enforce', 'strict'], case_sensitive=False),
    default='off', show_default=True,
    help='Sandbox mode: strict=write-locked+OS sandbox; enforce=policy only; warn=log; off=disabled',
)
@click.option('--agent', default=None, help='Named agent profile to load (e.g. jarvis). The REPL is always ephemeral — nothing is saved to disk.')
@click.option('--system-prompt', default=None, help='Override the default system prompt')
@click.option('--prompt', default=None, help='Inject an initial message so the agent starts immediately.')
@click.option('--session-file', default=None, hidden=True, help='Open a specific session file.')
def repl(cwd: str | None, model: str | None, provider: str | None, sandbox: str, agent: str | None, system_prompt: str | None, prompt: str | None, session_file: str | None) -> None:
    """Start the interactive agent REPL.

    Without --agent the session is ephemeral (nothing saved to disk).
    With --agent <name> the named profile is loaded and the session is persisted.
    """
    from dotenv import load_dotenv
    load_dotenv()

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    try:
        asyncio.run(_run_repl(cwd=cwd_path, model_id=model, provider=provider, sandbox=sandbox, agent=agent, system_prompt=system_prompt, prompt=prompt, session_file=session_file))
    except KeyboardInterrupt:
        pass
