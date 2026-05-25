from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Literal, Any
from program.engine.types import SteeringMode, FollowupMode
from program.inference.types import Transport, ThinkingLevel
from program.gateway.channels.types import ChannelsSettings
from program.acp.types import ACPSettings


class SCOPE(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"


@dataclass
class LockResult:
    result: Any
    next: str | None = None


@dataclass
class SettingsError:
    scope: SCOPE
    error: Exception


@dataclass
class ExtensionEntry:
    path: str
    name: Optional[str] = None
    enabled: bool = True
    source: Optional[str] = None
    author: Optional[str] = None
    settings: Optional[dict] = field(default=None)


@dataclass
class CompactionSettings:
    enabled: Optional[bool] = None
    reserve_tokens: Optional[int] = None
    keep_recent_tokens: Optional[int] = None


@dataclass
class BranchSummarySettings:
    reserve_tokens: Optional[int] = None
    skip_prompt: Optional[bool] = None


@dataclass
class ProviderRetrySettings:
    timeout_ms: Optional[int] = None
    max_retries: Optional[int] = None
    max_retry_delay_ms: Optional[int] = None


@dataclass
class RetrySettings:
    enabled: Optional[bool] = None
    max_retries: Optional[int] = None
    base_delay_ms: Optional[int] = None
    provider: Optional[ProviderRetrySettings] = None


@dataclass
class ThinkingBudgetsSettings:
    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None
    xhigh: Optional[int] = None
    max: Optional[int] = None


@dataclass
class ImageSettings:
    auto_resize: Optional[bool] = None    # resize images to 2000×2000 max before sending to LLM (default: True)
    block_images: Optional[bool] = None   # prevent all images from being sent to the LLM (default: False)


@dataclass
class STTSettings:
    enabled: Optional[bool] = None       # None = auto (only when AudioPart present), True = always, False = off
    model: Optional[str] = None          # audio model id, e.g. "whisper-1"
    provider: Optional[str] = None       # override provider
    language: Optional[str] = None       # BCP-47 language hint, e.g. "en"


@dataclass
class TTSSettings:
    enabled: Optional[bool] = None       # None = only for voice-originated messages, True = always, False = off
    model: Optional[str] = None          # audio model id, e.g. "tts-1"
    provider: Optional[str] = None       # override provider
    voice: Optional[str] = None          # voice name, e.g. "alloy"
    speed: Optional[float] = None        # playback speed multiplier (default: 1.0)
    language: Optional[str] = None       # target language hint


@dataclass
class MemorySettings:
    enabled: Optional[bool] = None
    provider: Optional[str] = None       # active memory provider id, e.g. "local_file"
    max_prompt_chars: Optional[int] = None
    sync_turns: Optional[bool] = None
    prefetch: Optional[bool] = None


@dataclass
class Settings:
    # Model / provider
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    default_thinking_level: Optional[ThinkingLevel] = None
    transport: Optional[Transport] = None
    enabled_models: Optional[list[str]] = None

    # Queue behaviour
    steering_mode: Optional[SteeringMode] = None
    follow_up_mode: Optional[FollowupMode] = None

    # Nested sub-settings
    compaction: Optional[CompactionSettings] = None
    retry: Optional[RetrySettings] = None
    thinking_budgets: Optional[ThinkingBudgetsSettings] = None
    branch_summary: Optional[BranchSummarySettings] = None
    image: Optional[ImageSettings] = None

    # Resource paths
    packages: Optional[list[str]] = None
    extensions: Optional[bool] = None          # global toggle for all extensions
    extension_list: Optional[list[ExtensionEntry]] = None  # per-extension config
    skills: Optional[list[str]] = None
    prompts: Optional[list[str]] = None

    # Execution
    execute_path: Optional[str] = None
    execute_command_prefix: Optional[str] = None

    # Session
    session_dir: Optional[str] = None

    # Feature flags
    enable_skill_commands: Optional[bool] = None
    cron_enabled: Optional[bool] = None
    unified_session: Optional[bool] = None       # share one session across all channels (default: True)

    # Channels
    channels: Optional[ChannelsSettings] = None

    # MCP servers — list of dicts, each matching MCPServerConfig fields
    mcp_servers: Optional[list[dict]] = None

    # ACP — enabled flag + agent registry
    acp: Optional[ACPSettings] = None

    # Audio I/O
    stt: Optional[STTSettings] = None
    tts: Optional[TTSSettings] = None

    # Memory
    memory: Optional[MemorySettings] = None
