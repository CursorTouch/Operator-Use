import base64
import logging
import os
from typing import Optional

from openai import AsyncOpenAI, OpenAI

from operator_use.providers.base import BaseImage

logger = logging.getLogger(__name__)

# GPT Image models use "low"/"medium"/"high"/"auto" for quality.
# DALL-E 3 uses "standard"/"hd". DALL-E 2 does not support quality at all.
_GPT_IMAGE_MODELS = {"gpt-image-1", "gpt-image-1.5", "gpt-image-1-mini", "chatgpt-image-latest"}
_DALLE3_MODELS = {"dall-e-3"}
_NO_EDIT_MODELS = {"dall-e-3"}  # dall-e-3 has no editing endpoint


class ImageOpenAI(BaseImage):
    """OpenAI Image Generation and Editing provider.

    Supports generation and editing via GPT Image and DALL-E models.

    **GPT Image models** (``gpt-image-1.5``, ``gpt-image-1``, ``gpt-image-1-mini``,
    ``chatgpt-image-latest``):
        - Text-to-image generation
        - Image editing (up to 16 reference images)
        - Quality: ``"low"``, ``"medium"``, ``"high"``, ``"auto"`` (default)
        - Note: DALL-E 2 and DALL-E 3 are deprecated and will be removed May 2026.

    **DALL-E 3** (deprecated — use GPT Image models instead):
        - Text-to-image only (no editing)
        - Quality: ``"standard"`` or ``"hd"``
        - Style: ``"vivid"`` or ``"natural"``

    **DALL-E 2** (deprecated — use GPT Image models instead):
        - Text-to-image and single-image editing with optional mask

    Args:
        model: The image model to use (default: ``"gpt-image-1.5"``).
        size: Image dimensions (default: ``"auto"``).
            GPT Image: ``"1024x1024"``, ``"1536x1024"``, ``"1024x1536"``, ``"auto"``.
            DALL-E 3: ``"1024x1024"``, ``"1024x1792"``, ``"1792x1024"``.
            DALL-E 2: ``"256x256"``, ``"512x512"``, ``"1024x1024"``.
        quality: Image quality.
            GPT Image models: ``"low"``, ``"medium"``, ``"high"``, ``"auto"`` (default).
            DALL-E 3: ``"standard"`` or ``"hd"``.
        style: DALL-E 3 only — ``"vivid"`` or ``"natural"`` (default: ``"vivid"``).
        api_key: OpenAI API key. Falls back to ``OPENAI_API_KEY`` env variable.
        base_url: Optional base URL override.

    Example:
        ```python
        from operator_use.providers.openai import ImageOpenAI

        provider = ImageOpenAI()  # uses gpt-image-1.5

        # Generate from scratch
        provider.generate("a red panda coding on a laptop", "output.png")

        # Edit with reference images
        provider.generate("add a hat", "output.png", images=["input.png"])
        ```
    """

    def __init__(
        self,
        model: str = "gpt-image-1.5",
        size: str = "auto",
        quality: str = "auto",
        style: str = "vivid",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self._model = model
        self.size = size
        self.quality = quality
        self.style = style
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self.aclient = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    @property
    def model(self) -> str:
        return self._model

    def _is_gpt_image(self) -> bool:
        return self._model in _GPT_IMAGE_MODELS

    def _save_response(self, data, output_path: str) -> None:
        if data.b64_json:
            image_bytes = base64.b64decode(data.b64_json)
            with open(output_path, "wb") as f:
                f.write(image_bytes)
        elif data.url:
            import urllib.request

            urllib.request.urlretrieve(data.url, output_path)  # nosec B310 — URL from OpenAI API response (HTTPS only)
        else:
            raise RuntimeError("No image data in response")

    def _build_generate_params(self, prompt: str, **kwargs) -> dict:
        params: dict = dict(
            model=self._model,
            prompt=prompt,
            size=kwargs.get("size", self.size),
            n=1,
            response_format="b64_json",
        )
        if self._is_gpt_image():
            params["quality"] = kwargs.get("quality", self.quality)
        elif self._model in _DALLE3_MODELS:
            params["quality"] = kwargs.get("quality", self.quality)
            params["style"] = kwargs.get("style", self.style)
        # DALL-E 2: no quality param
        return params

    def generate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        if images:
            if self._model in _NO_EDIT_MODELS:
                raise ValueError(
                    f"{self._model!r} does not support image editing. Use gpt-image-1.5 or gpt-image-1."
                )

            if self._is_gpt_image():
                image_files = [open(p, "rb") for p in images]
                try:
                    response = self.client.images.edit(
                        model=self._model,
                        image=image_files,
                        prompt=prompt,
                        size=kwargs.get("size", self.size),
                        quality=kwargs.get("quality", self.quality),
                        n=1,
                    )
                finally:
                    for f in image_files:
                        f.close()
            else:  # dall-e-2: first image = source, second = optional mask
                edit_kwargs: dict = dict(
                    model=self._model,
                    image=open(images[0], "rb"),
                    prompt=prompt,
                    size=kwargs.get("size", self.size),
                    n=1,
                    response_format="b64_json",
                )
                if len(images) > 1:
                    edit_kwargs["mask"] = open(images[1], "rb")
                try:
                    response = self.client.images.edit(**edit_kwargs)
                finally:
                    edit_kwargs["image"].close()
                    if "mask" in edit_kwargs:
                        edit_kwargs["mask"].close()
        else:
            response = self.client.images.generate(**self._build_generate_params(prompt, **kwargs))

        self._save_response(response.data[0], output_path)
        logger.debug(f"[ImageOpenAI] Image saved to {output_path}")

    async def agenerate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        if images:
            if self._model in _NO_EDIT_MODELS:
                raise ValueError(
                    f"{self._model!r} does not support image editing. Use gpt-image-1.5 or gpt-image-1."
                )

            if self._is_gpt_image():
                image_files = [open(p, "rb") for p in images]
                try:
                    response = await self.aclient.images.edit(
                        model=self._model,
                        image=image_files,
                        prompt=prompt,
                        size=kwargs.get("size", self.size),
                        quality=kwargs.get("quality", self.quality),
                        n=1,
                    )
                finally:
                    for f in image_files:
                        f.close()
            else:  # dall-e-2
                edit_kwargs: dict = dict(
                    model=self._model,
                    image=open(images[0], "rb"),
                    prompt=prompt,
                    size=kwargs.get("size", self.size),
                    n=1,
                    response_format="b64_json",
                )
                if len(images) > 1:
                    edit_kwargs["mask"] = open(images[1], "rb")
                try:
                    response = await self.aclient.images.edit(**edit_kwargs)
                finally:
                    edit_kwargs["image"].close()
                    if "mask" in edit_kwargs:
                        edit_kwargs["mask"].close()
        else:
            response = await self.aclient.images.generate(
                **self._build_generate_params(prompt, **kwargs)
            )

        self._save_response(response.data[0], output_path)
        logger.debug(f"[ImageOpenAI] Async image saved to {output_path}")
