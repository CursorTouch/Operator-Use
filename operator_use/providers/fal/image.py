import base64
import logging
import mimetypes
import os
import urllib.request
from typing import Optional

from operator_use.providers.base import BaseImage

logger = logging.getLogger(__name__)


def _encode_image_b64(path: str) -> str:
    """Encode a local image file as a base64 data URL."""
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{data}"


class ImageFal(BaseImage):
    """fal.ai image generation and editing provider.

    Uses the fal-client SDK to run FLUX and other models on fal.ai.
    Requires the ``fal-client`` package: ``pip install fal-client``

    Generation (no images):
        Runs the configured model with a text prompt.

    Editing (images provided):
        Switches to the ``image_to_image_model`` endpoint and passes the
        first image as ``image_url``. ``strength`` controls how much the
        output deviates from the input (0.0 = unchanged, 1.0 = fully
        regenerated).

    Args:
        model: The fal model ID for generation (default: "fal-ai/flux/schnell").
            Popular options:
              "fal-ai/flux/schnell"          (fastest, 4 steps)
              "fal-ai/flux/dev"              (higher quality)
              "fal-ai/flux-pro"              (best quality, paid)
              "fal-ai/flux-pro/v1.1"
              "fal-ai/stable-diffusion-v3-medium"
        image_to_image_model: Model used when input images are provided
            (default: "fal-ai/flux/dev/image-to-image").
            Popular options:
              "fal-ai/flux/dev/image-to-image"
              "fal-ai/flux-pro/v1/redux"
              "fal-ai/flux-lora/image-to-image"
        image_size: Output size preset for generation (default: "landscape_4_3").
            Options: "square_hd", "square", "portrait_4_3", "portrait_16_9",
                     "landscape_4_3", "landscape_16_9".
        num_inference_steps: Steps for generation (default: 4 for schnell).
        api_key: fal.ai API key. Falls back to FAL_KEY env variable.

    Example:
        ```python
        from operator_use.providers.fal import ImageFal

        provider = ImageFal()

        # Generate from scratch
        provider.generate("a red panda coding on a laptop", "output.png")

        # Edit with a reference image
        provider.generate("make it sunset", "output.png", images=["input.png"], strength=0.85)
        ```
    """

    def __init__(
        self,
        model: str = "fal-ai/flux/schnell",
        image_to_image_model: str = "fal-ai/flux/dev/image-to-image",
        image_size: str = "landscape_4_3",
        num_inference_steps: int = 4,
        api_key: Optional[str] = None,
    ):
        self._model = model
        self.image_to_image_model = image_to_image_model
        self.image_size = image_size
        self.num_inference_steps = num_inference_steps
        self.api_key = api_key or os.environ.get("FAL_KEY")
        if self.api_key:
            os.environ["FAL_KEY"] = self.api_key

    @property
    def model(self) -> str:
        return self._model

    def _build_arguments(self, prompt: str, images: list[str] | None, **kwargs) -> tuple[str, dict]:
        """Return (endpoint, arguments) depending on whether images are provided."""
        if images:
            endpoint = kwargs.get("image_to_image_model", self.image_to_image_model)
            args = {
                "prompt": prompt,
                "image_url": _encode_image_b64(images[0]),
                "strength": kwargs.get("strength", 0.85),
                "num_inference_steps": kwargs.get("num_inference_steps", 28),
                "num_images": 1,
                "enable_safety_checker": True,
            }
            if kwargs.get("image_size"):
                args["image_size"] = kwargs["image_size"]
        else:
            endpoint = self._model
            args = {
                "prompt": prompt,
                "image_size": kwargs.get("image_size", self.image_size),
                "num_inference_steps": kwargs.get("num_inference_steps", self.num_inference_steps),
                "num_images": 1,
                "enable_safety_checker": True,
            }
        return endpoint, args

    def _save_from_url(self, url: str, output_path: str) -> None:
        urllib.request.urlretrieve(url, output_path)

    def generate(self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs) -> None:
        try:
            import fal_client
        except ImportError:
            raise ImportError("fal-client is required: pip install fal-client")

        endpoint, args = self._build_arguments(prompt, images, **kwargs)
        result = fal_client.run(endpoint, arguments=args)
        url = result["images"][0]["url"]
        self._save_from_url(url, output_path)
        logger.debug(f"[ImageFal] Image saved to {output_path}")

    async def agenerate(self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs) -> None:
        try:
            import fal_client
        except ImportError:
            raise ImportError("fal-client is required: pip install fal-client")

        import aiohttp as _aiohttp

        endpoint, args = self._build_arguments(prompt, images, **kwargs)
        result = await fal_client.run_async(endpoint, arguments=args)
        url = result["images"][0]["url"]
        async with _aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.read()
        with open(output_path, "wb") as f:
            f.write(data)
        logger.debug(f"[ImageFal] Async image saved to {output_path}")
