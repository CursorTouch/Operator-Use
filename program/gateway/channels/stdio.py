from __future__ import annotations

import sys

from program.gateway.types import BaseChannel, GatewayEvent


def _blue(s: str) -> str:   return f"\033[1;34m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[1;33m{s}\033[0m"
def _green(s: str) -> str:  return f"\033[1;32m{s}\033[0m"
def _grey(s: str) -> str:   return f"\033[1;30m{s}\033[0m"
def _red(s: str) -> str:    return f"\033[1;31m{s}\033[0m"


class StdioChannel(BaseChannel):
    """
    Terminal channel — renders agent events to stdout/stderr with ANSI colour.

    Designed to be used with the Gateway REPL helper so that main.py can
    delegate rendering to a channel instead of a raw hook subscriber.
    """

    _ID = 'stdio'

    def __init__(self) -> None:
        self._streaming_kind: str | None = None

    @property
    def channel_id(self) -> str:
        return self._ID

    async def on_event(self, event: GatewayEvent) -> None:
        match event.type:
            case 'stream_start':
                self._streaming_kind = None

            case 'chunk':
                kind = event.data.get('kind', 'text')
                text = event.data.get('text', '')
                if not text:
                    return
                if kind == 'thinking':
                    if self._streaming_kind != 'thinking':
                        print(f"\n{_grey('[Thinking]')} ", end='', flush=True)
                        self._streaming_kind = 'thinking'
                    sys.stdout.write(_grey(text))
                    sys.stdout.flush()
                else:
                    if self._streaming_kind != 'text':
                        print(f"\n{_blue('[Assistant]')} ", end='', flush=True)
                        self._streaming_kind = 'text'
                    sys.stdout.write(text)
                    sys.stdout.flush()

            case 'stream_end':
                if self._streaming_kind is not None:
                    print()
                self._streaming_kind = None

            case 'tool_start':
                name = event.data.get('name', '')
                args = event.data.get('args', {})
                args_str = ', '.join(f'{k}={v!r}' for k, v in args.items())
                print(f"\n{_yellow(f'[Tool] {name}({args_str})')}")

            case 'tool_end':
                result = event.data.get('result', '')
                is_error = event.data.get('is_error', False)
                if len(result) > 500:
                    result = result[:500] + _grey(' … [truncated]')
                label = _red('[Error]') if is_error else _green('[Result]')
                print(f"{label} {result}")

            case 'error':
                msg = event.data.get('message', '')
                print(f"{_red('[Error]')} {msg}", file=sys.stderr)

            case 'done':
                pass  # REPL handles the next prompt itself
