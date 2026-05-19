from __future__ import annotations

import sys

from program.gateway.types import BaseChannel
from program.bus.types import IncomingMessage, OutgoingMessage, StreamPhase, text_from_parts
from program.gateway.channels.stdio.utils import blue, yellow, grey, red


class StdioChannel(BaseChannel):
    """
    Terminal channel — renders agent events to stdout/stderr with ANSI colour.

    channel_id = 'stdio'
    """

    _ID = 'stdio'

    def __init__(self) -> None:
        super().__init__()
        self._streaming_kind: dict[str, str | None] = {}

    @property
    def channel_id(self) -> str:
        return self._ID

    async def disconnect(self) -> None:
        """No-op: stdio doesn't need teardown."""
        ...

    async def send(self, msg: OutgoingMessage) -> None:
        """Render the outgoing message to stdout/stderr."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata

        if phase == StreamPhase.START:
            self._streaming_kind[chat_id] = None

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind', 'text')
            text = text_from_parts(msg.parts)

            if kind == 'thinking':
                if not text:
                    return
                if self._streaming_kind.get(chat_id) != 'thinking':
                    print(f"\n{grey('[Thinking]')} ", end='', flush=True)
                    self._streaming_kind[chat_id] = 'thinking'
                sys.stdout.write(grey(text))
                sys.stdout.flush()

            elif kind == 'text':
                if not text:
                    return
                if self._streaming_kind.get(chat_id) != 'text':
                    print(f"\n{blue('[Assistant]')} ", end='', flush=True)
                    self._streaming_kind[chat_id] = 'text'
                sys.stdout.write(text)
                sys.stdout.flush()

            elif kind == 'tool_start':
                name = metadata.get('name', '')
                args = metadata.get('args', {})
                args_str = ', '.join(f'{k}={v!r}' for k, v in args.items()) if isinstance(args, dict) else str(args)
                print(f"\n{yellow(f'[Tool] {name}({args_str})')}")
                self._streaming_kind[chat_id] = None

            elif kind == 'tool_end':
                result = metadata.get('result', '')
                if metadata.get('is_error', False):
                    if len(result) > 500:
                        result = result[:500] + grey(' … [truncated]')
                    print(f"{red('[Error]')} {result}")
                self._streaming_kind[chat_id] = None

        elif phase == StreamPhase.END:
            if self._streaming_kind.get(chat_id) is not None:
                print()
            self._streaming_kind[chat_id] = None

        elif phase == StreamPhase.ERROR:
            text = text_from_parts(msg.parts) or "Unknown error"
            print(f"{red('[Error]')} {text}", file=sys.stderr)
            self._streaming_kind[chat_id] = None

        elif phase == StreamPhase.DONE:
            pass  # REPL handles the next prompt itself
