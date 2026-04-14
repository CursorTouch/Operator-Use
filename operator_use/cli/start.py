"""Run Operator with channels and agents."""

import asyncio
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
import logging
from rich.console import Console

if TYPE_CHECKING:
    from operator_use.mcp import MCPManager

load_dotenv()

logger = logging.getLogger(__name__)

_console = Console()
_P = "#e5c07b"  # primary   – warm gold
_S = "#61afef"  # secondary – blue
_M = "#abb2bf"  # muted     – gray


def _row(label: str, value: str) -> None:
    _console.print(f"│ [{_M}]{label:<10}[/{_M}] [{_S}]{value}[/{_S}]")


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("operator-use")
    except Exception:
        return ""


def _print_startup(lines: list[tuple[str, str]], title_suffix: str = "") -> None:
    ver = _version()
    ver_str = f" [{_M}]v{ver}[/{_M}]" if ver else ""
    _console.print(f"┌ [bold {_P}]Operator[/bold {_P}]{ver_str}[{_M}]{title_suffix}[/{_M}]")
    _console.print("│")
    for label, value in lines:
        _row(label, value)


def setup_logging(userdata_dir: Path, verbose: bool = False) -> None:
    log_file = userdata_dir / "operator.log"
    userdata_dir.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(level=logging.WARNING, format=fmt, datefmt=datefmt, handlers=handlers)
    logging.getLogger("operator_use").setLevel(logging.INFO)


import operator_use
from operator_use.agent import Agent
from operator_use.orchestrator import Orchestrator
from operator_use.bus import Bus
from operator_use.gateway import Gateway
from operator_use.gateway.channels import (
    TelegramChannel,
    DiscordChannel,
    SlackChannel,
    TwitchChannel,
)
from operator_use.acp import ACPStdioChannel, ACPStdioConfig, ACPChannel, ACPServerConfig
from operator_use.gateway.channels.config import TelegramConfig
from operator_use.gateway.channels.discord import DiscordConfig
from operator_use.gateway.channels.slack import SlackConfig
from operator_use.gateway.channels.twitch import TwitchConfig
from operator_use.providers.base import BaseChatLLM, BaseSTT, BaseTTS

from operator_use.heartbeat import Heartbeat
from operator_use.crons.views import CronJob
from operator_use.crons import Cron
from operator_use.bus import OutgoingMessage, IncomingMessage, TextPart
from operator_use.config import Config, load_config, AgentDefinition
from operator_use.config.paths import get_named_workspace_dir
from typing import Optional
from pathlib import Path

LLM_CLASS_MAP = {
    "openai": "ChatOpenAI",
    "anthropic": "ChatAnthropic",
    "google": "ChatGoogle",
    "mistral": "ChatMistral",
    "groq": "ChatGroq",
    "nvidia": "ChatNvidia",
    "ollama": "ChatOllama",
    "cerebras": "ChatCerebras",
    "open_router": "ChatOpenRouter",
    "azure_openai": "ChatAzureOpenAI",
    "vllm": "ChatVLLM",
    "deepseek": "ChatDeepSeek",
    "xai": "ChatXai",
    "zai": "ChatZAI",
    "codex": "ChatCodex",
    "claude_code": "ChatClaudeCode",
    "antigravity": "ChatAntigravity",
    "github_copilot": "ChatGitHubCopilot",
}

STT_CLASS_MAP = {
    "openai": "STTOpenAI",
    "google": "STTGoogle",
    "groq": "STTGroq",
    "elevenlabs": "STTElevenLabs",
    "deepgram": "STTDeepgram",
    "sarvam": "STTSarvam",
}

TTS_CLASS_MAP = {
    "openai": "TTSOpenAI",
    "google": "TTSGoogle",
    "groq": "TTSGroq",
    "xai": "TTSXai",
    "elevenlabs": "TTSElevenLabs",
    "deepgram": "TTSDeepgram",
    "sarvam": "TTSSarvam",
}

IMAGE_CLASS_MAP = {
    "openai": "ImageOpenAI",
    "google": "ImageGoogle",
    "xai": "ImageXai",
    "together": "ImageTogether",
    "fal": "ImageFal",
}

SEARCH_CLASS_MAP = {
    "ddgs": "DDGSSearch",
    "exa": "ExaSearch",
    "tavily": "TavilySearch",
}


