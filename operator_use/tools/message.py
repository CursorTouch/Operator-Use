"""Message tools: send messages and reactions to the channel."""

import asyncio
import logging
import os
from operator_use.tools import Tool, ToolResult
from operator_use.bus import OutgoingMessage, TextPart, ImagePart, AudioPart, FilePart
from pydantic import BaseModel, Field
from typing import Literal

logger = logging.getLogger(__name__)


# Emoji reactions that work across ALL major platforms:
# Telegram (Bot API 7.0), WhatsApp, Discord, Slack.
# This is a strict subset of Telegram's setMessageReaction allowed set,
# filtered to emojis that are also universally recognised on other platforms.
# NOTE: Telegram requires bare ❤ (U+2764), NOT ❤️ (U+2764 U+FE0F).
UNIVERSAL_REACTIONS: set[str] = {
    "👍",  # thumbs up      — agree / approve
    "👎",  # thumbs down    — disagree
    "❤",  # heart (bare)   — love / care  ← Telegram requires no variation selector
    "🔥",  # fire           — great / hot
    "🎉",  # party popper   — celebrate / congrats
    "🤣",  # ROFL           — very funny
    "🤔",  # thinking       — hmm / considering
    "😱",  # scream         — shocked / wow
    "😢",  # crying         — sad / sorry
    "😡",  # angry          — frustrated
    "🥰",  # smiling face   — happy / sweet
    "😍",  # heart eyes     — love it
    "😎",  # cool           — no problem
    "🏆",  # trophy         — winner / best
    "💯",  # 100            — perfect / absolutely
    "⚡",  # lightning      — fast / urgent
    "🙏",  # pray           — thanks / please
    "👏",  # clap           — well done
    "🤩",  # star-struck    — amazing
    "😇",  # angel          — innocent / kind
}

_REACTIONS_HINT = " ".join(sorted(UNIVERSAL_REACTIONS))
# Tuple form for Literal[...] type annotation
_REACTIONS_TUPLE = tuple(sorted(UNIVERSAL_REACTIONS))


class IntermediateMessage(BaseModel):
    content: str = Field(
        ...,
        description=(
            "The message to send. "
            "When ask=False: a short progress update — e.g. 'Searching the web…', 'Found 12 results, reading them now…'. "
            "When ask=True: frame this as a direct question — e.g. 'Which city should I search flights from?', 'Do you want the result as PDF or DOCX?'. "
            "When to_parent=True: send to parent agent's context (if running as a delegated subagent)."
        ),
    )
    ask: bool = Field(
        default=False,
        description=(
            "If True, the content is treated as a question — the tool pauses and waits for the reply before returning. "
            "When ask=True + to_parent=True: wait for parent agent's response. "
            "The reply is returned as the tool result. Default is False (send and continue)."
        ),
    )
    to_parent: bool = Field(
        default=False,
        description=(
            "If True and you are a delegated subagent, send this message to your parent agent instead of the channel. "
            "Combine with ask=True to ask a clarifying question to the parent agent and wait for their response. "
            "Example: intermediate_message('Is this desktop or web?', ask=True, to_parent=True) — waits for parent's answer."
        ),
    )
    to_child: str | None = Field(
        default=None,
        description=(
            "Parent agent only: name of the delegated child agent waiting for a response. "
            "Use this to respond to a child's question (when they called with to_parent=True, ask=True). "
            "Example: parent calls intermediate_message('Yes, desktop version', to_child='documentation') "
            "to respond to the documentation agent's earlier question."
        ),
    )
    timeout: int = Field(
        default=300,
        description="How long to wait for a reply in seconds when ask=True. Default is 5 minutes.",
    )
    channel: str | None = Field(
        default=None,
        description="Channel name (e.g. 'telegram', 'discord'). Omit to use the current conversation's channel.",
    )
    chat_id: str | None = Field(
        default=None,
        description="Chat/conversation ID. Omit to use the current conversation's chat_id.",
    )


