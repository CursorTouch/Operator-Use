from program.image.model.types import Model
from program.llm.model.types import Cost, Modality

_TEXT_IMAGE = [Modality.Text, Modality.Image]
_IMAGE = [Modality.Image]

IMAGE_MODELS: list[Model] = [
    # Black Forest Labs FLUX
    Model(id="black-forest-labs/flux-2-flex",  name="FLUX.2 Flex",       provider="openrouter", cost=Cost(),                         input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-klein", name="FLUX.2 Klein 4B",   provider="openrouter", cost=Cost(),                         input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-max",   name="FLUX.2 Max",        provider="openrouter", cost=Cost(),                         input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="black-forest-labs/flux-2-pro",   name="FLUX.2 Pro",        provider="openrouter", cost=Cost(),                         input=_TEXT_IMAGE, output=_IMAGE),
    # Google Gemini Image
    Model(id="google/gemini-2.5-flash-image-generation",          name="Gemini 2.5 Flash Image",         provider="openrouter", cost=Cost(input=0.30, output=2.50),   input=_TEXT_IMAGE, output=_TEXT_IMAGE),
    Model(id="google/gemini-3-pro-image-generation-preview",      name="Gemini 3 Pro Image Preview",     provider="openrouter", cost=Cost(input=2.00, output=12.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE),
    Model(id="google/gemini-3.1-flash-image-generation-preview",  name="Gemini 3.1 Flash Image Preview", provider="openrouter", cost=Cost(input=0.50, output=3.00),   input=_TEXT_IMAGE, output=_TEXT_IMAGE),
    # OpenAI GPT Image
    Model(id="openai/gpt-5-image",      name="GPT-5 Image",       provider="openrouter", cost=Cost(input=10.00, output=10.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE),
    Model(id="openai/gpt-5-image-mini", name="GPT-5 Image Mini",  provider="openrouter", cost=Cost(input=2.50,  output=2.00),  input=_TEXT_IMAGE, output=_TEXT_IMAGE),
    Model(id="openai/gpt-5.4-image-2",  name="GPT-5.4 Image 2",  provider="openrouter", cost=Cost(input=8.00,  output=15.00), input=_TEXT_IMAGE, output=_TEXT_IMAGE),
    # ByteDance
    Model(id="bytedance/seedream-4.5", name="Seedream 4.5", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # Sourceful Riverflow V2
    Model(id="sourceful/riverflow-v2",       name="Riverflow V2",       provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-turbo", name="Riverflow V2 Turbo", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-max",   name="Riverflow V2 Max",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    Model(id="sourceful/riverflow-v2-pro",   name="Riverflow V2 Pro",   provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_IMAGE),
    # OpenRouter Auto
    Model(id="openrouter/auto", name="OpenRouter Auto", provider="openrouter", cost=Cost(), input=_TEXT_IMAGE, output=_TEXT_IMAGE),
]
