from program.inference.model.types import Cost, Model, Modality

_TEXT       = [Modality.Text]
_TEXT_IMAGE = [Modality.Text, Modality.Image]

models = [
    # OpenAI Codex (OAuth)
    Model(id="gpt-5.5",              name="GPT-5.5",              provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4",              name="GPT-5.4",              provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4-mini",         name="GPT-5.4 Mini",         provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.3-codex",        name="GPT-5.3 Codex",        provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.3-codex-spark",  name="GPT-5.3 Codex Spark",  provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.2",              name="GPT-5.2",              provider="openai-codex", cost=Cost(), thinking=True, context_window=400_000, input=_TEXT_IMAGE, output=_TEXT),
    # Anthropic Claude Code (OAuth)
    Model(id="claude-opus-4-7",           name="Claude Opus 4.7",   provider="anthropic-claude-code", cost=Cost(), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-sonnet-4-6",         name="Claude Sonnet 4.6", provider="anthropic-claude-code", cost=Cost(), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5",  provider="anthropic-claude-code", cost=Cost(),               context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # Google Antigravity (OAuth — free IDE quota)
    Model(id="gemini-3-flash",         name="Gemini 3 Flash",         provider="google-antigravity", cost=Cost(), thinking=True, context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-3.1-pro-preview", name="Gemini 3.1 Pro",       provider="google-antigravity", cost=Cost(), thinking=True, context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-3-flash-preview", name="Gemini 3 Flash",       provider="google-antigravity", cost=Cost(), thinking=True, context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-3.1-flash-lite",  name="Gemini 3.1 Flash Lite",provider="google-antigravity", cost=Cost(),               context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-2.5-pro",        name="Gemini 2.5 Pro",        provider="google-antigravity", cost=Cost(), thinking=True, context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-2.5-flash",      name="Gemini 2.5 Flash",      provider="google-antigravity", cost=Cost(), thinking=True, context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-2.5-flash-lite", name="Gemini 2.5 Flash Lite", provider="google-antigravity", cost=Cost(),               context_window=1_048_576, input=_TEXT_IMAGE, output=_TEXT),
    # GitHub Copilot
    Model(id="gpt-4o",               name="GPT-4o",             provider="github-copilot", cost=Cost(),               context_window=128_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-4o-mini",          name="GPT-4o Mini",        provider="github-copilot", cost=Cost(),               context_window=128_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o3-mini",              name="O3 Mini",            provider="github-copilot", cost=Cost(), thinking=True, context_window=200_000,   input=_TEXT,       output=_TEXT),
    Model(id="claude-3.5-sonnet",    name="Claude 3.5 Sonnet",  provider="github-copilot", cost=Cost(),               context_window=200_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-3.7-sonnet",    name="Claude 3.7 Sonnet",  provider="github-copilot", cost=Cost(), thinking=True, context_window=200_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-2.0-flash-001", name="Gemini 2.0 Flash",   provider="github-copilot", cost=Cost(),               context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="grok-3",               name="Grok 3",             provider="github-copilot", cost=Cost(),               context_window=131_072,   input=_TEXT,       output=_TEXT),
    # OpenAI (API key)
    Model(id="gpt-5.5",      name="GPT-5.5",      provider="openai", cost=Cost(input=5.0,  output=30.0, cache_read=0.50),  thinking=True, context_window=1_050_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4",      name="GPT-5.4",      provider="openai", cost=Cost(input=2.5,  output=15.0, cache_read=0.25),  thinking=True, context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-5.4-mini", name="GPT-5.4 Mini", provider="openai", cost=Cost(input=0.75, output=4.5,  cache_read=0.075), thinking=True, context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    # Anthropic (API key)
    Model(id="claude-opus-4-7",           name="Claude Opus 4.7",   provider="anthropic", cost=Cost(input=15.0, output=75.0, cache_read=1.5,  cache_write=3.75), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-sonnet-4-6",         name="Claude Sonnet 4.6", provider="anthropic", cost=Cost(input=3.0,  output=15.0, cache_read=0.30, cache_write=3.75), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5",  provider="anthropic", cost=Cost(input=0.80, output=4.0,  cache_read=0.08, cache_write=1.0),               context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # NVIDIA NIM
    Model(id="nvidia/llama-3.3-nemotron-super-49b-v1.5",  name="Nemotron Super 49B v1.5", provider="nvidia", cost=Cost(), thinking=True, context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="meta/llama-3.3-70b-instruct",               name="Llama 3.3 70B Instruct",  provider="nvidia", cost=Cost(),               context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="meta/llama-3.1-8b-instruct",                name="Llama 3.1 8B Instruct",   provider="nvidia", cost=Cost(),               context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3-next-80b-a3b-thinking",          name="Qwen3 Next 80B Thinking", provider="nvidia", cost=Cost(), thinking=True, context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3-next-80b-a3b-instruct",          name="Qwen3 Next 80B Instruct", provider="nvidia", cost=Cost(),               context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3.5-397b-a17b",                    name="Qwen3.5 397B A17B",       provider="nvidia", cost=Cost(), thinking=True, context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3.5-122b-a10b",                    name="Qwen3.5 122B A10B",       provider="nvidia", cost=Cost(), thinking=True, context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3-coder-480b-a35b-instruct",       name="Qwen3 Coder 480B",        provider="nvidia", cost=Cost(),               context_window=256_000,   input=_TEXT, output=_TEXT),
    Model(id="deepseek-ai/deepseek-v4-pro",               name="DeepSeek V4 Pro",         provider="nvidia", cost=Cost(), thinking=True, context_window=1_000_000, input=_TEXT, output=_TEXT),
    Model(id="deepseek-ai/deepseek-v4-flash",             name="DeepSeek V4 Flash",       provider="nvidia", cost=Cost(), thinking=True, context_window=1_000_000, input=_TEXT, output=_TEXT),
    Model(id="z-ai/glm-5.1",                              name="GLM-5.1",                 provider="nvidia", cost=Cost(), thinking=True, context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="mistralai/mistral-medium-3.5-128b",         name="Mistral Medium 3.5 128B", provider="nvidia", cost=Cost(),               context_window=128_000,   input=_TEXT, output=_TEXT),
    Model(id="mistralai/mistral-small-4-119b-2603",       name="Mistral Small 4 119B",    provider="nvidia", cost=Cost(),               context_window=256_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="google/gemma-4-31b-it",                     name="Gemma 4 31B IT",          provider="nvidia", cost=Cost(),               context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="minimaxai/minimax-m2.7",                    name="MiniMax M2.7",            provider="nvidia", cost=Cost(),               context_window=131_072,   input=_TEXT, output=_TEXT),
    Model(id="moonshotai/kimi-k2.6",                      name="Kimi K2.6",               provider="nvidia", cost=Cost(),               context_window=131_072,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", name="Nemotron 3 Nano Omni", provider="nvidia", cost=Cost(),              context_window=131_072,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="nvidia/nemotron-3-super-120b-a12b",         name="Nemotron 3 Super 120B",   provider="nvidia", cost=Cost(),               context_window=1_000_000, input=_TEXT, output=_TEXT),
    # Groq (caps max_completion_tokens at 8192)
    Model(id="openai/gpt-oss-120b",                       name="GPT-OSS 120B",          provider="groq", cost=Cost(input=0.15,  output=0.60), thinking=True, context_window=131_072, max_tokens=8192, input=_TEXT, output=_TEXT),
    Model(id="openai/gpt-oss-20b",                        name="GPT-OSS 20B",           provider="groq", cost=Cost(input=0.075, output=0.30), thinking=True, context_window=131_072, max_tokens=8192, input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3-32b",                            name="Qwen3 32B",             provider="groq", cost=Cost(input=0.29,  output=0.59), thinking=True, context_window=131_072, max_tokens=8192, input=_TEXT, output=_TEXT),
    Model(id="llama-3.3-70b-versatile",                   name="Llama 3.3 70B",         provider="groq", cost=Cost(input=0.59,  output=0.79),               context_window=131_072, max_tokens=8192, input=_TEXT, output=_TEXT),
    Model(id="llama-3.1-8b-instant",                      name="Llama 3.1 8B Instant",  provider="groq", cost=Cost(input=0.05,  output=0.08),               context_window=131_072, max_tokens=8192, input=_TEXT, output=_TEXT),
    Model(id="meta-llama/llama-4-scout-17b-16e-instruct", name="Llama 4 Scout 17Bx16E", provider="groq", cost=Cost(input=0.11,  output=0.34),               context_window=131_072, max_tokens=8192, input=_TEXT_IMAGE, output=_TEXT),
    # Mistral
    Model(id="mistral-large-latest",    name="Mistral Large 3",      provider="mistral", cost=Cost(input=0.50, output=1.50),                context_window=262_144, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-medium-latest",   name="Mistral Medium 3.5",   provider="mistral", cost=Cost(input=0.40, output=2.0),                 context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-small-latest",    name="Mistral Small 4",      provider="mistral", cost=Cost(input=0.10, output=0.30), thinking=True, context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="magistral-medium-latest", name="Magistral Medium 1.2", provider="mistral", cost=Cost(input=2.0,  output=5.0),  thinking=True, context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="devstral-latest",         name="Devstral 2",           provider="mistral", cost=Cost(input=0.40, output=2.0),                 context_window=262_144, input=_TEXT,       output=_TEXT),
    Model(id="codestral-latest",        name="Codestral",            provider="mistral", cost=Cost(input=0.30, output=0.90),                context_window=262_144, input=_TEXT,       output=_TEXT),
    # Ollama
    Model(id="deepseek-v4-pro:cloud",    name="DeepSeek V4 Pro Cloud", provider="ollama", cost=Cost(), thinking=True, context_window=1_000_000, input=_TEXT,       output=_TEXT),
    Model(id="deepseek-v4-flash:cloud",  name="DeepSeek V4 Flash Cloud", provider="ollama", cost=Cost(), thinking=True, context_window=1_000_000, input=_TEXT,       output=_TEXT),
    Model(id="deepseek-v3.2:cloud",      name="DeepSeek V3.2 Cloud",  provider="ollama", cost=Cost(), thinking=True, context_window=160_000,   input=_TEXT,       output=_TEXT),
    Model(id="gemma4:31b-cloud",         name="Gemma 4 31B Cloud",    provider="ollama", cost=Cost(), thinking=True, context_window=256_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="glm-5.1:cloud",            name="GLM 5.1 Cloud",        provider="ollama", cost=Cost(), thinking=True, context_window=198_000,   input=_TEXT,       output=_TEXT),
    Model(id="minimax-m2.7:cloud",       name="MiniMax M2.7 Cloud",   provider="ollama", cost=Cost(), thinking=True, context_window=200_000,   input=_TEXT,       output=_TEXT),
    Model(id="kimi-k2.6:cloud",          name="Kimi K2.6 Cloud",      provider="ollama", cost=Cost(), thinking=True, context_window=256_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="qwen3.5:397b-cloud",       name="Qwen 3.5 397B Cloud",  provider="ollama", cost=Cost(), thinking=True, context_window=256_000,   input=_TEXT_IMAGE, output=_TEXT),
]