@Tool(
    name="intermediate_message",
    description=(
        "Send a progress update or ask a clarifying question mid-task. "
        "Use to keep users/parent agents informed instead of waiting in silence. "
        "Call whenever you start a meaningful step — e.g. 'Searching…', 'Reading the file…', 'Running the command…'. "
        "Set ask=True when you need an answer before continuing — the tool pauses and returns the reply. "
        "Set to_parent=True (with ask=True) to ask the parent agent a clarifying question (subagents only). "
        "This is NOT the final reply — continue working after calling it."
    ),
    model=IntermediateMessage,
)
async def intermediate_message(
    content: str,
    ask: bool = False,
    to_parent: bool = False,
    to_child: str | None = None,
    timeout: int = 300,
    channel: str | None = None,
    chat_id: str | None = None,
    **kwargs,
) -> ToolResult:
    """Send a progress update or question, optionally to parent agent or waiting for reply."""
    bus = kwargs.get("_bus")
    session_id: str = kwargs.get("_session_id", "")
    metadata = kwargs.get("_metadata") or {}
    pending_replies: dict = kwargs.get("_pending_replies", {})

    content = (content or "").strip()
    if not content:
        return ToolResult.error_result("Content cannot be empty")

    # Handle parent-child communication: child asks parent
    if to_parent and ask:
        parent_session_id = metadata.get("_parent_session_id")
        parent_pending_replies = metadata.get("_parent_pending_replies")
        to_agent = metadata.get("to_agent")  # Child's agent name (set by localagents)

        if not parent_session_id or parent_pending_replies is None:
            return ToolResult.error_result(
                "to_parent=True only works when running as a delegated subagent. "
                "You are not in a parent-child context."
            )

        # Add this message to parent's pending replies so they see it
        # Use agent name as key for easier parent response
        child_request_key = f"child_ask_{to_agent}" if to_agent else f"child_ask_{session_id}"
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        parent_pending_replies[child_request_key] = {
            "content": content,
            "future": future,
            "from_agent": to_agent or "unknown",
            "from_session": session_id,
        }

        logger.info(f"Child agent '{to_agent or 'unknown'}' asking parent: {content[:80]}")

        try:
            reply = await asyncio.wait_for(future, timeout=timeout)
            return ToolResult.success_result(f"Parent replied: {reply}")
        except asyncio.TimeoutError:
            parent_pending_replies.pop(child_request_key, None)
            return ToolResult.error_result(
                f"Parent did not reply within {timeout}s — continuing without parent input."
            )

    # Handle parent responding to child
    if to_child:
        pending_replies_dict = pending_replies if isinstance(pending_replies, dict) else {}
        child_request_key = f"child_ask_{to_child}"

        if child_request_key not in pending_replies_dict:
            # List available child requests for debugging
            available = [
                k.replace("child_ask_", "")
                for k in pending_replies_dict.keys()
                if k.startswith("child_ask_")
            ]
            avail_str = (
                f" Available: {', '.join(available)}"
                if available
                else " No child agents are waiting."
            )
            return ToolResult.error_result(
                f"No pending ask from child agent '{to_child}'.{avail_str}"
            )

        request_info = pending_replies_dict[child_request_key]
        request_info["future"].set_result(content)
        pending_replies_dict.pop(child_request_key, None)

        logger.info(f"Parent responding to child '{to_child}': {content[:80]}")
        return ToolResult.success_result(f"Response sent to child agent '{to_child}'.")

    # Default: send to channel (user)
    ctx_channel = kwargs.get("_channel")
    ctx_chat_id = kwargs.get("_chat_id")
    ctx_account_id = kwargs.get("_account_id") or ""
    target_channel = channel if channel is not None else ctx_channel
    target_chat_id = chat_id if chat_id is not None else ctx_chat_id

    if not bus or target_channel is None or target_chat_id is None:
        return ToolResult.error_result("no channel context (internal error)")

    await bus.publish_outgoing(
        OutgoingMessage(
            chat_id=target_chat_id,
            channel=target_channel,
            account_id=ctx_account_id,
            parts=[TextPart(content=content)],
            metadata=metadata,
            reply=False,
            continue_typing=not ask,
        )
    )

    if ask:
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        pending_replies[session_id] = future
        try:
            reply = await asyncio.wait_for(future, timeout=timeout)
            return ToolResult.success_result(f"User replied: {reply}")
        except asyncio.TimeoutError:
            pending_replies.pop(session_id, None)
            return ToolResult.error_result(
                f"No reply received within {timeout}s — continuing without user input."
            )

    return ToolResult.success_result(
        f"Progress update sent to channel {target_channel} chat_id {target_chat_id}"
    )


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_AUDIO_EXTS = {".ogg", ".wav", ".mp3", ".m4a", ".aac", ".flac"}


class SendFile(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Absolute path to the file on disk to send to the user. "
            "Supports images (.jpg, .png, .gif, .webp), audio (.mp3, .ogg, .wav), "
            "and any other file type (sent as a document)."
        ),
    )
    caption: str | None = Field(
        default=None,
        description="Optional caption or message to accompany the file.",
    )
    channel: str | None = Field(
        default=None,
        description="Channel name (e.g. 'telegram', 'discord'). Omit to use the current conversation's channel.",
    )
    chat_id: str | None = Field(
        default=None,
        description="Chat/conversation ID. Omit to use the current conversation's chat_id.",
    )


