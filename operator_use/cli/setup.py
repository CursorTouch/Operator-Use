import sys
import json
import re as _re
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from operator_use.cli.tui import BackRequest, print_banner, print_start, print_step, select, text_input, confirm, print_end, print_end_first_install, console

# --- Registry Data ---

LLM_PROVIDERS: dict[str, list[tuple[str, str]]] = {
    "Groq": [
        ("Llama 4 Scout 17B (recommended)", "meta-llama/llama-4-scout-17b-16e-instruct"),
        ("GPT-OSS 120B (reasoning)", "openai/gpt-oss-120b"),
        ("GPT-OSS 20B (reasoning)", "openai/gpt-oss-20b"),
        ("Kimi K2 (256K context)", "moonshotai/kimi-k2-instruct-0905"),
        ("Qwen3 32B (reasoning)", "qwen/qwen3-32b"),
        ("Llama 3.3 70B", "llama-3.3-70b-versatile"),
        ("Llama 3.1 8B", "llama-3.1-8b-instant"),
        ("Compound (agentic)", "groq/compound"),
        ("Compound Mini (agentic)", "groq/compound-mini"),
    ],
    "NVIDIA": [
        ("Llama 3.3 70B (recommended)", "meta/llama-3.3-70b-instruct"),
        ("Llama 3.1 405B", "meta/llama-3.1-405b-instruct"),
        ("DeepSeek R1", "deepseek-ai/deepseek-r1"),
        ("Mistral Large", "mistralai/mistral-large"),
        ("Nemotron 4 340B", "nvidia/nemotron-4-340b-instruct"),
        ("Qwen 3.5 397B", "qwen/qwen3.5-397b-a17b"),
    ],
    "OpenAI": [
        ("GPT-5.4 (flagship)", "gpt-5.4"),
        ("GPT-5.4 mini", "gpt-5.4-mini"),
        ("GPT-5.4 nano", "gpt-5.4-nano"),
        ("GPT-4.1", "gpt-4.1"),
        ("GPT-4.1 mini", "gpt-4.1-mini"),
        ("GPT-4.1 nano", "gpt-4.1-nano"),
        ("o3", "o3"),
        ("o3-mini", "o3-mini"),
        ("o3-pro", "o3-pro"),
        ("o4-mini", "o4-mini"),
        ("o1", "o1"),
    ],
    "Anthropic": [
        ("Claude Sonnet 4.6 (recommended)", "claude-sonnet-4-6"),
        ("Claude Opus 4.6", "claude-opus-4-6"),
        ("Claude Haiku 4.5", "claude-haiku-4-5-20251001"),
        ("Claude Sonnet 4.5", "claude-sonnet-4-5"),
        ("Claude Opus 4.5", "claude-opus-4-5"),
        ("Claude Opus 4.1", "claude-opus-4-1"),
        ("Claude Sonnet 4", "claude-sonnet-4-20250514"),
        ("Claude Opus 4", "claude-opus-4-20250514"),
    ],
    "Google": [
        ("Gemini 3 Pro (recommended)", "gemini-3-pro"),
        ("Gemini 3 Flash", "gemini-3-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),
    ],
    "Mistral": [
        ("Mistral Large 3 (recommended)", "mistral-large-2512"),
        ("Mistral Small 4", "mistral-small-2603"),
        ("Magistral Medium 1.2 (reasoning)", "magistral-medium-2509"),
        ("Magistral Small 1.2 (reasoning)", "magistral-small-2509"),
        ("Mistral Medium 3.1", "mistral-medium-3.1"),
        ("Mistral Small 3.2", "mistral-small-2506"),
        ("Devstral 2 (code)", "devstral-2512"),
        ("Ministral 14B", "ministral-14b-2512"),
        ("Ministral 8B", "ministral-8b-2512"),
        ("Ministral 3B", "ministral-3b-2512"),
    ],
    "DeepSeek": [
        ("DeepSeek V3 Chat (recommended)", "deepseek-chat"),
        ("DeepSeek R1 Reasoner", "deepseek-reasoner"),
        ("DeepSeek R1", "deepseek-r1"),
        ("DeepSeek V3", "deepseek-v3"),
    ],
    "Cerebras": [
        ("GPT-OSS 120B (recommended)", "gpt-oss-120b"),
        ("Qwen3 235B", "qwen-3-235b-a22b-instruct"),
        ("Qwen3 32B", "qwen3-32b"),
        ("Llama 4 Scout", "llama4-scout"),
        ("Llama 3.3 70B", "llama-3.3-70b"),
        ("Llama 3.1 8B", "llama3.1-8b"),
        ("ZAI-GLM 4.7", "zai-glm-4.7"),
        ("DeepSeek R1 Distill Llama 70B", "deepseek-r1-distill-llama-70b"),
    ],
    "OpenRouter": [
        ("Llama 4 Scout (recommended)", "meta-llama/llama-4-scout-17b-16e-instruct"),
        ("Claude Sonnet 4.6", "anthropic/claude-sonnet-4-6"),
        ("GPT-5.4", "openai/gpt-5.4"),
        ("Gemini 3 Flash", "google/gemini-3-flash"),
        ("DeepSeek V3", "deepseek/deepseek-chat"),
        ("Llama 3.3 70B", "meta-llama/llama-3.3-70b-instruct"),
    ],
    "Ollama": [
        ("Llama 3.3 70B", "llama3.3"),
        ("Llama 3.2", "llama3.2"),
        ("DeepSeek R1", "deepseek-r1"),
        ("Qwen 3.5", "qwen3.5"),
        ("Mistral", "mistral"),
        ("Gemma 3", "gemma3"),
    ],
    "Antigravity": [
        ("Gemini 3 Pro (recommended)", "gemini-3-pro"),
        ("Gemini 3 Flash", "gemini-3-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Claude Opus 4.6", "claude-opus-4-6"),
        ("Claude Sonnet 4.6", "claude-sonnet-4-6"),
    ],
    "Codex": [
        ("GPT-5.4 (recommended)", "gpt-5.4"),
        ("GPT-5.4 mini", "gpt-5.4-mini"),
    ],
    "Claude Code": [
        ("Claude Sonnet 4.6 (recommended)", "claude-sonnet-4-6"),
        ("Claude Opus 4.6", "claude-opus-4-6"),
    ],
    "GitHub Copilot": [
        ("GPT-5.4 (recommended)", "gpt-5.4"),
        ("Claude Sonnet 4.6", "claude-sonnet-4-6"),
        ("Claude Opus 4.6", "claude-opus-4-6"),
        ("Gemini 3 Pro", "gemini-3-pro"),
        ("GPT-4.1", "gpt-4.1"),
        ("o3", "o3"),
    ],
}

STT_PROVIDERS: dict[str, list[tuple[str, str]]] = {
    "Groq": [
        ("Whisper Large v3 Turbo (recommended)", "whisper-large-v3-turbo"),
        ("Whisper Large v3", "whisper-large-v3"),
    ],
    "OpenAI": [
        ("Whisper 1", "whisper-1"),
    ],
    "Google": [
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
    ],
    "ElevenLabs": [
        ("Scribe v1", "scribe_v1"),
    ],
    "Deepgram": [
        ("Nova 3 (recommended)", "nova-3"),
        ("Nova 2", "nova-2"),
    ],
    "Sarvam": [
        ("Saaras v3 (recommended)", "saaras:v3"),
    ],
}

TTS_PROVIDERS: dict[str, list[tuple[str, str]]] = {
    "Groq": [
        ("Orpheus v1 English", "canopylabs/orpheus-v1-english"),
    ],
    "OpenAI": [
        ("TTS-1 HD", "tts-1-hd"),
        ("TTS-1", "tts-1"),
    ],
    "Google": [
        ("Gemini 2.0 Flash Preview TTS", "gemini-2.0-flash-preview-tts"),
    ],
    "ElevenLabs": [
        ("Multilingual v2", "eleven_multilingual_v2"),
        ("Flash v2.5", "eleven_flash_v2_5"),
    ],
    "Deepgram": [
        ("Aura 2", "aura-2"),
    ],
    "Sarvam": [
        ("Bulbul v3 (recommended)", "bulbul:v3"),
    ],
}

VOICES: dict[str, list[str]] = {
    "OpenAI": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
    "Groq": ["autumn", "diana", "hannah", "austin", "daniel", "troy"],
    "Google": ["Aoede", "Charon", "Fenrir", "Kore", "Puck"],
    "ElevenLabs": ["Rachel", "Drew", "Clyde", "Paul", "Domi"],
    "Deepgram": ["asteria-en", "luna-en", "stella-en", "athena-en", "hera-en"],
    "Sarvam": ["aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan", "shubh", "advait", "amelia", "sophia", "anand", "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali"],
}

OAUTH_PROVIDERS = {"Antigravity", "Codex", "Claude Code", "GitHub Copilot"}

# Providers that never require an API key (local / keyless)
NO_KEY_PROVIDERS = {"Ollama"}

OAUTH_NOTES = {
    "Antigravity": "Uses Google Cloud Code Assist OAuth. Run: operator auth antigravity",
    "Codex": "Uses ChatGPT subscription OAuth. Run: operator auth codex",
    "Claude Code": "Uses Claude Code CLI OAuth. Run: operator auth claude-code",
    "GitHub Copilot": "Uses GitHub OAuth (Device Flow). Run: operator auth github-copilot",
}

CHANNEL_NOTES = {
    "Telegram": "Create a bot via @BotFather on Telegram and copy the token.",
    "Discord":  "Create a bot at discord.com/developers, enable Message Content Intent, and copy the token.",
    "Slack":    "Create a Slack app at api.slack.com. Bot token starts with xoxb-, app token with xapp-.",
}


def get_provider_key(name: str) -> str:
    key_map = {
        "OpenRouter": "open_router",
        "ElevenLabs": "elevenlabs",
        "Deepgram": "deepgram",
        "NVIDIA": "nvidia",
        "DeepSeek": "deepseek",
        "Sarvam": "sarvam",
        "Claude Code": "claude_code",
        "GitHub Copilot": "github_copilot",
    }
    return key_map.get(name, name.lower())


def _select_model(label: str, options: list[tuple[str, str]]) -> str:
    custom_label = "Custom (enter model ID)..."
    display_names = [d for d, _ in options] + [custom_label]
    chosen_display = select(label, display_names)
    if chosen_display == custom_label:
        return text_input("Enter model ID:")
    return next(mid for d, mid in options if d == chosen_display)


from operator_use.config import (
    Config, LLMConfig, STTConfig, TTSConfig,
    AgentDefaults, AgentsConfig, ProvidersConfig, ProviderConfig,
    ChannelsConfig, TelegramConfig, DiscordConfig, SlackConfig,
    AgentDefinition, ACPServerSettings, ACPAgentEntry, HeartbeatConfig,
)


def configure_channel(existing: ChannelsConfig | None = None) -> ChannelsConfig:
    """Prompt the user to pick and configure one channel. Returns updated ChannelsConfig."""
    channels = existing or ChannelsConfig()
    channel_name = select("Pick a channel to connect:", ["Telegram", "Discord", "Slack"])

    note = CHANNEL_NOTES.get(channel_name, "")
    if note:
        console.print("│")
        console.print(f"│  [dim]{note}[/dim]")

    if channel_name == "Telegram":
        token = text_input("Enter Telegram Bot Token:", is_password=True)
        channels.telegram = TelegramConfig(enabled=True, token=token)
    elif channel_name == "Discord":
        token = text_input("Enter Discord Bot Token:", is_password=True)
        channels.discord = DiscordConfig(enabled=True, token=token)
    elif channel_name == "Slack":
        bot_token = text_input("Enter Slack Bot Token (xoxb-...):", is_password=True)
        app_token = text_input("Enter Slack App Token (xapp-...):", is_password=True)
        channels.slack = SlackConfig(enabled=True, bot_token=bot_token, app_token=app_token)

    return channels


def _save_config(
    agent_defs: list[dict],
    stt_enabled: bool,
    stt_provider_key: str,
    stt_model: str,
    tts_enabled: bool,
    tts_provider_key: str,
    tts_model: str,
    tts_voice: str | None,
    heartbeat_enabled: bool,
    heartbeat_llm_provider_key: str,
    heartbeat_llm_model: str,
    api_keys_dict: dict[str, str],
    acp_server: "ACPServerSettings | None" = None,
    acp_agents: "dict[str, ACPAgentEntry] | None" = None,
) -> None:
    """Build the Config object and persist it to disk."""
    from operator_use.paths import get_userdata_dir

    providers = ProvidersConfig()
    for prov, key in api_keys_dict.items():
        if hasattr(providers, prov):
            setattr(providers, prov, ProviderConfig(api_key=key))

    agent_list = []
    for a in agent_defs:
        defn_kwargs: dict = {"id": a["id"]}
        if a["llm_provider_key"] and a["llm_model"]:
            defn_kwargs["llm_config"] = LLMConfig(provider=a["llm_provider_key"], model=a["llm_model"])
        ch = a.get("channels", {})
        if ch.get("telegram") or ch.get("discord") or ch.get("slack_bot"):
            agent_channels = ChannelsConfig()
            if ch.get("telegram"):
                agent_channels.telegram = TelegramConfig(enabled=True, token=ch["telegram"])
            if ch.get("discord"):
                agent_channels.discord = DiscordConfig(enabled=True, token=ch["discord"])
            if ch.get("slack_bot"):
                agent_channels.slack = SlackConfig(enabled=True, bot_token=ch["slack_bot"], app_token=ch.get("slack_app", ""))
            defn_kwargs["channels"] = agent_channels
        defn_kwargs["browser_use"] = bool(a.get("browser_use", True))
        defn_kwargs["computer_use"] = bool(a.get("computer_use", False))
        agent_list.append(AgentDefinition(**defn_kwargs))

    hb_llm = LLMConfig(provider=heartbeat_llm_provider_key, model=heartbeat_llm_model) if heartbeat_llm_provider_key and heartbeat_llm_model else None
    config_obj = Config(
        heartbeat=HeartbeatConfig(enabled=heartbeat_enabled, llm_config=hb_llm),
        agents=AgentsConfig(
            defaults=AgentDefaults(),
            list=agent_list,
        ),
        stt=STTConfig(enabled=stt_enabled, provider=stt_provider_key or None, model=stt_model or None),
        tts=TTSConfig(enabled=tts_enabled, provider=tts_provider_key or None, model=tts_model or None, voice=tts_voice),
        providers=providers,
        acp_server=acp_server or ACPServerSettings(),
        acp_agents=acp_agents or {},
    )

    operator_use_dir = get_userdata_dir()
    operator_use_dir.mkdir(parents=True, exist_ok=True)
    config_path = operator_use_dir / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_obj.model_dump(by_alias=True, exclude_none=True), f, indent=4, ensure_ascii=False)

    key_to_env = {
        "groq":        "GROQ_API_KEY",
        "openai":      "OPENAI_API_KEY",
        "anthropic":   "ANTHROPIC_API_KEY",
        "google":      "GEMINI_API_KEY",
        "nvidia":      "NVIDIA_API_KEY",
        "deepseek":    "DEEPSEEK_API_KEY",
        "cerebras":    "CEREBRAS_API_KEY",
        "open_router": "OPENROUTER_API_KEY",
        "elevenlabs":  "ELEVENLABS_API_KEY",
        "deepgram":    "DEEPGRAM_API_KEY",
        "mistral":     "MISTRAL_API_KEY",
        "azure_openai":"AZURE_OPENAI_API_KEY",
        "sarvam":      "SARVAM_API_KEY",
    }
    env_vars = {key_to_env[k]: v for k, v in api_keys_dict.items() if k in key_to_env}
    if env_vars:
        with open(".env", "a") as f:
            f.write("\n")
            for k, v in env_vars.items():
                f.write(f"{k}={v}\n")


