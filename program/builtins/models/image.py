from program.inference.model.types import Cost, Model, Modality

_TEXT           = [Modality.Text]
_IMAGE          = [Modality.Image]
_TEXT_IMAGE     = [Modality.Text, Modality.Image]
_TEXT_IMAGE_OUT = [Modality.Text, Modality.Image]

models = [
    # OpenAI DALL-E
    Model(id="dall-e-3", name="DALL-E 3", provider="openai", cost=Cost(input=40.0), input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="dall-e-2", name="DALL-E 2", provider="openai", cost=Cost(input=20.0), input=_TEXT, output=_IMAGE, api="openai-image"),
    # Together AI
    Model(id="black-forest-labs/FLUX.1-schnell-Free", name="FLUX.1 Schnell Free", provider="together", cost=Cost(),            input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="black-forest-labs/FLUX.1-schnell",      name="FLUX.1 Schnell",      provider="together", cost=Cost(input=0.053), input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="black-forest-labs/FLUX.1-dev",          name="FLUX.1 Dev",          provider="together", cost=Cost(input=0.35),  input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="black-forest-labs/FLUX.1.1-pro",        name="FLUX.1.1 Pro",        provider="together", cost=Cost(input=0.40),  input=_TEXT, output=_IMAGE, api="openai-image"),
    # Fireworks AI
    Model(id="accounts/fireworks/models/flux-1-schnell-fp8", name="FLUX.1 Schnell FP8", provider="fireworks", cost=Cost(input=0.053), input=_TEXT, output=_IMAGE, api="openai-image"),
    Model(id="accounts/fireworks/models/flux-1-dev-fp8",     name="FLUX.1 Dev FP8",     provider="fireworks", cost=Cost(input=0.35),  input=_TEXT, output=_IMAGE, api="openai-image"),
    # Black Forest Labs FLUX via OpenRouter
    Model(id="black-forest-labs/flux-2-flex",  name="FLUX.2 Flex",     provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-klein", name="FLUX.2 Klein 4B", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-max",   name="FLUX.2 Max",      provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-pro",   name="FLUX.2 Pro",      provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Google Imagen (native gemini-image API)
    Model(id="imagen-3.0-generate-002",      name="Imagen 3",      provider="google", cost=Cost(input=0.04), input=_TEXT, output=_IMAGE, api="gemini-image"),
    Model(id="imagen-3.0-fast-generate-001", name="Imagen 3 Fast", provider="google", cost=Cost(input=0.02), input=_TEXT, output=_IMAGE, api="gemini-image"),
    # Google Gemini Image via OpenRouter
    Model(id="google/gemini-2.5-flash-image-generation",         name="Gemini 2.5 Flash Image",         provider="openrouter", cost=Cost(input=0.30,  output=2.50),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="google/gemini-3-pro-image-generation-preview",     name="Gemini 3 Pro Image Preview",     provider="openrouter", cost=Cost(input=2.00,  output=12.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="google/gemini-3.1-flash-image-generation-preview", name="Gemini 3.1 Flash Image Preview", provider="openrouter", cost=Cost(input=0.50,  output=3.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    # OpenAI GPT Image via OpenRouter
    Model(id="openai/gpt-5-image",      name="GPT-5 Image",      provider="openrouter", cost=Cost(input=10.00, output=10.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="openai/gpt-5-image-mini", name="GPT-5 Image Mini", provider="openrouter", cost=Cost(input=2.50,  output=2.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    Model(id="openai/gpt-5.4-image-2",  name="GPT-5.4 Image 2",  provider="openrouter", cost=Cost(input=8.00,  output=15.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE_OUT),
    # ByteDance
    Model(id="bytedance/seedream-4.5", name="Seedream 4.5", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Sourceful Riverflow via OpenRouter
    Model(id="sourceful/riverflow-v2",       name="Riverflow V2",       provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-turbo", name="Riverflow V2 Turbo", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-max",   name="Riverflow V2 Max",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-pro",   name="Riverflow V2 Pro",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
]
