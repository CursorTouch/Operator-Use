import os
import sys
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.enum import INQUIRERPY_KEYBOARD_INTERRUPT
from InquirerPy.utils import get_style
from rich.console import Console


def _configure_console_encoding() -> None:
    """Prefer UTF-8 on Windows so Rich can render box-drawing characters."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


_configure_console_encoding()
console = Console()
BACK_SIGNAL = "__operator_back__"
ESCAPE_FLUSH_TIMEOUT_SECONDS = 0.01


class NavigateBack(Exception):
    """Raised when the user requests backward navigation in the TUI."""


PRIMARY = "#e5c07b"
SECONDARY = "#61afef"
MUTED = "#abb2bf"

clack_style = get_style(
    {
        "questionmark": f"{PRIMARY} bold",
        "question": f"{PRIMARY} bold",
        "answermark": f"{SECONDARY}",
        "answered_question": f"{PRIMARY} bold",
        "answer": f"{MUTED}",
        "pointer": f"{SECONDARY} bold",
        "highlighted": f"{SECONDARY} bold",
        "selected": f"{SECONDARY} bold",
        "instruction": f"{MUTED}",
    },
    False,
)


class BackRequest(Exception):
    """Raised when the user requests to go back one prompt with Esc."""


BACK_KEYBINDINGS = {"skip": [{"key": "escape"}]}


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("operator-use")
    except Exception:
        return ""


def print_banner():
    ver = _version()
    ver_str = f"  [dim]v{ver}[/dim]" if ver else ""
    console.print()
    console.print(
        f"               [bold {PRIMARY}]OPERATOR[/bold {PRIMARY}]{ver_str}", justify="left"
    )
    console.print()


def print_start(title: str = "Initial Setup"):
    ver = _version()
    ver_str = f" [dim]v{ver}[/dim]" if ver else ""
    console.print(
        f"┌ [bold {PRIMARY}]Operator[/bold {PRIMARY}]{ver_str} [bold {PRIMARY}]{title}[/bold {PRIMARY}]"
    )
    console.print("│")


def print_step(n: int, total: int, title: str, hint: str = "") -> None:
    """Print a numbered step header."""
    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")
    step_label = f"[dim]Step {n}/{total}[/dim]"
    console.print(
        f"[bold {SECONDARY}]◆[/bold {SECONDARY}] {step_label}  [bold {PRIMARY}]{title}[/bold {PRIMARY}]"
    )
    if hint:
        console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]  [dim]{hint}[/dim]")


def print_info(title: str, info_dict: dict):
    console.print(
        f"[bold {SECONDARY}]◇[/bold {SECONDARY}] [bold {PRIMARY}]{title}[/bold {PRIMARY}]"
    )
    console.print("│")
    for k, v in info_dict.items():
        console.print(f"│ [bright_black]{k}:[/bright_black] [white]{v}[/white]")
    console.print("│")


def clear_screen() -> None:
    """Clear the terminal in a way that works reliably across Windows shells."""
    if os.name == "nt":
        os.system("cls")
    else:
        console.clear(home=True)


def _execute_prompt(prompt, allow_back: bool = True):
    app = getattr(prompt, "application", None)
    if app is None:
        session = getattr(prompt, "_session", None)
        app = getattr(session, "app", None)

    if app is not None:
        # Avoid the default prompt_toolkit delay when disambiguating Escape vs Alt+key.
        app.ttimeoutlen = ESCAPE_FLUSH_TIMEOUT_SECONDS

    if allow_back:

        @prompt.register_kb("escape")
        def _go_back(event):
            event.app.exit(result=BACK_SIGNAL)

    result = prompt.execute()
    if result == BACK_SIGNAL:
        raise NavigateBack
    return result


def select(message: str, choices: list, is_last: bool = False, allow_back: bool = True) -> str:
    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")

    formatted_choices = []
    for c in choices:
        formatted_choices.append(Choice(c, f"o {c}"))

    prompt = inquirer.select(
        message=message,
        choices=formatted_choices,
        qmark="◆",
        amark="◇",
        pointer="◉",  # The active pointer replaces the margin slot before the Choice item
        instruction=" ",
        style=clack_style,
        keybindings=BACK_KEYBINDINGS,
        mandatory=False,
    )
    result = _execute_prompt(prompt, allow_back=allow_back)

    if result == INQUIRERPY_KEYBOARD_INTERRUPT:
        console.print("└ Cancelled.")
        sys.exit(1)
    if result is None:
        raise BackRequest()

    return result


def text_input(
    message: str,
    is_password: bool = False,
    is_last: bool = False,
    default: str = "",
    allow_back: bool = True,
) -> str:
    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")

    if is_password:
        prompt = inquirer.secret(
            message=message,
            qmark="◆",
            amark="◇",
            style=clack_style,
            keybindings=BACK_KEYBINDINGS,
            mandatory=False,
        )
    else:
        kwargs = dict(message=message, qmark="◆", amark="◇", style=clack_style)
        if default:
            kwargs["default"] = default
        kwargs["keybindings"] = BACK_KEYBINDINGS
        kwargs["mandatory"] = False
        prompt = inquirer.text(**kwargs)
    result = _execute_prompt(prompt, allow_back=allow_back)

    if result == INQUIRERPY_KEYBOARD_INTERRUPT:
        console.print("└ Cancelled.")
        sys.exit(1)
    if result is None:
        raise BackRequest()

    return result


def confirm(message: str, is_last: bool = False, allow_back: bool = True) -> bool:
    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")

    prompt = inquirer.confirm(
        message=message,
        qmark="◆",
        amark="◇",
        default=True,
        style=clack_style,
        keybindings=BACK_KEYBINDINGS,
        mandatory=False,
    )
    result = _execute_prompt(prompt, allow_back=allow_back)

    if result == INQUIRERPY_KEYBOARD_INTERRUPT:
        console.print("└ Cancelled.")
        sys.exit(1)
    if result is None:
        raise BackRequest()

    return result


def print_end():
    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")
    console.print(
        f"└ [bold {SECONDARY}]Setup complete![/bold {SECONDARY}] [dim]Restart operator to apply changes.[/dim]"
    )
    console.print()


def print_end_first_install():
    console.print(f"[bold {PRIMARY}]│[/bold {PRIMARY}]")
    console.print(
        f"└ [bold {SECONDARY}]Setup complete![/bold {SECONDARY}] Starting your agent now..."
    )
    console.print()