def _make_llm(config: Config, llm_conf) -> Optional[BaseChatLLM]:
    import operator_use.providers as providers

    if not llm_conf.provider:
        return None
    llm_cls_name = LLM_CLASS_MAP.get(llm_conf.provider)
    if not llm_cls_name or not hasattr(providers, llm_cls_name):
        return None
    llm_cls = getattr(providers, llm_cls_name)
    p_conf = getattr(config.providers, llm_conf.provider, None)
    return llm_cls(
        model=llm_conf.model,
        api_key=(p_conf.api_key or None) if p_conf else None,
        base_url=(p_conf.api_base or None) if p_conf else None,
    )


def _make_stt(config: Config) -> Optional[BaseSTT]:
    import operator_use.providers as providers

    stt_conf = config.stt
    if not stt_conf.enabled or not stt_conf.provider:
        return None
    stt_cls_name = STT_CLASS_MAP.get(stt_conf.provider)
    if not stt_cls_name or not hasattr(providers, stt_cls_name):
        return None
    stt_cls = getattr(providers, stt_cls_name)
    p_conf = getattr(config.providers, stt_conf.provider, None)
    return stt_cls(model=stt_conf.model, api_key=p_conf.api_key if p_conf else None)


def _make_tts(config: Config) -> Optional[BaseTTS]:
    import operator_use.providers as providers

    tts_conf = config.tts
    if not tts_conf.enabled or not tts_conf.provider:
        return None
    tts_cls_name = TTS_CLASS_MAP.get(tts_conf.provider)
    if not tts_cls_name or not hasattr(providers, tts_cls_name):
        return None
    tts_cls = getattr(providers, tts_cls_name)
    p_conf = getattr(config.providers, tts_conf.provider, None)
    tts_kwargs = {"model": tts_conf.model, "api_key": p_conf.api_key if p_conf else None}
    if tts_conf.voice:
        tts_kwargs["voice"] = tts_conf.voice
    return tts_cls(**tts_kwargs)


def _make_search(config: Config):
    """Always returns a search provider — falls back to DDGSSearch if not configured."""
    import operator_use.providers as providers

    srch_conf = config.search
    provider_key = srch_conf.provider or "ddgs"
    srch_cls_name = SEARCH_CLASS_MAP.get(provider_key, "DDGSSearch")
    srch_cls = getattr(providers, srch_cls_name, None)
    if srch_cls is None:
        from operator_use.providers.ddgs import DDGSSearch

        return DDGSSearch()
    if provider_key in ("exa", "tavily"):
        return srch_cls(api_key=srch_conf.api_key or "")
    return srch_cls()


def _make_image(config: Config):
    import operator_use.providers as providers

    img_conf = config.image
    if not img_conf.enabled or not img_conf.provider:
        return None
    img_cls_name = IMAGE_CLASS_MAP.get(img_conf.provider)
    if not img_cls_name or not hasattr(providers, img_cls_name):
        return None
    img_cls = getattr(providers, img_cls_name)
    p_conf = getattr(config.providers, img_conf.provider, None)
    img_kwargs = {"api_key": p_conf.api_key if p_conf else None}
    if img_conf.model:
        img_kwargs["model"] = img_conf.model
    if img_conf.size:
        img_kwargs["size"] = img_conf.size
    if img_conf.quality:
        img_kwargs["quality"] = img_conf.quality
    if img_conf.style:
        img_kwargs["style"] = img_conf.style
    return img_cls(**img_kwargs)


def _make_models(
    config: Config,
) -> tuple[Optional[BaseChatLLM], Optional[BaseSTT], Optional[BaseTTS]]:
    """Build LLM + STT + TTS from the first agent's config (used by REPL and other single-agent commands)."""
    first_defn = config.agents.list[0] if config.agents.list else None
    llm_conf = first_defn.llm_config if first_defn and first_defn.llm_config else None
    llm = _make_llm(config, llm_conf) if llm_conf else None
    return llm, _make_stt(config), _make_tts(config)


def _resolve_agent_workspace(defn: AgentDefinition) -> Path:
    if defn.workspace:
        return Path(defn.workspace).expanduser().resolve()
    return get_named_workspace_dir(defn.id)


PLUGIN_REGISTRY: dict[str, type] = {}


def _get_plugin_registry() -> dict[str, type]:
    global PLUGIN_REGISTRY
    if not PLUGIN_REGISTRY:
        from operator_use.computer.plugin import ComputerPlugin
        from operator_use.web.plugin import BrowserPlugin

        PLUGIN_REGISTRY = {
            "browser_use": BrowserPlugin,
            "computer_use": ComputerPlugin,
        }
    return PLUGIN_REGISTRY


