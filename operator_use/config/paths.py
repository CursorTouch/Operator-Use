from pathlib import Path

_DEFAULT_BASE_DIR = ".operator-use"
_CONFIG_FILE_NAME = "config.json"


def get_userdata_dir() -> Path:
    """Return the user data directory: ~/.operator-use"""
    return Path.home() / _DEFAULT_BASE_DIR


def get_config_file() -> Path:
    """Return the configuration file path: ~/.operator-use/config.json"""
    return get_userdata_dir() / _CONFIG_FILE_NAME


def get_workspaces_dir() -> Path:
    """Return the multi-agent workspaces directory: ~/.operator-use/workspaces"""
    return get_userdata_dir() / "workspaces"


def get_named_workspace_dir(name: str) -> Path:
    """Return a named agent's workspace directory: ~/.operator-use/workspaces/<name>"""
    return get_workspaces_dir() / name


def get_media_dir() -> Path:
    """Return the media storage directory: ~/.operator-use/media"""
    return get_userdata_dir() / "media"
