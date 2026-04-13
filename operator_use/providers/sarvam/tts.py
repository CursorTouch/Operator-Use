import os
import logging
import base64
from typing import Optional

from sarvamai import SarvamAI, AsyncSarvamAI

from operator_use.providers.base import BaseTTS

logger = logging.getLogger(__name__)


class TTSSarvam(BaseTTS):
    """Sarvam AI Text-to-Speech provider using official SDK.

    Uses the sarvamai Python SDK to generate spoken audio from text.

    Supported models:
        - bulbul:v3 (recommended)

    Available voices (bulbul:v3):
        aditya, ritu, ashutosh, priya, neha, rahul, pooja, rohan, simran, kavya,
        amit, dev, ishita, shreya, ratan, varun, manan, sumit, roopa, kabir, aayan,
        shubh, advait, amelia, sophia, anand, tanya, tarun, sunny, mani, gokul,
        vijay, shruti, suhani, mohit, kavitha, rehan, soham, rupali

    Args:
        model: The TTS model to use (default: "bulbul:v3").
        voice: The voice to use for synthesis (default: "anushka").
        language: Target language code, e.g. "en-IN", "hi-IN" (default: "en-IN").
        api_key: Sarvam API key. Falls back to SARVAM_API_KEY env variable.
        speed: Playback speed multiplier / pace (default: 1.0).
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        model: str = "bulbul:v3",
        voice: str = "anand",
        language: str = "en-IN",
        api_key: Optional[str] = None,
        speed: float = 1.0,
        timeout: float = 120.0,
    ):
        self._model = model
        self.voice = voice
        self.language = language
        self.speed = speed
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY")

        if not self.api_key:
            logger.warning("SARVAM_API_KEY is not set.")

        self.client = SarvamAI(api_subscription_key=self.api_key or "", timeout=self.timeout)
        self.aclient = AsyncSarvamAI(api_subscription_key=self.api_key or "", timeout=self.timeout)

    @property
    def model(self) -> str:
        return self._model

    def synthesize(self, text: str, output_path: str) -> None:
        """Synthesize text into an audio file using Sarvam AI SDK.

        Args:
            text: The text to convert to speech.
            output_path: Path where the generated audio file will be saved.
        """
        response = self.client.text_to_speech.convert(
            text=text,
            target_language_code=self.language,
            speaker=self.voice,
            model=self._model,
            pace=self.speed,
        )

        if response.audios and len(response.audios) > 0:
            audio_base64 = response.audios[0]
            audio_bytes = base64.b64decode(audio_base64)
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            logger.debug(f"[TTSSarvam] Audio saved to {output_path}")
        else:
            logger.error(f"[TTSSarvam] No audio data in response: {response}")

    async def asynthesize(self, text: str, output_path: str) -> None:
        """Asynchronously synthesize text into an audio file using Sarvam AI SDK.

        Args:
            text: The text to convert to speech.
            output_path: Path where the generated audio file will be saved.
        """
        response = await self.aclient.text_to_speech.convert(
            text=text,
            target_language_code=self.language,
            speaker=self.voice,
            model=self._model,
            pace=self.speed,
        )

        if response.audios and len(response.audios) > 0:
            audio_base64 = response.audios[0]
            audio_bytes = base64.b64decode(audio_base64)
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
            logger.debug(f"[TTSSarvam] Async audio saved to {output_path}")
        else:
            logger.error(f"[TTSSarvam] No audio data in response: {response}")
