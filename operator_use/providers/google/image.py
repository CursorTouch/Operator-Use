import asyncio
import base64
import logging
import os
from typing import Optional

from operator_use.providers.base import BaseImage

logger = logging.getLogger(__name__)

# Models whose names start with "gemini" use generate_content() with IMAGE modality.
# Models whose names start with "imagen" use generate_images().
_GEMINI_PREFIX = "gemini"
_IMAGEN_PREFIX = "imagen"


class ImageGoogle(BaseImage):
    """Google image generation and editing provider.

    Supports two model families via a standard Gemini API key:

    **Gemini native models** (``gemini-2.5-flash-image``, ``gemini-3-pro-image-preview``,
    ``gemini-3.1-flash-image-preview``):
        - Text-to-image generation
        - Image editing (image + prompt → new image)
        - Conversational / multi-turn editing
        Uses ``generate_content()`` with ``response_modalities=["IMAGE", "TEXT"]``.

    **Imagen 4 models** (``imagen-4.0-generate-001``, ``imagen-4.0-ultra-generate-001``,
    ``imagen-4.0-fast-generate-001``):
        - Text-to-image generation only (no image editing)
        Uses ``generate_images()``.

    Args:
        model: Model ID (default: ``"gemini-2.5-flash-image"``).
        api_key: Google / Gemini API key. Falls back to ``GEMINI_API_KEY`` or
            ``GOOGLE_API_KEY`` env variables.
        negative_prompt: Optional — what to exclude (Imagen models only).

    Example:
        ```python
        from operator_use.providers.google import ImageGoogle

        provider = ImageGoogle()  # uses gemini-2.5-flash-image

        # Generate from text
        provider.generate("a red panda coding on a laptop", "output.png")

        # Edit an existing image
        provider.generate("make it look like a watercolour", "output.png", images=["input.png"])

        # Imagen 4 (text-to-image only)
        provider = ImageGoogle(model="imagen-4.0-generate-001")
        provider.generate("a red panda coding on a laptop", "output.png")
        ```
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash-image",
        api_key: Optional[str] = None,
        negative_prompt: Optional[str] = None,
    ):
        self._model = model
        self.negative_prompt = negative_prompt
        self.api_key = (
            api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )

    @property
    def model(self) -> str:
        return self._model

    def _make_client(self):
        from google import genai

        return genai.Client(api_key=self.api_key)

    def _is_gemini_model(self) -> bool:
        return self._model.startswith(_GEMINI_PREFIX)

    def _generate_gemini(
        self, prompt: str, output_path: str, images: list[str] | None, **kwargs
    ) -> None:
        """Generate or edit using Gemini native image output."""
        from google.genai import types

        client = self._make_client()
        contents: list = []

        if images:
            from PIL import Image as PILImage

            for path in images:
                contents.append(PILImage.open(path).convert("RGB"))

        contents.append(prompt)

        response = client.models.generate_content(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        image_bytes: bytes | None = None
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                image_bytes = base64.b64decode(part.inline_data.data)
                break

        if image_bytes is None:
            raise ValueError(
                f"Gemini model {self._model!r} returned no image. "
                "Check that the model supports image output."
            )

        with open(output_path, "wb") as f:
            f.write(image_bytes)
        logger.debug(f"[ImageGoogle] Gemini image saved to {output_path}")

    def _generate_imagen(
        self, prompt: str, output_path: str, images: list[str] | None, **kwargs
    ) -> None:
        """Generate using Imagen 4 (text-to-image only)."""
        from google.genai import types

        if images:
            raise ValueError(
                f"Imagen model {self._model!r} does not support image editing. "
                "Use a Gemini native model (e.g. gemini-2.5-flash-image) for editing."
            )

        client = self._make_client()
        config_kwargs: dict = {
            "number_of_images": 1,
            "output_mime_type": "image/png",
        }
        if self.negative_prompt or kwargs.get("negative_prompt"):
            config_kwargs["negative_prompt"] = kwargs.get("negative_prompt", self.negative_prompt)
        if kwargs.get("aspect_ratio"):
            config_kwargs["aspect_ratio"] = kwargs["aspect_ratio"]

        response = client.models.generate_images(
            model=self._model,
            prompt=prompt,
            config=types.GenerateImagesConfig(**config_kwargs),
        )
        image_bytes = response.generated_images[0].image.image_bytes

        with open(output_path, "wb") as f:
            f.write(image_bytes)
        logger.debug(f"[ImageGoogle] Imagen image saved to {output_path}")

    def generate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        if self._is_gemini_model():
            self._generate_gemini(prompt, output_path, images, **kwargs)
        else:
            self._generate_imagen(prompt, output_path, images, **kwargs)

    async def agenerate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        await asyncio.to_thread(self.generate, prompt, output_path, images, **kwargs)
