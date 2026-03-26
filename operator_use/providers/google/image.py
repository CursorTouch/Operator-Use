import asyncio
import logging
import os
from typing import Optional

from operator_use.providers.base import BaseImage

logger = logging.getLogger(__name__)


class ImageGoogle(BaseImage):
    """Google Imagen image generation and editing provider.

    Uses the Google GenAI SDK for text-to-image generation (Imagen 3) and
    image editing (requires Vertex AI credentials).

    Generation (no images):
        Uses ``imagen-3.0-generate-002`` via the standard GenAI API key.

    Editing (images provided):
        Uses ``models.edit_image()`` with Vertex AI — requires
        ``GOOGLE_CLOUD_PROJECT`` and ``GOOGLE_CLOUD_LOCATION`` environment
        variables in addition to the API key, and model
        ``imagen-3.0-capability-001``.

    Args:
        model: Generation model (default: "imagen-3.0-generate-002").
        edit_model: Editing model (default: "imagen-3.0-capability-001").
        api_key: Google API key. Falls back to GEMINI_API_KEY env variable.
        negative_prompt: Optional description of what to exclude.
        project: Google Cloud project ID for Vertex AI editing.
            Falls back to GOOGLE_CLOUD_PROJECT env variable.
        location: Google Cloud location for Vertex AI editing.
            Falls back to GOOGLE_CLOUD_LOCATION env variable (default: "us-central1").

    Example:
        ```python
        from operator_use.providers.google import ImageGoogle

        # Generation (standard API key)
        provider = ImageGoogle()
        provider.generate("a red panda coding on a laptop", "output.png")

        # Editing (Vertex AI)
        provider = ImageGoogle(project="my-project")
        provider.generate("make it sunset", "output.png", images=["input.png"])
        ```
    """

    def __init__(
        self,
        model: str = "imagen-3.0-generate-002",
        edit_model: str = "imagen-3.0-capability-001",
        api_key: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        project: Optional[str] = None,
        location: Optional[str] = None,
    ):
        self._model = model
        self.edit_model = edit_model
        self.negative_prompt = negative_prompt
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    @property
    def model(self) -> str:
        return self._model

    def _make_client(self):
        from google import genai
        return genai.Client(api_key=self.api_key)

    def _make_vertex_client(self):
        from google import genai
        if not self.project:
            raise ValueError(
                "Google image editing requires a Vertex AI project. "
                "Set GOOGLE_CLOUD_PROJECT env variable or pass project= to ImageGoogle()."
            )
        return genai.Client(vertexai=True, project=self.project, location=self.location)

    def generate(self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs) -> None:
        from google import genai

        if images:
            client = self._make_vertex_client()
            reference_images = [
                genai.types.RawReferenceImage(
                    reference_id=i + 1,
                    reference_image=genai.types.Image.from_file(path),
                )
                for i, path in enumerate(images)
            ]
            config = genai.types.EditImageConfig(
                edit_mode=kwargs.get("edit_mode", "EDIT_MODE_DEFAULT"),
                number_of_images=1,
                output_mime_type="image/png",
                negative_prompt=kwargs.get("negative_prompt", self.negative_prompt),
            )
            response = client.models.edit_image(
                model=self.edit_model,
                prompt=prompt,
                reference_images=reference_images,
                config=config,
            )
            image_bytes = response.generated_images[0].image.image_bytes
        else:
            client = self._make_client()
            config = genai.types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/png",
                negative_prompt=kwargs.get("negative_prompt", self.negative_prompt),
            )
            response = client.models.generate_images(
                model=self._model,
                prompt=prompt,
                config=config,
            )
            image_bytes = response.generated_images[0].image.image_bytes

        with open(output_path, "wb") as f:
            f.write(image_bytes)
        logger.debug(f"[ImageGoogle] Image saved to {output_path}")

    async def agenerate(self, prompt: str, output_path: str, images: list[str] | None = None, **kwargs) -> None:
        await asyncio.to_thread(self.generate, prompt, output_path, images, **kwargs)
