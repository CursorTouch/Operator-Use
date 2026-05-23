from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from program.inference.api.audio.service import AudioLLM
from program.inference.types import AudioFormat, STTContext

logger = logging.getLogger(__name__)

_MIME_TO_FORMAT: dict[str, AudioFormat] = {
    'audio/ogg': AudioFormat.OPUS,
    'audio/opus': AudioFormat.OPUS,
    'audio/mpeg': AudioFormat.MP3,
    'audio/mp3': AudioFormat.MP3,
    'audio/wav': AudioFormat.WAV,
    'audio/x-wav': AudioFormat.WAV,
    'audio/aac': AudioFormat.AAC,
    'audio/flac': AudioFormat.FLAC,
    'audio/mp4': AudioFormat.MP3,
    'audio/webm': AudioFormat.OPUS,
}


def _mime_to_format(mime_type: str | None) -> AudioFormat:
    if not mime_type:
        return AudioFormat.MP3
    return _MIME_TO_FORMAT.get(mime_type.lower().split(';')[0].strip(), AudioFormat.MP3)


async def _on_message_receive(event) -> object:
    from program.hooks.types import MessageReceiveEvent, MessageReceiveResult
    from program.bus.types import AudioPart, TextPart

    if not isinstance(event, MessageReceiveEvent):
        return None

    audio_parts = [p for p in event.parts if isinstance(p, AudioPart)]
    if not audio_parts:
        return None

    try:
        from program.settings.manager import SettingsManager
        settings_mgr = SettingsManager.get_instance()
        stt = settings_mgr.get_stt_settings()
    except Exception:
        stt = None

    if stt and stt.enabled is False:
        return None

    model_id = (stt.model if stt and stt.model else "whisper-1")
    provider = stt.provider if stt else None
    language = stt.language if stt else None

    new_parts: list = []
    any_failed = False

    for p in event.parts:
        if not isinstance(p, AudioPart):
            new_parts.append(p)
            continue
        audio_path = Path(p.audio)
        if not audio_path.exists():
            logger.warning("STT hook: audio file not found: %s", audio_path)
            new_parts.append(TextPart(f"[Audio file: {audio_path}]"))
            any_failed = True
            continue
        try:
            audio_bytes = audio_path.read_bytes()
            fmt = _mime_to_format(p.mime_type)
            llm = AudioLLM(model_id, provider=provider)
            result = await llm.transcribe(STTContext(audio=audio_bytes, format=fmt, language=language))
            if result.text:
                new_parts.append(TextPart(result.text))
                logger.debug("STT: transcribed %d chars from %s", len(result.text), audio_path.name)
            else:
                logger.warning("STT hook: empty transcription for %s", audio_path.name)
                new_parts.append(TextPart(f"[Audio file: {audio_path}]"))
                any_failed = True
        except Exception:
            logger.exception("STT hook: transcription failed for %s", audio_path)
            new_parts.append(TextPart(f"[Audio file: {audio_path}]"))
            any_failed = True

    if any_failed:
        # Partial or full failure — still forward the message with whatever we have
        return MessageReceiveResult(action='transform', parts=new_parts)

    return MessageReceiveResult(action='transform', parts=new_parts)


hooks = [('message:receive', _on_message_receive)]
