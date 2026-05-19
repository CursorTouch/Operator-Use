from __future__ import annotations

import logging
import time
from pathlib import Path

from program.inference.api.audio.service import AudioLLM
from program.inference.types import AudioFormat, TTSContext

_MEDIA_DIR = Path.home() / '.program' / 'media'

logger = logging.getLogger(__name__)


_FORMAT_EXT: dict[AudioFormat, str] = {
    AudioFormat.MP3: 'mp3',
    AudioFormat.WAV: 'wav',
    AudioFormat.OPUS: 'opus',
    AudioFormat.AAC: 'aac',
    AudioFormat.FLAC: 'flac',
    AudioFormat.PCM: 'pcm',
}


async def _on_message_send(event) -> object:
    from program.hooks.types import MessageSendEvent, MessageSendResult
    from program.bus.types import AudioPart

    if not isinstance(event, MessageSendEvent):
        return None

    if not event.response_text:
        return None

    try:
        from program.settings.manager import SettingsManager
        settings_mgr = SettingsManager.get_instance()
        tts = settings_mgr.get_tts_settings()
    except Exception:
        tts = None

    enabled = tts.enabled if tts else None
    if enabled is False:
        return None
    # enabled=None → only synthesize when the user spoke (voice-originated message)
    if enabled is None and not event.is_voice:
        return None

    model_id = (tts.model if tts else None)
    provider = tts.provider if tts else None
    voice = (tts.voice if tts else None)
    speed = (tts.speed if tts else None)
    language = tts.language if tts else None

    try:
        llm = AudioLLM(model_id, provider=provider)
        result = await llm.synthesize(TTSContext(
            input=event.response_text,
            voice=voice,
            speed=speed,
            response_format=AudioFormat.MP3,
            language=language,
        ))
        fmt_ext = _FORMAT_EXT.get(result.format, 'mp3')
        _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        audio_path = _MEDIA_DIR / f"tts_{int(time.time() * 1000)}.{fmt_ext}"
        audio_path.write_bytes(result.audio)
        logger.debug("TTS: synthesized %d bytes → %s", len(result.audio), audio_path)
        return MessageSendResult(parts=[AudioPart(audio=str(audio_path), mime_type=f'audio/{fmt_ext}')])
    except Exception:
        logger.exception("TTS hook: synthesis failed")
        return None


hooks = [('message:send', _on_message_send)]
