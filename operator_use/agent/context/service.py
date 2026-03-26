"""Context: system prompt builder and message history manager."""

import operator_use
from datetime import datetime
from enum import Enum
from getpass import getuser
from pathlib import Path
from platform import machine, system, python_version

from operator_use.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from operator_use.agent.skills import Skills
from operator_use.agent.memory import Memory
from operator_use.agent.knowledge import Knowledge

BOOTSTRAP_FILENAMES = ["RULES.md", "SOUL.md", "USER.md", "CODE.md", "AGENTS.md"]


class PromptMode(str, Enum):
    FULL    = "full"     # Main agent: full prompt with memory, bootstrap files, respond rules
    MINIMAL = "minimal"  # Delegated agent: identity + skills only, no memory/user/soul files
    NONE    = "none"     # Raw subagent: single-line identity only


class Context:
    """Builds conversation context: system prompt + message history."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.codebase = Path(operator_use.__file__).resolve().parent.parent
        self.skills = Skills(self.workspace)
        self.memory = Memory(self.workspace)
        self.knowledge = Knowledge(self.workspace)
        self._plugin_prompt_sections: list[str] = []

    def register_plugin_prompt(self, section: str) -> None:
        self._plugin_prompt_sections.append(section)

    def unregister_plugin_prompt(self, section: str) -> None:
        self._plugin_prompt_sections = [s for s in self._plugin_prompt_sections if s != section]

    def _build_runtime_context(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        _sys = system()
        os_name = "MacOS" if _sys == "Darwin" else _sys
        runtime = f"{os_name} {machine()} Python {python_version()}"
        username = getuser()
        home = Path.home()
        lines = []
        lines.append(f"## Today's Date: {now}")
        lines.append(f"## Username: {username}")
        lines.append(f"## Runtime: {runtime}")
        lines.append(f"## Home: {home.as_posix()}")
        lines.append(f"## Downloads: {(home / 'Downloads').as_posix()}")
        lines.append(f"## Desktop: {(home / 'Desktop').as_posix()}")
        lines.append(f"## Documents: {(home / 'Documents').as_posix()}")
        if _sys == "Windows":
            lines.append("## Shell: Windows CMD / PowerShell. Use `dir` not `ls`, `del` not `rm`. Pass commands as plain strings without surrounding quotes.")
        elif _sys == "Darwin":
            lines.append("## Shell: macOS bash/zsh.")
        else:
            lines.append("## Shell: Linux bash.")
        return "\n".join(lines)

    def _build_codebase_context(self) -> str:
        codebase_path = self.codebase.expanduser().resolve().as_posix()
        workspace_path = self.workspace.expanduser().resolve().as_posix()
        return f"""## Codebase: {codebase_path}
You have access to your CODEBASE of implementation.

- Codebase (Summary of your codebase): {workspace_path}/CODE.md

Use the CODE.md file to update your codebase to improve your capabilities on demand.
"""

    def _build_workspace_context(self) -> str:
        workspace_path = self.workspace.expanduser().resolve().as_posix()
        return f"""## Workspace: {workspace_path}
Where you store your memory, skills and notes.

 - Hard Constraints (non-negotiable rules, always enforced): {workspace_path}/RULES.md
 - Agent Instructions: {workspace_path}/AGENTS.md
 - Soul (Your personality and goals): {workspace_path}/SOUL.md
 - User (User Profile and preferences): {workspace_path}/USER.md
 - Memory (Long Term Memory): {workspace_path}/memory/MEMORY.md
 - Daily Log (append during sessions): {workspace_path}/memory/YYYY-MM-DD.md
 - Heartbeat tasks (periodic tasks you need to perform): {workspace_path}/HEARTBEAT.md
 - Custom Skills (Skills you can use to enhance your capabilities): {workspace_path}/skills/{{skill-name}}/SKILL.md
 - Knowledge (Persistent reference docs, read selectively): {workspace_path}/knowledge/
 - Custom Tools (Python tool scripts, auto-loaded at startup): {workspace_path}/tools/

