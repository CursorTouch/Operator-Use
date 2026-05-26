from pydantic import BaseModel


class ContextPruningSettings(BaseModel):
    enabled: bool = False                  # off by default; opt-in per profile
    min_prune_chars: int = 200             # ignore results shorter than this
    soft_trim_chars: int = 2000            # results longer than this are soft-trimmed (head…tail)
    soft_trim_head: int = 200              # chars to keep at the start of a soft-trimmed result
    soft_trim_tail: int = 200              # chars to keep at the end of a soft-trimmed result
    protected_tail_tokens: int = 40_000   # tool results in the most recent N tokens are never touched
    placeholder: str = "[tool output cleared to save context]"
