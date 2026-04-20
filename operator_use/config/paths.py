from pathlib import Path

_DEFAULT_BASE_DIR = ".operator-use"
_CONFIG_FILE_NAME = "config.json"


def get_userdata_dir() -> Path:
    """Return the user data directory: ~/.operator-use"""
    return Path.home() / _DEFAULT_BASE_DIR


def get_config_file() -> Path:
    """Return the configuration file path: ~/.operator-use/config.json"""
    return get_userdata_dir() / _CONFIG_FILE_NAME


def get_profiles_dir() -> Path:
    """Return the multi-agent profiles directory: ~/.operator-use/profiles"""
    return get_userdata_dir() / "profiles"


def get_named_profile_dir(name: str) -> Path:
    """Return a named agent's profile directory: ~/.operator-use/profiles/<name>"""
    return get_profiles_dir() / name


def get_named_workspace_dir(name: str) -> Path:
    """Backward-compatible alias for get_named_profile_dir()."""
    return get_named_profile_dir(name)


def get_media_dir() -> Path:
    """Return the media storage directory: ~/.operator-use/media"""
    return get_userdata_dir() / "media"
