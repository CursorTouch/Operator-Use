# Messages

The message layer defines every type of content that flows through the agent — from raw user text and LLM responses to tool calls, images, thinking blocks, and session-specific synthetic messages used for context reconstruction.

## Content types

Content is the unit carried inside a message. Each content type is a dataclass with a `type` literal:

| Type | Class | Description |
|---|---|---|
| `"text"` | `TextContent` | Plain text |
| `"image"` | `ImageContent` | One or more images (PIL, bytes, base64 string, or URL) |
| `"thinking"` | `ThinkingContent` | Extended thinking block (Anthropic) |
| `"tool_call"` | `ToolCallContent` | A tool the LLM wants to invoke |
| `"tool_result"` | `ToolResultContent` | The result of a tool invocation |

Per-role constraints (for reference — the types themselves do not enforce this):

```
SystemContent    = TextContent
UserContent      = TextContent | ImageContent | ToolResultContent
AssistantContent = TextContent | ThinkingContent | ToolCallContent
ToolContent      = ToolResultContent
```

## TextContent

```python
@dataclass
class TextContent:
    type: Literal["text"] = "text"
    content: str = ""
```

Used in user messages, assistant responses, system prompts, and compaction summaries.

## ImageContent

```python
@dataclass
class ImageContent:
    type: Literal["image"] = "image"
    images: list[str | Image.Image | bytes] = []
```

Accepts three image forms:

- **`str`** — either a URL (passed through unchanged) or a base64-encoded string (MIME type auto-detected from the data prefix).
- **`Image.Image`** (PIL) — converted to bytes on `to_base64()` using the image's own format.
- **`bytes`** — MIME type auto-detected from magic bytes (`JPEG`, `PNG`, `GIF`, `WEBP`).

`to_base64()` returns `list[tuple[base64_data, mime_type]]`. URL strings produce `(url, "")`.

Factory methods:

```python
ImageContent.from_file(path)   # reads bytes from disk
ImageContent.from_url(url)     # wraps a URL string
```

## ThinkingContent

```python
@dataclass
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    content: str = ""
    signature: str = ""    # opaque token required by Anthropic to re-submit thinking blocks
```

Emitted only by Anthropic models with extended thinking enabled. The `signature` must be preserved if the thinking block is re-submitted in a follow-up message.

## ToolCallContent

```python
@dataclass
class ToolCallContent:
    type: Literal["tool_call"] = "tool_call"
    id: str = ""
    name: str = ""
    kind: ToolKind | None = None   # optional hint for UIs
    args: dict[str, Any] = {}
```

Produced by the LLM when it wants to call a tool. The `id` correlates with the `ToolResultContent.id` in the tool-result message.

## ToolResultContent

```python
@dataclass
class ToolResultContent:
    type: Literal["tool_result"] = "tool_result"
    id: str = ""              # matches the tool_call id
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = {}
    terminate: bool = False   # hint: skip follow-up LLM call
```

`metadata` is not sent to the LLM — it is for internal tracking (hooks, logging). `terminate=True` is a signal to the Engine; it is stripped before the tool result is sent to the LLM.

## Message types

Messages group content by role. All message types inherit from `BaseMessage`:

```python
@dataclass
class BaseMessage:
    role: Role
    contents: list[Content] = []
    id: str                         # UUID, auto-generated
    timestamp: float                # Unix timestamp, auto-generated
```

### Role enum

```python
class Role(Enum):
    SYSTEM           = "system"
    USER             = "user"
    ASSISTANT        = "assistant"
    TOOL             = "tool"
    CUSTOM           = "custom"
    BRANCH_SUMMARY   = "branch_summary"
    COMPACTION_SUMMARY = "compaction_summary"
```

### LLM messages

These four are the only message types sent to the LLM:

**`UserMessage`** — carries `TextContent`, `ImageContent`, and/or `ToolResultContent`. Factory methods:
```python
UserMessage.text("hello")
UserMessage.with_images("describe this", [img1, img2])
```

**`AssistantMessage`** — carries `TextContent`, `ThinkingContent`, and `ToolCallContent`. Also tracks usage and stop reason:
```python
@dataclass
class AssistantMessage(BaseMessage):
    usage: Usage
    stop_reason: StopReason
    error: str = ""

    def text_content(self) -> str: ...      # joins all TextContent
    def tool_calls(self) -> list[ToolCallContent]: ...
    def thinking(self) -> list[ThinkingContent]: ...
```

**`SystemMessage`** — carries `TextContent`. Factory method:
```python
SystemMessage.text("You are a helpful assistant.")
```

**`ToolMessage`** — carries `ToolResultContent` items (one per tool call in the batch). Factory methods:
```python
ToolMessage.from_results([result1, result2])
ToolMessage.from_result(result)
```

`LLMMessage = SystemMessage | UserMessage | AssistantMessage | ToolMessage`

### Session-only messages

These never go to the LLM. They exist for UI display and context reconstruction.

**`CustomMessage`** — arbitrary content written by extensions via `SessionManager.append_custom_message()`. Has a `custom_type` string for dispatch.

**`BranchSummaryMessage`** — synthesized from a `BranchEntry` during `build_session_context()`. Informs the LLM that the conversation branched here and provides a summary of the previous branch.

**`CompactionSummaryMessage`** — synthesized from a `CompactionEntry` during `build_session_context()`. The first message the LLM sees when a compaction exists — it carries the LLM-generated summary of the compacted history.

`AgentMessage = LLMMessage | CustomMessage | BranchSummaryMessage | CompactionSummaryMessage`

## Usage and cost

```python
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: UsageCost = UsageCost()

@dataclass
class UsageCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0
```

`Usage` is set on `AssistantMessage` by the Engine after each LLM turn via the `EndEvent`. Agent reads `input_tokens + output_tokens` to track context size.

## Provider API boundary

LLM API implementations in `program/inference/api/llm/` convert from `LLMMessage` objects to provider-specific wire formats. Each provider's conversion handles:

- Merging `ToolResultContent` from `UserMessage` into the provider's tool-result format.
- Serializing `ThinkingContent` with its required `signature` (Anthropic).
- Converting `ImageContent.to_base64()` into the provider's image block format.

## Related documents

- [engine.md](./engine.md) — How the Engine builds and accumulates messages during a turn
- [session.md](./session.md) — How messages are persisted as `MessageEntry` and reconstructed
- [tool.md](./tool.md) — How `ToolCallContent` and `ToolResultContent` relate to `Tool.execute()`
- [inference.md](./inference.md) — How LLM events produce message content
