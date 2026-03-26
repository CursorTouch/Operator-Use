import base64
import logging
import os
from typing import Optional

from openai import AsyncOpenAI, OpenAI

from operator_use.providers.base import BaseImage

logger = logging.getLogger(__name__)


class ImageOpenAI(BaseImage):
    """OpenAI Image Generation and Editing provider.

    Supports generation and editing via DALL-E 2, DALL-E 3, and gpt-image-1.

    Generation (no images):
        All three models support text-to-image generation.

    Editing (images provided):
        - gpt-image-1: up to 16 reference images as a list.
        - dall-e-2: single source image, optional second image as mask.
        - dall-e-3: editing not supported — raises ValueError.

    Args:
        model: The image model to use (default: "dall-e-3").
            Options: "dall-e-3", "dall-e-2", "gpt-image-1".
        size: Image dimensions (default: "1024x1024").
            DALL-E 3: "1024x1024", "1024x1792", "1792x1024".
            DALL-E 2: "256x256", "512x512", "1024x1024".
            gpt-image-1: "1024x1024", "1536x1024", "1024x1536", "auto".
        quality: Image quality (default: "standard").
            DALL-E 3 / gpt-image-1: "standard", "hd".
        style: Image style for DALL-E 3 only (default: "vivid").
            Options: "vivid", "natural".
        api_key: OpenAI API key. Falls back to OPENAI_API_KEY env variable.
        base_url: Optional base URL override. Falls back to OPENAI_BASE_URL env variable.

    Example:
        ```python
        from operator_use.providers.openai import ImageOpenAI

        provider = ImageOpenAI(model="gpt-image-1")

        # Generate from scratch
        provider.generate("a red panda coding on a laptop", "output.png")

        # Edit with reference images
        provider.generate("add a hat to the character", "output.png", images=["input.png"])
        ```
    """

    def __init__(
        self,
        model: str = "dall-e-3",
        size: str = "1024x1024",
        quality: str = "standard",
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

    def _save_response(self, data, output_path: str) -> None:
        if data.b64_json:
            image_bytes = base64.b64decode(data.b64_json)
            with open(output_path, "wb") as f:
                f.write(image_bytes)
        elif data.url:
            import urllib.request
            urllib.request.urlretrieve(data.url, output_path)
        else:
            raise RuntimeError("No image data in response")

    def generate(self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs) -> None:
        if images:
            if self._model == "dall-e-3":
                raise ValueError("dall-e-3 does not support image editing. Use gpt-image-1 or dall-e-2.")

            if self._model == "gpt-image-1":
                image_files = [open(p, "rb") for p in images]
                try:
                    response = self.client.images.edit(
                        model=self._model,
                        image=image_files,
                        prompt=prompt,
                        size=kwargs.get("size", self.size),
                        n=1,
                    )
                finally:
                    for f in image_files:
                        f.close()
            else:  # dall-e-2: first image = source, second = mask
                edit_kwargs = dict(
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
            params = dict(
                model=self._model,
                prompt=prompt,
                size=kwargs.get("size", self.size),
                quality=kwargs.get("quality", self.quality),
                n=1,
                response_format="b64_json",
            )
            if self._model == "dall-e-3":
                params["style"] = kwargs.get("style", self.style)
            response = self.client.images.generate(**params)

        self._save_response(response.data[0], output_path)
        logger.debug(f"[ImageOpenAI] Image saved to {output_path}")

    async def agenerate(self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs) -> None:
        if images:
            if self._model == "dall-e-3":
                raise ValueError("dall-e-3 does not support image editing. Use gpt-image-1 or dall-e-2.")

            if self._model == "gpt-image-1":
                image_files = [open(p, "rb") for p in images]
                try:
                    response = await self.aclient.images.edit(
                        model=self._model,
                        image=image_files,
                        prompt=prompt,
                        size=kwargs.get("size", self.size),
                        n=1,
                    )
                finally:
                    for f in image_files:
                        f.close()
            else:  # dall-e-2
                edit_kwargs = dict(
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
            params = dict(
                model=self._model,
                prompt=prompt,
                size=kwargs.get("size", self.size),
                quality=kwargs.get("quality", self.quality),
                n=1,
                response_format="b64_json",
            )
            if self._model == "dall-e-3":
                params["style"] = kwargs.get("style", self.style)
            response = await self.aclient.images.generate(**params)

        self._save_response(response.data[0], output_path)
        logger.debug(f"[ImageOpenAI] Async image saved to {output_path}")
