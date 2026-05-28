"""Named agent profiles — loaded from AGENT.md files in ~/.operator/profiles/<name>/.

Each profile is a self-contained environment with its own system prompt, model/provider
override, tool allowlist, and per-profile resource directories (tools, skills, knowledge,
extensions, sessions) plus identity-file overrides (SOUL.md, USER.md, Memory.md).

Discovery path: ~/.operator/profiles/<name>/AGENT.md

File format (AGENT.md):
    ---
    name: coder
    description: Senior software engineer focused on Python and TypeScript.
    model: claude-opus-4-7
    provider: anthropic
    tools: read, edit, write, grep, glob, bash, terminal
    ---

    You are a senior software engineer ...

The body becomes the custom system prompt (replaces the default). An empty body
means "use the default system prompt".

Per-profile resources are looked up under the profile directory:
    ~/.operator/profiles/<name>/
        AGENT.md          ← profile definition
        settings.json     ← per-profile settings overlay
        SOUL.md           ← persona override
        USER.md           ← user profile override
        MEMORY.md         ← memory override
        sessions/         ← session history
        tools/            ← tools
        skills/           ← skills
        extensions/       ← extensions
        commands/         ← slash commands
        hooks/            ← hooks
        subagents/        ← subagent templates
        knowledge/        ← knowledge docs
        workflows/        ← workflows
        teams/            ← team state
        acp/              ← ACP session files
        temp/             ← scratch space
        crons.json        ← scheduled jobs
"""

from __future__ import annotations

import atexit
import tempfile
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from operator_use.diagnostics.types import ResourceDiagnostic
from operator_use.subagent.profile import _parse_frontmatter


class LoadAgentProfilesResult(BaseModel):
    profiles: list['AgentProfile'] = Field(default_factory=list)
    diagnostics: list[ResourceDiagnostic] = Field(default_factory=list)

    model_config = {'arbitrary_types_allowed': True}


AGENT_FILE_NAME = 'AGENT.md'
MAX_NAME_LENGTH = 64


class AgentProfile(BaseModel):
    name: str
    description: str
    profile_dir: Path             # ~/.operator/profiles/<name>/
    system_prompt: str            # empty string = use default system prompt
    tools: list[str]              # empty list = all tools allowed
    model_id: str | None = None   # LLM model override, e.g. "claude-opus-4-7"
    provider: str | None = None   # LLM provider override, e.g. "anthropic"
    file_path: Path               # profile_dir / AGENT.md

    model_config = {'arbitrary_types_allowed': True}

    # ── Per-profile resource paths ────────────────────────────────────────────

    @property
    def settings_path(self) -> Path:
        return self.profile_dir / 'settings.json'

    @property
    def soul_path(self) -> Path:
        return self.profile_dir / 'SOUL.md'

    @property
    def user_path(self) -> Path:
        return self.profile_dir / 'USER.md'

    @property
    def memory_path(self) -> Path:
        return self.profile_dir / 'MEMORY.md'

    @property
    def sessions_dir(self) -> Path:
        return self.profile_dir / 'sessions'

    @property
    def tools_dir(self) -> Path:
        return self.profile_dir / 'tools'

    @property
    def skills_dir(self) -> Path:
        return self.profile_dir / 'skills'

    @property
    def knowledge_dir(self) -> Path:
        return self.profile_dir / 'knowledge'

    @property
    def extensions_dir(self) -> Path:
        return self.profile_dir / 'extensions'

    @property
    def commands_dir(self) -> Path:
        return self.profile_dir / 'commands'

    @property
    def hooks_dir(self) -> Path:
        return self.profile_dir / 'hooks'

    @property
    def subagents_dir(self) -> Path:
        return self.profile_dir / 'subagents'

    @property
    def temp_dir(self) -> Path:
        return self.profile_dir / 'temp'

    @property
    def tasks_dir(self) -> Path:
        return self.profile_dir / 'tasks'

    @property
    def crons_path(self) -> Path:
        return self.profile_dir / 'crons.json'

    @property
    def teams_dir(self) -> Path:
        return self.profile_dir / 'teams'

    @property
    def acp_dir(self) -> Path:
        return self.profile_dir / 'acp'

    @property
    def acp_tokens_path(self) -> Path:
        return self.profile_dir / 'acp' / 'tokens.json'

    @property
    def acp_sessions_dir(self) -> Path:
        """Server-side ACP session storage — conversations this agent received."""
        return self.profile_dir / 'acp' / 'sessions'

    @property
    def workflows_dir(self) -> Path:
        return self.profile_dir / 'workflows'

    @property
    def workflow_runs_dir(self) -> Path:
        return self.profile_dir / 'workflows' / '.runs'

    @property
    def auth_dir(self) -> Path:
        return self.profile_dir / 'auth'

    @property
    def auth_channels_path(self) -> Path:
        return self.profile_dir / 'auth' / 'channels.json'

    # ── Peer-to-peer paths ────────────────────────────────────────────────────

    @property
    def peer_dir(self) -> Path:
        """Root peer directory — holds bookmarks and session history."""
        return self.profile_dir / 'peer'

    def peer_bookmark_path(self, profile_name: str) -> Path:
        """Bookmark file for a specific peer profile."""
        return self.peer_dir / f'{profile_name}.json'

    def peer_sessions_dir(self, other_profile: str) -> Path:
        """Session directory for conversations with *other_profile*.

        Named from this profile's perspective:
          profiles/alice/peer/sessions/bob/  — alice's sessions talking to bob
          profiles/bob/peer/sessions/alice/  — bob's sessions talking to alice
        """
        return self.peer_dir / 'sessions' / other_profile