def _build_agents(
    config: Config, cron, gateway, bus, image=None, search=None
) -> tuple[dict[str, Agent], "MCPManager | None"]:
    """Instantiate one Agent per agent definition in config."""
    from operator_use.agent.tools.builtin import resolve_tools

    defaults = config.agents.defaults
    agent_defs = config.agents.list

    if not agent_defs:
        raise ValueError("No agents defined in config. Run 'operator onboard' to set up an agent.")

    registry = _get_plugin_registry()

    agents = {}
    for defn in agent_defs:
        llm_conf = defn.llm_config
        if not llm_conf:
            raise ValueError(
                f"Agent '{defn.id}' has no llmConfig. Set it in config.json or run 'operator onboard'."
            )
        llm = _make_llm(config, llm_conf)
        if llm is None:
            raise ValueError(
                f"Agent '{defn.id}': failed to initialize LLM provider '{llm_conf.provider}'. Check the provider name and API key."
            )
        workspace = _resolve_agent_workspace(defn)

        plugins = []
        for p in defn.plugins:
            cls = registry.get(p.id)
            if cls is not None:
                plugins.append(cls(enabled=p.enabled))
            else:
                logger.warning("Unknown plugin id '%s' for agent '%s' — skipped.", p.id, defn.id)

        tools_cfg = defn.tools
        resolved_tools = resolve_tools(
            profile=tools_cfg.profile,
            also_allow=tools_cfg.also_allow,
            deny=tools_cfg.deny,
        )

        from operator_use.agent.context.service import PromptMode

        agents[defn.id] = Agent(
            llm=llm,
            agent_id=defn.id,
            description=defn.description,
            workspace=workspace,
            max_iterations=defn.max_tool_iterations or defaults.max_tool_iterations,
            cron=cron,
            gateway=gateway,
            bus=bus,
            tools=resolved_tools,
            prompt_mode=PromptMode(defn.prompt_mode),
            system_prompt=defn.system_prompt,
            subagent_config=defaults.subagent,
            acp_registry=config.acp_agents,
            plugins=plugins,
            image=image,
            search=search,
            mcp_servers={name: cfg.model_dump() for name, cfg in config.mcp_servers.items()}
            if config.mcp_servers
            else None,
        )

    for agent in agents.values():
        agent.tool_register.set_extension("_agent_registry", agents)

    # Wire shared MCP manager to all agents
    # (reference counting allows multiple agents to share the same server connection)
    from operator_use.mcp import MCPManager

    mcp_manager = MCPManager(list(config.mcp_servers.values())) if config.mcp_servers else None
    for agent in agents.values():
        agent.tool_register.set_extension("_mcp_manager", mcp_manager)

    return agents, mcp_manager


def _build_router(config: Config):
    """Build a router callable from config bindings (top-down, first match wins).

    Each agent owns its own channel bot (account_id == agent id), so routing is
    done purely by account_id. Explicit bindings from config are checked first.
    """
    bindings = list(config.bindings)

    def router(msg) -> str:
        # Check explicit bindings first
        for binding in bindings:
            m = binding.match
            if not m.channel or m.channel != msg.channel:
                continue
            if m.account_id and m.account_id != getattr(msg, "account_id", ""):
                continue
            if m.peer:
                if msg.chat_id == m.peer.id:
                    return binding.agent_id
                continue
            return binding.agent_id
        # Route by account_id — each agent's channel sets account_id=agent.id
        account_id = getattr(msg, "account_id", "")
        if account_id:
            for defn in config.agents.list:
                if defn.id == account_id:
                    return defn.id
        # Fallback to first agent
        return config.agents.list[0].id if config.agents.list else ""

    return router


def copy_templates_to_workspace(user_data_dir: Path, workspace: Path) -> None:
    """Copy template files to workspace, skipping files that already exist."""
    template_dir = Path(operator_use.__file__).resolve().parent / "templates"

    if not template_dir.exists():
        return

    (workspace / "skills").mkdir(parents=True, exist_ok=True)
    (workspace / "knowledge").mkdir(parents=True, exist_ok=True)
    (workspace / "tools").mkdir(parents=True, exist_ok=True)

    for src in template_dir.iterdir():
        if src.is_file():
            dest = workspace / src.name
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        elif src.is_dir():
            dest_dir = workspace / src.name
            dest_dir.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                if f.is_file():
                    dest_file = dest_dir / f.name
                    if not dest_file.exists():
                        shutil.copy2(f, dest_file)


