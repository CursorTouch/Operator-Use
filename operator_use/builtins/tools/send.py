"""send — Deliver files and intermediate updates to the user mid-turn."""
from __future__ import annotations

import mimetypes
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

from operator_use.subagent.manager import _session_channel, _session_chat_id, _session_message_id
from operator_use.tool.types import Tool, ToolContext, ToolKind, ToolExecutionMode, ToolInvocation, ToolResult

if TYPE_CHECKING:
    from operator_use.bus.service import Bus


class SendAction(str, Enum):
    file = 'file'
    intermediate = 'intermediate'
    react = 'react'


class SendSchema(BaseModel):
    action: SendAction = Field(
        description=(
            'What to send:\n'
            '  file         — deliver a local file (document, image, PDF, CSV, etc.) to the user.\n'
            '                 Use after generating, downloading, or locating a file on disk.\n'
            '                 Requires `path`. Optionally include `caption`.\n'
            '  intermediate — send a plain-text status update to the user mid-turn without ending\n'
            '                 the current response. Use to report progress on long tasks\n'
            '                 ("Fetching page 3 of 10…", "Found 42 results, now filtering…").\n'
            '                 Do NOT use for the final answer — just respond normally for that.\n'
            '                 Requires `text`.\n'
            '  react        — add an emoji reaction to the user\'s message that triggered this turn.\n'
            '                 Use to acknowledge receipt ("👍"), signal completion ("✅"), or\n'
            '                 express a quick sentiment without sending a text reply.\n'
            '                 For Slack use the emoji name without colons (e.g. "thumbsup").\n'
            '                 For Telegram and Discord use the emoji character (e.g. "👍").\n'
            '                 Requires `emoji`.'
        )
    )
    path: str | None = Field(
        default=None,
        description='Absolute path to the local file to send. Required when action=file.',
    )
    caption: str | None = Field(
        default=None,
        description='Optional caption shown alongside the file. Only used when action=file.',
    )
    text: str | None = Field(
        default=None,
        description='Status message to send to the user. Required when action=intermediate.',
    )
    emoji: str | None = Field(
        default=None,
        description=(
            'Unicode emoji character to react with. Required when action=react. '
            'Use the actual emoji character, not a shortcode (e.g. "👍" not "+1"). '
            'Supported: "👍" "👎" "❤" "🔥" "🥰" "👏" "😁" "🤔" "😢" "🎉" "🤩" "💩" '
            '"🙏" "👌" "🤡" "🥱" "😍" "💯" "🤣" "⚡" "🏆" "😭" "👻" "👀" "😇" '
            '"🤗" "✍" "🫡" "🤪" "🗿" "🆒" "🦄" "😎" "👾" "😡".'
        ),
    )
    message_id: str | None = Field(
        default=None,
        description=(
            'Channel-side message ID to target. '
            'For action=react: the message to add the reaction to (defaults to the user\'s triggering message). '
            'For action=file/intermediate with reply=True: the message to reply to (defaults to the user\'s triggering message). '
            'Use this when you need to react or reply to a specific earlier message rather than the current one.'
        ),
    )
    reply: bool = Field(
        default=False,
        description=(
            'When True, send this message as a reply to the triggering message (or message_id if provided). '
            'Applicable to action=file and action=intermediate. '
            'On Slack this creates a threaded reply; on Telegram/Discord it quotes the original message.'
        ),
    )

    @model_validator(mode='after')
    def _check_required_fields(self) -> SendSchema:
        if self.action == SendAction.file and not self.path:
            raise ValueError("'path' is required when action='file'")
        if self.action == SendAction.intermediate and not self.text:
            raise ValueError("'text' is required when action='intermediate'")
        if self.action == SendAction.react and not self.emoji:
            raise ValueError("'emoji' is required when action='react'")
        return self


