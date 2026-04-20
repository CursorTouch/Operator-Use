"""Context: system prompt builder and message history manager."""

import operator_use
from datetime import datetime
from enum import Enum
from getpass import getuser
from pathlib import Path
from platform import machine, system, python_version

from operator_use.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ImageMessage
from operator_use.agent.skills import Skills
from operator_use.agent.memory import Memory
from operator_use.agent.knowledge import Knowledge
from operator_use.interceptor import RestartInterceptor

BOOTSTRAP_FILENAMES = ["IDENTITY.md", "SOUL.md", "USER.md", "AGENTS.md"]


class PromptMode(str, Enum):
    FULL = "full"  # Main agent: full prompt with memory, bootstrap files, respond rules
    MINIMAL = "minimal"  # Delegated agent: identity + skills only, no memory/user/soul files
    NONE = "none"  # Raw subagent: single-line identity only


class Context:
    """Builds conversation context: system prompt + message history."""

    def __init__(self, workspace: Path, mcp_servers: dict | None = None):
        self.workspace = workspace
        self.codebase = Path(operator_use.__file__).resolve().parent.parent
        self.skills = Skills(self.workspace)
        self.memory = Memory(self.workspace)
        self.knowledge = Knowledge(self.workspace)
        self.mcp_servers = mcp_servers or {}
        from operator_use.config.paths import get_userdata_dir

        self.interceptor = RestartInterceptor(
            userdata=get_userdata_dir(),
            project_root=self.codebase,
        )
        self._plugin_prompt_sections: list[str] = []

    def register_plugin_prompt(self, section: str) -> None:
        self._plugin_prompt_sections.append(section)

    def unregister_plugin_prompt(self, section: str) -> None:
        self._plugin_prompt_sections = [s for s in self._plugin_prompt_sections if s != section]

    def _build_environment_context(self) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        _sys = system()
        os_name = "MacOS" if _sys == "Darwin" else _sys
        username = getuser()
        home = Path.home()
        if _sys == "Windows":
            shell = "Windows CMD / PowerShell. Use `dir` not `ls`, `del` not `rm`. Pass commands as plain strings without surrounding quotes."
        elif _sys == "Darwin":
            shell = "macOS bash/zsh."
        else:
            shell = "Linux bash."
        return (
            f"## Environment\n\n"
            f"- Date: {now}\n"
            f"- User: {username}\n"
            f"- OS: {os_name} {machine()} Python {python_version()}\n"
            f"- Shell: {shell}\n"
            f"- Home: {home.as_posix()}\n"
            f"- Downloads: {(home / 'Downloads').as_posix()}\n"
            f"- Desktop: {(home / 'Desktop').as_posix()}\n"
            f"- Documents: {(home / 'Documents').as_posix()}"
        )

    def _build_workspace_context(self) -> str:
        workspace_path = self.workspace.expanduser().resolve().as_posix()
        return (
            f"## Workspace: {workspace_path}\n\n"
            f"- HEARTBEAT.md — periodic maintenance tasks\n"
            f"- memory/MEMORY.md — long-term memory (write here to remember things)\n"
            f"- memory/YYYY-MM-DD.md — daily session log (append during sessions)\n"
            f"- skills/{{name}}/SKILL.md — skill guides (invoked via `skill` tool)\n"
            f"- knowledge/ — reference docs (read on demand)\n"
            f"- tools/ — custom Python tools (auto-loaded at startup)\n"
            f"- temp/ — scratchpad, working files, terminal CWD"
        )

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

    def _build_mcp_context(self) -> str | None:
        """Build context about available MCP servers."""
        if not self.mcp_servers:
            return None

        lines = ["## Available MCP Servers"]
        lines.append(
            "You can connect to external MCP servers to access additional tools and capabilities."
        )
        lines.append(
            'Use the `mcp(action="list")` tool to see all configured servers and their connection status.'
        )
        lines.append(
            'Use `mcp(action="connect", server_name="...")` to connect and load tools from a server.'
        )
        lines.append("\n### Configured MCP Servers:\n")

        for name, config in self.mcp_servers.items():
            # Handle both dict (legacy) and MCPServerConfig (Pydantic model)
            if isinstance(config, dict):
                transport = config.get("transport", "unknown")
                cmd = config.get("command", "?")
                args = config.get("args", [])
                url = config.get("url", "?")
            else:
                # MCPServerConfig object - use attribute access
                transport = config.transport
                cmd = config.command
                args = config.args or []
                url = config.url

            if transport == "stdio":
                args_str = f" {' '.join(args)}" if args else ""
                lines.append(f"- **{name}** ({transport}): `{cmd}{args_str}`")
            elif transport in ("http", "sse"):
                lines.append(f"- **{name}** ({transport}): `{url}`")
            else:
                lines.append(f"- **{name}** ({transport})")

        lines.append("\nTo use an MCP server's tools:")
        lines.append('1. Call `mcp(action="connect", server_name="<server-name>")`')
        lines.append("2. The server's tools will be loaded and available for use")
        lines.append('3. Call `mcp(action="disconnect", server_name="<server-name>")` when done')

        return "\n".join(lines)

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
        parts.append(
            voice
            if is_voice
            else base
            + "\n- NEVER include message IDs like [bot_msg_id:N] or [msg_id:N] in your response. These are for your reference only."
        )
        return "\n".join(parts)

    def build_system_prompt(
        self,
        is_voice: bool = False,
        prompt_mode: PromptMode = PromptMode.FULL,
        system_prompt: str | None = None,
    ) -> str:
        if prompt_mode == PromptMode.NONE:
            if self._plugin_prompt_sections:
                parts = list(self._plugin_prompt_sections)
                if system_prompt:
                    parts.append(f"## Instructions\n\n{system_prompt}")
                return "\n\n".join(parts)
            parts = [
                "You are a subagent. Complete the delegated task and return your findings clearly. "
                "Do not send messages to the user — your response is relayed by the delegating agent."
            ]
            if system_prompt:
                parts.append(system_prompt)
            return "\n\n".join(parts)

        parts = []

        # 1. Identity and persona — who the agent is
        if prompt_mode == PromptMode.FULL:
            if bootstrap_parts := self._load_bootstrap_files():
                parts.extend(bootstrap_parts)

        # 2. Operator-supplied instructions (overrides / task-specific config)
        if system_prompt:
            parts.append(f"## Instructions\n\n{system_prompt}")

        # 3. Runtime environment
        parts.append(self._build_environment_context())

        # 4. Workspace file map
        parts.append(self._build_workspace_context())

        # 5. Available skills
        skills_summary = self.skills.build_skills_summary() or "(No skills available)"
        parts.append(
            f"## Skills\n\n"
            f"Use the `skill` tool to invoke a skill: `skill(name=\"skill-name\")`.\n\n"
            f"{skills_summary}"
        )

        # 6. MCP servers (if configured)
        if mcp_context := self._build_mcp_context():
            parts.append(mcp_context)

        # 7. Plugin capability sections
        if self._plugin_prompt_sections:
            parts.extend(self._plugin_prompt_sections)

        # 8. Long-term memory
        if prompt_mode == PromptMode.FULL:
            if memory_context := self.memory.get_memory_context():
                parts.append(memory_context)

        # 9. Knowledge index
        if prompt_mode == PromptMode.FULL:
            if knowledge_index := self.knowledge.build_knowledge_index():
                parts.append(knowledge_index)

        # 10. Response behaviour rules (last = highest recency weight for the LLM)
        if prompt_mode == PromptMode.FULL:
            parts.append(self.get_respond_behavior(is_voice=is_voice))

        return "\n\n".join(parts)

    def _hydrate_history(self, history: list[BaseMessage]) -> list[BaseMessage]:
        """Inject channel metadata into message content so the LLM can see IDs for reactions/references.

        Also strips image data from all ImageMessage instances except the most recent one —
        older images are replaced with a lightweight HumanMessage referencing the file paths,
        avoiding repeated large base64 payloads on every subsequent LLM call.
        """
        # Find index of the last ImageMessage so we keep its pixel data intact
        last_image_idx = -1
        for i, msg in enumerate(history):
            if isinstance(msg, ImageMessage):
                last_image_idx = i

        hydrated = []
        for i, msg in enumerate(history):
            # Downgrade old ImageMessages to plain text references
            if isinstance(msg, ImageMessage) and i != last_image_idx:
                paths = msg.metadata.get("image_paths") or []
                if paths:
                    path_str = ", ".join(paths)
                    ref = f"[{len(paths)} image(s): {path_str}]"
                else:
                    n = len(msg.images) if msg.images else 1
                    ref = f"[{n} image(s) — data no longer available]"
                text = f"{ref} {msg.content}".strip()
                msg_id = msg.metadata.get("message_id")
                if msg_id is not None:
                    text = f"[msg_id:{msg_id}] {text}"
                hydrated.append(HumanMessage(content=text, metadata=msg.metadata))
                continue

            if isinstance(msg, (HumanMessage, ImageMessage)) and msg.metadata:
                msg_id = msg.metadata.get("message_id")
                if msg_id is not None:
                    hydrated.append(
                        HumanMessage(
                            content=f"[msg_id:{msg_id}] {msg.content}",
                            metadata=msg.metadata,
                        )
                    )
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
                hydrated.append(
                    AIMessage(
                        content=f"[bot_msg_id:{bot_msg_id}{reaction_str}] {msg.content or ''}",
                        thinking=msg.thinking,
                        thinking_signature=msg.thinking_signature,
                        usage=msg.usage,
                        metadata=msg.metadata,
                    )
                )
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
        messages = [
            SystemMessage(
                content=self.build_system_prompt(
                    is_voice=is_voice,
                    prompt_mode=prompt_mode,
                    system_prompt=system_prompt,
                )
            )
        ]
        messages.extend(self._hydrate_history(history))
        return messages
