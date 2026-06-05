from __future__ import annotations

import logging
from pathlib import Path

from operator_use.inference.api.audio.service import AudioLLM
from operator_use.inference.types import AudioFormat, STTContext

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
    """Convert MIME type string to AudioFormat enum, defaulting to MP3."""
    if not mime_type:
        return AudioFormat.MP3
    return _MIME_TO_FORMAT.get(mime_type.lower().split(';')[0].strip(), AudioFormat.MP3)


async def _on_message_receive(event) -> object:
    """Transform incoming audio messages to text via STT before agent processing."""
    from operator_use.hooks.types import MessageReceiveEvent, MessageReceiveResult
    from operator_use.bus.types import AudioPart, TextPart

    if not isinstance(event, MessageReceiveEvent):
        return None

    audio_parts = [p for p in event.parts if isinstance(p, AudioPart)]
    if not audio_parts:
        return None

    try:
        from operator_use.settings.manager import SettingsManager
        settings_mgr = SettingsManager.get_instance()
        stt = settings_mgr.get_stt_settings() if settings_mgr is not None else None
        aux_stt = settings_mgr.get_auxiliary_task("stt") if settings_mgr is not None else None
    except Exception:
        stt = None
        aux_stt = None

    # STT is meaningless in the terminal REPL — no audio can arrive there.
    if event.channel_id == 'stdio':
        return None

    # Profile-level override takes precedence over global settings.
    enabled = event.stt_enabled if event.stt_enabled is not None else (stt.enabled if stt else None)
    if enabled is False:
        return None

    model_id = (aux_stt.model if aux_stt and aux_stt.model else "whisper-1")
    provider = aux_stt.provider if aux_stt else None
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
            from operator_use.inference.api.audio.utils import to_wav_stt
            import asyncio
            wav_path = await asyncio.get_event_loop().run_in_executor(None, to_wav_stt, audio_path)
            audio_bytes = wav_path.read_bytes()
            fmt = AudioFormat.WAV if wav_path.suffix.lower() == '.wav' else _mime_to_format(p.mime_type)
            llm = AudioLLM(model_id, provider=provider)
            context = STTContext(audio=audio_bytes, format=fmt, language=language)
            result = await llm.transcribe(context)
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
