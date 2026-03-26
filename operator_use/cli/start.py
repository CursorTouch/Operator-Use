"""Run Operator with channels and agents."""

import asyncio
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
import logging
from rich.console import Console

load_dotenv()

logger = logging.getLogger(__name__)

_console = Console()
_P = "#e5c07b"   # primary   – warm gold
_S = "#61afef"   # secondary – blue
_M = "#abb2bf"   # muted     – gray


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
from operator_use.gateway.channels import TelegramChannel, DiscordChannel, SlackChannel, TwitchChannel
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
from operator_use.paths import get_named_workspace_dir
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
    "elevenlabs": "TTSElevenLabs",
    "deepgram": "TTSDeepgram",
    "sarvam": "TTSSarvam",
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


def _make_models(config: Config) -> tuple[Optional[BaseChatLLM], Optional[BaseSTT], Optional[BaseTTS]]:
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


def _build_agents(config: Config, cron, gateway, bus) -> dict[str, Agent]:
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
            raise ValueError(f"Agent '{defn.id}' has no llmConfig. Set it in config.json or run 'operator onboard'.")
        llm = _make_llm(config, llm_conf)
        if llm is None:
            raise ValueError(f"Agent '{defn.id}': failed to initialize LLM provider '{llm_conf.provider}'. Check the provider name and API key.")
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
        )

    for agent in agents.values():
        agent.tool_register.set_extension("_agent_registry", agents)

    return agents


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

async def main():
    from operator_use.paths import get_userdata_dir
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
    gateway = Gateway(bus=bus)

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

    # Add ACP server channel if enabled in config
    acp_server_cfg = config.acp_server
    acp_server_enabled = acp_server_cfg.enabled
    if acp_server_enabled:
        acp_srv_config = ACPServerConfig(
            enabled=True,
            host=acp_server_cfg.host,
            port=acp_server_cfg.port,
            agent_id=acp_server_cfg.agent_id,
            agent_name=acp_server_cfg.agent_name,
            agent_description=acp_server_cfg.agent_description,
            auth_token=acp_server_cfg.auth_token,
            public_url=acp_server_cfg.public_url,
        )
        acp_channel = ACPChannel(config=acp_srv_config, bus=bus)
        gateway.add_channel(acp_channel)

    any_channel_active = (
        any(
            defn.channels and (
                (defn.channels.telegram.enabled and defn.channels.telegram.token) or
                (defn.channels.discord.enabled and defn.channels.discord.token) or
                (defn.channels.slack.enabled and defn.channels.slack.bot_token) or
                (defn.channels.twitch.enabled and defn.channels.twitch.token)
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

    agents = _build_agents(config, cron=cron, gateway=gateway, bus=bus)

    async def _graceful_restart() -> None:
        """Cancel all running asyncio tasks so main()'s finally block can run cleanly."""
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

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
            restart_file.unlink()
            task = restart_data.get("task", "")
            resume_channel = restart_data.get("channel")
            resume_chat_id = restart_data.get("chat_id")
            resume_account_id = restart_data.get("account_id", "")
            print(f"[restart] Continuation found (channel={resume_channel} chat_id={resume_chat_id}): {task[:80]}", flush=True)
            if task and resume_channel and resume_chat_id:
                async def _dispatch_continuation():
                    await asyncio.sleep(10)
                    print(f"[restart] Dispatching continuation to channel={resume_channel} chat_id={resume_chat_id}", flush=True)
                    await bus.publish_incoming(
                        IncomingMessage(
                            channel=resume_channel,
                            chat_id=resume_chat_id,
                            account_id=resume_account_id,
                            parts=[TextPart(content=task)],
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
        try:
            await asyncio.shield(gateway.stop())
        except asyncio.CancelledError:
            pass


RESTART_EXIT_CODE = 75


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
        asyncio.run(main())
        sys.exit(requested_exit_code())

    worker_env = {**os.environ, "IS_WORKER": "1", "OPERATOR_VERBOSE": "1" if verbose else "0"}
    while True:
        result = subprocess.run([sys.executable, "-m", "operator_use"] + sys.argv[1:], env=worker_env)
        if result.returncode == RESTART_EXIT_CODE:
            print("[supervisor] Restarting...", flush=True)
            continue
        sys.exit(result.returncode)


if __name__ == "__main__":
    run()
