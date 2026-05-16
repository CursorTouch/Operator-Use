from program.inference.model.types import Cost, Model, Modality

_TEXT_IMAGE = [Modality.Text, Modality.Image]
_TEXT = [Modality.Text]
_IMAGE = [Modality.Image]
_TEXT_IMAGE_OUT = [Modality.Text, Modality.Image]

LLM_MODELS: list[Model] = [
    # OpenAI Codex (OAuth)
    Model(id="gpt-4o",      name="GPT-4o",      provider="openai-codex", cost=Cost(), context_window=128_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai-codex", cost=Cost(), context_window=128_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o3",      name="O3",      provider="openai-codex", cost=Cost(), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o4-mini", name="O4 Mini", provider="openai-codex", cost=Cost(), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # Anthropic Claude Code (OAuth)
    Model(id="claude-opus-4-7",          name="Claude Opus 4.7",   provider="anthropic-claude-code", cost=Cost(), thinking=True,  context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-sonnet-4-6",        name="Claude Sonnet 4.6", provider="anthropic-claude-code", cost=Cost(), thinking=True,  context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5", provider="anthropic-claude-code", cost=Cost(), context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # GitHub Copilot
    Model(id="gpt-4o",              name="GPT-4o",               provider="github-copilot", cost=Cost(), context_window=128_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-4o-mini",         name="GPT-4o Mini",          provider="github-copilot", cost=Cost(), context_window=128_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o3-mini",             name="O3 Mini",              provider="github-copilot", cost=Cost(), thinking=True, context_window=200_000,   input=_TEXT, output=_TEXT),
    Model(id="claude-3.5-sonnet",   name="Claude 3.5 Sonnet",    provider="github-copilot", cost=Cost(), context_window=200_000,   input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-3.7-sonnet",   name="Claude 3.7 Sonnet",    provider="github-copilot", cost=Cost(), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gemini-2.0-flash-001", name="Gemini 2.0 Flash",    provider="github-copilot", cost=Cost(), context_window=1_000_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="grok-3",              name="Grok 3",               provider="github-copilot", cost=Cost(), context_window=131_072,   input=_TEXT, output=_TEXT),
    # OpenAI (API key)
    Model(id="gpt-4o",      name="GPT-4o",      provider="openai", cost=Cost(input=2.5,   output=10.0,  cache_read=1.25),          context_window=128_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai", cost=Cost(input=0.15,  output=0.6,   cache_read=0.075),         context_window=128_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o3",          name="O3",          provider="openai", cost=Cost(input=10.0,  output=40.0,  cache_read=2.5),   thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="o4-mini",     name="O4 Mini",     provider="openai", cost=Cost(input=1.1,   output=4.4,   cache_read=0.275), thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # Anthropic (API key)
    Model(id="claude-opus-4-7",           name="Claude Opus 4.7",   provider="anthropic", cost=Cost(input=15.0, output=75.0, cache_read=1.5,   cache_write=3.75),  thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-sonnet-4-6",         name="Claude Sonnet 4.6", provider="anthropic", cost=Cost(input=3.0,  output=15.0, cache_read=0.30,  cache_write=3.75),  thinking=True, context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5",  provider="anthropic", cost=Cost(input=0.80, output=4.0,  cache_read=0.08,  cache_write=1.0),               context_window=200_000, input=_TEXT_IMAGE, output=_TEXT),
    # NVIDIA
    Model(id="nvidia/llama-3.1-nemotron-70b-instruct",    name="Nemotron 70B Instruct", provider="nvidia", cost=Cost(input=0.35, output=0.40),          context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="nvidia/nemotron-3-super-120b-a12b",         name="Nemotron 3 Super 120B", provider="nvidia", cost=Cost(input=0.45, output=0.55),          context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="nvidia/llama-3.3-nemotron-super-49b-v1",    name="Nemotron Super 49B",    provider="nvidia", cost=Cost(input=0.23, output=0.42), thinking=True, context_window=131_072, input=_TEXT, output=_TEXT),
    Model(id="meta/llama-3.3-70b-instruct",               name="Llama 3.3 70B Instruct",provider="nvidia", cost=Cost(input=0.23, output=0.42),          context_window=128_000, input=_TEXT, output=_TEXT),
    Model(id="meta/llama-3.1-8b-instruct",                name="Llama 3.1 8B Instruct", provider="nvidia", cost=Cost(input=0.10, output=0.10),          context_window=128_000, input=_TEXT, output=_TEXT),
    Model(id="qwen/qwen3.5-397b-a17b",                    name="Qwen 3.5 397B",         provider="nvidia", cost=Cost(input=0.90, output=0.90), thinking=True, context_window=131_072, input=_TEXT, output=_TEXT),
    # Mistral
    Model(id="mistral-medium-3-5",    name="Mistral Medium 3.5", provider="mistral", cost=Cost(input=0.80, output=4.0,  cache_read=0.08, cache_write=1.0), context_window=256_000, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-large-latest",  name="Mistral Large",      provider="mistral", cost=Cost(input=2.0,  output=6.0),                                   context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-medium-latest", name="Mistral Medium",     provider="mistral", cost=Cost(input=0.40, output=2.0),                                   context_window=131_072, input=_TEXT_IMAGE, output=_TEXT),
    Model(id="mistral-small-latest",  name="Mistral Small",      provider="mistral", cost=Cost(input=0.10, output=0.30),                                  context_window=32_768,  input=_TEXT_IMAGE, output=_TEXT),
    Model(id="codestral-latest",      name="Codestral",          provider="mistral", cost=Cost(input=0.30, output=0.90),                                  context_window=262_144, input=_TEXT, output=_TEXT),
    Model(id="mistral-saba-latest",   name="Mistral Saba",       provider="mistral", cost=Cost(input=0.20, output=0.60),                                  context_window=32_768,  input=_TEXT, output=_TEXT),
]

IMAGE_MODELS: list[Model] = [
    # Black Forest Labs FLUX
    Model(id="black-forest-labs/flux-2-flex",  name="FLUX.2 Flex",       provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-klein", name="FLUX.2 Klein 4B",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-max",   name="FLUX.2 Max",        provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-pro",   name="FLUX.2 Pro",        provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Google Gemini Image
    Model(id="google/gemini-2.5-flash-image-generation",         name="Gemini 2.5 Flash Image",         provider="openrouter", cost=Cost(input=0.30,  output=2.50),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="google/gemini-3-pro-image-generation-preview",     name="Gemini 3 Pro Image Preview",     provider="openrouter", cost=Cost(input=2.00,  output=12.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="google/gemini-3.1-flash-image-generation-preview", name="Gemini 3.1 Flash Image Preview", provider="openrouter", cost=Cost(input=0.50,  output=3.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    # OpenAI GPT Image
    Model(id="openai/gpt-5-image",      name="GPT-5 Image",      provider="openrouter", cost=Cost(input=10.00, output=10.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="openai/gpt-5-image-mini", name="GPT-5 Image Mini", provider="openrouter", cost=Cost(input=2.50,  output=2.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="openai/gpt-5.4-image-2",  name="GPT-5.4 Image 2", provider="openrouter", cost=Cost(input=8.00,  output=15.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    # ByteDance
    Model(id="bytedance/seedream-4.5", name="Seedream 4.5", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Sourceful Riverflow V2
    Model(id="sourceful/riverflow-v2",       name="Riverflow V2",       provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-turbo", name="Riverflow V2 Turbo", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-max",   name="Riverflow V2 Max",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-pro",   name="Riverflow V2 Pro",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # OpenRouter Auto
    Model(id="openrouter/auto", name="OpenRouter Auto", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
]
