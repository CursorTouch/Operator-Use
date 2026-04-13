import sys
import json
import re as _re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))
from operator_use.cli.tui import (
    BackRequest,
    NavigateBack,
    clear_screen,
    print_banner,
    print_start,
    print_step,
    select,
    text_input,
    confirm,
    print_end,
    print_end_first_install,
    console,
)
from operator_use.cli.mcp_setup import show_mcp_menu, validate_mcp_servers

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
        ("Nemotron Super 120B (reasoning)", "nvidia/nemotron-3-super-120b-a12b"),
        ("Qwen 3.5 122B", "qwen/qwen3.5-122b-a10b"),
        ("Qwen 3.5 397B", "qwen/qwen3.5-397b-a17b"),
        ("GLM-5 744B (reasoning)", "z-ai/glm-5"),
        ("GLM-4.7 (agentic coding)", "z-ai/glm-4.7"),
        ("MiniMax M2.5 230B", "minimaxai/minimax-m2.5"),
        ("Step 3.5 Flash 200B (reasoning)", "stepfun-ai/step-3.5-flash"),
        ("Kimi K2.5 1T (multimodal)", "moonshotai/kimi-k2.5"),
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
        ("Gemini 2.5 Flash (recommended)", "gemini-2.5-flash"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 2.5 Flash Lite", "gemini-2.5-flash-lite"),
        ("Gemini 3.1 Pro (preview)", "gemini-3.1-pro-preview"),
        ("Gemini 3 Flash (preview)", "gemini-3-flash-preview"),
        ("Gemini 3.1 Flash Lite (preview)", "gemini-3.1-flash-lite-preview"),
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
        ("Codestral (code)", "codestral-2508"),
        ("Ministral 14B", "ministral-14b-2512"),
        ("Ministral 8B", "ministral-8b-2512"),
        ("Ministral 3B", "ministral-3b-2512"),
    ],
    "xAI": [
        ("Grok 4 (recommended)", "grok-4"),
        ("Grok 3", "grok-3"),
        ("Grok 3 Mini (reasoning)", "grok-3-mini"),
        ("Grok 3 Fast", "grok-3-fast"),
        ("Grok 3 Mini Fast (reasoning)", "grok-3-mini-fast"),
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
    "xAI": [
        ("Grok TTS (recommended)", "grok-tts"),
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

IMAGE_PROVIDERS: dict[str, list[tuple[str, str]]] = {
    "xAI": [
        ("Grok Imagine (recommended)", "grok-imagine-image"),
        ("Grok Imagine Pro", "grok-imagine-image-pro"),
    ],
    "OpenAI": [
        ("GPT Image 1.5 (recommended, latest)", "gpt-image-1.5"),
        ("GPT Image 1", "gpt-image-1"),
        ("GPT Image 1 Mini (cost-efficient)", "gpt-image-1-mini"),
        ("ChatGPT Image Latest (alias → newest snapshot)", "chatgpt-image-latest"),
    ],
    "Google": [
        ("Gemini 2.5 Flash Image (gen + edit, recommended)", "gemini-2.5-flash-image"),
        ("Gemini 3 Pro Image Preview (gen + edit, studio quality)", "gemini-3-pro-image-preview"),
        (
            "Gemini 3.1 Flash Image Preview (gen + edit, high volume)",
            "gemini-3.1-flash-image-preview",
        ),
        ("Imagen 4 Standard (text-to-image only)", "imagen-4.0-generate-001"),
        ("Imagen 4 Ultra (text-to-image only, highest quality)", "imagen-4.0-ultra-generate-001"),
        ("Imagen 4 Fast (text-to-image only, low latency)", "imagen-4.0-fast-generate-001"),
    ],
    "Together": [
        ("FLUX.1 Schnell Free (free tier)", "black-forest-labs/FLUX.1-schnell-Free"),
        ("FLUX.1.1 Pro (recommended quality)", "black-forest-labs/FLUX.1.1-pro"),
        ("FLUX.2 Pro (latest generation)", "black-forest-labs/FLUX.2-pro"),
        ("FLUX.2 Max (highest quality)", "black-forest-labs/FLUX.2-max"),
        ("FLUX.1 Kontext Pro (instruction-based editing)", "black-forest-labs/FLUX.1-kontext-pro"),
        ("FLUX.1 Kontext Max (editing, max quality)", "black-forest-labs/FLUX.1-kontext-max"),
        ("Ideogram 3.0 (strong typography)", "ideogram/ideogram-3.0"),
    ],
    "fal": [
        ("FLUX.1 Pro v1.1 (recommended quality)", "fal-ai/flux-pro/v1.1"),
        ("FLUX.1 Pro v1.1 Ultra (up to 2K)", "fal-ai/flux-pro/v1.1-ultra"),
        ("FLUX.2 Pro (latest generation)", "fal-ai/flux-2-pro"),
        ("FLUX.2 Max (highest quality)", "fal-ai/flux-2-max"),
        ("FLUX.1 Kontext Pro (instruction-based editing)", "fal-ai/flux-pro/kontext"),
        ("FLUX.1 Schnell (fastest)", "fal-ai/flux/schnell"),
        ("FLUX.1 Dev (open weights)", "fal-ai/flux/dev"),
        ("Recraft V3 (vectors & typography)", "fal-ai/recraft/v3/text-to-image"),
        ("Ideogram V3 (strong typography)", "fal-ai/ideogram/v3"),
    ],
}

SEARCH_PROVIDERS: dict[str, str] = {
    "DuckDuckGo (free, no key needed)": "ddgs",
    "Exa (semantic search, API key required)": "exa",
    "Tavily (AI-optimized, API key required)": "tavily",
}

VOICES: dict[str, list[str]] = {
    "OpenAI": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
    "xAI": ["eve", "ara", "rex", "sal", "leo"],
    "Groq": ["autumn", "diana", "hannah", "austin", "daniel", "troy"],
    "Google": ["Aoede", "Charon", "Fenrir", "Kore", "Puck"],
    "ElevenLabs": ["Rachel", "Drew", "Clyde", "Paul", "Domi"],
    "Deepgram": ["asteria-en", "luna-en", "stella-en", "athena-en", "hera-en"],
    "Sarvam": [
        "aditya",
        "ritu",
        "ashutosh",
        "priya",
        "neha",
        "rahul",
        "pooja",
        "rohan",
        "simran",
        "kavya",
        "amit",
        "dev",
        "ishita",
        "shreya",
        "ratan",
        "varun",
        "manan",
        "sumit",
        "roopa",
        "kabir",
        "aayan",
        "shubh",
        "advait",
        "amelia",
        "sophia",
        "anand",
        "tanya",
        "tarun",
        "sunny",
        "mani",
        "gokul",
        "vijay",
        "shruti",
        "suhani",
        "mohit",
        "kavitha",
        "rehan",
        "soham",
        "rupali",
    ],
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
    "Discord": "Create a bot at discord.com/developers, enable Message Content Intent, and copy the token.",
    "Slack": "Create a Slack app at api.slack.com. Bot token starts with xoxb-, app token with xapp-.",
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
        "xAI": "xai",
    }
    return key_map.get(name, name.lower())


def _get_ollama_models() -> list[tuple[str, str]]:
    """Query the local Ollama server for installed models."""
    try:
        from ollama import Client

        client = Client()
        response = client.list()
        models = response.models if hasattr(response, "models") else response.get("models", [])
        if models:
            return [
                (
                    m.model if hasattr(m, "model") else m["name"],
                    m.model if hasattr(m, "model") else m["name"],
                )
                for m in models
            ]
    except Exception:
        pass
    return []


def _get_model_options(prov_name: str) -> list[tuple[str, str]]:
    """Return model options for a provider. Ollama queries the local server dynamically."""
    if prov_name == "Ollama":
        models = _get_ollama_models()
        if models:
            return models
        console.print(
            "[yellow]⚠  Could not reach Ollama or no models installed. Run [bold]ollama pull <model>[/bold] first.[/yellow]"
        )
    return LLM_PROVIDERS[prov_name]


def _select_model(label: str, options: list[tuple[str, str]]) -> str:
    custom_label = "Custom (enter model ID)..."
    display_names = [d for d, _ in options] + [custom_label]
    chosen_display = select(label, display_names)
    if chosen_display == custom_label:
        return text_input("Enter model ID:")
    return next(mid for d, mid in options if d == chosen_display)


from operator_use.config import (
    Config,
    LLMConfig,
    STTConfig,
    TTSConfig,
    ImageConfig,
    SearchConfig,
    AgentDefaults,
    AgentsConfig,
    ProvidersConfig,
    ProviderConfig,
    ChannelsConfig,
    TelegramConfig,
    DiscordConfig,
    SlackConfig,
    AgentDefinition,
    ACPServerSettings,
    ACPAgentEntry,
    HeartbeatConfig,
    ToolsConfig,
    PluginConfig,
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
    image_enabled: bool,
    image_provider_key: str,
    image_model: str,
    search_provider_key: str,
    search_api_key: str,
    heartbeat_enabled: bool,
    heartbeat_llm_provider_key: str,
    heartbeat_llm_model: str,
    api_keys_dict: dict[str, str],
    acp_server: "ACPServerSettings | None" = None,
    acp_agents: "dict[str, ACPAgentEntry] | None" = None,
    mcp_servers: dict | None = None,
) -> None:
    """Build the Config object and persist it to disk."""
    from operator_use.config.paths import get_userdata_dir

    providers = ProvidersConfig()
    for prov, key in api_keys_dict.items():
        if hasattr(providers, prov):
            setattr(providers, prov, ProviderConfig(api_key=key))

    agent_list = []
    for a in agent_defs:
        defn_kwargs: dict = {"id": a["id"]}
        if a["llm_provider_key"] and a["llm_model"]:
            defn_kwargs["llm_config"] = LLMConfig(
                provider=a["llm_provider_key"], model=a["llm_model"]
            )
        ch = a.get("channels", {})
        if ch.get("telegram") or ch.get("discord") or ch.get("slack_bot"):
            agent_channels = ChannelsConfig()
            if ch.get("telegram"):
                agent_channels.telegram = TelegramConfig(enabled=True, token=ch["telegram"])
            if ch.get("discord"):
                agent_channels.discord = DiscordConfig(enabled=True, token=ch["discord"])
            if ch.get("slack_bot"):
                agent_channels.slack = SlackConfig(
                    enabled=True, bot_token=ch["slack_bot"], app_token=ch.get("slack_app", "")
                )
            defn_kwargs["channels"] = agent_channels
        defn_kwargs["plugins"] = [
            PluginConfig(id=p["id"], enabled=p.get("enabled", True)) for p in a.get("plugins", [])
        ]
        defn_kwargs["prompt_mode"] = a.get("prompt_mode", "full")
        if a.get("system_prompt"):
            defn_kwargs["system_prompt"] = a["system_prompt"]
        defn_kwargs["tools"] = ToolsConfig(
            profile=a.get("tools_profile", "full"),
            also_allow=a.get("tools_allow", []),
            deny=a.get("tools_deny", []),
        )
        agent_list.append(AgentDefinition(**defn_kwargs))

    hb_llm = (
        LLMConfig(provider=heartbeat_llm_provider_key, model=heartbeat_llm_model)
        if heartbeat_llm_provider_key and heartbeat_llm_model
        else None
    )
    config_obj = Config(
        heartbeat=HeartbeatConfig(enabled=heartbeat_enabled, llm_config=hb_llm),
        agents=AgentsConfig(
            defaults=AgentDefaults(),
            list=agent_list,
        ),
        stt=STTConfig(
            enabled=stt_enabled, provider=stt_provider_key or None, model=stt_model or None
        ),
        tts=TTSConfig(
            enabled=tts_enabled,
            provider=tts_provider_key or None,
            model=tts_model or None,
            voice=tts_voice,
        ),
        image=ImageConfig(
            enabled=image_enabled, provider=image_provider_key or None, model=image_model or None
        ),
        search=SearchConfig(provider=search_provider_key or "ddgs", api_key=search_api_key or None),
        providers=providers,
        acp_server=acp_server or ACPServerSettings(),
        acp_agents=acp_agents or {},
        mcp_servers=mcp_servers or {},
    )

    operator_use_dir = get_userdata_dir()
    operator_use_dir.mkdir(parents=True, exist_ok=True)
    config_path = operator_use_dir / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(
            config_obj.model_dump(by_alias=True, exclude_none=True), f, indent=4, ensure_ascii=False
        )

    key_to_env = {
        "groq": "GROQ_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GEMINI_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "xai": "XAI_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "open_router": "OPENROUTER_API_KEY",
        "elevenlabs": "ELEVENLABS_API_KEY",
        "deepgram": "DEEPGRAM_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "azure_openai": "AZURE_OPENAI_API_KEY",
        "sarvam": "SARVAM_API_KEY",
    }
    env_vars = {key_to_env[k]: v for k, v in api_keys_dict.items() if k in key_to_env}
    if env_vars:
        with open(".env", "a") as f:
            f.write("\n")
            for k, v in env_vars.items():
                f.write(f"{k}={v}\n")


def run_first_install():
    """Linear step-by-step wizard for first-time installation (no config.json exists)."""

    def _render_step(step: int) -> None:
        clear_screen()
        print_banner()
        print_start()
        if step == 0:
            print_step(
                1, 3, "Your agent", "Give your agent a name — used for its workspace folder."
            )
        elif step == 1:
            print_step(
                2, 3, "Language model", "This is the AI brain. Pick a provider you have access to."
            )
        else:
            print_step(
                3,
                3,
                "Messaging channel",
                "Connect a channel to message your agent. You can add more later with `operator channel add`.",
            )

    api_keys_dict: dict[str, str] = {}
    agent_id = "operator"
    llm_provider_key = ""
    llm_model = ""
    agent_channels: dict = {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""}

    def _need_key(prov_key: str, prov_name: str) -> bool:
        return (
            prov_key not in api_keys_dict
            and prov_name not in OAUTH_PROVIDERS
            and prov_name not in NO_KEY_PROVIDERS
        )

    step = 0
    while step < 3:
        try:
            _render_step(step)
            if step == 0:
                raw = text_input("Name your agent (e.g. mybot, personal, work):", default=agent_id)
                agent_id = _re.sub(r"[^a-z0-9_-]", "-", raw.strip().lower()) or "operator"
                step = 1
                continue

            if step == 1:
                prov_name = select("Pick the LLM provider:", list(LLM_PROVIDERS.keys()))
                prov_key = get_provider_key(prov_name)
                if prov_name in OAUTH_PROVIDERS:
                    console.print("│")
                    console.print(f"│  [dim]ℹ  {OAUTH_NOTES[prov_name]}[/dim]")
                elif _need_key(prov_key, prov_name):
                    api_keys_dict[prov_key] = text_input(
                        f"Enter API Key for {prov_name}:", is_password=True
                    )
                llm_model = _select_model("Pick the LLM model:", _get_model_options(prov_name))
                llm_provider_key = prov_key
                step = 2
                continue

            agent_channels = {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""}
            ch_name = select(
                "Pick a channel to connect:", ["Telegram", "Discord", "Slack", "Skip for now"]
            )
            if ch_name != "Skip for now":
                note = CHANNEL_NOTES.get(ch_name, "")
                if note:
                    console.print("│")
                    console.print(f"│  [dim]{note}[/dim]")
                if ch_name == "Telegram":
                    agent_channels["telegram"] = text_input(
                        "Enter Telegram Bot Token:", is_password=True
                    )
                elif ch_name == "Discord":
                    agent_channels["discord"] = text_input(
                        "Enter Discord Bot Token:", is_password=True
                    )
                elif ch_name == "Slack":
                    agent_channels["slack_bot"] = text_input(
                        "Enter Slack Bot Token (xoxb-...):", is_password=True
                    )
                    agent_channels["slack_app"] = text_input(
                        "Enter Slack App Token (xapp-...):", is_password=True
                    )
            step = 3
        except BackRequest:
            if step > 0:
                step -= 1

    agent_defs = [
        {
            "id": agent_id,
            "llm_provider_key": llm_provider_key,
            "llm_model": llm_model,
            "channels": agent_channels,
        }
    ]

    _save_config(
        agent_defs=agent_defs,
        stt_enabled=False,
        stt_provider_key="",
        stt_model="",
        tts_enabled=False,
        tts_provider_key="",
        tts_model="",
        tts_voice=None,
        image_enabled=False,
        image_provider_key="",
        image_model="",
        search_provider_key="ddgs",
        search_api_key="",
        heartbeat_enabled=False,
        heartbeat_llm_provider_key="",
        heartbeat_llm_model="",
        api_keys_dict=api_keys_dict,
    )
    print_end_first_install()


# TTS, STT, heartbeat configurable via: operator onboard


def run_initial_setup():
    def _render_configure_screen() -> None:
        clear_screen()
        print_banner()
        print_start("Configure")

    from operator_use.config.paths import get_userdata_dir

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
    _img = existing_data.get("image", {})
    _hb = existing_data.get("heartbeat", {})
    heartbeat_enabled: bool = bool(_hb.get("enabled", False))
    _hb_llm = _hb.get("llmConfig", _hb.get("llm_config", {})) or {}
    heartbeat_llm_provider_key: str = _hb_llm.get("provider", "")
    heartbeat_llm_model: str = _hb_llm.get("model", "")

    # --- Global mutable state ---
    stt_enabled: bool = bool(_stt.get("enabled", False))
    stt_provider_key: str = _stt.get("provider", "") or ""
    stt_model: str = _stt.get("model", "") or ""

    tts_enabled: bool = bool(_tts.get("enabled", False))
    tts_provider_key: str = _tts.get("provider", "") or ""
    tts_model: str = _tts.get("model", "") or ""
    tts_voice: str | None = _tts.get("voice", None)

    image_enabled: bool = bool(_img.get("enabled", False))
    image_provider_key: str = _img.get("provider", "") or ""
    image_model: str = _img.get("model", "") or ""

    _srch = existing_data.get("search", {})
    search_provider_key: str = _srch.get("provider", "") or "ddgs"
    search_api_key: str = _srch.get("api_key", "") or ""

    # ACP server settings
    _acp_srv = existing_data.get("acpServer", existing_data.get("acp_server", {}))
    acp_server = ACPServerSettings(**_acp_srv) if _acp_srv else ACPServerSettings()

    # ACP remote agents registry
    _acp_agents_raw = existing_data.get("acpAgents", existing_data.get("acp_agents", {}))
    acp_agents: dict[str, ACPAgentEntry] = (
        {k: ACPAgentEntry(**v) for k, v in _acp_agents_raw.items()} if _acp_agents_raw else {}
    )

    # MCP servers registry
    mcp_servers: dict[str, dict] = existing_data.get(
        "mcpServers", existing_data.get("mcp_servers", {})
    )

    # Agent definitions: list of dicts with per-agent overrides.
    # None values mean "use global default".
    agent_defs: list[dict] = []
    for a in existing_data.get("agents", {}).get("list", []):
        _a_llm = a.get("llmConfig", a.get("llm_config")) or {}
        _a_ch = a.get("channels", {}) or {}
        _a_tools = a.get("tools") or {}
        agent_defs.append(
            {
                "id": a.get("id", ""),
                "llm_provider_key": _a_llm.get("provider") or None,
                "llm_model": _a_llm.get("model") or None,
                "channels": {
                    "telegram": _a_ch.get("telegram", {}).get("token", "") or "",
                    "discord": _a_ch.get("discord", {}).get("token", "") or "",
                    "slack_bot": _a_ch.get("slack", {}).get("botToken", "") or "",
                    "slack_app": _a_ch.get("slack", {}).get("appToken", "") or "",
                },
                "plugins": [
                    {"id": p["id"], "enabled": p.get("enabled", True)}
                    for p in (a.get("plugins") or [])
                ],
                "prompt_mode": a.get("promptMode", a.get("prompt_mode", "full")),
                "system_prompt": a.get("systemPrompt", a.get("system_prompt", "")),
                "tools_profile": _a_tools.get("profile", "full"),
                "tools_allow": _a_tools.get("alsoAllow", _a_tools.get("also_allow", [])),
                "tools_deny": _a_tools.get("deny", []),
            }
        )

    # Ensure at least one agent entry exists (edge case: corrupted config)
    if not agent_defs:
        agent_defs.append(
            {
                "id": "operator",
                "llm_provider_key": None,
                "llm_model": None,
                "channels": {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""},
                "plugins": [],
                "prompt_mode": "full",
                "system_prompt": "",
                "tools_profile": "full",
                "tools_allow": [],
                "tools_deny": [],
            }
        )

    def _need_key(prov_key: str, prov_name: str) -> bool:
        return (
            prov_key not in api_keys_dict
            and prov_name not in OAUTH_PROVIDERS
            and prov_name not in NO_KEY_PROVIDERS
        )

    def _configure_llm(cur_prov: str, cur_model: str) -> tuple[str, str]:
        """Shared LLM picker. Returns (provider_key, model)."""
        prov_name = select("Pick the LLM provider:", list(LLM_PROVIDERS.keys()))
        prov_key = get_provider_key(prov_name)
        model = _select_model("Pick the LLM model:", _get_model_options(prov_name))
        if prov_name in OAUTH_PROVIDERS:
            console.print("│")
            console.print(f"│  [dim]ℹ  {OAUTH_NOTES[prov_name]}[/dim]")
        elif _need_key(prov_key, prov_name):
            api_keys_dict[prov_key] = text_input(
                f"Enter API Key for {prov_name}:", is_password=True
            )
        return prov_key, model

    # ── Per-agent submenu ─────────────────────────────────────────────────────
    def _agent_submenu(idx: int) -> None:
        while True:
            try:
                _render_configure_screen()
                a = agent_defs[idx]

                if a["llm_provider_key"] and a["llm_model"]:
                    a_llm_label = f"{a['llm_provider_key']} / {a['llm_model']}"
                else:
                    a_llm_label = "not configured"

                ch = a.get("channels", {})
                configured_chs = [
                    n for n in ("telegram", "discord", "slack") if ch.get(n) or ch.get(f"{n}_bot")
                ]
                ch_label = ", ".join(configured_chs) if configured_chs else "none"

                plugins_list = a.get("plugins", [])
                enabled_plugin_ids = [p["id"] for p in plugins_list if p.get("enabled", True)]
                plugins_label = ", ".join(enabled_plugin_ids) if enabled_plugin_ids else "none"
                pm_label = a.get("prompt_mode", "full")
                sp_label = "set" if a.get("system_prompt") else "not set"
                tools_profile = a.get("tools_profile", "full")
                tools_allow = a.get("tools_allow", [])
                tools_deny = a.get("tools_deny", [])
                tools_label = tools_profile
                if tools_allow:
                    tools_label += f"  +{len(tools_allow)}"
                if tools_deny:
                    tools_label += f"  -{len(tools_deny)}"

                choice = select(
                    f"Configure agent: {a['id']}",
                    [
                        f"Rename         {a['id']}",
                        f"LLM            {a_llm_label}",
                        f"Channels       {ch_label}",
                        f"Plugins        {plugins_label}",
                        f"Prompt Mode    {pm_label}",
                        f"System Prompt  {sp_label}",
                        f"Tools          {tools_label}",
                        "Remove agent",
                        "← Back",
                    ],
                )

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
                    prov_choice = select(
                        "Pick LLM provider for this agent:", list(LLM_PROVIDERS.keys())
                    )
                    prov_key = get_provider_key(prov_choice)
                    model = _select_model("Pick the LLM model:", _get_model_options(prov_choice))
                    if prov_choice in OAUTH_PROVIDERS:
                        console.print("│")
                        console.print(f"│  [dim]ℹ  {OAUTH_NOTES[prov_choice]}[/dim]")
                    elif _need_key(prov_key, prov_choice):
                        api_keys_dict[prov_key] = text_input(
                            f"Enter API Key for {prov_choice}:", is_password=True
                        )
                    agent_defs[idx]["llm_provider_key"] = prov_key
                    agent_defs[idx]["llm_model"] = model

                elif choice.startswith("Channels"):
                    ch = agent_defs[idx].setdefault(
                        "channels",
                        {"telegram": "", "discord": "", "slack_bot": "", "slack_app": ""},
                    )
                    while True:
                        try:
                            _render_configure_screen()
                            tg_label = "✓ configured" if ch.get("telegram") else "not set"
                            dc_label = "✓ configured" if ch.get("discord") else "not set"
                            sl_label = "✓ configured" if ch.get("slack_bot") else "not set"
                            ch_choice = select(
                                f"Channels for {a['id']}:",
                                [
                                    f"Telegram   {tg_label}",
                                    f"Discord    {dc_label}",
                                    f"Slack      {sl_label}",
                                    "← Back",
                                ],
                            )
                            if ch_choice.startswith("←"):
                                break
                            elif ch_choice.startswith("Telegram"):
                                note = CHANNEL_NOTES.get("Telegram", "")
                                console.print("│")
                                console.print(f"│  [dim]{note}[/dim]")
                                if ch.get("telegram") and not confirm(
                                    "Replace existing Telegram token?"
                                ):
                                    continue
                                ch["telegram"] = text_input(
                                    f"Telegram Bot Token for {a['id']}:", is_password=True
                                )
                            elif ch_choice.startswith("Discord"):
                                note = CHANNEL_NOTES.get("Discord", "")
                                console.print("│")
                                console.print(f"│  [dim]{note}[/dim]")
                                if ch.get("discord") and not confirm(
                                    "Replace existing Discord token?"
                                ):
                                    continue
                                ch["discord"] = text_input(
                                    f"Discord Bot Token for {a['id']}:", is_password=True
                                )
                            elif ch_choice.startswith("Slack"):
                                note = CHANNEL_NOTES.get("Slack", "")
                                console.print("│")
                                console.print(f"│  [dim]{note}[/dim]")
                                if ch.get("slack_bot") and not confirm(
                                    "Replace existing Slack tokens?"
                                ):
                                    continue
                                ch["slack_bot"] = text_input(
                                    f"Slack Bot Token (xoxb-...) for {a['id']}:", is_password=True
                                )
                                ch["slack_app"] = text_input(
                                    f"Slack App Token (xapp-...) for {a['id']}:", is_password=True
                                )
                        except BackRequest:
                            continue

                elif choice.startswith("Plugins"):
                    AVAILABLE_PLUGINS = ["browser_use", "computer_use"]
                    while True:
                        try:
                            _render_configure_screen()
                            current_plugins: list[dict] = agent_defs[idx].get("plugins", [])
                            plugin_choices = []
                            for pid in AVAILABLE_PLUGINS:
                                entry = next((p for p in current_plugins if p["id"] == pid), None)
                                if entry:
                                    state = "enabled" if entry.get("enabled", True) else "disabled"
                                    plugin_choices.append(f"{pid}  {state}")
                                else:
                                    plugin_choices.append(f"{pid}  not added")
                            plugin_choices.append("← Back")
                            p_choice = select("Plugins:", plugin_choices)
                            if p_choice.startswith("←"):
                                break
                            selected_id = p_choice.split()[0]
                            entry = next(
                                (p for p in current_plugins if p["id"] == selected_id), None
                            )
                            if entry is None:
                                current_plugins.append({"id": selected_id, "enabled": True})
                                console.print(f"│  [green]{selected_id} added and enabled.[/green]")
                            else:
                                action = select(
                                    f"{selected_id}:", ["Toggle enabled/disabled", "Remove"]
                                )
                                if action.startswith("Toggle"):
                                    entry["enabled"] = not entry.get("enabled", True)
                                elif action.startswith("Remove"):
                                    current_plugins = [
                                        p for p in current_plugins if p["id"] != selected_id
                                    ]
                            agent_defs[idx]["plugins"] = current_plugins
                        except BackRequest:
                            break

                elif choice.startswith("Prompt Mode"):
                    mode = select("Prompt mode:", ["full", "minimal", "none"])
                    agent_defs[idx]["prompt_mode"] = mode

                elif choice.startswith("System Prompt"):
                    current_sp = a.get("system_prompt", "")
                    val = text_input("System prompt (leave blank to clear):", default=current_sp)
                    agent_defs[idx]["system_prompt"] = val.strip()

                elif choice.startswith("Tools"):
                    while True:
                        try:
                            _render_configure_screen()
                            tp = agent_defs[idx].get("tools_profile", "full")
                            ta = ", ".join(agent_defs[idx].get("tools_allow", [])) or "none"
                            td = ", ".join(agent_defs[idx].get("tools_deny", [])) or "none"
                            tools_choice = select(
                                "Tools configuration:",
                                [
                                    f"Profile    {tp}",
                                    f"Also Allow {ta}",
                                    f"Deny       {td}",
                                    "← Back",
                                ],
                            )
                            if tools_choice.startswith("←"):
                                break
                            elif tools_choice.startswith("Profile"):
                                agent_defs[idx]["tools_profile"] = select(
                                    "Tools profile:", ["full", "coding", "minimal"]
                                )
                            elif tools_choice.startswith("Also Allow"):
                                raw = text_input(
                                    "Extra tools to allow (comma-separated):",
                                    default=", ".join(agent_defs[idx].get("tools_allow", [])),
                                )
                                agent_defs[idx]["tools_allow"] = [
                                    t.strip() for t in raw.split(",") if t.strip()
                                ]
                            elif tools_choice.startswith("Deny"):
                                raw = text_input(
                                    "Tools to deny (comma-separated):",
                                    default=", ".join(agent_defs[idx].get("tools_deny", [])),
                                )
                                agent_defs[idx]["tools_deny"] = [
                                    t.strip() for t in raw.split(",") if t.strip()
                                ]
                        except BackRequest:
                            break

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
                _render_configure_screen()
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
                        agent_defs.append(
                            {
                                "id": new_id,
                                "llm_provider_key": None,
                                "llm_model": None,
                                "channels": {
                                    "telegram": "",
                                    "discord": "",
                                    "slack_bot": "",
                                    "slack_app": "",
                                },
                                "plugins": [],
                                "prompt_mode": "full",
                                "system_prompt": "",
                                "tools_profile": "full",
                                "tools_allow": [],
                                "tools_deny": [],
                            }
                        )
                        _agent_submenu(len(agent_defs) - 1)

                else:
                    for i, a in enumerate(agent_defs):
                        if choice.startswith(a["id"] + "  "):
                            _agent_submenu(i)
                            break
            except BackRequest:
                break

    # ── ACP submenu ───────────────────────────────────────────────────────────
    def _acp_menu() -> None:
        while True:
            try:
                _render_configure_screen()
                srv_label = f"enabled  port={acp_server.port}" if acp_server.enabled else "disabled"
                agents_label = f"{len(acp_agents)} registered" if acp_agents else "none"
                choice = select(
                    "ACP (Agent Communication Protocol):",
                    [
                        f"Server        {srv_label}",
                        f"Remote Agents {agents_label}",
                        "← Back",
                    ],
                )

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
                        raw_token = text_input(
                            "Auth token (leave blank for none):", default=acp_server.auth_token
                        )
                        acp_server.auth_token = raw_token.strip()
                        raw_url = text_input(
                            "Public URL (leave blank to skip):", default=acp_server.public_url
                        )
                        acp_server.public_url = raw_url.strip()
                    else:
                        acp_server.enabled = False

                elif choice.startswith("Remote Agents"):
                    while True:
                        try:
                            _render_configure_screen()
                            agent_choices = [
                                f"{name}  —  {entry.base_url}" for name, entry in acp_agents.items()
                            ]
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
                                    console.print(
                                        f"│  [red]Agent '{name}' already registered.[/red]"
                                    )
                                    continue
                                base_url = text_input(
                                    "Base URL (e.g. http://localhost:9000):"
                                ).strip()
                                agent_id = text_input(
                                    "Remote agent ID (leave blank to auto-discover):", default=""
                                ).strip()
                                auth_token = text_input(
                                    "Auth token (leave blank for none):", default=""
                                ).strip()
                                description = text_input(
                                    "Description (shown to LLM):", default=""
                                ).strip()
                                acp_agents[name] = ACPAgentEntry(
                                    base_url=base_url,
                                    agent_id=agent_id,
                                    auth_token=auth_token,
                                    description=description,
                                )

                            else:
                                matched = next(
                                    (n for n in acp_agents if sub.startswith(n + "  ")), None
                                )
                                if matched and confirm(f"Remove agent '{matched}'?"):
                                    del acp_agents[matched]
                        except BackRequest:
                            continue
            except BackRequest:
                break

    # --- Main menu loop ---
    while True:
        try:
            _render_configure_screen()
            stt_label = f"{stt_provider_key} / {stt_model}" if stt_enabled else "disabled"
            tts_label = f"{tts_provider_key} / {tts_model}" if tts_enabled else "disabled"
            image_label = f"{image_provider_key} / {image_model}" if image_enabled else "disabled"
            agents_label = ", ".join(a["id"] for a in agent_defs)
            hb_llm_label = (
                f"  [{heartbeat_llm_provider_key} / {heartbeat_llm_model}]"
                if heartbeat_enabled and heartbeat_llm_provider_key
                else ""
            )
            hb_label = f"enabled{hb_llm_label}" if heartbeat_enabled else "disabled"

            acp_srv_label = f"server:{acp_server.port}" if acp_server.enabled else "disabled"
            acp_agents_count = len(acp_agents)
            acp_label = (
                f"{acp_srv_label}, {acp_agents_count} remote agent{'s' if acp_agents_count != 1 else ''}"
                if acp_server.enabled or acp_agents_count
                else "disabled"
            )

            search_label = search_provider_key if search_provider_key else "ddgs"
            mcp_label = f"{len(mcp_servers)} servers" if mcp_servers else "none"
            choice = select(
                "What would you like to configure?",
                [
                    f"STT           {stt_label}",
                    f"TTS           {tts_label}",
                    f"Image         {image_label}",
                    f"Search        {search_label}",
                    f"Heartbeat     {hb_label}",
                    f"Agents        {agents_label}",
                    f"ACP           {acp_label}",
                    f"MCP           {mcp_label}",
                    "Save & Exit",
                ],
            )

            if choice.startswith("STT"):
                if confirm("Enable Speech-to-Text (STT)?"):
                    prov_name = select("Pick the STT provider:", list(STT_PROVIDERS.keys()))
                    stt_provider_key = get_provider_key(prov_name)
                    stt_model = _select_model("Pick the STT model:", STT_PROVIDERS[prov_name])
                    if _need_key(stt_provider_key, prov_name):
                        api_keys_dict[stt_provider_key] = text_input(
                            f"Enter API Key for {prov_name}:", is_password=True
                        )
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
                        api_keys_dict[tts_provider_key] = text_input(
                            f"Enter API Key for {prov_name}:", is_password=True
                        )
                    tts_enabled = True
                else:
                    tts_enabled = False
                    tts_provider_key = ""
                    tts_model = ""
                    tts_voice = None

            elif choice.startswith("Image"):
                if confirm("Enable Image Generation?"):
                    prov_name = select("Pick the image provider:", list(IMAGE_PROVIDERS.keys()))
                    image_provider_key = get_provider_key(prov_name)
                    image_model = _select_model("Pick the image model:", IMAGE_PROVIDERS[prov_name])
                    if _need_key(image_provider_key, prov_name):
                        api_keys_dict[image_provider_key] = text_input(
                            f"Enter API Key for {prov_name}:", is_password=True
                        )
                    image_enabled = True
                else:
                    image_enabled = False
                    image_provider_key = ""
                    image_model = ""

            elif choice.startswith("Search"):
                prov_display = select("Pick the search provider:", list(SEARCH_PROVIDERS.keys()))
                search_provider_key = SEARCH_PROVIDERS[prov_display]
                if search_provider_key in ("exa", "tavily"):
                    search_api_key = text_input(
                        f"Enter API key for {prov_display}:", is_password=True
                    )
                else:
                    search_api_key = ""

            elif choice.startswith("Heartbeat"):
                heartbeat_enabled = confirm(
                    "Enable Heartbeat? (agent runs periodic self-maintenance tasks)"
                )
                if heartbeat_enabled:
                    hb_prov_name = select(
                        "Pick the LLM provider for Heartbeat:", list(LLM_PROVIDERS.keys())
                    )
                    hb_prov_key = get_provider_key(hb_prov_name)
                    heartbeat_llm_model = _select_model(
                        "Pick the Heartbeat LLM model:", LLM_PROVIDERS[hb_prov_name]
                    )
                    if hb_prov_name in OAUTH_PROVIDERS:
                        console.print("│")
                        console.print(f"│  [dim]ℹ  {OAUTH_NOTES[hb_prov_name]}[/dim]")
                    elif _need_key(hb_prov_key, hb_prov_name):
                        api_keys_dict[hb_prov_key] = text_input(
                            f"Enter API Key for {hb_prov_name}:", is_password=True
                        )
                    heartbeat_llm_provider_key = hb_prov_key
                else:
                    heartbeat_llm_provider_key = ""
                    heartbeat_llm_model = ""

            elif choice.startswith("Agents"):
                _agents_menu()

            elif choice.startswith("ACP"):
                _acp_menu()

            elif choice.startswith("MCP"):
                mcp_servers = show_mcp_menu(mcp_servers)
                if validate_mcp_servers(mcp_servers):
                    console.print("│")
                    console.print("│  [green]MCP servers validated[/green]")
                else:
                    console.print("│")
                    console.print("│  [red]MCP configuration has errors[/red]")

            elif choice.startswith("Save"):
                if any(not a.get("llm_provider_key") for a in agent_defs):
                    console.print("│")
                    console.print(
                        "│  [red]All agents must have an LLM configured.[/red] Go to Agents to set one."
                    )
                    continue
                break
        except (BackRequest, NavigateBack):
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
        image_enabled=image_enabled,
        image_provider_key=image_provider_key,
        image_model=image_model,
        search_provider_key=search_provider_key,
        search_api_key=search_api_key,
        heartbeat_enabled=heartbeat_enabled,
        heartbeat_llm_provider_key=heartbeat_llm_provider_key,
        heartbeat_llm_model=heartbeat_llm_model,
        api_keys_dict=api_keys_dict,
        acp_server=acp_server,
        acp_agents=acp_agents,
        mcp_servers=mcp_servers,
    )
    print_end()


if __name__ == "__main__":
    run_initial_setup()
