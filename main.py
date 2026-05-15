from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from program.agent_session import AgentSessionRuntime, AgentSessionServicesConfig
from program.engine.types import (
    MessageUpdateEvent, MessageEndEvent,
    ToolExecutionStartEvent, ToolExecutionEndEvent, AgentErrorEvent,
    AgentStartEvent,
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

        case AgentErrorEvent(error=err):
            print(f"{_red('[Error]')} {err}", file=sys.stderr)


# ── REPL ──────────────────────────────────────────────────────────────────────

async def run(cwd: Path, model_id: str | None, provider: str | None) -> None:
    config = AgentSessionServicesConfig(
        cwd=cwd,
        model_id=model_id or 'claude-sonnet-4-6',
        provider=provider,
    )

    print(f"Agent starting in {cwd}  (model: {config.model_id})")
    print("Type /help for commands, Ctrl-C or /quit to exit.\n")

    runtime = await AgentSessionRuntime.create(config)

    # Wire the event renderer into the agent loop
    await runtime.current_session._loop.subscribe(_render_event)

    while True:
        try:
            user_input = input(_cyan('\n[You] ')).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input in ('/quit', '/exit', '/q'):
            break

        global _attempt
        _attempt = 0
        try:
            await runtime.handle_input(user_input)
        except Exception as e:
            err_msg = str(e) or f"{type(e).__name__} (no message)"
            print(f"{_red('[Error]')} {err_msg}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description='Agent harness REPL')
    parser.add_argument('--cwd', type=Path, default=Path.cwd())
    parser.add_argument('--model', default=None, help='Model ID (e.g. claude-sonnet-4-6)')
    parser.add_argument('--provider', default=None, help='Provider override')
    args = parser.parse_args()
    asyncio.run(run(cwd=args.cwd.resolve(), model_id=args.model, provider=args.provider))


if __name__ == '__main__':
    main()
