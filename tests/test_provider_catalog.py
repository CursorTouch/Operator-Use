from operator_use.auth.providers import ProviderAuthManager
from operator_use.inference.model.registry import ModelRegistry
from operator_use.inference.provider.registry import TextProviderRegistry


def test_new_text_providers_are_registered():
    registry = TextProviderRegistry.from_builtins()

    for provider_id in ("bedrock", "kimi", "minimax", "deepseek", "kilocode"):
        assert registry.get(provider_id) is not None


def test_new_provider_models_are_registered():
    registry = ModelRegistry.from_llm_builtins()

    expected = [
        ("bedrock", "openai.gpt-oss-120b"),
        ("kimi", "kimi-k2.6"),
        ("minimax", "MiniMax-M2.7"),
        ("deepseek", "deepseek-v4-pro"),
        ("kilocode", "kilo-auto/balanced"),
    ]
    for provider_id, model_id in expected:
        model = registry.get(model_id, provider=provider_id)
        assert model is not None
        assert model.provider == provider_id


async def test_kilocode_accepts_kilo_api_key_alias(monkeypatch):
    registry = TextProviderRegistry.from_builtins()
    manager = ProviderAuthManager.in_memory(registry)

    monkeypatch.setenv("KILO_API_KEY", "kilo-test-key")

    status = manager.get_auth_status("kilocode")
    assert status.configured is True
    assert status.source == "env"
    assert status.label == "KILO_API_KEY"
    assert await manager.get_api_key("kilocode") == "kilo-test-key"
