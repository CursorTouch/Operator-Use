from pydantic import BaseModel


class SlidingWindowCompactionSettings(BaseModel):
    enabled: bool = True
    trigger_percent: float = 0.5   # fire when context exceeds this fraction (0.5 = 50%)
    batch_tokens: int = 10_000     # max tokens to compact per round
    keep_recent_tokens: int = 20_000  # tail always preserved verbatim