class SendTool(Tool):
    def __init__(self, bus: Bus | None = None) -> None:
        super().__init__(
            name='send',
            description=(
                'Send content to the user in the current channel outside the normal response flow.\n\n'
                'Three actions:\n'
                '  file         — upload a local file (any format) to the chat.\n'
                '  intermediate — push a mid-turn status update so the user knows what you are doing\n'
                '                 while a long task is still in progress.\n'
                '  react        — add an emoji reaction to the user\'s triggering message.\n\n'
                'Do not use this tool for your final answer — just write it as your response.'
            ),
            schema=SendSchema,
            kind=ToolKind.Execute,
            execution_mode=ToolExecutionMode.Sequential,        )
        self._bus = bus

    def get_display_name(self, args: dict) -> str:
        """Return a human-readable description of the action."""
        action = args.get('action', '')
        path = args.get('path', '') or ''
        emoji = args.get('emoji', '') or ''
        if action == 'file':
            name = path.rsplit('/', 1)[-1] if path else ''
            return f"Sending file: {name}" if name else "Sending file"
        if action == 'intermediate': return "Sending update"
        if action == 'react': return f"Reacting: {emoji}" if emoji else "Reacting"
        return "Sending"

    def is_available(self, context) -> bool:
        """Check that required service is available in context."""
        return (self._bus or context.bus) is not None

    async def execute(
        self,
        invocation: ToolInvocation,
        tool_execution_update_callback=None,
        signal=None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Dispatch the requested action."""
        bus = self._bus or (context.bus if context else None)
        if bus is None:
            return ToolResult.error(id=invocation.id, content='send: bus is not available.')

        channel = _session_channel.get()
        chat_id = _session_chat_id.get()

        if not channel or not chat_id:
            return ToolResult.error(
                id=invocation.id,
                content='send: no active channel session — cannot determine where to deliver.',
            )

        params = invocation.params
        action = params.get('action')

        match action:
            case SendAction.file:
                return await self._send_file(invocation, channel, chat_id, params, bus)
            case SendAction.intermediate:
                return await self._send_intermediate(invocation, channel, chat_id, params, bus)
            case SendAction.react:
                return await self._send_react(invocation, channel, chat_id, params, bus, context)
            case _:
                return ToolResult.error(id=invocation.id, content=f"send: unknown action '{action}'.")

    async def _send_file(
        self,
        invocation: ToolInvocation,
        channel: str,
        chat_id: str,
        params: dict,
        bus,
    ) -> ToolResult:
        from operator_use.bus.types import OutgoingMessage, FilePart, TextPart

        file_path = params.get('path', '')
        caption = params.get('caption')
        reply = params.get('reply', False)

        path = Path(file_path)
        if not path.exists():
            return ToolResult.error(id=invocation.id, content=f'send: file not found: {file_path}')
        if not path.is_file():
            return ToolResult.error(id=invocation.id, content=f'send: path is not a file: {file_path}')

        mime_type, _ = mimetypes.guess_type(str(path))
        parts: list = [FilePart(path=str(path), mime_type=mime_type)]
        if caption:
            parts.append(TextPart(caption))

        metadata: dict = {}
        if reply:
            target_id = params.get('message_id') or _session_message_id.get()
            if target_id:
                metadata['reply_to'] = target_id

        await bus.publish_outgoing(OutgoingMessage(channel=channel, chat_id=chat_id, parts=parts, metadata=metadata))
        return ToolResult.ok(
            id=invocation.id,
            content=f'File sent: {path.name}' + (f' — {caption}' if caption else ''),
        )

    async def _send_intermediate(
        self,
        invocation: ToolInvocation,
        channel: str,
        chat_id: str,
        params: dict,
        bus,
    ) -> ToolResult:
        from operator_use.bus.types import OutgoingMessage, TextPart

        text = params.get('text', '')
        reply = params.get('reply', False)

        metadata: dict = {}
        if reply:
            target_id = params.get('message_id') or _session_message_id.get()
            if target_id:
                metadata['reply_to'] = target_id

        await bus.publish_outgoing(OutgoingMessage(channel=channel, chat_id=chat_id, parts=[TextPart(text)], metadata=metadata))
        return ToolResult.ok(id=invocation.id, content='Intermediate message sent.')

    async def _send_react(
        self,
        invocation: ToolInvocation,
        channel: str,
        chat_id: str,
        params: dict,
        bus,
        context: ToolContext | None = None,
    ) -> ToolResult:
        from operator_use.bus.types import OutgoingMessage

        emoji = params.get('emoji', '')
        target_id = params.get('message_id') or _session_message_id.get()

        if not target_id:
            return ToolResult.error(
                id=invocation.id,
                content='send react: no message_id available — provide message_id or ensure this is triggered by a channel message.',
            )

        await bus.publish_outgoing(OutgoingMessage(
            channel=channel,
            chat_id=chat_id,
            metadata={'kind': 'react', 'message_id': target_id, 'emoji': emoji},
        ))

        sm = context.session_manager if context else None
        if sm and emoji:
            sm.add_reaction(target_id, emoji)

        return ToolResult.ok(id=invocation.id, content=f'Reaction {emoji!r} sent.')


tool = SendTool()
