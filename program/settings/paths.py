from pathlib import Path

APP_NAME = "Program"
CONFIG_DIR_NAME = ".program"
AGENT_DIR_NAME = "agent"

CONFIG_DIR_PATH = Path.home() / CONFIG_DIR_NAME


# ── Config root ───────────────────────────────────────────────────────────────
# ~/.program/  or  <project>/.program/
# Contains user-editable files: settings.json, auth.json, SYSTEM.md, AGENTS.md

def get_config_dir(cwd: Path | None = None) -> Path:
    if cwd is not None and cwd.exists():
        return cwd / CONFIG_DIR_NAME
    return CONFIG_DIR_PATH


# ── User-facing files (at config root, human-editable) ───────────────────────

def get_settings_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "settings.json"


def get_auth_path() -> Path:
    return get_config_dir() / "auth.json"


def get_channels_auth_path() -> Path:
    return get_config_dir() / "channels_auth.json"


def get_system_prompt_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "SYSTEM.md"


def get_append_system_prompt_path(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / "APPEND_SYSTEM.md"


# ── Agent runtime dir ─────────────────────────────────────────────────────────
# ~/.program/agent/  or  <project>/.program/agent/
# Managed by the runtime — extensions, prompts, sessions, tools, skills

def get_agent_dir(cwd: Path | None = None) -> Path:
    return get_config_dir(cwd) / AGENT_DIR_NAME


def get_sessions_dir() -> Path:
    """Global session storage — always ~/.program/agent/sessions/, keyed by project cwd slug."""
    return get_agent_dir() / "sessions"


def get_extensions_dir(cwd: Path | None = None) -> Path:
    return get_agent_dir(cwd) / "extensions"


def get_prompts_dir(cwd: Path | None = None) -> Path:
    return get_agent_dir(cwd) / "prompts"


def get_tools_dir(cwd: Path | None = None) -> Path:
    return get_agent_dir(cwd) / "tools"


def get_skills_dir(cwd: Path | None = None) -> Path:
    return get_agent_dir(cwd) / "skills"


def get_commands_dir(cwd: Path | None = None) -> Path:
    return get_agent_dir(cwd) / "commands"


def get_hooks_dir(cwd: Path | None = None) -> Path:
    return get_agent_dir(cwd) / "hooks"


# ── Builtin resource dirs (shipped with the package) ─────────────────────────

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


def get_crons_path(cwd: Path | None = None) -> Path:
    """Path to crons.json. Project-level if cwd given, else global (~/.program/)."""
    return get_config_dir(cwd) / 'crons.json'


def get_acp_sessions_dir() -> Path:
    """Per-agent ACP session files — always ~/.program/agent/acp/."""
    return get_agent_dir() / 'acp'


def get_knowledge_dir(cwd: Path | None = None) -> Path:
    """Knowledge reference docs — ~/.program/knowledge/ or <project>/.program/knowledge/."""
    return get_config_dir(cwd) / 'knowledge'
