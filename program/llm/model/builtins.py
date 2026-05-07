from program.llm.model.types import Cost, Model, Modality

MODELS: list[Model] = [
    Model(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai",
        cost=Cost(input=2.5, output=10.0, cache_read=1.25),
        context_window=128_000,
        input=[Modality.Text, Modality.Image],
        output=[Modality.Text],
    ),
    Model(
        id="gpt-4o-mini",
        name="GPT-4o Mini",
        provider="openai",
        cost=Cost(input=0.15, output=0.6, cache_read=0.075),
        context_window=128_000,
        input=[Modality.Text, Modality.Image],
        output=[Modality.Text],
    ),
    Model(
        id="o3",
        name="O3",
        provider="openai",
        cost=Cost(input=10.0, output=40.0, cache_read=2.5),
        thinking=True,
        context_window=200_000,
        input=[Modality.Text, Modality.Image],
        output=[Modality.Text],
    ),
    Model(
        id="o4-mini",
        name="O4 Mini",
        provider="openai",
        cost=Cost(input=1.1, output=4.4, cache_read=0.275),
        thinking=True,
        context_window=200_000,
        input=[Modality.Text, Modality.Image],
        output=[Modality.Text],
    ),
]