def create_ephemeral_profile() -> AgentProfile:
    """Create a transient profile backed by a temporary directory.

    Used when no named profile is active (ephemeral REPL session, base gateway/
    ACP runtime).  The temporary directory is removed automatically when the
    process exits via an atexit handler.

    The profile is fully functional in memory — tools, crons, and ACP all work
    normally.  Nothing survives process exit.
    """
    tmpdir = tempfile.mkdtemp(prefix='operator-ephemeral-')
    atexit.register(_cleanup_tmpdir, tmpdir)

    profile_dir = Path(tmpdir)
    short_id = uuid.uuid4().hex[:8]
    return AgentProfile(
        name=f'ephemeral-{short_id}',
        description='Ephemeral profile — in memory only, not persisted to disk.',
        profile_dir=profile_dir,
        system_prompt='',
        tools=[],
        file_path=profile_dir / AGENT_FILE_NAME,
    )


def _cleanup_tmpdir(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def load_agent_profile_from_file(
    file_path: Path,
) -> tuple[AgentProfile | None, list[ResourceDiagnostic]]:
    diagnostics: list[ResourceDiagnostic] = []
    try:
        raw = file_path.read_text(encoding='utf-8')
        front, body = _parse_frontmatter(raw)

        profile_dir = file_path.parent
        name = str(front.get('name') or profile_dir.name)
        description = str(front.get('description', '')).strip()
        tools_raw = str(front.get('tools', '')).strip()
        tools = [t.strip() for t in tools_raw.split(',') if t.strip()] if tools_raw else []
        model_id = str(front['model']).strip() if front.get('model') else None
        provider = str(front['provider']).strip() if front.get('provider') else None

        if len(name) > MAX_NAME_LENGTH:
            diagnostics.append(ResourceDiagnostic(
                type='warning',
                message=f'name exceeds {MAX_NAME_LENGTH} characters',
                path=str(file_path),
            ))

        profile = AgentProfile(
            name=name,
            description=description,
            profile_dir=profile_dir,
            system_prompt=body.strip(),
            tools=tools,
            model_id=model_id,
            provider=provider,
            file_path=file_path,
        )

        # Ensure all profile subdirs and default config files are present.
        from operator_use.runtime.bootstrap import bootstrap_profile
        bootstrap_profile(profile_dir, name, description)

        return profile, diagnostics

    except Exception as exc:
        diagnostics.append(ResourceDiagnostic(type='warning', message=str(exc), path=str(file_path)))
        return None, diagnostics


def _load_from_dir(dir_path: Path) -> LoadAgentProfilesResult:
    """Scan dir_path for profile subdirectories, each containing an AGENT.md."""
    profiles: list[AgentProfile] = []
    diagnostics: list[ResourceDiagnostic] = []

    if not dir_path.is_dir():
        return LoadAgentProfilesResult(profiles=profiles, diagnostics=diagnostics)

    try:
        entries = list(dir_path.iterdir())
    except OSError:
        return LoadAgentProfilesResult(profiles=profiles, diagnostics=diagnostics)

    for entry in sorted(entries, key=lambda e: e.name):
        if entry.name.startswith('.') or entry.name == '__pycache__':
            continue
        if not entry.is_dir():
            continue
        agent_file = entry / AGENT_FILE_NAME
        if agent_file.is_file():
            profile, diags = load_agent_profile_from_file(agent_file)
            diagnostics.extend(diags)
            if profile:
                profiles.append(profile)

    return LoadAgentProfilesResult(profiles=profiles, diagnostics=diagnostics)


def load_agent_profiles(dirs: list[Path]) -> LoadAgentProfilesResult:
    """Load profiles from multiple directories; first-found wins on name collision."""
    seen: set[str] = set()
    all_profiles: list[AgentProfile] = []
    all_diagnostics: list[ResourceDiagnostic] = []

    for dir_path in dirs:
        result = _load_from_dir(dir_path)
        all_diagnostics.extend(result.diagnostics)
        for profile in result.profiles:
            if profile.name not in seen:
                seen.add(profile.name)
                all_profiles.append(profile)

    return LoadAgentProfilesResult(profiles=all_profiles, diagnostics=all_diagnostics)
