from __future__ import annotations

from acp.schema import (
    AudioContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
)

ContentBlock = (
    TextContentBlock
    | ImageContentBlock
    | AudioContentBlock
    | ResourceContentBlock
    | EmbeddedResourceContentBlock
)


def text_from_content_blocks(blocks: list) -> str:
    """Extract and concatenate text from a list of ACP ContentBlocks."""
    return ''.join(b.text for b in blocks if isinstance(b, TextContentBlock))


def content_blocks_from_text(text: str) -> list[TextContentBlock]:
    """Wrap plain text into a single TextContentBlock list."""
    return [TextContentBlock(type='text', text=text)]