def run_first_install():
    """Linear step-by-step wizard for first-time installation (no config.json exists)."""
    print_banner()
    print_start()

    api_keys_dict: dict[str, str] = {}
    agent_id = "operator"
    llm_provider_key = ""
    llm_model = ""
    agent_channels: dict = {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""}

    def _need_key(prov_key: str, prov_name: str) -> bool:
        return prov_key not in api_keys_dict and prov_name not in OAUTH_PROVIDERS and prov_name not in NO_KEY_PROVIDERS

    step = 0
    while step < 3:
        try:
            if step == 0:
                print_step(1, 3, "Your agent", "Give your agent a name — used for its workspace folder.")
                raw = text_input("Name your agent (e.g. mybot, personal, work):", default=agent_id)
                agent_id = _re.sub(r"[^a-z0-9_-]", "-", raw.strip().lower()) or "operator"
                step = 1
                continue

            if step == 1:
                print_step(2, 3, "Language model", "This is the AI brain. Pick a provider you have access to.")
                prov_name = select("Pick the LLM provider:", list(LLM_PROVIDERS.keys()))
                prov_key = get_provider_key(prov_name)
                if prov_name in OAUTH_PROVIDERS:
                    console.print("│")
                    console.print(f"│  [dim]ℹ  {OAUTH_NOTES[prov_name]}[/dim]")
                elif _need_key(prov_key, prov_name):
                    api_keys_dict[prov_key] = text_input(f"Enter API Key for {prov_name}:", is_password=True)
                llm_model = _select_model("Pick the LLM model:", LLM_PROVIDERS[prov_name])
                llm_provider_key = prov_key
                step = 2
                continue

            print_step(3, 3, "Messaging channel", "Connect a channel to message your agent. You can add more later with `operator channel add`.")
            agent_channels = {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""}
            ch_name = select("Pick a channel to connect:", ["Telegram", "Discord", "Slack", "Skip for now"])
            if ch_name != "Skip for now":
                note = CHANNEL_NOTES.get(ch_name, "")
                if note:
                    console.print("│")
                    console.print(f"│  [dim]{note}[/dim]")
                if ch_name == "Telegram":
                    agent_channels["telegram"] = text_input("Enter Telegram Bot Token:", is_password=True)
                elif ch_name == "Discord":
                    agent_channels["discord"] = text_input("Enter Discord Bot Token:", is_password=True)
                elif ch_name == "Slack":
                    agent_channels["slack_bot"] = text_input("Enter Slack Bot Token (xoxb-...):", is_password=True)
                    agent_channels["slack_app"] = text_input("Enter Slack App Token (xapp-...):", is_password=True)
            step = 3
        except BackRequest:
            if step > 0:
                step -= 1

    agent_defs = [{
        "id": agent_id,
        "llm_provider_key": llm_provider_key,
        "llm_model": llm_model,
        "channels": agent_channels,
    }]

    _save_config(
        agent_defs=agent_defs,
        stt_enabled=False,
        stt_provider_key="",
        stt_model="",
        tts_enabled=False,
        tts_provider_key="",
        tts_model="",
        tts_voice=None,
        heartbeat_enabled=False,
        heartbeat_llm_provider_key="",
        heartbeat_llm_model="",
        api_keys_dict=api_keys_dict,
    )
    print_end_first_install()


