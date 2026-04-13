import base64
import logging
import mimetypes
import os
import urllib.request
from typing import Optional

from operator_use.providers.base import BaseImage

logger = logging.getLogger(__name__)

TOGETHER_BASE_URL = "https://api.together.xyz/v1"


def _encode_image_b64(path: str) -> str:
    """Encode a local image file as a base64 data URL."""
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{data}"


class ImageTogether(BaseImage):
    """Together AI image generation and editing provider.

    Uses the Together AI Images API (OpenAI-compatible) for text-to-image
    generation and image-to-image editing.

    Generation (no images):
        Supports FLUX.1, FLUX.1.1, FLUX.2, Ideogram, Seedream, HiDream,
        and other open-weight models hosted on Together AI.

    Editing (images provided):
        The first image is sent as ``image_url`` in the request. Best results
        with FLUX.1 Kontext models (instruction-based editing) or FLUX.2 Flex/Pro.
        ``strength`` controls deviation from the input (0.0 = unchanged, 1.0 = new).

    Args:
        model: The model to use (default: ``"black-forest-labs/FLUX.1.1-pro"``).
            Popular options:
              ``"black-forest-labs/FLUX.1-schnell-Free"``  (free tier)
              ``"black-forest-labs/FLUX.1.1-pro"``         (recommended quality)
              ``"black-forest-labs/FLUX.2-pro"``           (latest generation)
              ``"black-forest-labs/FLUX.2-max"``           (highest quality)
              ``"black-forest-labs/FLUX.1-kontext-pro"``   (instruction-based editing)
              ``"black-forest-labs/FLUX.1-kontext-max"``   (editing, max quality)
              ``"ideogram/ideogram-3.0"``                  (strong typography)
              ``"stabilityai/stable-diffusion-3-medium"``
        width: Image width in pixels (default: 1024).
        height: Image height in pixels (default: 1024).
        steps: Number of inference steps (default: 4).
        api_key: Together AI API key. Falls back to ``TOGETHER_API_KEY`` env variable.

    Example:
        ```python
        from operator_use.providers.together import ImageTogether

        provider = ImageTogether(model="black-forest-labs/FLUX.1.1-pro")

        # Generate from scratch
        provider.generate("a red panda coding on a laptop", "output.png")

        # Edit with a reference image (use a Kontext or FLUX.2 model)
        provider = ImageTogether(model="black-forest-labs/FLUX.1-kontext-pro")
        provider.generate("make it look like a watercolour painting", "output.png", images=["input.png"])
        ```
    """

    def __init__(
        self,
        model: str = "black-forest-labs/FLUX.1.1-pro",
        width: int = 1024,
        height: int = 1024,
        steps: int = 4,
        api_key: Optional[str] = None,
    ):
        self._model = model
        self.width = width
        self.height = height
        self.steps = steps
        self.api_key = api_key or os.environ.get("TOGETHER_API_KEY")

    @property
    def model(self) -> str:
        return self._model

    def _make_clients(self):
        from openai import AsyncOpenAI, OpenAI

        client = OpenAI(api_key=self.api_key, base_url=TOGETHER_BASE_URL)
        aclient = AsyncOpenAI(api_key=self.api_key, base_url=TOGETHER_BASE_URL)
        return client, aclient

    def _extra_body(self, images: list[str] | None, **kwargs) -> dict:
        body: dict = {
            "width": kwargs.get("width", self.width),
            "height": kwargs.get("height", self.height),
            "steps": kwargs.get("steps", self.steps),
        }
        if images:
            body["image_url"] = _encode_image_b64(images[0])
            body["strength"] = kwargs.get("strength", 0.8)
        return body

    def generate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        client, _ = self._make_clients()
        response = client.images.generate(
            model=self._model,
            prompt=prompt,
            n=1,
            extra_body=self._extra_body(images, **kwargs),
        )
        url = response.data[0].url
        urllib.request.urlretrieve(url, output_path)
        logger.debug(f"[ImageTogether] Image saved to {output_path}")

    async def agenerate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        import aiohttp as _aiohttp

        _, aclient = self._make_clients()
        response = await aclient.images.generate(
            model=self._model,
            prompt=prompt,
            n=1,
            extra_body=self._extra_body(images, **kwargs),
        )
        url = response.data[0].url
        async with _aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.read()
        with open(output_path, "wb") as f:
            f.write(data)
        logger.debug(f"[ImageTogether] Async image saved to {output_path}")
