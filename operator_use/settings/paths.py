from pathlib import Path

APP_NAME = "Operator"
CONFIG_DIR_NAME = ".operator"

CONFIG_DIR_PATH = Path.home() / CONFIG_DIR_NAME


# ── Config root ───────────────────────────────────────────────────────────────

def get_config_dir() -> Path:
    return CONFIG_DIR_PATH


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_auth_dir() -> Path:
    return CONFIG_DIR_PATH / 'auth'


def get_providers_auth_path() -> Path:
    return get_auth_dir() / 'providers.json'



def get_acp_auth_path() -> Path:
    return get_auth_dir() / 'acp.json'


# ── Profiles ──────────────────────────────────────────────────────────────────

def get_profiles_dir() -> Path:
    return CONFIG_DIR_PATH / 'profiles'


# ── Global config ─────────────────────────────────────────────────────────────

def get_settings_path() -> Path:
    return CONFIG_DIR_PATH / 'settings.json'



def get_packages_dir() -> Path:
    return CONFIG_DIR_PATH / 'packages'


# ── Gateway ───────────────────────────────────────────────────────────────────

def get_gateway_dir() -> Path:
    return CONFIG_DIR_PATH / 'gateway'


def get_gateway_pid_path() -> Path:
    return get_gateway_dir() / 'gateway.pid'


def get_gateway_stdout_path() -> Path:
    return get_gateway_dir() / 'gateway.out.log'


def get_gateway_stderr_path() -> Path:
    return get_gateway_dir() / 'gateway.err.log'


# ── Docs (shipped with the package) ──────────────────────────────────────────

_DOCS_ROOT = Path(__file__).parent.parent.parent / 'docs'


def get_docs_dir() -> Path:
    return _DOCS_ROOT


# ── Builtins (shipped with the package) ───────────────────────────────────────

_BUILTINS_ROOT = Path(__file__).parent.parent / 'builtins'


def get_builtins_dir() -> Path:
    return _BUILTINS_ROOT


def get_builtins_commands_dir() -> Path:
    return _BUILTINS_ROOT / 'commands'


def get_builtins_tools_dir() -> Path:
    return _BUILTINS_ROOT / 'tools'


def get_builtins_skills_dir() -> Path:
    return _BUILTINS_ROOT / 'skills'


def get_builtins_extensions_dir() -> Path:
    return _BUILTINS_ROOT / 'extensions'


def get_builtins_hooks_dir() -> Path:
    return _BUILTINS_ROOT / 'hooks'


def get_builtins_subagents_dir() -> Path:
    return _BUILTINS_ROOT / 'subagents'


def get_builtins_workflows_dir() -> Path:
    return _BUILTINS_ROOT / 'workflows'