# TTS, STT, heartbeat configurable via: operator onboard


def run_initial_setup():
    print_banner()
    print_start("Configure")

    from operator_use.paths import get_userdata_dir
    _config_path = get_userdata_dir() / "config.json"

    # --- Load existing config ---
    existing_data: dict = {}
    if _config_path.exists():
        try:
            with open(_config_path, encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            pass

    # Collect already-saved API keys so we never re-ask for them
    api_keys_dict: dict[str, str] = {}
    for prov, pconf in existing_data.get("providers", {}).items():
        k = pconf.get("apiKey") or pconf.get("api_key", "")
        if k:
            api_keys_dict[prov] = k

    # Unpack existing global defaults
    _defaults = existing_data.get("agents", {}).get("defaults", {})
    _stt = existing_data.get("stt", {})
    _tts = existing_data.get("tts", {})
    _hb = existing_data.get("heartbeat", {})
    heartbeat_enabled: bool = bool(_hb.get("enabled", False))
    _hb_llm = _hb.get("llmConfig", _hb.get("llm_config", {})) or {}
    heartbeat_llm_provider_key: str = _hb_llm.get("provider", "")
    heartbeat_llm_model: str = _hb_llm.get("model", "")

    # --- Global mutable state ---
    stt_enabled: bool     = bool(_stt.get("enabled", False))
    stt_provider_key: str = _stt.get("provider", "") or ""
    stt_model: str        = _stt.get("model", "") or ""

    tts_enabled: bool     = bool(_tts.get("enabled", False))
    tts_provider_key: str = _tts.get("provider", "") or ""
    tts_model: str        = _tts.get("model", "") or ""
    tts_voice: str | None = _tts.get("voice", None)

    # ACP server settings
    _acp_srv = existing_data.get("acpServer", existing_data.get("acp_server", {}))
    acp_server = ACPServerSettings(**_acp_srv) if _acp_srv else ACPServerSettings()

    # ACP remote agents registry
    _acp_agents_raw = existing_data.get("acpAgents", existing_data.get("acp_agents", {}))
    acp_agents: dict[str, ACPAgentEntry] = {
        k: ACPAgentEntry(**v) for k, v in _acp_agents_raw.items()
    } if _acp_agents_raw else {}

    # Agent definitions: list of dicts with per-agent overrides.
    # None values mean "use global default".
    agent_defs: list[dict] = []
    for a in existing_data.get("agents", {}).get("list", []):
        _a_llm = a.get("llmConfig", a.get("llm_config")) or {}
        _a_ch  = a.get("channels", {}) or {}
        agent_defs.append({
            "id":               a.get("id", ""),
            "llm_provider_key": _a_llm.get("provider") or None,
            "llm_model":        _a_llm.get("model") or None,
            "channels":         {
                "telegram": _a_ch.get("telegram", {}).get("token", "") or "",
                "discord":  _a_ch.get("discord",  {}).get("token", "") or "",
                "slack_bot": _a_ch.get("slack", {}).get("botToken", "") or "",
                "slack_app": _a_ch.get("slack", {}).get("appToken", "") or "",
            },
            "browser_use": bool(a.get("browserUse", a.get("browser_use", True))),
            "computer_use": bool(a.get("computerUse", a.get("computer_use", False))),
        })

    # Ensure at least one agent entry exists (edge case: corrupted config)
    if not agent_defs:
        agent_defs.append({"id": "operator", "llm_provider_key": None, "llm_model": None,
                           "channels": {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""},
                           "browser_use": True, "computer_use": False})

    def _need_key(prov_key: str, prov_name: str) -> bool:
        return prov_key not in api_keys_dict and prov_name not in OAUTH_PROVIDERS and prov_name not in NO_KEY_PROVIDERS

    def _configure_llm(cur_prov: str, cur_model: str) -> tuple[str, str]:
        """Shared LLM picker. Returns (provider_key, model)."""
        prov_name = select("Pick the LLM provider:", list(LLM_PROVIDERS.keys()))
        prov_key  = get_provider_key(prov_name)
        model     = _select_model("Pick the LLM model:", LLM_PROVIDERS[prov_name])
        if prov_name in OAUTH_PROVIDERS:
            console.print("│")
            console.print(f"│  [dim]ℹ  {OAUTH_NOTES[prov_name]}[/dim]")
        elif _need_key(prov_key, prov_name):
            api_keys_dict[prov_key] = text_input(f"Enter API Key for {prov_name}:", is_password=True)
        return prov_key, model

    # ── Per-agent submenu ─────────────────────────────────────────────────────
    def _agent_submenu(idx: int) -> None:
        while True:
            try:
                a = agent_defs[idx]

                if a["llm_provider_key"] and a["llm_model"]:
                    a_llm_label = f"{a['llm_provider_key']} / {a['llm_model']}"
                else:
                    a_llm_label = "not configured"

                ch = a.get("channels", {})
                configured_chs = [n for n in ("telegram", "discord", "slack") if ch.get(n) or ch.get(f"{n}_bot")]
                ch_label = ", ".join(configured_chs) if configured_chs else "none"

                browser_use = a.get("browser_use", True)
                computer_use = a.get("computer_use", False)
                bu_label = "enabled" if browser_use else "disabled"
                cu_label = "enabled" if computer_use else "disabled"

                choice = select(f"Configure agent: {a['id']}", [
                    f"Rename         {a['id']}",
                    f"LLM            {a_llm_label}",
                    f"Channels       {ch_label}",
                    f"Browser Use    {bu_label}",
                    f"Computer Use   {cu_label}",
                    "Remove agent",
                    "← Back",
                ])

                if choice.startswith("←"):
                    break

                elif choice.startswith("Rename"):
                    raw = text_input("New agent name:", default=a["id"])
                    new_id = _re.sub(r"[^a-z0-9_-]", "-", raw.strip().lower()) or a["id"]
                    if any(o["id"] == new_id for i2, o in enumerate(agent_defs) if i2 != idx):
                        console.print("│")
                        console.print(f"│  [red]Name '{new_id}' is already taken.[/red]")
                    else:
                        agent_defs[idx]["id"] = new_id

                elif choice.startswith("LLM"):
                    prov_choice = select("Pick LLM provider for this agent:", list(LLM_PROVIDERS.keys()))
                    prov_key = get_provider_key(prov_choice)
                    model = _select_model("Pick the LLM model:", LLM_PROVIDERS[prov_choice])
                    if prov_choice in OAUTH_PROVIDERS:
                        console.print("│")
                        console.print(f"│  [dim]ℹ  {OAUTH_NOTES[prov_choice]}[/dim]")
                    elif _need_key(prov_key, prov_choice):
                        api_keys_dict[prov_key] = text_input(f"Enter API Key for {prov_choice}:", is_password=True)
                    agent_defs[idx]["llm_provider_key"] = prov_key
                    agent_defs[idx]["llm_model"] = model

                elif choice.startswith("Channels"):
                    ch = agent_defs[idx].setdefault("channels", {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""})
                    while True:
                        try:
                            tg_label  = "✓ configured" if ch.get("telegram")  else "not set"
                            dc_label  = "✓ configured" if ch.get("discord")   else "not set"
                            sl_label  = "✓ configured" if ch.get("slack_bot") else "not set"
                            ch_choice = select(f"Channels for {a['id']}:", [
                                f"Telegram   {tg_label}",
                                f"Discord    {dc_label}",
                                f"Slack      {sl_label}",
                                "← Back",
                            ])
                            if ch_choice.startswith("←"):
                                break
                            elif ch_choice.startswith("Telegram"):
                                note = CHANNEL_NOTES.get("Telegram", "")
                                console.print("│")
                                console.print(f"│  [dim]{note}[/dim]")
                                if ch.get("telegram") and not confirm("Replace existing Telegram token?"):
                                    continue
                                ch["telegram"] = text_input(f"Telegram Bot Token for {a['id']}:", is_password=True)
                            elif ch_choice.startswith("Discord"):
                                note = CHANNEL_NOTES.get("Discord", "")
                                console.print("│")
                                console.print(f"│  [dim]{note}[/dim]")
                                if ch.get("discord") and not confirm("Replace existing Discord token?"):
                                    continue
                                ch["discord"] = text_input(f"Discord Bot Token for {a['id']}:", is_password=True)
                            elif ch_choice.startswith("Slack"):
                                note = CHANNEL_NOTES.get("Slack", "")
                                console.print("│")
                                console.print(f"│  [dim]{note}[/dim]")
                                if ch.get("slack_bot") and not confirm("Replace existing Slack tokens?"):
                                    continue
                                ch["slack_bot"] = text_input(f"Slack Bot Token (xoxb-...) for {a['id']}:", is_password=True)
                                ch["slack_app"] = text_input(f"Slack App Token (xapp-...) for {a['id']}:", is_password=True)
                        except BackRequest:
                            continue

                elif choice.startswith("Browser Use"):
                    new_val = not agent_defs[idx].get("browser_use", True)
                    if new_val and agent_defs[idx].get("computer_use", False):
                        console.print("│")
                        console.print("│  [yellow]Computer Use disabled — only one can be active.[/yellow]")
                        agent_defs[idx]["computer_use"] = False
                    agent_defs[idx]["browser_use"] = new_val

                elif choice.startswith("Computer Use"):
                    new_val = not agent_defs[idx].get("computer_use", False)
                    if new_val and agent_defs[idx].get("browser_use", True):
                        console.print("│")
                        console.print("│  [yellow]Browser Use disabled — only one can be active.[/yellow]")
                        agent_defs[idx]["browser_use"] = False
                    agent_defs[idx]["computer_use"] = new_val

                elif choice.startswith("Remove"):
                    if len(agent_defs) <= 1:
                        console.print("│")
                        console.print("│  [red]Cannot remove the last agent.[/red]")
                    elif confirm(f"Remove agent '{a['id']}'?"):
                        agent_defs.pop(idx)
                        break
            except BackRequest:
                break

    # ── Agents submenu ────────────────────────────────────────────────────────
    def _agents_menu() -> None:
        while True:
            try:
                agent_choices = []
                for a in agent_defs:
                    if a["llm_provider_key"] and a["llm_model"]:
                        llm_lbl = f"{a['llm_provider_key']} / {a['llm_model']}"
                    else:
                        llm_lbl = "not configured"
                    agent_choices.append(f"{a['id']}  —  {llm_lbl}")
                agent_choices.append("+ Add agent")
                agent_choices.append("← Back")

                choice = select("Manage Agents:", agent_choices)

                if choice.startswith("←"):
                    break

                elif choice.startswith("+"):
                    raw = text_input("New agent name:")
                    new_id = _re.sub(r"[^a-z0-9_-]", "-", raw.strip().lower()) or "agent"
                    if any(a["id"] == new_id for a in agent_defs):
                        console.print("│")
                        console.print(f"│  [red]Agent '{new_id}' already exists.[/red]")
                    else:
                        agent_defs.append({
                            "id": new_id,
                            "llm_provider_key": None,
                            "llm_model": None,
                            "channels": {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""},
                            "browser_use": True,
                            "computer_use": False,
                        })
                        _agent_submenu(len(agent_defs) - 1)

                else:
                    for i, a in enumerate(agent_defs):
                        if choice.startswith(a["id"]):
                            _agent_submenu(i)
                            break
            except BackRequest:
                break

    # ── ACP submenu ───────────────────────────────────────────────────────────
    def _acp_menu() -> None:
        while True:
            try:
                srv_label = f"enabled  port={acp_server.port}" if acp_server.enabled else "disabled"
                agents_label = f"{len(acp_agents)} registered" if acp_agents else "none"
                choice = select("ACP (Agent Communication Protocol):", [
                    f"Server        {srv_label}",
                    f"Remote Agents {agents_label}",
                    "← Back",
                ])

                if choice.startswith("←"):
                    break

                elif choice.startswith("Server"):
                    if confirm("Enable ACP server? (exposes this Operator as an ACP endpoint)"):
                        acp_server.enabled = True
                        raw_port = text_input("Port:", default=str(acp_server.port))
                        try:
                            acp_server.port = int(raw_port)
                        except ValueError:
                            pass
                        raw_token = text_input("Auth token (leave blank for none):", default=acp_server.auth_token)
                        acp_server.auth_token = raw_token.strip()
                        raw_url = text_input("Public URL (leave blank to skip):", default=acp_server.public_url)
                        acp_server.public_url = raw_url.strip()
                    else:
                        acp_server.enabled = False

                elif choice.startswith("Remote Agents"):
                    while True:
                        try:
                            agent_choices = [f"{name}  —  {entry.base_url}" for name, entry in acp_agents.items()]
                            agent_choices += ["+ Add agent", "← Back"]
                            sub = select("Remote ACP Agents:", agent_choices)

                            if sub.startswith("←"):
                                break

                            elif sub.startswith("+"):
                                name = text_input("Agent name (e.g. claude-code):").strip()
                                if not name:
                                    continue
                                if name in acp_agents:
                                    console.print("│")
                                    console.print(f"│  [red]Agent '{name}' already registered.[/red]")
                                    continue
                                base_url = text_input("Base URL (e.g. http://localhost:9000):").strip()
                                agent_id = text_input("Remote agent ID (leave blank to auto-discover):", default="").strip()
                                auth_token = text_input("Auth token (leave blank for none):", default="").strip()
                                description = text_input("Description (shown to LLM):", default="").strip()
                                acp_agents[name] = ACPAgentEntry(
                                    base_url=base_url,
                                    agent_id=agent_id,
                                    auth_token=auth_token,
                                    description=description,
                                )

                            else:
                                matched = next((n for n in acp_agents if sub.startswith(n)), None)
                                if matched and confirm(f"Remove agent '{matched}'?"):
                                    del acp_agents[matched]
                        except BackRequest:
                            continue
            except BackRequest:
                break

    # --- Main menu loop ---
    while True:
        try:
            stt_label    = f"{stt_provider_key} / {stt_model}" if stt_enabled else "disabled"
            tts_label    = f"{tts_provider_key} / {tts_model}" if tts_enabled else "disabled"
            agents_label = ", ".join(a["id"] for a in agent_defs)
            hb_llm_label = f"  [{heartbeat_llm_provider_key} / {heartbeat_llm_model}]" if heartbeat_enabled and heartbeat_llm_provider_key else ""
            hb_label     = f"enabled{hb_llm_label}" if heartbeat_enabled else "disabled"

            acp_srv_label = f"server:{acp_server.port}" if acp_server.enabled else "disabled"
            acp_agents_count = len(acp_agents)
            acp_label = f"{acp_srv_label}, {acp_agents_count} remote agent{'s' if acp_agents_count != 1 else ''}" if acp_server.enabled or acp_agents_count else "disabled"

            choice = select("What would you like to configure?", [
                f"STT           {stt_label}",
                f"TTS           {tts_label}",
                f"Heartbeat     {hb_label}",
                f"Agents        {agents_label}",
                f"ACP           {acp_label}",
                "Save & Exit",
            ])

            if choice.startswith("STT"):
                if confirm("Enable Speech-to-Text (STT)?"):
                    prov_name = select("Pick the STT provider:", list(STT_PROVIDERS.keys()))
                    stt_provider_key = get_provider_key(prov_name)
                    stt_model = _select_model("Pick the STT model:", STT_PROVIDERS[prov_name])
                    if _need_key(stt_provider_key, prov_name):
                        api_keys_dict[stt_provider_key] = text_input(f"Enter API Key for {prov_name}:", is_password=True)
                    stt_enabled = True
                else:
                    stt_enabled = False
                    stt_provider_key = ""
                    stt_model = ""

            elif choice.startswith("TTS"):
                if confirm("Enable Text-to-Speech (TTS)?"):
                    prov_name = select("Pick the TTS provider:", list(TTS_PROVIDERS.keys()))
                    tts_provider_key = get_provider_key(prov_name)
                    tts_model = _select_model("Pick the TTS model:", TTS_PROVIDERS[prov_name])
                    tts_voice = None
                    if prov_name in VOICES:
                        tts_voice = select("Pick a voice:", VOICES[prov_name])
                    if _need_key(tts_provider_key, prov_name):
                        api_keys_dict[tts_provider_key] = text_input(f"Enter API Key for {prov_name}:", is_password=True)
                    tts_enabled = True
                else:
                    tts_enabled = False
                    tts_provider_key = ""
                    tts_model = ""
                    tts_voice = None

            elif choice.startswith("Heartbeat"):
                heartbeat_enabled = confirm("Enable Heartbeat? (agent runs periodic self-maintenance tasks)")
                if heartbeat_enabled:
                    hb_prov_name = select("Pick the LLM provider for Heartbeat:", list(LLM_PROVIDERS.keys()))
                    hb_prov_key  = get_provider_key(hb_prov_name)
                    heartbeat_llm_model = _select_model("Pick the Heartbeat LLM model:", LLM_PROVIDERS[hb_prov_name])
                    if hb_prov_name in OAUTH_PROVIDERS:
                        console.print("│")
                        console.print(f"│  [dim]ℹ  {OAUTH_NOTES[hb_prov_name]}[/dim]")
                    elif _need_key(hb_prov_key, hb_prov_name):
                        api_keys_dict[hb_prov_key] = text_input(f"Enter API Key for {hb_prov_name}:", is_password=True)
                    heartbeat_llm_provider_key = hb_prov_key
                else:
                    heartbeat_llm_provider_key = ""
                    heartbeat_llm_model = ""

            elif choice.startswith("Agents"):
                _agents_menu()

            elif choice.startswith("ACP"):
                _acp_menu()

            elif choice.startswith("Save"):
                if any(not a.get("llm_provider_key") for a in agent_defs):
                    console.print("│")
                    console.print("│  [red]All agents must have an LLM configured.[/red] Go to Agents to set one.")
                    continue
                break
        except BackRequest:
            continue

    # --- Build and save config ---
    _save_config(
        agent_defs=agent_defs,
        stt_enabled=stt_enabled,
        stt_provider_key=stt_provider_key,
        stt_model=stt_model,
        tts_enabled=tts_enabled,
        tts_provider_key=tts_provider_key,
        tts_model=tts_model,
        tts_voice=tts_voice,
        heartbeat_enabled=heartbeat_enabled,
        heartbeat_llm_provider_key=heartbeat_llm_provider_key,
        heartbeat_llm_model=heartbeat_llm_model,
        api_keys_dict=api_keys_dict,
        acp_server=acp_server,
        acp_agents=acp_agents,
    )
    print_end()


if __name__ == "__main__":
    run_initial_setup()
