"""Image generation and editing tool.

Generates or edits images using the configured image provider.

If the current conversation contains images (sent by the user via any channel),
they are automatically available via the ``_incoming_image_paths`` extension and
used as input images when the ``images`` parameter is omitted.

Flow:
  1. User sends a message — optionally with one or more images + caption.
  2. Agent calls ``imagegen`` with the caption as ``prompt``.
  3. Tool picks up any incoming image paths automatically.
  4. Provider generates or edits the image.
  5. Result is sent back to the user and the output path is returned.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from operator_use.bus.views import ImagePart, OutgoingMessage, TextPart
from operator_use.tools import Tool, ToolResult


class ImageGen(BaseModel):
    prompt: str = Field(
        description=(
            "Text description of the image to generate, or the edit/modification to apply "
            "when input images are provided (e.g. 'make it look like a watercolour painting', "
            "'add a sunset sky', 'remove the background')."
        )
    )
    images: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of input image file paths to edit or use as references. "
            "If omitted, any images the user sent in the current message are used automatically. "
            "Pass an empty list [] to force pure text-to-image generation even when the user sent images."
        ),
    )
    output_path: Optional[str] = Field(
        default=None,
        description=(
            "Where to save the generated image. "
            "If omitted, a unique file is created inside the agent workspace."
        ),
    )
    caption: Optional[str] = Field(
        default=None,
        description="Optional caption to send alongside the generated image.",
    )
    send_result: bool = Field(
        default=True,
        description="If True (default), send the generated image back to the user automatically.",
    )


@Tool(
    name="image_gen",
    description=(
        "Generate or edit an image using the configured image provider.\n\n"
        "Generation: call with just a prompt to create an image from scratch.\n"
        "Editing: provide input images (or let the tool pick up images the user sent) "
        "and a prompt describing the edit — e.g. 'make it a pencil sketch', "
        "'add a rainbow', 'change the background to a forest'.\n\n"
        "The generated image is sent back to the user automatically (send_result=True). "
        "The output file path is always returned so you can reference or share it further."
    ),
    model=ImageGen,
)
async def imagegen(
    prompt: str,
    images: list[str] | None = None,
    output_path: str | None = None,
    caption: str | None = None,
    send_result: bool = True,
    **kwargs,
) -> ToolResult:
    provider = kwargs.get("_image")
    if provider is None:
        return ToolResult.error_result(
            "No image provider is configured. "
            "Enable one under 'image' in config (e.g. provider: openai, model: dall-e-3)."
        )

    # Resolve input images: explicit list → incoming message images → None (pure generation)
    # Passing [] explicitly skips auto-detection and forces generation.
    if images is None:
        images = kwargs.get("_incoming_image_paths") or None
    elif images == []:
        images = None

    # Build output path inside workspace if not provided
    if not output_path:
        workspace: Path | None = kwargs.get("_workspace")
        if workspace:
            gen_dir = workspace / "generated"
        else:
            import tempfile

            gen_dir = Path(tempfile.gettempdir()) / "operator_generated"
        gen_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(gen_dir / f"{ts}_{uuid.uuid4().hex[:8]}.png")

    # Generate / edit
    try:
        await provider.agenerate(prompt, output_path, images=images)
    except Exception as e:
        return ToolResult.error_result(f"Image generation failed: {type(e).__name__}: {e}")

    # Send result back to user
    if send_result:
        bus = kwargs.get("_bus")
        channel = kwargs.get("_channel")
        chat_id = kwargs.get("_chat_id")
        account_id = kwargs.get("_account_id", "")

        if bus and channel and chat_id:
            parts: list = [ImagePart(paths=[output_path])]
            if caption:
                parts.append(TextPart(content=caption))
            await bus.publish_outgoing(
                OutgoingMessage(
                    channel=channel,
                    chat_id=chat_id,
                    account_id=account_id,
                    parts=parts,
                )
            )

    action = "edited" if images else "generated"
    return ToolResult.success_result(f"Image {action} and saved to: {output_path}")
