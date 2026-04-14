from operator_use.agent.tools.builtin.filesystem import read_file, write_file, edit_file, list_dir
from operator_use.agent.tools.builtin.patch import patch_file
from operator_use.agent.tools.builtin.web import web_search, web_fetch
from operator_use.agent.tools.builtin.terminal import terminal
from operator_use.agent.tools.builtin.message import intermediate_message, react_message, send_file
from operator_use.agent.tools.builtin.cron import cron
from operator_use.agent.tools.builtin.subagents import subagents
from operator_use.agent.tools.builtin.process import process
from operator_use.agent.tools.builtin.channel import channel
from operator_use.agent.tools.builtin.acp_agents import acpagents
from operator_use.agent.tools.builtin.local_agents import localagents
from operator_use.agent.tools.builtin.control_center import control_center
from operator_use.agent.tools.builtin.imagegen import imagegen
from operator_use.agent.tools.builtin.mcp import mcp as mcp_tool
from operator_use.agent.tools.builtin.skill import skill

FILESYSTEM_TOOLS = [read_file, write_file, edit_file, list_dir, patch_file]
WEB_TOOLS = [web_search, web_fetch]
TERMINAL_TOOLS = [terminal]
MESSAGE_TOOLS = [intermediate_message, react_message, send_file]
CRON_TOOLS = [cron]
PROCESS_TOOLS = [process, control_center]
OTHER_AGENT_TOOLS = [subagents, acpagents, localagents]
CHANNEL_TOOLS = [channel]
IMAGE_TOOLS = [imagegen]
MCP_TOOLS = [mcp_tool]
SKILL_TOOLS = [skill]

AGENT_TOOLS = (
    FILESYSTEM_TOOLS
    + WEB_TOOLS
    + TERMINAL_TOOLS
    + CRON_TOOLS
    + PROCESS_TOOLS
    + OTHER_AGENT_TOOLS
    + IMAGE_TOOLS
    + MCP_TOOLS
    + SKILL_TOOLS
)

NON_AGENT_TOOLS = MESSAGE_TOOLS + CHANNEL_TOOLS

ALL_TOOLS = AGENT_TOOLS + NON_AGENT_TOOLS

# CLI profile — tools that work in a terminal session (no channels, no gateway)
CLI_TOOLS = FILESYSTEM_TOOLS + WEB_TOOLS + TERMINAL_TOOLS + PROCESS_TOOLS

MINIMAL_TOOLS = FILESYSTEM_TOOLS + WEB_TOOLS

CODING_TOOLS = FILESYSTEM_TOOLS + WEB_TOOLS + TERMINAL_TOOLS

# Tool profiles — base presets for per-agent tool configuration
TOOL_PROFILES: dict[str, list] = {
    "minimal": MINIMAL_TOOLS,
    "coding": CODING_TOOLS,
    "full": ALL_TOOLS,
}


def resolve_tools(
    profile: str = "full",
    also_allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> list:
    """Resolve a final tool list from a profile + allow/deny lists.

    Args:
        profile: Base preset name — "minimal", "coding", or "full".
        also_allow: Tool names to add on top of the profile.
        deny: Tool names to remove from the resolved list.
    """
    base = list(TOOL_PROFILES.get(profile, ALL_TOOLS))
    tool_map = {t.name: t for t in ALL_TOOLS}

    if also_allow:
        present = {t.name for t in base}
        for name in also_allow:
            if name not in present and name in tool_map:
                base.append(tool_map[name])

    if deny:
        deny_set = set(deny)
        base = [t for t in base if t.name not in deny_set]

    return base


__all__ = ["AGENT_TOOLS", "NON_AGENT_TOOLS", "ALL_TOOLS", "TOOL_PROFILES", "resolve_tools"]