async def _build_recovery_message(
    deferred_task: str,
    improvement_session: str,
    startup_error: str,
    run_id: str,
    agents: dict,
    userdata: "Path",
) -> str:
    """Build the continuation message for a failed self-improvement attempt.

    Loads diffs + recent log history, then asks the LLM to distill them into a
    concise analysis.  Falls back to a plain structured message on any error so
    the agent still gets useful context even if the synthesis call fails.
    """
    from operator_use.interceptor import load_session_diffs, InterceptorLog

    diffs = load_session_diffs(improvement_session, userdata)
    log_entries = InterceptorLog(userdata).get_for_run(run_id)

    diff_text = (
        "\n\n".join(f"### {d['file']}\n```diff\n{d['diff'][:600]}\n```" for d in diffs)
        or "(no diffs recorded)"
    )

    log_text = (
        "\n".join(
            f"- Attempt {e['attempt']} ({e['timestamp'][:16]}) | "
            f"files: {e['files_changed']} | error: {e['error_preview'][:120]}"
            for e in log_entries
        )
        or "(no previous attempts)"
    )

    fallback = (
        f"[SELF-IMPROVEMENT RECOVERY]\n\n"
        f"Startup failed after your last code change. Files were automatically reverted.\n\n"
        f"**Error:**\n```\n{startup_error[-1500:]}\n```\n\n"
        f"**Changes you made (diffs):**\n{diff_text}\n\n"
        f"**Previous attempt history:**\n{log_text}\n\n"
        f"**Deferred task:**\n{deferred_task}\n\n"
        f"Analyze the error, determine the root cause, and try a corrected approach."
    )

    # Attempt LLM synthesis to produce a cleaner, more actionable message.
    if not agents:
        return fallback
    try:
        agent = next(iter(agents.values()))
        from operator_use.messages import HumanMessage

        synthesis_prompt = (
            f"An AI agent attempted to improve its own codebase but the change caused a startup failure.\n\n"
            f"Error:\n{startup_error[-800:]}\n\n"
            f"Diffs:\n{diff_text[:1200]}\n\n"
            f"Previous attempts:\n{log_text}\n\n"
            f"In 3-5 sentences, explain: what was attempted, what went wrong (root cause), "
            f"and what a corrected approach should do differently."
        )
        event = await agent.llm.ainvoke(
            messages=[HumanMessage(content=synthesis_prompt)],
            tools=[],
        )
        synthesis = (event.content or "").strip()
        if synthesis:
            return (
                f"[SELF-IMPROVEMENT RECOVERY]\n\n"
                f"**Analysis:**\n{synthesis}\n\n"
                f"**Full error:**\n```\n{startup_error[-1000:]}\n```\n\n"
                f"**Diffs:**\n{diff_text}\n\n"
                f"**Deferred task:**\n{deferred_task}\n\n"
                f"Files have been reverted. Please try a corrected approach."
            )
    except Exception as exc:
        logger.warning("_build_recovery_message: LLM synthesis failed (%s), using fallback", exc)

    return fallback


