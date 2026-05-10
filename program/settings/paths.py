from pathlib import Path
from program.utils import ensure_directory

APP_NAME="Program"
CONFIG_DIR_NAME = ".program"

CONFIG_DIR=Path.home() / CONFIG_DIR_NAME


def get_config_dir()->Path:
    ensure_directory(CONFIG_DIR)
    return CONFIG_DIR

def get_agent_dir() -> Path:
    path = CONFIG_DIR / "agent"
    ensure_directory(path)
    return path

def get_settings_path() -> Path:
    path = get_config_dir() / "settings.json"
    return path

def get_auth_path() -> Path:
    path = get_config_dir() / "auth.json"
    return path

def get_models_path() -> Path:
    path = get_config_dir() / "models.json"
    return path

def get_session_path() -> Path:
    path = get_agent_dir() / "sessions"
    ensure_directory(path)
    return path

def get_prompts_path() -> Path:
    path = get_agent_dir() / "prompts"
    ensure_directory(path)
    return path

def get_tools_path() -> Path:
    path = get_agent_dir() / "tools"
    ensure_directory(path)
    return path