@Tool(
    name="send_file",
    description=(
        "Send a file from the computer to the user on the current channel. "
        "Use this to share images, documents, audio, or any other file you have generated or found. "
        "Supports images (.jpg, .png, .gif, .webp), audio (.mp3, .ogg, .wav), and any other file type (sent as a document). "
        "Optionally include a caption to accompany the file."
    ),
    model=SendFile,
)
async def send_file(
    path: str,
    caption: str | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
    **kwargs,
) -> ToolResult:
    """Send a file from the computer to the user via the channel."""
    bus = kwargs.get("_bus")
    ctx_channel = kwargs.get("_channel")
    ctx_chat_id = kwargs.get("_chat_id")
    ctx_account_id = kwargs.get("_account_id") or ""
    metadata = kwargs.get("_metadata") or {}
    target_channel = channel if channel is not None else ctx_channel
    target_chat_id = chat_id if chat_id is not None else ctx_chat_id

    if not bus or target_channel is None or target_chat_id is None:
        return ToolResult.error_result("no channel context (internal error)")

    path = (path or "").strip()
    if not path:
        return ToolResult.error_result("path cannot be empty")
    if not os.path.isfile(path):
        return ToolResult.error_result(f"file not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    parts: list = []

    if ext in _IMAGE_EXTS:
        parts.append(ImagePart(paths=[path]))
    elif ext in _AUDIO_EXTS:
        parts.append(AudioPart(audio=path))
    else:
        parts.append(FilePart(path=path))

    if caption:
        parts.append(TextPart(content=caption.strip()))

    await bus.publish_outgoing(
        OutgoingMessage(
            chat_id=target_chat_id,
            channel=target_channel,
            account_id=ctx_account_id,
            parts=parts,
            metadata=metadata,
            reply=False,
            continue_typing=False,
        )
    )

    filename = os.path.basename(path)
    return ToolResult.success_result(
        f"File '{filename}' sent to channel {target_channel} chat_id {target_chat_id}"
    )


class ReactMessage(BaseModel):
    emoji: Literal[*_REACTIONS_TUPLE] = Field(
        ...,
        description=(
            "A single emoji to react with. Must be one of the universal reactions that work across all channels: "
            f"{_REACTIONS_HINT}"
        ),
    )
    message_id: int | None = Field(
        default=None,
        description=(
            "The ID of the message to react to. "
            "Omit to react to the user's latest message (resolved automatically from context). "
            "Pass a specific message_id to react to any message — user or bot. "
            "User message IDs appear as [msg_id:N] in history; bot message IDs appear as [bot_msg_id:N]."
        ),
    )
    channel: str | None = Field(
        default=None,
        description="Channel to send the reaction on (e.g. 'telegram', 'discord'). Omit to use the current conversation's channel.",
    )
    chat_id: str | None = Field(
        default=None,
        description="Chat/conversation ID. Omit to use the current conversation's chat_id.",
    )


@Tool(
    name="react_message",
    description=(
        "React to a message with an emoji reaction. "
        "Use to instantly acknowledge a request (👍), confirm task completion (🎉), or show empathy (❤). "
        "Reactions feel more natural than a text reply for simple acknowledgements. "
        "Optionally specify channel and chat_id to react on a different channel or conversation than the current one. "
        f"Only use emojis from the universal set: {_REACTIONS_HINT}"
    ),
    model=ReactMessage,
)
async def react_message(
    emoji: str,
    message_id: int | None = None,
    channel: str | None = None,
    chat_id: str | None = None,
    **kwargs,
) -> ToolResult:
    """React to a message with an emoji. Each channel handles reactions in its own way."""
    bus = kwargs.get("_bus")
    ctx_channel = kwargs.get("_channel")
    ctx_chat_id = kwargs.get("_chat_id")
    ctx_account_id = kwargs.get("_account_id") or ""
    metadata = kwargs.get("_metadata") or {}

    target_channel = channel if channel is not None else ctx_channel
    target_chat_id = chat_id if chat_id is not None else ctx_chat_id

    if not bus or target_channel is None or target_chat_id is None:
        return ToolResult.error_result("no channel context (internal error)")

    emoji = (emoji or "").strip()
    if not emoji:
        return ToolResult.error_result("emoji cannot be empty")

    # Validate against the universal set — channels may support more but the agent must stay cross-platform
    if emoji not in UNIVERSAL_REACTIONS:
        allowed = " ".join(sorted(UNIVERSAL_REACTIONS))
        return ToolResult.error_result(
            f"'{emoji}' is not a universal reaction. Use one of: {allowed}"
        )

    # Resolve message_id: explicit or fall back to the incoming message_id from context
    target_message_id = message_id or metadata.get("message_id")

    await bus.publish_outgoing(
        OutgoingMessage(
            chat_id=target_chat_id,
            channel=target_channel,
            account_id=ctx_account_id,
            parts=[],
            metadata={
                **metadata,
                "_reaction": True,
                "_reaction_emoji": emoji,
                "_reaction_message_id": str(target_message_id) if target_message_id else None,
            },
            reply=False,
        )
    )
    return ToolResult.success_result(
        f"Reacted with {emoji} on message_id {target_message_id} in channel {target_channel} chat_id {target_chat_id}"
    )
