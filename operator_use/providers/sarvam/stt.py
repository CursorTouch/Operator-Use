import os
import logging
from typing import Optional

from sarvamai import SarvamAI, AsyncSarvamAI

from operator_use.providers.base import BaseSTT

logger = logging.getLogger(__name__)


class STTSarvam(BaseSTT):
    """Sarvam AI Speech-to-Text provider using official SDK.

    Uses the sarvamai Python SDK to transcribe audio files.

    Supported models:
        - saaras:v3 (recommended)

    Args:
        model: The STT model to use (default: "saaras:v3").
        api_key: Sarvam API key. Falls back to SARVAM_API_KEY env variable.
        language: Target language code for translation/transcription (e.g. "hi-IN", "en-IN"). Optional.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        model: str = "saaras:v3",
        api_key: Optional[str] = None,
        language: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self._model = model
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY")
        self.language = language
        self.timeout = timeout

        if not self.api_key:
            logger.warning("SARVAM_API_KEY is not set.")

        self.client = SarvamAI(api_subscription_key=self.api_key or "", timeout=self.timeout)
        self.aclient = AsyncSarvamAI(api_subscription_key=self.api_key or "", timeout=self.timeout)

    @property
    def model(self) -> str:
        return self._model

    def transcribe(self, file_path: str) -> str:
        """Transcribe an audio file using Sarvam AI SDK.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text from the audio.
        """
        with open(file_path, "rb") as f:
            response = self.client.speech_to_text.translate(
                file=f,
                model=self._model,
                # prompt=self.language if self.language else None # SDK might handle language differently
            )
            transcript = response.transcript
            logger.debug(f"[STTSarvam] Transcription complete: {len(transcript)} chars")
            return transcript

    async def atranscribe(self, file_path: str) -> str:
        """Asynchronously transcribe an audio file using Sarvam AI SDK.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text from the audio.
        """
        with open(file_path, "rb") as f:
            # Note: For async SDK, some might expect the file content or a byte stream
            # Let's check if it accepts the file object
            response = await self.aclient.speech_to_text.translate(
                file=f,
                model=self._model,
            )
            transcript = response.transcript
            logger.debug(f"[STTSarvam] Async transcription complete: {len(transcript)} chars")
            return transcript
