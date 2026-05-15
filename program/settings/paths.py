from pathlib import Path

APP_NAME = "Program"
CONFIG_DIR_NAME = ".program"

CONFIG_DIR_PATH = Path.home() / CONFIG_DIR_NAME


def get_config_dir(cwd: Path | None = None) -> Path:
    if cwd is not None and cwd.exists():
        return cwd / CONFIG_DIR_NAME
    return CONFIG_DIR_PATH


def get_agent_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "agent"


def get_settings_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "settings.json"


def get_auth_path() -> Path:
    return get_config_dir() / "auth.json"


def get_models_path() -> Path:
    return get_config_dir() / "models.json"


def get_sessions_dir() -> Path:
    return get_config_dir() / "sessions"


def get_prompts_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "prompts"


def get_tools_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "tools"


def get_themes_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "themes"


def get_extensions_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "extensions"