async def main():
    from operator_use.config.paths import get_userdata_dir

    USERDATA_DIR = get_userdata_dir()
    verbose = os.getenv("OPERATOR_VERBOSE", "").lower() in ("1", "true", "yes")
    setup_logging(USERDATA_DIR, verbose=verbose)

    try:
        config = load_config(USERDATA_DIR)
    except FileNotFoundError:
        print("Error: No config.json found. Please run 'uv run main.py onboard' first.")
        return

    # Copy templates for each defined agent workspace
    if not config.agents.list:
        print("Error: No agents defined in config. Run 'operator onboard' to set up an agent.")
        return
    for defn in config.agents.list:
        copy_templates_to_workspace(USERDATA_DIR, workspace=_resolve_agent_workspace(defn))

    bus = Bus()
    _restart_file = USERDATA_DIR / "restart.json"

    def _on_gateway_ready() -> None:
        """Called by the gateway the moment all channels are live.
        Deleting restart.json is the startup-ok signal — its presence after
        worker exit means the gateway never came up (startup failure)."""
        import json as _json

        notify = None
        try:
            if _restart_file.exists():
                data = _json.loads(_restart_file.read_text())
                notify = data.get("notify_restart")
            _restart_file.unlink(missing_ok=True)
            logger.info("restart.json deleted — startup probe passed")
        except Exception as exc:
            logger.warning("Could not delete restart.json in on_ready: %s", exc)

        if notify:

            async def _send_restart_notification() -> None:
                await asyncio.sleep(3)  # give channels time to connect
                await bus.publish_outgoing(
                    OutgoingMessage(
                        channel=notify["channel"],
                        chat_id=notify["chat_id"],
                        account_id=notify.get("account_id", ""),
                        parts=[
                            TextPart(content="System restarted. Send me a message to continue.")
                        ],
                        reply=False,
                    )
                )

            asyncio.create_task(_send_restart_notification())

    gateway = Gateway(bus=bus, on_ready=_on_gateway_ready)

    # Wire per-agent channels — each agent owns its own bot token
    for defn in config.agents.list:
        if not defn.channels:
            continue
        tg = defn.channels.telegram
        if tg.enabled and tg.token:
            per_tg = TelegramConfig(
                enabled=True,
                token=tg.token,
                account_id=defn.id,
                allow_from=tg.allow_from,
                reply_to_message=tg.reply_to_message,
            )
            gateway.add_channel(TelegramChannel(config=per_tg, bus=bus))
        dc = defn.channels.discord
        if dc.enabled and dc.token:
            per_dc = DiscordConfig(
                enabled=True,
                token=dc.token,
                account_id=defn.id,
                allow_from=dc.allow_from,
                reply_to_message=dc.reply_to_message,
            )
            gateway.add_channel(DiscordChannel(config=per_dc, bus=bus))
        sl = defn.channels.slack
        if sl.enabled and sl.bot_token and sl.app_token:
            per_sl = SlackConfig(
                enabled=True,
                bot_token=sl.bot_token,
                app_token=sl.app_token,
                account_id=defn.id,
                allow_from=sl.allow_from,
                reply_to_message=sl.reply_to_message,
            )
            gateway.add_channel(SlackChannel(config=per_sl, bus=bus))
        tw = defn.channels.twitch
        if tw.enabled and tw.token and tw.nick and tw.channel_name:
            per_tw = TwitchConfig(
                enabled=True,
                token=tw.token,
                nick=tw.nick,
                channel_name=tw.channel_name,
                account_id=defn.id,
                allow_from=tw.allow_from,
                prefix=tw.prefix,
            )
            gateway.add_channel(TwitchChannel(config=per_tw, bus=bus))

    # Add JSON-RPC 2.0 stdio channel if OPERATOR_STDIO=1
    stdio_enabled = os.getenv("OPERATOR_STDIO", "").lower() in ("1", "true", "yes")
    if stdio_enabled:
        stdio_cfg = ACPStdioConfig(enabled=True)
        stdio_channel = ACPStdioChannel(config=stdio_cfg, bus=bus)
        gateway.add_channel(stdio_channel)

    acp_server_cfg = config.acp_server
    acp_server_enabled = acp_server_cfg.enabled

    any_channel_active = (
        any(
            defn.channels
            and (
                (defn.channels.telegram.enabled and defn.channels.telegram.token)
                or (defn.channels.discord.enabled and defn.channels.discord.token)
                or (defn.channels.slack.enabled and defn.channels.slack.bot_token)
                or (defn.channels.twitch.enabled and defn.channels.twitch.token)
            )
            for defn in config.agents.list
        )
        or stdio_enabled
        or acp_server_enabled
    )

    if not any_channel_active:
        print("Error: No channel configured. Add a channels block to each agent in config.json.")
        return

    stt = _make_stt(config)
    tts = _make_tts(config)

    async def on_job(job: CronJob):
        channel = job.payload.channel
        chat_id = job.payload.chat_id
        message = job.payload.message
        if not message or not channel or not chat_id:
            return

        if job.payload.deliver:
            await bus.publish_outgoing(
                OutgoingMessage(
                    chat_id=chat_id,
                    channel=channel,
                    account_id=job.payload.account_id,
                    parts=[TextPart(content=message)],
                    reply=False,
                )
            )
        else:
            await bus.publish_incoming(
                IncomingMessage(
                    channel=channel,
                    chat_id=chat_id,
                    account_id=job.payload.account_id,
                    parts=[TextPart(content=message)],
                    user_id="cron",
                    metadata={"_cron_job": True, "job_id": job.id, "job_name": job.name},
                )
            )

    cron_store = USERDATA_DIR / "crons.json"
    cron = Cron(store_path=cron_store, on_job=on_job)

    image_provider = _make_image(config)
    search_provider = _make_search(config)  # always set, DDGS is the default
    agents, mcp_manager = _build_agents(
        config, cron=cron, gateway=gateway, bus=bus, image=image_provider, search=search_provider
    )

    # Add ACP server channel after agents are built so all agents are discoverable
    if acp_server_enabled:
        # Build per-agent token map from agent definitions that have acp_token set.
        # When any agent has a token, per-agent auth is used and global auth_token is ignored.
        per_agent_tokens = {
            defn.id: defn.acp_token for defn in config.agents.list if defn.acp_token
        }
        acp_srv_config = ACPServerConfig(
            enabled=True,
            host=acp_server_cfg.host,
            port=acp_server_cfg.port,
            id=acp_server_cfg.id,
            auth_token=acp_server_cfg.auth_token,
            per_agent_tokens=per_agent_tokens,
            public_url=acp_server_cfg.public_url,
        )
        acp_channel = ACPChannel(config=acp_srv_config, bus=bus, agents=agents)
        gateway.add_channel(acp_channel)

    async def _graceful_restart() -> None:
        """Cancel all running asyncio tasks so main()'s finally block can run cleanly."""
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    async def _on_gateway_restart() -> None:
        from operator_use.agent.tools.builtin.control_center import request_restart

        request_restart()
        await _graceful_restart()

    gateway.on_restart = _on_gateway_restart

    for agent in agents.values():
        agent.tool_register.set_extension("_graceful_restart_fn", _graceful_restart)

    router = _build_router(config)

    defaults = config.agents.defaults
    orchestrator = Orchestrator(
        bus=bus,
        agents=agents,
        stt=stt,
        tts=tts,
        streaming=defaults.streaming,
        gateway=gateway,
        cron=cron,
        router=router,
    )

    async def on_heartbeat(content: str) -> None:
        return await orchestrator.process_direct(
            content=content,
            channel="cli",
            chat_id="heartbeat",
        )

    first_agent_workspace = _resolve_agent_workspace(config.agents.list[0])
    heartbeat = Heartbeat(workspace=first_agent_workspace, on_heartbeat=on_heartbeat)

    shared_channels = []
    if stdio_enabled:
        shared_channels.append("Stdio(JSON-RPC 2.0)")
    if acp_server_enabled:
        shared_channels.append(f"ACP({acp_server_cfg.host}:{acp_server_cfg.port})")

    restart_file = USERDATA_DIR / "restart.json"
    if restart_file.exists():
        _console.clear()

    stt_conf = config.stt
    tts_conf = config.tts

    suffix = "  (Ctrl+C to stop)"
    ver = _version()
    ver_str = f" [{_M}]v{ver}[/{_M}]" if ver else ""
    _console.print(f"┌ [bold {_P}]Operator[/bold {_P}]{ver_str}[{_M}]{suffix}[/{_M}]")
    _console.print("│")

    for defn in config.agents.list:
        llm = defn.llm_config
        llm_str = f"{llm.provider} / {llm.model}" if llm else "not configured"

        # Channels for this agent
        agent_channels = []
        if defn.channels:
            if defn.channels.telegram.enabled and defn.channels.telegram.token:
                agent_channels.append("Telegram")
            if defn.channels.discord.enabled and defn.channels.discord.token:
                agent_channels.append("Discord")
            if defn.channels.slack.enabled and defn.channels.slack.bot_token:
                agent_channels.append("Slack")
            if defn.channels.twitch.enabled and defn.channels.twitch.token:
                agent_channels.append("Twitch")
        ch_str = ", ".join(agent_channels) if agent_channels else "none"

        enabled_plugins = [p.id for p in defn.plugins if p.enabled]
        caps_str = ", ".join(enabled_plugins) if enabled_plugins else "none"

        _console.print(f"│ [{_P}]{defn.id}[/{_P}]")
        _console.print(f"│   [{_M}]{'llm':<10}[/{_M}] [{_S}]{llm_str}[/{_S}]")
        _console.print(f"│   [{_M}]{'channels':<10}[/{_M}] [{_S}]{ch_str}[/{_S}]")
        _console.print(f"│   [{_M}]{'use':<10}[/{_M}] [{_S}]{caps_str}[/{_S}]")
        _console.print("│")

    if stt_conf.enabled and stt_conf.provider:
        _row("STT", f"{stt_conf.provider} / {stt_conf.model}")
    if tts_conf.enabled and tts_conf.provider:
        _row("TTS", f"{tts_conf.provider} / {tts_conf.model}")
    img_conf = config.image
    if img_conf.enabled and img_conf.provider:
        _row("Image", f"{img_conf.provider} / {img_conf.model}")

    restart_file = USERDATA_DIR / "restart.json"

    try:
        if config.heartbeat.enabled:
            heartbeat.start()
        cron.start()

        cron_jobs = cron.list_jobs()
        heartbeat_mins = int(heartbeat.interval // 60)
        _row("Heartbeat", f"every {heartbeat_mins} min" if config.heartbeat.enabled else "disabled")
        _row("Cron", f"{len(cron_jobs)} jobs")

        if restart_file.exists():
            import json as _json

            restart_data = _json.loads(restart_file.read_text(encoding="utf-8"))
            resume_task = restart_data.get("resume_task", "")
            resume_channel = restart_data.get("channel")
            resume_chat_id = restart_data.get("chat_id")
            resume_account_id = restart_data.get("account_id", "")
            improvement_session = restart_data.get("improvement_session")
            startup_error = restart_data.get("startup_error")
            deferred_task = restart_data.get("deferred_task", resume_task)
            run_id = restart_data.get("run_id")
            # Inject run_id into all agents so control_center can carry it
            # forward into the next restart.json if the agent retries.
            if run_id:
                for _agent in agents.values():
                    _agent.tool_register.set_extension("_run_id", run_id)
            print(
                f"[restart] Continuation found (channel={resume_channel} chat_id={resume_chat_id}): {resume_task[:80]}",
                flush=True,
            )
            if resume_task and resume_channel and resume_chat_id:

                async def _dispatch_continuation():
                    await asyncio.sleep(10)
                    final_task = resume_task

                    # Recovery path: build an informative message for the agent.
                    if improvement_session and startup_error and run_id:
                        final_task = await _build_recovery_message(
                            deferred_task=deferred_task,
                            improvement_session=improvement_session,
                            startup_error=startup_error,
                            run_id=run_id,
                            agents=agents,
                            userdata=USERDATA_DIR,
                        )
                    elif (
                        run_id
                        and not startup_error
                        and deferred_task
                        and deferred_task != resume_task
                    ):
                        # Successful restart after one or more recovery cycles —
                        # resume the deferred task now that the fix succeeded.
                        final_task = deferred_task

                    print(
                        f"[restart] Dispatching continuation to channel={resume_channel} chat_id={resume_chat_id}",
                        flush=True,
                    )
                    await bus.publish_incoming(
                        IncomingMessage(
                            channel=resume_channel,
                            chat_id=resume_chat_id,
                            account_id=resume_account_id,
                            parts=[TextPart(content=final_task)],
                            user_id="restart",
                        )
                    )

                asyncio.ensure_future(_dispatch_continuation())

        await asyncio.gather(
            gateway.start(),
            orchestrator.ainvoke(),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        if config.heartbeat.enabled:
            heartbeat.stop()
        cron.stop()
        # Gracefully disconnect all MCP servers
        if mcp_manager is not None:
            try:
                await mcp_manager.disconnect_all()
            except Exception as e:
                logger.warning(f"Error disconnecting MCP servers during shutdown: {e}")
        try:
            await asyncio.shield(gateway.stop())
        except asyncio.CancelledError:
            pass


RESTART_EXIT_CODE = 75
# Exit code written by the worker when asyncio.run(main()) raises an unhandled
# exception — signals the supervisor that startup failed and recovery is needed.
STARTUP_FAILURE_EXIT_CODE = 76


def _attempt_startup_recovery(exit_code: int) -> bool:
    """Called by the supervisor when the worker exits abnormally.

    If ``restart.json`` contains an ``improvement_session`` (written by
    control_center before a self-improvement restart), the agent made code
    changes, restarted, and the new worker crashed before reading restart.json.
    In this case, we revert files and build a recovery message.

    If ``startup_error.json`` exists but restart.json doesn't (or has no
    improvement_session), it's a regular startup error — we read and display it
    for debugging but don't attempt recovery.

    Steps for self-improvement recovery:
    1. Read ``startup_error.json`` written by the worker exception handler.
    2. Call ``revert_session()`` to restore every snapshotted file from its
       original content — no git required.
    3. Append to ``interceptor_log.jsonl`` so the agent can see the full
       consecutive failure history on the next attempt.
    4. Rewrite ``restart.json`` keeping the improvement_session and raw error
       so the clean worker can run LLM synthesis before dispatching to agent.

    Returns True if recovery was attempted (caller should restart),
    False otherwise (caller should sys.exit).
    """
    import json as _json
    from operator_use.config.paths import get_userdata_dir as _gud
    from operator_use.interceptor import revert_session, InterceptorLog

    userdata = _gud()
    restart_file = userdata / "restart.json"
    error_file = userdata / "startup_error.json"

    # Read error if present (exists for both regular and self-improvement crashes).
    error_text = None
    if error_file.exists():
        try:
            err_data = _json.loads(error_file.read_text(encoding="utf-8"))
            error_file.unlink()
            error_text = err_data.get("error")
        except Exception:
            pass

    # Check if restart.json exists (indicates a prior self-improvement restart).
    if not restart_file.exists():
        # Regular startup error (no self-improvement in flight) — show error and exit.
        if error_text:
            print(
                f"\n[supervisor] Startup failure (exit={exit_code}):\n{error_text[-1500:]}",
                flush=True,
            )
        return False

    try:
        restart_data = _json.loads(restart_file.read_text(encoding="utf-8"))
    except Exception:
        if error_text:
            print(
                f"\n[supervisor] Startup failure (exit={exit_code}):\n{error_text[-1500:]}",
                flush=True,
            )
        return False

    # Check if this is a self-improvement recovery scenario.
    improvement_session = restart_data.get("improvement_session")
    if not improvement_session:
        # restart.json exists but no improvement_session — not a recovery scenario.
        if error_text:
            print(
                f"\n[supervisor] Startup failure (exit={exit_code}):\n{error_text[-1500:]}",
                flush=True,
            )
        return False

    # Self-improvement recovery: revert files, log failure, and prepare recovery message.
    if not error_text:
        error_text = "(no traceback captured — likely an import or syntax error)"

    print(
        f"\n[supervisor] Self-improvement startup failure (exit={exit_code}, "
        f"session={improvement_session}). Reverting files...",
        flush=True,
    )
    print(f"[supervisor] Error:\n{error_text[-800:]}", flush=True)

    # Restore files from snapshots.
    reverted_files = revert_session(improvement_session, userdata)
    if reverted_files:
        print(
            f"[supervisor] Reverted {len(reverted_files)} file(s): "
            f"{[__import__('pathlib').Path(p).name for p in reverted_files]}",
            flush=True,
        )
    else:
        print("[supervisor] No snapshots found — files not reverted.", flush=True)

    # Determine run_id: carry forward the existing one if this is a retry,
    # otherwise generate a fresh one to mark the start of a new failure run.
    # Preserve deferred_task across retries — first cycle seeds it from resume_task.
    deferred_task = restart_data.get("deferred_task") or restart_data.get("resume_task", "")
    run_id: str = (
        restart_data.get("run_id")
        or f"R-{__import__('datetime').datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    print(f"[supervisor] run_id={run_id}", flush=True)

    # Append to consecutive failure log.
    try:
        InterceptorLog(userdata).append(
            run_id=run_id,
            task_preview=deferred_task,
            session_id=improvement_session,
            files_changed=reverted_files,
            error_preview=error_text,
            reverted_files=reverted_files,
        )
    except Exception as log_exc:
        print(f"[supervisor] Could not write improvement log: {log_exc}", flush=True)

    # Rewrite restart.json — keep improvement_session, run_id, and raw error for
    # the LLM synthesis step that runs inside the clean worker's startup.
    restart_data["deferred_task"] = deferred_task
    restart_data["startup_error"] = error_text[-3000:]
    restart_data["run_id"] = run_id
    # task field left as-is so channel/chat_id dispatch still works

    try:
        restart_file.write_text(_json.dumps(restart_data), encoding="utf-8")
        print("[supervisor] Recovery context saved to restart.json.", flush=True)
    except Exception as e:
        print(f"[supervisor] Could not save recovery context: {e}", flush=True)

    return True


def run(verbose: bool = False) -> None:
    """Supervisor/worker restart pattern.

    If IS_WORKER=1 is set, this is the worker — run main() directly and exit.
    Otherwise this is the supervisor — spawn the worker as a child subprocess
    and relaunch it whenever it exits with RESTART_EXIT_CODE (75).

    This gives a fresh Python process on every restart (fresh imports, fresh
    state) while keeping the terminal attached on Windows, because the
    supervisor blocks on subprocess.run() the whole time.
    """
    import subprocess
    import sys

    if os.getenv("IS_WORKER"):
        from operator_use.agent.tools.builtin.control_center import requested_exit_code

        try:
            asyncio.run(main())
        except Exception:
            import json as _json
            import traceback as _tb
            from operator_use.config.paths import get_userdata_dir as _gud

            _error_file = _gud() / "startup_error.json"
            try:
                _error_file.parent.mkdir(parents=True, exist_ok=True)
                _error_file.write_text(
                    _json.dumps({"error": _tb.format_exc()}),
                    encoding="utf-8",
                )
            except Exception:
                pass
            sys.exit(STARTUP_FAILURE_EXIT_CODE)
        sys.exit(requested_exit_code())

    worker_env = {**os.environ, "IS_WORKER": "1", "OPERATOR_VERBOSE": "1" if verbose else "0"}
    while True:
        result = subprocess.run(
            [sys.executable, "-m", "operator_use"] + sys.argv[1:], env=worker_env
        )
        if result.returncode == RESTART_EXIT_CODE:
            print("[supervisor] Restarting...", flush=True)
            continue
        if result.returncode != 0 and _attempt_startup_recovery(result.returncode):
            print("[supervisor] Restarting after recovery...", flush=True)
            continue
        sys.exit(result.returncode)


if __name__ == "__main__":
    run()
