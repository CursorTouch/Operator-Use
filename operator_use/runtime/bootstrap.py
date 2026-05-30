"""Bootstrap — ensure ~/.operator/ and profile directories exist with default configs.

Two public functions:
  bootstrap_global_dir()     — idempotent; creates global ~/.operator/ structure
  bootstrap_profile(dir, name, description) — idempotent; scaffolds a profile dir

Both functions only write files that do not already exist (never overwrite).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Default file contents ─────────────────────────────────────────────────────

_GLOBAL_SETTINGS = {
    "default_provider": "anthropic",
    "default_model": "claude-sonnet-4-6",
    "compaction": {
        "enabled": True,
        "strategy": "summarization"
    },
    "cron_enabled": True,
    "extensions": True,
    "curator": {
        "enabled": True,
        "interval_hours": 168,
        "stale_after_days": 30,
        "archive_after_days": 90,
        "paused": False
    },
    "acp": {
        "enabled": True,
        "agents": [
            {
                "enabled": True,
                "name": "codex",
                "transport": "stdio",
                "command": "codex-acp",
                "args": []
            },
            {
                "enabled": True,
                "name": "claude-code",
                "transport": "stdio",
                "command": "claude-agent-acp",
                "args": []
            }
        ]
    },
    "profiles": []
}

_PROVIDERS_AUTH: dict = {}          # populated via /auth set-key; empty by default
_ACP_AUTH: dict = {}                # populated via ACP OAuth flow

_CHANNELS_AUTH = {
    "telegram": {"bot_token": ""},
    "discord":  {"bot_token": ""},
    "slack":    {"bot_token": "", "app_token": ""},
    "twitch":   {"token": ""},
    "email":    {"username": "", "password": ""}
}

_PROFILE_SETTINGS = {
    "channels": {
        "telegram":  {"enabled": False},
        "discord":   {"enabled": False},
        "slack":     {"enabled": False},
        "websocket": {"enabled": False, "host": "127.0.0.1", "port": 8765},
        "twitch":    {"enabled": False, "channel_name": "", "nick": ""},
        "email":     {
            "enabled": False,
            "imap_host": "", "imap_port": 993,
            "smtp_host": "", "smtp_port": 587
        }
    }
}

_CRONS: dict = {'version': 1, 'jobs': []}
_ACP_TOKENS: dict = {}

_KNOWLEDGE_INDEX = """\
# Knowledge index for this profile.
# Add entries below to make documents available to the agent in the system prompt.
#
# Fields:
#   path        — path relative to this knowledge/ directory (required)
#   always_load — true: inject content directly into prompt; false: list as available (default: false)
#   priority    — hint for the agent: high | normal | low (default: normal)
#   tags        — list of topic labels for filtering
#
# Example:
# - path: company/overview.md
#   always_load: true
#   priority: high
#   tags: [company, overview]
#
# - path: api/reference.md
#   priority: normal
#   tags: [api, reference]
"""



def _agent_md(name: str, description: str) -> str:
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"---\n"
    )


_TEMPLATES_DIR = Path(__file__).parent / 'templates'

def _read_template(name: str) -> str:
    path = _TEMPLATES_DIR / name
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return f'# {name.split(".")[0].capitalize()}\n'


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_json(path: Path, data: object) -> None:
    """Write JSON only if the file does not already exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)
    logger.debug('bootstrap: created %s', path)


def _write_text(path: Path, content: str) -> None:
    """Write text only if the file does not already exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    logger.debug('bootstrap: created %s', path)


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _bootstrap_listed_profiles(root: Path) -> None:
    """Read settings.json['profiles'] and scaffold any missing profile dirs."""
    settings_path = root / 'settings.json'
    if not settings_path.exists():
        return
    try:
        data = json.loads(settings_path.read_text(encoding='utf-8'))
    except Exception:
        return
    names = data.get('profiles') or []
    if not isinstance(names, list):
        return
    profiles_dir = root / 'profiles'
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        profile_dir = profiles_dir / name
        if not (profile_dir / 'AGENT.md').exists():
            logger.info('bootstrap: creating missing profile %r', name)
            bootstrap_profile(profile_dir, name)


# ── Public API ────────────────────────────────────────────────────────────────


def bootstrap_global_dir(config_dir: Path | None = None) -> None:
    """Ensure the global ~/.operator/ structure exists with default config files.

    Also reads settings.json['profiles'] and auto-creates any listed profile
    directories that don't exist yet.

    Safe to call on every startup — only creates missing files/dirs.
    """
    from operator_use.settings.paths import get_config_dir
    root = config_dir or get_config_dir()

    # Directories
    for sub in ('auth', 'packages', 'profiles', 'gateway'):
        _mkdir(root / sub)

    # Global config files
    _write_json(root / 'settings.json',           _GLOBAL_SETTINGS)
    _write_json(root / 'auth' / 'providers.json', _PROVIDERS_AUTH)
    _write_json(root / 'auth' / 'acp.json',       _ACP_AUTH)

    # Auto-create any profiles listed in settings.json['profiles']
    _bootstrap_listed_profiles(root)


def bootstrap_profile(profile_dir: Path, name: str, description: str = '') -> None:
    """Ensure a profile directory is fully scaffolded with default config files.

    Safe to call every time a profile is loaded — only creates missing items.
    """
    desc = description or f'{name} agent profile'

    # Resource directories
    for sub in (
        'sessions', 'tools', 'skills', 'extensions',
        'commands', 'hooks', 'subagents', 'knowledge',
        'temp', 'tasks', 'teams', 'acp', 'workflows', 'auth',
        'memory',
    ):
        _mkdir(profile_dir / sub)

    # Identity / persona files
    _write_text(profile_dir / 'AGENT.md',  _agent_md(name, desc))
    _write_text(profile_dir / 'SOUL.md',   _read_template('SOUL.md'))
    _write_text(profile_dir / 'USER.md',   _read_template('USER.md'))
    _write_text(profile_dir / 'MEMORY.md', _read_template('MEMORY.md'))

    # Config files
    _write_json(profile_dir / 'settings.json',           _PROFILE_SETTINGS)
    _write_json(profile_dir / 'auth' / 'channels.json',  _CHANNELS_AUTH)
    _write_json(profile_dir / 'crons.json',              _CRONS)
    _write_json(profile_dir / 'acp' / 'tokens.json',    _ACP_TOKENS)
    _write_text(profile_dir / 'knowledge' / 'index.yaml', _KNOWLEDGE_INDEX)

