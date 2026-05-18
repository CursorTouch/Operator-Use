from __future__ import annotations

import asyncio
import select
import signal
import sys
import termios
import threading
from pathlib import Path

import click

from program.runtime import Runtime, RuntimeConfig
from program.hooks.types import (
    MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent, AgentErrorEvent,
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

        case MessageEndEvent(message=msg) if msg.role == Role.ASSISTANT:
            if _streaming_role is not None:
                print()
            _streaming_role = None

        case ToolExecutionStartEvent(tool_call=tc):
            args_str = ', '.join(f'{k}={v!r}' for k, v in tc.args.items())
            print(f"\n{_yellow(f'[Tool] {tc.name}({args_str})')}")

        case ToolExecutionEndEvent(tool_result=res):
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


# ── Esc key watcher ───────────────────────────────────────────────────────────

def _watch_for_esc(
    stop: threading.Event,
    loop: asyncio.AbstractEventLoop,
    cancel: asyncio.Event,
) -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        import tty
        tty.setcbreak(fd)
        while not stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if ready:
                ch = sys.stdin.read(1)
                if ch == '\x1b':
                    loop.call_soon_threadsafe(cancel.set)
                    return
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


async def _run_with_esc_cancel(coro) -> bool:
    loop = asyncio.get_running_loop()
    cancel = asyncio.Event()
    stop = threading.Event()

    watcher = threading.Thread(
        target=_watch_for_esc, args=(stop, loop, cancel), daemon=True
    )
    watcher.start()

    agent_task = asyncio.ensure_future(coro)
    cancel_task = asyncio.ensure_future(cancel.wait())

    done, pending = await asyncio.wait(
        [agent_task, cancel_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    stop.set()

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    was_cancelled = cancel.is_set()
    if not was_cancelled and agent_task.exception():
        raise agent_task.exception()
    return was_cancelled


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


async def _run_repl(cwd: Path, model_id: str | None, provider: str | None, sandbox: str = 'off') -> None:
    config = RuntimeConfig(
        cwd=cwd,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
        sandbox=sandbox if sandbox != 'off' else None,
    )

    print(f"Agent starting in {cwd}  (model: {config.model_id})")
    print("Type /help for commands, Ctrl-C or /quit to exit.\n")

    runtime = await Runtime.create(config)
    subscribed_session, unsubscribe_renderer = _bind_renderer(runtime, None, None)
    last_interrupt = False

    while True:
        _asyncio_sigint = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            user_input = input(_cyan('\n[You] ')).strip()
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
        finally:
            signal.signal(signal.SIGINT, _asyncio_sigint)

        if not user_input:
            continue
        if user_input in ('/quit', '/exit', '/q'):
            break

        global _attempt
        _attempt = 0
        try:
            if user_input.startswith('/'):
                await runtime.user_input(user_input)
            else:
                interrupted = await _run_with_esc_cancel(runtime.user_input(user_input))
                if interrupted:
                    print(f"\n{_yellow('[Interrupted]')}")
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

    if unsubscribe_renderer is not None:
        unsubscribe_renderer()
    runtime.shutdown()


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
def repl(cwd: str | None, model: str | None, provider: str | None, sandbox: str) -> None:
    """Start the interactive agent REPL."""
    from dotenv import load_dotenv
    load_dotenv()

    cwd_path = Path(cwd).resolve() if cwd else Path.cwd()
    try:
        asyncio.run(_run_repl(cwd=cwd_path, model_id=model, provider=provider, sandbox=sandbox))
    except KeyboardInterrupt:
        pass
