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

    Uses the fal-client SDK to run FLUX, Kontext, Recraft, Ideogram, and
    other models on fal.ai.
    Requires the ``fal-client`` package: ``pip install fal-client``

    Generation (no images):
        Runs the configured ``model`` endpoint with a text prompt.

    Editing (images provided):
        Switches to the ``image_to_image_model`` endpoint and passes the first
        image as ``image_url``. The default editing model is FLUX.1 Kontext Pro
        — an instruction-based editor that follows natural-language edit prompts
        without needing ``strength`` tuning.

    Args:
        model: The fal endpoint for text-to-image generation
            (default: ``"fal-ai/flux-pro/v1.1"``).
            Popular options:
              ``"fal-ai/flux/schnell"``             (fastest, 4 steps)
              ``"fal-ai/flux/dev"``                 (quality, open weights)
              ``"fal-ai/flux-pro/v1.1"``            (recommended quality)
              ``"fal-ai/flux-pro/v1.1-ultra"``      (up to 2K resolution)
              ``"fal-ai/flux-2-pro"``               (latest FLUX.2)
              ``"fal-ai/flux-2-max"``               (highest quality FLUX.2)
              ``"fal-ai/recraft/v3/text-to-image"`` (vectors & typography)
              ``"fal-ai/ideogram/v3"``              (strong typography)
        image_to_image_model: Endpoint used when input images are provided
            (default: ``"fal-ai/flux-pro/kontext"``).
            Popular options:
              ``"fal-ai/flux-pro/kontext"``         (instruction-based, recommended)
              ``"fal-ai/flux-pro/kontext/max"``     (kontext, max quality)
              ``"fal-ai/flux-2-pro/edit"``          (FLUX.2 editing)
              ``"fal-ai/flux-2-flex/edit"``         (FLUX.2 flex editing)
              ``"fal-ai/flux/dev/image-to-image"``  (classic img2img)
        image_size: Output size preset for generation (default: ``"landscape_4_3"``).
            Options: ``"square_hd"``, ``"square"``, ``"portrait_4_3"``,
                     ``"portrait_16_9"``, ``"landscape_4_3"``, ``"landscape_16_9"``.
        num_inference_steps: Steps for generation (default: 4).
        api_key: fal.ai API key. Falls back to ``FAL_KEY`` env variable.

    Example:
        ```python
        from operator_use.providers.fal import ImageFal

        provider = ImageFal()

        # Generate from scratch
        provider.generate("a red panda coding on a laptop", "output.png")

        # Edit with a reference image (uses FLUX.1 Kontext Pro by default)
        provider.generate("make it look like a pencil sketch", "output.png", images=["input.png"])
        ```
    """

    def __init__(
        self,
        model: str = "fal-ai/flux-pro/v1.1",
        image_to_image_model: str = "fal-ai/flux-pro/kontext",
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
            args: dict = {
                "prompt": prompt,
                "image_url": _encode_image_b64(images[0]),
                "num_images": 1,
                "enable_safety_checker": True,
            }
            # strength is relevant for classic img2img but not Kontext — pass only if explicitly set
            if "strength" in kwargs:
                args["strength"] = kwargs["strength"]
            if kwargs.get("num_inference_steps"):
                args["num_inference_steps"] = kwargs["num_inference_steps"]
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

    def generate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
        try:
            import fal_client
        except ImportError:
            raise ImportError("fal-client is required: pip install fal-client")

        endpoint, args = self._build_arguments(prompt, images, **kwargs)
        result = fal_client.run(endpoint, arguments=args)
        url = result["images"][0]["url"]
        urllib.request.urlretrieve(url, output_path)  # nosec B310 — URL from fal API response (HTTPS only)
        logger.debug(f"[ImageFal] Image saved to {output_path}")

    async def agenerate(
        self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs
    ) -> None:
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
