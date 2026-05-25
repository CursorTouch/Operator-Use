from program.memory.provider.types import MemoryProvider
from program.memory.types import MemoryOptions

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
]
