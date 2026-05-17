from dataclasses import dataclass, field
from enum import Enum
from program.message.types import Usage, UsageCost


class Modality(str, Enum):
    Text  = "text"
    Image = "image"
    Audio = "audio"
    Video = "video"


@dataclass
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass
class Model:
    id: str
    name: str
    provider: str
    cost: Cost = field(default_factory=Cost)
    thinking: bool = False
    context_window: int = 0
    max_tokens: int = 16384
    input: list[Modality] = field(default_factory=list)
    output: list[Modality] = field(default_factory=list)
    api: str | None = None
    base_url: str | None = None
    voices: list[str] = field(default_factory=list)

    def get_name(self) -> str:
        return self.name

    def get_model_id(self) -> str:
        return self.id

    def get_cost(self) -> Cost:
        return self.cost

    def calculate_cost(self, usage: Usage) -> UsageCost:
        usage.cost.input = (self.cost.input / 1_000_000) * usage.input_tokens
        usage.cost.output = (self.cost.output / 1_000_000) * usage.output_tokens
        usage.cost.cache_read = (self.cost.cache_read / 1_000_000) * usage.cache_read_tokens
        usage.cost.cache_write = (self.cost.cache_write / 1_000_000) * usage.cache_write_tokens
        usage.cost.total = usage.cost.input + usage.cost.output + usage.cost.cache_read + usage.cost.cache_write
        return usage.cost


# Type aliases
TextModel  = Model
ImageModel = Model
AudioModel = Model
VideoModel = Model
