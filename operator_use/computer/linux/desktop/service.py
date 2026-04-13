"""Linux desktop control and state via xdotool + wmctrl + xprop (X11).

Requires:
    sudo apt install xdotool wmctrl xprop     # Debian/Ubuntu
    sudo dnf install xdotool wmctrl xprop     # Fedora
    sudo pacman -S xdotool wmctrl xorg-xprop  # Arch

Only works with X11. Wayland requires additional setup (e.g. ydotool).
"""

import os
import re
import subprocess
import time
from operator_use.computer.linux.desktop.views import DesktopState, Window, Status

_ENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}

# ── Key map ───────────────────────────────────────────────────────────────────

KEY_MAP = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "super",
    "windows": "super",
    "command": "super",
    "enter": "Return",
    "return": "Return",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "ins": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pgup": "Page_Up",
    "pagedown": "Page_Down",
    "pgdn": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "space": "space",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}

# ── Internal helpers ──────────────────────────────────────────────────────────


def _xdo(*args, check=True) -> str:
    """Run an xdotool command."""
    result = subprocess.run(["xdotool"] + list(args), capture_output=True, text=True, env=_ENV)
    if check and result.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _run(*cmd) -> str:
    """Run any command, return stdout or empty string on failure."""
    result = subprocess.run(list(cmd), capture_output=True, text=True, env=_ENV)
    return result.stdout.strip() if result.returncode == 0 else ""


# ── Mouse ─────────────────────────────────────────────────────────────────────


def GetCursorPos():
    out = _xdo("getmouselocation", "--shell")
    x = int(re.search(r"X=(\d+)", out).group(1))
    y = int(re.search(r"Y=(\d+)", out).group(1))
    return x, y


def SetCursorPos(x, y):
    _xdo("mousemove", str(x), str(y))


def MoveTo(x, y):
    _xdo("mousemove", "--sync", str(x), str(y))


def Click(x, y):
    _xdo("mousemove", "--sync", str(x), str(y), "click", "1")


def DoubleClick(x, y):
    _xdo("mousemove", "--sync", str(x), str(y), "click", "--repeat", "2", "--delay", "100", "1")


def RightClick(x, y):
    _xdo("mousemove", "--sync", str(x), str(y), "click", "3")


def MiddleClick(x, y):
    _xdo("mousemove", "--sync", str(x), str(y), "click", "2")


def DragTo(x1, y1, x2, y2):
    _xdo("mousemove", "--sync", str(x1), str(y1))
    _xdo("mousedown", "1")
    time.sleep(0.05)
    _xdo("mousemove", "--sync", str(x2), str(y2))
    time.sleep(0.05)
    _xdo("mouseup", "1")


def WheelUp(times=1):
    for _ in range(times):
        _xdo("click", "4")
        time.sleep(0.05)


def WheelDown(times=1):
    for _ in range(times):
        _xdo("click", "5")
        time.sleep(0.05)


def WheelLeft(times=1):
    for _ in range(times):
        _xdo("click", "6")
        time.sleep(0.05)


def WheelRight(times=1):
    for _ in range(times):
        _xdo("click", "7")
        time.sleep(0.05)


# ── Keyboard ──────────────────────────────────────────────────────────────────


def TypeText(text):
    _xdo("type", "--clearmodifiers", "--", text)


def HotKey(*keys):
    mapped = [KEY_MAP.get(k.lower(), k) for k in keys]
    _xdo("key", "--clearmodifiers", "+".join(mapped))


def SendKeys(shortcut: str):
    parts = shortcut.split("+")
    mapped = [KEY_MAP.get(p.strip().lower(), p.strip()) for p in parts]
    _xdo("key", "--clearmodifiers", "+".join(mapped))


# ── Virtual desktops ──────────────────────────────────────────────────────────


def _get_desktops() -> tuple[dict, list[dict]]:
    out = _run("wmctrl", "-d")
    if not out:
        default = {"id": 0, "name": "Desktop 0"}
        return default, [default]

    active = {"id": 0, "name": "Desktop 0"}
    all_desktops = []
    for line in out.splitlines():
        parts = line.split(None, 9)
        if len(parts) < 2:
            continue
        idx = int(parts[0])
        star = parts[1] == "*"
        name = parts[-1].strip() if len(parts) >= 10 else f"Desktop {idx}"
        entry = {"id": idx, "name": name}
        all_desktops.append(entry)
        if star:
            active = entry

    return active, all_desktops


# ── Window state ──────────────────────────────────────────────────────────────


def _get_window_state(wid_hex: str) -> Status:
    out = _run("xprop", "-id", wid_hex, "_NET_WM_STATE")
    if not out:
        return Status.NORMAL
    if "_NET_WM_STATE_HIDDEN" in out:
        return Status.MINIMIZED
    if "_NET_WM_STATE_MAXIMIZED_VERT" in out or "_NET_WM_STATE_MAXIMIZED_HORZ" in out:
        return Status.MAXIMIZED
    return Status.NORMAL


def _get_windows(active_desktop_id: int) -> list[Window]:
    out = _run("wmctrl", "-lG")
    if not out:
        return []

    windows = []
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        wid_hex = parts[0]
        desktop = int(parts[1])
        x, y, w, h = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
        title = parts[7].strip() if len(parts) >= 8 else ""

        if desktop != active_desktop_id and desktop != -1:
            continue
        if not title:
            continue

        status = _get_window_state(wid_hex)
        windows.append(
            Window(
                name=title,
                status=status,
                x=x,
                y=y,
                width=w,
                height=h,
                handle=int(wid_hex, 16),
            )
        )
    return windows


def _get_active_window(windows: list[Window]) -> Window | None:
    out = _run("xdotool", "getactivewindow")
    if not out:
        return windows[0] if windows else None
    try:
        active_id = int(out.strip())
        for w in windows:
            if w.handle == active_id:
                return w
        title = _run("xdotool", "getactivewindow", "getwindowname")
        for w in windows:
            if w.name == title:
                return w
    except ValueError:
        pass
    return windows[0] if windows else None


# ── Desktop service ───────────────────────────────────────────────────────────


class Desktop:
    def __init__(self, use_accessibility: bool = True, use_vision: bool = False):
        self.use_accessibility = use_accessibility
        self.use_vision = use_vision

    def get_state(self) -> DesktopState:
        active_desktop, all_desktops = _get_desktops()
        windows = _get_windows(active_desktop["id"])
        active_window = _get_active_window(windows)
        other_windows = [w for w in windows if w is not active_window]

        return DesktopState(
            active_desktop=active_desktop,
            all_desktops=all_desktops,
            active_window=active_window,
            windows=other_windows,
        )
