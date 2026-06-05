"""Onboarding wizard — create a new agent profile interactively.

Custom raw-terminal TUI: no InquirerPy / prompt_toolkit dependency.
Arrow-key menus, inline text input with cursor, yes/no toggles.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from rich.console import Console

console = Console()

# ── Palette ───────────────────────────────────────────────────────────────────

PRIMARY   = "#e5c07b"
SECONDARY = "#61afef"
MUTED     = "#abb2bf"


def _hex_fg(h: str) -> str:
    h = h.lstrip("#")
    r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"\x1b[38;2;{r};{g};{b}m"


_RST  = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM  = "\x1b[2m"
_REV  = "\x1b[7m"
_PC   = _hex_fg(PRIMARY)
_SC   = _hex_fg(SECONDARY)
_MC   = _hex_fg(MUTED)


def _out(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()

def _clrln() -> None:
    _out("\r\x1b[2K")

def _up(n: int) -> None:
    if n > 0:
        _out(f"\x1b[{n}A")

def _hide_cur() -> None:
    _out("\x1b[?25l")

def _show_cur() -> None:
    _out("\x1b[?25h")


# ── Platform raw keyboard ─────────────────────────────────────────────────────

try:
    import tty
    import termios
    import select as _sel

    class _RawMode:  # pyright: ignore[reportRedeclaration]
        def __enter__(self) -> "_RawMode":
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            return self  # pyright: ignore[reportReturnType]

        def __exit__(self, *_: object) -> None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def _read_key() -> str:
        # Use os.read() to bypass Python's BufferedReader — sys.stdin.read(1) buffers
        # the full escape sequence on the first call, leaving nothing for select() to see.
        fd = sys.stdin.fileno()
        ch = os.read(fd, 1).decode("latin-1")
        if ch == "\x1b":
            if _sel.select([fd], [], [], 0.05)[0]:
                seq = os.read(fd, 1).decode("latin-1")
                if seq in ("[", "O"):  # \x1b[ (normal) or \x1bO (application cursor mode)
                    seq2 = os.read(fd, 1).decode("latin-1")
                    if seq2 == "A": return "up"
                    if seq2 == "B": return "down"
                    if seq2 == "C": return "right"
                    if seq2 == "D": return "left"
            return "escape"
        if ch in ("\r", "\n"): return "enter"
        if ch in ("\x7f", "\x08"): return "backspace"
        if ch == "\x03": return "ctrl_c"
        if ch == "\x04": return "ctrl_d"
        return ch

except ImportError:
    # Windows fallback
    import msvcrt  # type: ignore[import-untyped]

    class _RawMode:  # type: ignore[no-redef, misc]
        def __enter__(self) -> "_RawMode": return self
        def __exit__(self, *_: object) -> None: pass

    def _read_key() -> str:  # type: ignore[misc]
        b = msvcrt.getch()  # type: ignore[attr-defined]
        if b == b"\x03": return "ctrl_c"
        if b in (b"\r", b"\n"): return "enter"
        if b == b"\x08": return "backspace"
        if b in (b"\x00", b"\xe0"):
            b2 = msvcrt.getch()  # type: ignore[attr-defined]
            return {b"H": "up", b"P": "down", b"K": "left", b"M": "right"}.get(b2, "unknown")
        if b == b"\x1b": return "escape"
        return b.decode("utf-8", errors="replace")


# ── Internal frame helpers ────────────────────────────────────────────────────

def _erase_frame(fh: int) -> None:
    _up(fh)
    for i in range(fh):
        _clrln()
        if i < fh - 1:
            _out("\n")
    _up(fh - 1)


def _settle_frame(fh: int, label: str, answer: str) -> None:
    """Replace the interactive frame with a settled answered line."""
    _up(fh)
    _clrln()
    _out(f"{_SC}◇{_RST} {_PC}{_BOLD}{label}{_RST}  {_MC}{answer}{_RST}\n")
    for _ in range(fh - 1):
        _clrln()
        _out("\n")
    _up(fh - 1)


def _cancel() -> None:
    _show_cur()
    console.print("\n└ Cancelled.")
    sys.exit(0)


class GoBack(Exception):
    """Raised by widgets when the user presses Escape — go back one step."""


# ── TUI widgets ───────────────────────────────────────────────────────────────

_VISIBLE = 10  # max rows in select before scrolling


def select(label: str, choices: list[str]) -> str:
    """Arrow-key selection. Returns the chosen string."""
    n = len(choices)
    cursor = 0
    offset = 0
    visible = min(_VISIBLE, n)
    scrollable = n > _VISIBLE
    fh = 1 + visible + (1 if scrollable else 0)

    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")
    _hide_cur()

    def _draw(first: bool = False) -> None:
        if not first:
            _up(fh)
        _clrln()
        _out(f"{_SC}{_BOLD}◆{_RST} {_PC}{_BOLD}{label}{_RST}\n")
        for i in range(visible):
            idx = offset + i
            _clrln()
            if idx == cursor:
                _out(f"{_SC}{_BOLD}◉ {choices[idx]}{_RST}\n")
            else:
                _out(f"{_DIM}○{_RST} {_MC}{choices[idx]}{_RST}\n")
        if scrollable:
            _clrln()
            above, below = offset, n - offset - visible
            parts: list[str] = []
            if above: parts.append(f"↑{above}")
            if below: parts.append(f"↓{below}")
            _out(f"{_DIM}{'  '.join(parts)}{_RST}\n")

    try:
        _draw(first=True)
        with _RawMode():
            while True:
                key = _read_key()
                if key == "ctrl_c":
                    _erase_frame(fh)
                    _cancel()
                elif key == "up":
                    cursor = (cursor - 1) % n
                    if cursor == n - 1:
                        offset = max(0, n - visible)  # wrapped top→bottom
                    elif cursor < offset:
                        offset = cursor
                    _draw()
                elif key == "down":
                    cursor = (cursor + 1) % n
                    if cursor == 0:
                        offset = 0  # wrapped bottom→top
                    elif cursor >= offset + visible:
                        offset = cursor - visible + 1
                    _draw()
                elif key == "escape":
                    _erase_frame(fh)
                    raise GoBack
                elif key == "enter":
                    chosen = choices[cursor]
                    _show_cur()
                    _settle_frame(fh, label, chosen)
                    return chosen
    finally:
        _show_cur()
    assert False, "unreachable"


def text_input(label: str, is_password: bool = False, default: str = "") -> str:
    """Single-line text input with inline cursor. Returns the entered string."""
    chars: list[str] = list(default)
    pos = len(chars)
    fh = 2

    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")
    _hide_cur()

    def _draw(first: bool = False) -> None:
        if not first:
            _up(fh)
        _clrln()
        _out(f"{_SC}{_BOLD}◆{_RST} {_PC}{_BOLD}{label}{_RST}\n")
        _clrln()
        raw = "".join(chars)
        disp = "*" * len(raw) if is_password else raw
        if pos < len(disp):
            d = disp[:pos] + f"{_REV}{disp[pos]}{_RST}" + disp[pos + 1:]
        else:
            d = disp + f"{_REV} {_RST}"
        _out(f"› {d}\n")

    try:
        _draw(first=True)
        with _RawMode():
            while True:
                key = _read_key()
                if key == "ctrl_c":
                    _erase_frame(fh)
                    _cancel()
                elif key == "enter":
                    result = "".join(chars)
                    settled = "••••••••" if is_password else result
                    _show_cur()
                    _settle_frame(fh, label, settled)
                    return result
                elif key == "backspace" and pos > 0:
                    chars.pop(pos - 1); pos -= 1; _draw()
                elif key == "delete" and pos < len(chars):
                    chars.pop(pos); _draw()
                elif key == "left" and pos > 0:
                    pos -= 1; _draw()
                elif key == "right" and pos < len(chars):
                    pos += 1; _draw()
                elif key == "escape":
                    _erase_frame(fh)
                    raise GoBack
                elif len(key) == 1 and key not in ("\t",):
                    chars.insert(pos, key); pos += 1; _draw()
    finally:
        _show_cur()
    assert False, "unreachable"


def confirm(label: str, default: bool = True) -> bool:
    """Yes / No toggle. Returns True for Yes."""
    value = default
    fh = 2

    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")
    _hide_cur()

    def _draw(first: bool = False) -> None:
        if not first:
            _up(fh)
        _clrln()
        _out(f"{_SC}{_BOLD}◆{_RST} {_PC}{_BOLD}{label}{_RST}\n")
        _clrln()
        yes = f"{_SC}{_BOLD}Yes{_RST}" if value else f"{_DIM}Yes{_RST}"
        no  = f"{_SC}{_BOLD}No{_RST}"  if not value else f"{_DIM}No{_RST}"
        _out(f"{yes}  {no}  {_DIM}(← → or y/n · Enter to confirm){_RST}\n")

    try:
        _draw(first=True)
        with _RawMode():
            while True:
                key = _read_key()
                if key == "ctrl_c":
                    _erase_frame(fh)
                    _cancel()
                elif key in ("left", "right", "tab"):
                    value = not value; _draw()
                elif key in ("y", "Y"):
                    value = True; _draw()
                elif key in ("n", "N"):
                    value = False; _draw()
                elif key == "escape":
                    _erase_frame(fh)
                    raise GoBack
                elif key == "enter":
                    _show_cur()
                    _settle_frame(fh, label, "Yes" if value else "No")
                    return value
    finally:
        _show_cur()
    assert False, "unreachable"


# ── Provider / model catalogue ────────────────────────────────────────────────

# (provider_id, display_name, needs_api_key, oauth_auth_command_or_None)
_PROVIDERS: list[tuple[str, str, bool, str | None]] = [
    ("anthropic",           "Anthropic",           True,  None),
    ("openai",              "OpenAI",              True,  None),
    ("google",              "Google",              True,  None),
    ("groq",                "Groq",                True,  None),
    ("xai",                 "xAI",                 True,  None),
    ("deepseek",            "DeepSeek",            True,  None),
    ("mistral",             "Mistral",             True,  None),
    ("openrouter",          "OpenRouter",          True,  None),
    ("nvidia",              "NVIDIA",              True,  None),
    ("perplexity",          "Perplexity",          True,  None),
    ("kimi",                "Kimi / Moonshot",     True,  None),
    ("minimax",             "MiniMax",             True,  None),
    ("kilocode",            "Kilo Code",           True,  None),
    ("bedrock",             "AWS Bedrock",         True,  None),
    ("ollama",              "Ollama (local)",      False, None),
    ("anthropic-claude-code", "Claude Code",       False, "operator auth claude-code"),
    ("openai-codex",        "OpenAI Codex",        False, "operator auth codex"),
    ("github-copilot",      "GitHub Copilot",      False, "operator auth github-copilot"),
    ("google-antigravity",  "Google Antigravity",  False, "operator auth antigravity"),
]

_MODELS: dict[str, list[str]] = {
    "anthropic":   ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-5", "claude-sonnet-4-5"],
    "openai":      ["gpt-5.4", "gpt-5.4-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini", "o3-mini"],
    "google":      ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-3-flash-preview"],
    "groq":        ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"],
    "xai":         ["grok-4", "grok-3", "grok-3-mini", "grok-3-fast"],
    "deepseek":    ["deepseek-chat", "deepseek-reasoner", "deepseek-r1"],
    "mistral":     ["mistral-large-2512", "mistral-small-2506", "magistral-medium-2509", "codestral-2508"],
    "openrouter":  ["meta-llama/llama-4-scout-17b-16e-instruct", "anthropic/claude-sonnet-4-6", "openai/gpt-5.4", "deepseek/deepseek-chat"],
    "nvidia":      ["meta/llama-3.3-70b-instruct", "meta/llama-3.1-405b-instruct", "deepseek-ai/deepseek-r1"],
    "perplexity":  ["llama-3.1-sonar-large-128k-online", "llama-3.1-sonar-small-128k-online"],
    "kimi":        ["moonshot-v1-128k", "kimi-k2-instruct"],
    "minimax":     ["MiniMax-M1", "MiniMax-Text-01"],
    "kilocode":    ["kilo-claude-sonnet-4-6", "kilo-gpt-4.1"],
    "ollama":      ["llama3.3", "llama3.2", "mistral", "gemma3", "qwen3.5", "deepseek-r1"],
    "anthropic-claude-code": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
    "openai-codex":          ["gpt-5.4", "gpt-5.4-mini"],
    "github-copilot":        ["gpt-5.4", "claude-sonnet-4-6", "gemini-3-pro"],
    "google-antigravity":    ["gemini-2.5-pro", "gemini-2.5-flash", "claude-sonnet-4-6"],
}

_CHANNEL_NOTES: dict[str, str] = {
    "Telegram": "Create a bot via @BotFather on Telegram, then copy the token.",
    "Discord":  "Create a bot at discord.com/developers and enable Message Content Intent.",
    "Slack":    "Create a Slack app at api.slack.com (bot token starts xoxb-, app token xapp-).",
    "Twitch":   "Provide a valid Twitch OAuth token, your bot's nick, and the channel to join.",
}

_STT_PROVIDERS: dict[str, list[str]] = {
    "Groq":     ["whisper-large-v3-turbo", "whisper-large-v3"],
    "OpenAI":   ["whisper-1"],
    "Google":   ["gemini-2.0-flash", "gemini-2.5-flash"],
    "Deepgram": ["nova-3", "nova-2"],
}

_TTS_PROVIDERS: dict[str, list[str]] = {
    "Groq":       ["canopylabs/orpheus-v1-english"],
    "xAI":        ["grok-tts"],
    "OpenAI":     ["tts-1-hd", "tts-1"],
    "Google":     ["gemini-2.0-flash-preview-tts"],
    "ElevenLabs": ["eleven_multilingual_v2", "eleven_flash_v2_5"],
    "Deepgram":   ["aura-2"],
}

_TTS_VOICES: dict[str, list[str]] = {
    "OpenAI":     ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
    "xAI":        ["eve", "ara", "rex", "sal", "leo"],
    "Groq":       ["autumn", "diana", "hannah", "austin", "daniel", "troy"],
    "Google":     ["Aoede", "Charon", "Fenrir", "Kore", "Puck"],
    "ElevenLabs": ["Rachel", "Drew", "Clyde", "Paul", "Domi"],
    "Deepgram":   ["asteria-en", "luna-en", "stella-en", "athena-en", "hera-en"],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

# Profile names that cannot be created via onboarding (used internally)
_RESERVED_NAMES = {"ephemeral"}

def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", s.strip().lower()).strip("-") or "agent"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("operator-use")
    except Exception:
        return ""


def _parse_agent_md(path: Path) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body) from an AGENT.md file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}, ""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    front_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    front: dict[str, str] = {}
    for line in front_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            front[k.strip()] = v.strip()
    return front, body


def _patch_settings(path: Path, patch: dict) -> None:
    """Deep-merge patch into a settings JSON file."""
    def _merge(base: dict, upd: dict) -> None:
        for k, v in upd.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                _merge(base[k], v)
            else:
                base[k] = v
    data = _load_json(path)
    _merge(data, patch)
    _save_json(path, data)


def _write_agent_md(path: Path, front: dict[str, str], body: str) -> None:
    lines = ["---"]
    for k, v in front.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    text = "\n".join(lines) + "\n"
    if body:
        text += f"\n{body}"
    path.write_text(text, encoding="utf-8")


# ── Category configurators (each is a pick-list loop like the category menu) ──

_DONE_LABEL = "Done"


def _val(v: object) -> str:
    """Format a setting value for display next to its menu label."""
    if v is None or v == "":  return "not set"
    if v is True:              return "on"
    if v is False:             return "off"
    return str(v)


def _cfg_language_model(profile_dir: Path, providers_path: Path) -> None:
    while True:
        front, body = _parse_agent_md(profile_dir / "AGENT.md")
        cur_prov  = front.get("provider", "")
        cur_model = front.get("model", "")
        ep = _load_json(providers_path)
        key_state = "stored" if ep.get(cur_prov, {}).get("api_key") else "not set"

        choices = [
            f"Provider  ({_val(cur_prov)})",
            f"Model     ({_val(cur_model)})",
            f"API key   ({key_state})",
            _DONE_LABEL,
        ]
        console.print("│")
        try:
            picked = select("Language model:", choices)
        except GoBack:
            return

        if picked == _DONE_LABEL:
            return

        idx = choices.index(picked)
        try:
            if idx == 0:  # Provider
                chosen_name = select("Provider:", [p[1] for p in _PROVIDERS])
                prov_id = next(p[0] for p in _PROVIDERS if p[1] == chosen_name)
                front["provider"] = prov_id
                _write_agent_md(profile_dir / "AGENT.md", front, body)

            elif idx == 1:  # Model
                prov_id = cur_prov
                _CUSTOM = "Custom (type model ID)..."
                model_opts = _MODELS.get(prov_id, []) + [_CUSTOM]
                chosen = select("Model:", model_opts)
                model_id = text_input("Model ID:", default=cur_model) if chosen == _CUSTOM else chosen
                front["model"] = model_id
                _write_agent_md(profile_dir / "AGENT.md", front, body)

            elif idx == 2:  # API key
                prov_entry = next((p for p in _PROVIDERS if p[0] == cur_prov), None)
                if prov_entry and prov_entry[2]:  # needs_key
                    if ep.get(cur_prov, {}).get("api_key"):
                        if not confirm(f"Replace stored key for {cur_prov}?", default=False):
                            continue
                    new_key = text_input(f"API key for {cur_prov}:", is_password=True)
                    if new_key:
                        store = _load_json(providers_path)
                        store[cur_prov] = {"api_key": new_key}
                        _save_json(providers_path, store)
                elif prov_entry and prov_entry[3]:  # oauth_cmd
                    console.print(f"│  [dim]Run:[/dim]  [bold {SECONDARY}]{prov_entry[3]}[/bold {SECONDARY}]")
                else:
                    console.print(f"│  [dim]No API key needed for {cur_prov}.[/dim]")
        except GoBack:
            continue  # back to this settings menu


def _cfg_tts(profile_dir: Path) -> None:
    settings_path = profile_dir / "settings.json"
    while True:
        s   = _load_json(settings_path)
        tts = s.get("tts", {})
        aux = s.get("auxiliary", {}).get("tts", {})
        choices = [
            f"Enable    ({_val(tts.get('enabled', False))})",
            f"Provider  ({_val(aux.get('provider'))})",
            f"Model     ({_val(aux.get('model'))})",
            f"Voice     ({_val(tts.get('voice'))})",
            f"Speed     ({_val(tts.get('speed', 1.0))})",
            _DONE_LABEL,
        ]
        console.print("│")
        try:
            picked = select("Text-to-speech:", choices)
        except GoBack:
            return

        if picked == _DONE_LABEL:
            return

        idx = choices.index(picked)
        try:
            if idx == 0:
                _patch_settings(settings_path, {"tts": {"enabled": confirm("Enable TTS?", default=bool(tts.get("enabled", False)))}})
            elif idx == 1:
                prov = select("TTS provider:", list(_TTS_PROVIDERS))
                _patch_settings(settings_path, {"auxiliary": {"tts": {"provider": prov.lower().replace(" ", "_")}}})
            elif idx == 2:
                cur_prov = aux.get("provider", "")
                display_prov = next((k for k in _TTS_PROVIDERS if k.lower().replace(" ", "_") == cur_prov), list(_TTS_PROVIDERS)[0])
                models = _TTS_PROVIDERS[display_prov]
                m = select("TTS model:", models) if len(models) > 1 else models[0]
                _patch_settings(settings_path, {"auxiliary": {"tts": {"model": m}}})
            elif idx == 3:
                cur_prov = aux.get("provider", "")
                display_prov = next((k for k in _TTS_VOICES if k.lower().replace(" ", "_") == cur_prov), "")
                voices = _TTS_VOICES.get(display_prov, list(_TTS_VOICES.values())[0])
                _patch_settings(settings_path, {"tts": {"voice": select("Voice:", voices)}})
            elif idx == 4:
                raw = text_input("Speed (e.g. 1.0, 1.25):", default=str(tts.get("speed", 1.0)))
                try:
                    _patch_settings(settings_path, {"tts": {"speed": float(raw)}})
                except ValueError:
                    pass
        except GoBack:
            continue


def _cfg_stt(profile_dir: Path) -> None:
    settings_path = profile_dir / "settings.json"
    while True:
        s   = _load_json(settings_path)
        stt = s.get("stt", {})
        aux = s.get("auxiliary", {}).get("stt", {})
        choices = [
            f"Enable    ({_val(stt.get('enabled', False))})",
            f"Provider  ({_val(aux.get('provider'))})",
            f"Model     ({_val(aux.get('model'))})",
            f"Language  ({_val(stt.get('language')) or 'auto'})",
            _DONE_LABEL,
        ]
        console.print("│")
        try:
            picked = select("Speech-to-text:", choices)
        except GoBack:
            return

        if picked == _DONE_LABEL:
            return

        idx = choices.index(picked)
        try:
            if idx == 0:
                _patch_settings(settings_path, {"stt": {"enabled": confirm("Enable STT?", default=bool(stt.get("enabled", False)))}})
            elif idx == 1:
                prov = select("STT provider:", list(_STT_PROVIDERS))
                _patch_settings(settings_path, {"auxiliary": {"stt": {"provider": prov.lower()}}})
            elif idx == 2:
                cur_prov = aux.get("provider", "")
                display_prov = next((k for k in _STT_PROVIDERS if k.lower() == cur_prov), list(_STT_PROVIDERS)[0])
                models = _STT_PROVIDERS[display_prov]
                m = select("STT model:", models) if len(models) > 1 else models[0]
                _patch_settings(settings_path, {"auxiliary": {"stt": {"model": m}}})
            elif idx == 3:
                lang = text_input("Language (BCP-47, e.g. en, fr — blank for auto):", default=stt.get("language", ""))
                _patch_settings(settings_path, {"stt": {"language": lang.strip() or None}})
        except GoBack:
            continue


def _cfg_channels(profile_dir: Path) -> None:
    settings_path  = profile_dir / "settings.json"
    auth_path      = profile_dir / "auth" / "channels.json"
    _CH_NAMES      = ["Telegram", "Discord", "Slack", "Twitch"]

    while True:
        s    = _load_json(settings_path).get("channels", {})
        auth = _load_json(auth_path)

        def _ch_state(ch: str) -> str:
            configured = bool(auth.get(ch.lower()))
            enabled    = s.get(ch.lower(), {}).get("enabled", False)
            if not configured: return "not configured"
            return "on" if enabled else "off"

        choices = [f"{ch}  ({_ch_state(ch)})" for ch in _CH_NAMES] + [_DONE_LABEL]
        console.print("│")
        try:
            picked = select("Channels:", choices)
        except GoBack:
            return

        if picked == _DONE_LABEL:
            return

        ch_name = _CH_NAMES[choices.index(picked)]
        ch_id   = ch_name.lower()
        note    = _CHANNEL_NOTES.get(ch_name, "")
        if note:
            console.print(f"│  [dim]{note}[/dim]")

        # Per-channel settings menu
        while True:
            cur_auth    = _load_json(auth_path).get(ch_id, {})
            configured  = bool(cur_auth)
            cur_enabled = _load_json(settings_path).get("channels", {}).get(ch_id, {}).get("enabled", False)

            ch_choices = [
                f"Token     ({'stored' if configured else 'not set'})",
                f"Enabled   ({_val(cur_enabled)})",
                _DONE_LABEL,
            ]
            if ch_name == "Twitch":
                cur_nick = _load_json(settings_path).get("channels", {}).get("twitch", {}).get("nick", "")
                cur_chan = _load_json(settings_path).get("channels", {}).get("twitch", {}).get("channel_name", "")
                ch_choices = [
                    f"Token     ({'stored' if configured else 'not set'})",
                    f"Enabled   ({_val(cur_enabled)})",
                    f"Nick      ({_val(cur_nick)})",
                    f"Channel   ({_val(cur_chan)})",
                    _DONE_LABEL,
                ]

            console.print("│")
            try:
                ch_picked = select(f"{ch_name}:", ch_choices)
            except GoBack:
                break  # back to channel list

            if ch_picked == _DONE_LABEL:
                break

            ch_idx = ch_choices.index(ch_picked)
            try:
                if ch_idx == 0:  # Token
                    if ch_name == "Telegram":
                        token = text_input("Bot token:", is_password=True)
                        if token:
                            a = _load_json(auth_path)
                            a[ch_id] = {**a.get(ch_id, {}), "bot_token": token}
                            _save_json(auth_path, a)
                    elif ch_name == "Discord":
                        token = text_input("Bot token:", is_password=True)
                        if token:
                            a = _load_json(auth_path)
                            a[ch_id] = {**a.get(ch_id, {}), "bot_token": token}
                            _save_json(auth_path, a)
                    elif ch_name == "Slack":
                        bt = text_input("Bot token (xoxb-...):", is_password=True)
                        at = text_input("App token (xapp-...):", is_password=True)
                        if bt or at:
                            a = _load_json(auth_path)
                            a[ch_id] = {**a.get(ch_id, {}), **({"bot_token": bt} if bt else {}), **({"app_token": at} if at else {})}
                            _save_json(auth_path, a)
                    elif ch_name == "Twitch":
                        token = text_input("OAuth token:", is_password=True)
                        if token:
                            a = _load_json(auth_path)
                            a[ch_id] = {**a.get(ch_id, {}), "token": token}
                            _save_json(auth_path, a)
                elif ch_idx == 1:  # Enabled
                    new_en = confirm(f"Enable {ch_name}?", default=cur_enabled)
                    _patch_settings(settings_path, {"channels": {ch_id: {"enabled": new_en}}})
                elif ch_name == "Twitch" and ch_idx == 2:  # Nick
                    nick = text_input("Bot nickname:", default=cur_nick)
                    if nick:
                        _patch_settings(settings_path, {"channels": {"twitch": {"nick": nick}}})
                elif ch_name == "Twitch" and ch_idx == 3:  # Channel
                    chan = text_input("Channel to join (without #):", default=cur_chan)
                    if chan:
                        _patch_settings(settings_path, {"channels": {"twitch": {"channel_name": chan}}})
            except GoBack:
                continue  # back to per-channel settings menu


def _cfg_browser(profile_dir: Path) -> None:
    settings_path = profile_dir / "settings.json"
    while True:
        cur = _load_json(settings_path).get("browser_use", {})
        choices = [
            f"Enable    ({_val(cur.get('enabled', False))})",
            f"Headless  ({_val(cur.get('headless', False))})",
            f"Browser   ({_val(cur.get('browser')) or 'auto-detect'})",
            _DONE_LABEL,
        ]
        console.print("│")
        try:
            picked = select("Browser use:", choices)
        except GoBack:
            return

        if picked == _DONE_LABEL:
            return

        idx = choices.index(picked)
        try:
            if idx == 0:
                _patch_settings(settings_path, {"browser_use": {"enabled": confirm("Enable browser automation?", default=cur.get("enabled", False))}})
            elif idx == 1:
                _patch_settings(settings_path, {"browser_use": {"headless": confirm("Run headless?", default=cur.get("headless", False))}})
            elif idx == 2:
                bt = select("Browser:", ["auto-detect", "chrome", "edge"])
                _patch_settings(settings_path, {"browser_use": {"browser": None if bt == "auto-detect" else bt}})
        except GoBack:
            continue


def _cfg_computer(profile_dir: Path) -> None:
    settings_path = profile_dir / "settings.json"
    while True:
        cur = _load_json(settings_path).get("computer_use", {})
        choices = [
            f"Enable    ({_val(cur.get('enabled', False))})",
            _DONE_LABEL,
        ]
        console.print("│")
        try:
            picked = select("Computer use:", choices)
        except GoBack:
            return

        if picked == _DONE_LABEL:
            return

        try:
            _patch_settings(settings_path, {"computer_use": {"enabled": confirm("Enable computer use?", default=cur.get("enabled", False))}})
        except GoBack:
            continue


def _cfg_description(profile_dir: Path) -> None:
    front, body = _parse_agent_md(profile_dir / "AGENT.md")
    console.print("│")
    try:
        new_desc = text_input("Description:", default=front.get("description", ""))
    except GoBack:
        return
    if new_desc != front.get("description", ""):
        front["description"] = new_desc
        _write_agent_md(profile_dir / "AGENT.md", front, body)


# ── Wizard ────────────────────────────────────────────────────────────────────

_CREATE_NEW = "+ Create new agent"


def run_onboard() -> None:
    """Run the interactive agent-profile creation wizard."""
    from operator_use.settings.paths import get_config_dir, get_profiles_dir
    from operator_use.runtime.bootstrap import bootstrap_global_dir, bootstrap_profile

    ver = _version()
    ver_str = f"  [dim]v{ver}[/dim]" if ver else ""

    console.print()
    console.print(f"┌ [bold {PRIMARY}]Operator[/bold {PRIMARY}]{ver_str} [bold {PRIMARY}]Agents[/bold {PRIMARY}]")
    console.print("│")
    console.print(f"│  [dim]Select an agent to configure, or create a new one.[/dim]")

    profiles_dir = get_profiles_dir()
    providers_path = get_config_dir() / "auth" / "providers.json"

    _DONE = "Done"
    _SEP  = "  —  "
    _CATEGORIES = [
        "Language model", "Text-to-speech", "Speech-to-text",
        "Channels", "Browser use", "Computer use", "Description", _DONE,
    ]

    def _build_choices() -> tuple[list[str], dict[str, str]]:
        names = sorted(d.name for d in profiles_dir.iterdir() if d.is_dir()) \
                if profiles_dir.exists() else []
        lmap: dict[str, str] = {}
        choices: list[str] = []
        for n in names:
            fp, _ = _parse_agent_md(profiles_dir / n / "AGENT.md")
            desc = fp.get("description", "").strip()
            label = f"{n}{_SEP}{desc}" if desc else n
            lmap[label] = n
            choices.append(label)
        choices.append(_CREATE_NEW)
        return choices, lmap

    # ── Outer loop: agent picker ──────────────────────────────────────────────
    while True:
        display_choices, label_to_name = _build_choices()

        try:
            picked_label = select("Agent:", display_choices)
        except GoBack:
            console.print(f"└ [dim]Cancelled.[/dim]")
            console.print()
            return

        # ══════════════════════════════════════════════════════════════════════
        # EXISTING AGENT — category loop (Esc = back to agent picker)
        # ══════════════════════════════════════════════════════════════════════
        if picked_label != _CREATE_NEW:
            name = label_to_name[picked_label]
            profile_dir = profiles_dir / name
            front, _ = _parse_agent_md(profile_dir / "AGENT.md")
            prov_hint  = front.get("provider") or "not set"
            model_hint = front.get("model")    or "not set"
            console.print("│")
            console.print(f"[bold {SECONDARY}]◇[/bold {SECONDARY}] [bold {PRIMARY}]{name}[/bold {PRIMARY}]  "
                          f"[dim]{prov_hint} / {model_hint}[/dim]")

            done = False
            while not done:
                console.print("│")
                try:
                    cat = select(f"Configure {name}:", _CATEGORIES)
                except GoBack:
                    break  # Esc on category menu → back to agent picker

                if cat == _DONE:
                    done = True
                else:
                    try:
                        if   cat == "Language model":  _cfg_language_model(profile_dir, providers_path)
                        elif cat == "Text-to-speech":  _cfg_tts(profile_dir)
                        elif cat == "Speech-to-text":  _cfg_stt(profile_dir)
                        elif cat == "Channels":        _cfg_channels(profile_dir)
                        elif cat == "Browser use":     _cfg_browser(profile_dir)
                        elif cat == "Computer use":    _cfg_computer(profile_dir)
                        elif cat == "Description":     _cfg_description(profile_dir)
                    except GoBack:
                        pass  # Esc inside a configurator → back to category menu

            if done:
                console.print("│")
                console.print(f"└ [bold {SECONDARY}]Done.[/bold {SECONDARY}]  "
                              f"[dim]Run with:[/dim]  [bold]operator --profile {name}[/bold]")
                console.print()
                return
            # else: break from category loop → continue outer loop (re-show agent picker)
            continue

        # ══════════════════════════════════════════════════════════════════════
        # NEW AGENT — step machine (Esc = back one step; at step 0 = back to picker)
        # ══════════════════════════════════════════════════════════════════════
        # Steps: 0=name  1=description  2=provider  3=model  4=credentials  5=confirm
        step = 0
        na_name: str = ""
        na_desc: str = ""
        na_prov_id: str = ""
        na_chosen_name: str = ""
        na_needs_key: bool = False
        na_oauth_cmd: str | None = None
        na_model_id: str = ""
        na_api_key: str | None = None

        back_to_picker = False

        while step <= 5:
            try:
                if step == 0:
                    console.print("│")
                    console.print(f"[bold {SECONDARY}]◆[/bold {SECONDARY}]  [bold {PRIMARY}]Name[/bold {PRIMARY}]")
                    console.print(f"│  [dim]Short identifier, e.g. coder, assistant, work.[/dim]")
                    raw = _slugify(text_input("Agent name:", default=na_name))
                    if raw in _RESERVED_NAMES or raw.startswith("ephemeral-"):
                        console.print(f"│  [red]'{raw}' is reserved — choose a different name.[/red]")
                        continue
                    if (profiles_dir / raw).exists():
                        console.print(f"│  [yellow]'{raw}' already exists. Pick it from the list instead.[/yellow]")
                        continue
                    na_name = raw
                    step += 1

                elif step == 1:
                    console.print("│")
                    console.print(f"[bold {SECONDARY}]◆[/bold {SECONDARY}]  [bold {PRIMARY}]Description[/bold {PRIMARY}]")
                    console.print(f"│  [dim]One sentence describing this agent's role (optional).[/dim]")
                    na_desc = text_input("Description:", default=na_desc)
                    step += 1

                elif step == 2:
                    console.print("│")
                    console.print(f"[bold {SECONDARY}]◆[/bold {SECONDARY}]  [bold {PRIMARY}]Language model[/bold {PRIMARY}]")
                    console.print(f"│  [dim]Pick the AI provider you have access to.[/dim]")
                    na_chosen_name = select("Provider:", [p[1] for p in _PROVIDERS])
                    na_prov_id, _, na_needs_key, na_oauth_cmd = next(
                        p for p in _PROVIDERS if p[1] == na_chosen_name
                    )
                    step += 1

                elif step == 3:
                    _CUSTOM = "Custom (type model ID)..."
                    model_opts = _MODELS.get(na_prov_id, []) + [_CUSTOM]
                    chosen_model = select("Model:", model_opts)
                    na_model_id = text_input("Enter model ID:") if chosen_model == _CUSTOM else chosen_model
                    step += 1

                elif step == 4:
                    console.print("│")
                    console.print(f"[bold {SECONDARY}]◆[/bold {SECONDARY}]  [bold {PRIMARY}]Credentials[/bold {PRIMARY}]")
                    if na_needs_key:
                        ep = _load_json(providers_path)
                        if ep.get(na_prov_id, {}).get("api_key"):
                            console.print(f"│  [dim]An API key for {na_chosen_name} is already stored.[/dim]")
                            if not confirm(f"Reuse existing {na_chosen_name} key?", default=True):
                                na_api_key = text_input(f"New API key for {na_chosen_name}:", is_password=True)
                            else:
                                na_api_key = None
                        else:
                            na_api_key = text_input(f"API key for {na_chosen_name}:", is_password=True)
                    elif na_oauth_cmd:
                        console.print(f"│  [dim]Authenticate after setup:[/dim]  [bold {SECONDARY}]{na_oauth_cmd}[/bold {SECONDARY}]")
                    else:
                        console.print(f"│  [dim]{na_chosen_name} runs locally — no API key needed.[/dim]")
                    step += 1

                elif step == 5:
                    profile_dir = profiles_dir / na_name  # noqa: F841
                    console.print("│")
                    console.print(f"[bold {SECONDARY}]◇[/bold {SECONDARY}] [bold {PRIMARY}]Review[/bold {PRIMARY}]")
                    console.print("│")
                    console.print(f"│  [dim]name    [/dim]  [{SECONDARY}]{na_name}[/{SECONDARY}]")
                    if na_desc:
                        console.print(f"│  [dim]desc    [/dim]  {na_desc}")
                    console.print(f"│  [dim]provider[/dim]  [{SECONDARY}]{na_chosen_name}[/{SECONDARY}]")
                    console.print(f"│  [dim]model   [/dim]  [{SECONDARY}]{na_model_id}[/{SECONDARY}]")
                    console.print(f"│  [dim]profile [/dim]  [dim]{profile_dir}[/dim]")
                    console.print("│")
                    if not confirm("Create this agent?"):
                        console.print(f"└ [dim]Cancelled — nothing written.[/dim]")
                        console.print()
                        return
                    step += 1  # exit loop

            except GoBack:
                if step == 0:
                    back_to_picker = True
                    break
                step -= 1

        if back_to_picker:
            continue  # back to outer agent-picker loop

        if step > 5:
            # ── Write ──────────────────────────────────────────────────────────
            profile_dir = profiles_dir / na_name
            bootstrap_global_dir()
            bootstrap_profile(profile_dir, na_name, na_desc or f"{na_name} agent")
            _write_agent_md(profile_dir / "AGENT.md",
                            {"name": na_name, "description": na_desc,
                             "model": na_model_id, "provider": na_prov_id}, "")

            if na_api_key:
                prov_store = _load_json(providers_path)
                prov_store[na_prov_id] = {"api_key": na_api_key}
                _save_json(providers_path, prov_store)

            gs_path = get_config_dir() / "settings.json"
            gs = _load_json(gs_path)
            pl: list[str] = gs.get("profiles", [])
            if na_name not in pl:
                pl.append(na_name)
                gs["profiles"] = pl
                _save_json(gs_path, gs)

            console.print("│")
            console.print(
                f"└ [bold {SECONDARY}]Agent '{na_name}' created![/bold {SECONDARY}]  "
                f"[dim]Configure more with:[/dim]  [bold]operator onboard[/bold]"
            )
            console.print()
        return
