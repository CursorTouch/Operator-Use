import json

import pytest

from program.settings.manager import SettingsManager


@pytest.mark.asyncio
async def test_persist_default_model_and_provider(tmp_path):
    config_dir = tmp_path / "config"
    cwd = tmp_path / "project"
    cwd.mkdir()

    settings = SettingsManager.create(cwd, config_dir=config_dir)
    settings.set_default_model_settings(model="gpt-5.5", provider="openai")
    await settings.flush()

    data = json.loads((config_dir / "settings.json").read_text())
    assert data["default_model"] == "gpt-5.5"
    assert data["default_provider"] == "openai"


@pytest.mark.asyncio
async def test_persist_default_model_without_provider_preserves_existing_provider(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"default_provider": "anthropic"}),
        encoding="utf-8",
    )
    cwd = tmp_path / "project"
    cwd.mkdir()

    settings = SettingsManager.create(cwd, config_dir=config_dir)
    settings.set_default_model_settings(model="claude-opus-4-7", provider=None)
    await settings.flush()

    data = json.loads((config_dir / "settings.json").read_text())
    assert data["default_model"] == "claude-opus-4-7"
    assert data["default_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_persist_default_provider_without_model_preserves_existing_model(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"default_model": "claude-sonnet-4-6"}),
        encoding="utf-8",
    )
    cwd = tmp_path / "project"
    cwd.mkdir()

    settings = SettingsManager.create(cwd, config_dir=config_dir)
    settings.set_default_model_settings(model=None, provider="anthropic")
    await settings.flush()

    data = json.loads((config_dir / "settings.json").read_text())
    assert data["default_model"] == "claude-sonnet-4-6"
    assert data["default_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_unset_default_model_and_provider(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"default_model": "gpt-5.5", "default_provider": "openai"}),
        encoding="utf-8",
    )
    cwd = tmp_path / "project"
    cwd.mkdir()

    settings = SettingsManager.create(cwd, config_dir=config_dir)
    settings.unset_default_model_settings(model=True, provider=True)
    await settings.flush()

    data = json.loads((config_dir / "settings.json").read_text())
    assert data["default_model"] is None
    assert data["default_provider"] is None


@pytest.mark.asyncio
async def test_unset_default_provider_preserves_existing_model(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"default_model": "gpt-5.5", "default_provider": "openai"}),
        encoding="utf-8",
    )
    cwd = tmp_path / "project"
    cwd.mkdir()

    settings = SettingsManager.create(cwd, config_dir=config_dir)
    settings.unset_default_model_settings(model=False, provider=True)
    await settings.flush()

    data = json.loads((config_dir / "settings.json").read_text())
    assert data["default_model"] == "gpt-5.5"
    assert data["default_provider"] is None
