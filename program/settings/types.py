from dataclasses import dataclass  
from typing import Optional, TypeVar,Literal
from program.llm.types import ThinkingLevel, TransportType
from program.agent.types import FollowupMode, SteeringMode

SCOPE = Literal["global", "project"]

T = TypeVar('T')  
  
@dataclass  
class LockResult:  
    result: T  
    next: str | None = None  

@dataclass  
class SettingsError:  
    scope: SCOPE  
    error: Exception  
  
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
class Settings:  
    # Agent Settings - LLM interaction and agent behavior  
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    default_thinking_level: Optional[ThinkingLevel] = None
    transport: Optional[TransportType] = None
    steering_mode: Optional[SteeringMode] = None
    follow_up_mode: Optional[FollowupMode] = None
    compaction: Optional[CompactionSettings] = None
    retry: Optional[RetrySettings] = None
    thinking_budgets: Optional[ThinkingBudgetsSettings] = None
    enabled_models: Optional[list[str]] = None
    branch_summary: Optional[BranchSummarySettings] = None
    session_dir: Optional[str] = None
    packages: Optional[list] = None
    extensions: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    prompts: Optional[list[str]] = None
    enable_skill_commands: Optional[bool] = None