from pathlib import Path
from program.utils import ensure_directory

APP_NAME="Program"
CONFIG_DIR_NAME = ".program"

CONFIG_DIR_PATH=Path.home() / CONFIG_DIR_NAME


def get_config_dir(cwd:Path|None)->Path:
    path = CONFIG_DIR_PATH
    if cwd is not None and cwd.exists():
        path = cwd / CONFIG_DIR_NAME
    return path

def get_agent_dir(cwd:Path|None) -> Path:
    path = get_config_dir(cwd) / "agent"
    return path

def get_settings_path(path:Path|None=None) -> Path:
    path = get_config_dir(path) / "settings.json"
    return path

def get_auth_path() -> Path:
    path = get_config_dir() / "auth.json"
    return path

def get_models_path() -> Path:
    path = get_config_dir() / "models.json"
    return path

def get_sessions_path() -> Path:
    path = get_config_dir() / "sessions"
    return path

def get_prompts_path(cwd:Path|None) -> Path:
    path = get_config_dir(cwd) / "prompts"
    return path

def get_tools_path(cwd:Path|None) -> Path:
    path = get_config_dir(cwd) / "tools"
    return path

def get_themes_path(cwd:Path|None) -> Path:
    path = get_config_dir(cwd) / "themes"
    return path

def get_extensions_path(cwd:Path|None) -> Path:
    path = get_config_dir(cwd) / "extensions"
    return path