When you need to remember something, write to {workspace_path}/memory/MEMORY.md
"""

    def _load_bootstrap_files(self) -> list[str]:
        parts = []
        for filename in BOOTSTRAP_FILENAMES:
            path = self.workspace / filename
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if not content:
                    continue
                parts.append(f"""### {filename}\n\n{content}""")
        return parts

    def get_respond_behavior(self, is_voice: bool = False) -> str:
        parts = ["## Respond Behavior"]
        base = """
- Be direct, concise, and use the send_message tool only for intermediate updates and don't use it for the final response.
- Don't give lengthy explanations, caveats, or preamble unless the task requires it.
- Respond like a human would—brief and to the point if required use Markdown for formatting.
"""
        voice = """
- Your response will be spoken via TTS. Keep it short, conversational, and natural for speech.
- Use plain text only. Markdown is not allowed in voice replies.
- NEVER include message IDs like [bot_msg_id:N] or [msg_id:N] in your response. These are for your reference only.
"""
        parts.append(voice if is_voice else base + "\n- NEVER include message IDs like [bot_msg_id:N] or [msg_id:N] in your response. These are for your reference only.")
        return "\n".join(parts)

    def build_system_prompt(
        self,
        is_voice: bool = False,
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str | None = None,
    ) -> str:
        if prompt_mode == PromptMode.NONE:
            parts = [
                "You are a subagent. Complete the delegated task and return your findings clearly. "
                "Do not send messages to the user — your response is relayed by the delegating agent."
            ]
            if system_prompt:
                parts.append(system_prompt)
            return "\n\n".join(parts)

        parts = []
        parts.append(self.get_identity())

        if prompt_mode == PromptMode.FULL:
            if bootstrap_parts := self._load_bootstrap_files():
                parts.extend(bootstrap_parts)

        skills_summary = self.skills.build_skills_summary() or "(No skills available)"
        parts.append(f'''## Skills

You have access to the following skills to enhance your capabilities, to use a skill, read the SKILL.md file for the skill.

Available Skills:
{skills_summary}
''')
        if prompt_mode == PromptMode.FULL:
            if memory_context := self.memory.get_memory_context():
                parts.append(memory_context)

        if prompt_mode == PromptMode.FULL:
            if knowledge_index := self.knowledge.build_knowledge_index():
                parts.append(knowledge_index)

        if system_prompt:
            parts.append(f"## Task Context\n\n{system_prompt}")

        if self._plugin_prompt_sections:
            parts.extend(self._plugin_prompt_sections)

        if prompt_mode == PromptMode.FULL:
            parts.append(self.get_respond_behavior(is_voice=is_voice))

        return "\n".join(parts)

    def get_identity(self) -> str:
        runtime_context = self._build_runtime_context()
        codebase_context = self._build_codebase_context()
        workspace_context = self._build_workspace_context()
        return f'''
You are operator-use created by CursorTouch.

You are a helpful personal assistant.

{runtime_context}

{workspace_context}

{codebase_context}
'''

    def _hydrate_history(self, history: list[BaseMessage]) -> list[BaseMessage]:
        """Inject channel metadata into message content so the LLM can see IDs for reactions/references."""
        hydrated = []
        for msg in history:
            if isinstance(msg, HumanMessage) and msg.metadata:
                msg_id = msg.metadata.get("message_id")
                if msg_id is not None:
                    hydrated.append(HumanMessage(
                        content=f"[msg_id:{msg_id}] {msg.content}",
                        metadata=msg.metadata,
                    ))
                    continue
            elif isinstance(msg, AIMessage) and msg.metadata.get("message_id") is not None:
                bot_msg_id = msg.metadata["message_id"]
                reactions = msg.metadata.get("reactions", [])
                reaction_str = ""
                if reactions:
                    counts: dict[str, int] = {}
                    for r in reactions:
                        for e in r.get("emojis", []):
                            counts[e] = counts.get(e, 0) + 1
                    reaction_str = " reactions:" + ",".join(
                        f"{e}({c})" if c > 1 else e for e, c in counts.items()
                    )
                hydrated.append(AIMessage(
                    content=f"[bot_msg_id:{bot_msg_id}{reaction_str}] {msg.content or ''}",
                    thinking=msg.thinking,
                    thinking_signature=msg.thinking_signature,
                    usage=msg.usage,
                    metadata=msg.metadata,
                ))
                continue
            hydrated.append(msg)
        return hydrated

    async def build_messages(
        self,
        history: list[BaseMessage],
        is_voice: bool = False,
        session_id: str | None = None,
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str | None = None,
    ) -> list[BaseMessage]:
        """Build messages: [System, history]."""
        messages = [SystemMessage(content=self.build_system_prompt(
            is_voice=is_voice,
            prompt_mode=prompt_mode,
            system_prompt=system_prompt,
        ))]
        messages.extend(self._hydrate_history(history))
        return messages
