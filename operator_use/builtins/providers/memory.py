from operator_use.memory.provider.types import MemoryProvider
from operator_use.memory.types import MemoryOptions

providers = [
    MemoryProvider(
        id="mem0",
        name="Mem0",
        api="mem0",
        options=MemoryOptions(api_key_env="MEM0_API_KEY"),
    ),
    MemoryProvider(
        id="supermemory",
        name="Supermemory",
        api="supermemory",
        options=MemoryOptions(api_key_env="SUPERMEMORY_API_KEY"),
    ),
    MemoryProvider(
        id="hindsight",
        name="Hindsight",
        api="hindsight",
        options=MemoryOptions(
            api_key_env="HINDSIGHT_API_KEY",
            config={"bank_id": "operator", "budget": "mid", "prefetch_method": "recall"},
        ),
    ),
    MemoryProvider(
        id="holographic",
        name="Holographic",
        api="holographic",
        options=MemoryOptions(config={"default_trust": 0.5}),
    ),
    MemoryProvider(
        id="openviking",
        name="OpenViking",
        api="openviking",
        options=MemoryOptions(
            api_key_env="OPENVIKING_API_KEY",
            config={"target_uri": "viking://memory/"},
        ),
    ),
    MemoryProvider(
        id="local",
        name="Local",
        api="local",
        options=MemoryOptions(),
    ),
]
