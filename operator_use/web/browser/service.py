from operator_use.web.browser.config import BrowserConfig, BROWSER_ARGS
from operator_use.web.browser.events import (
    BrowserEvent,
    NavigationSettledEvent,
    NavigationStartedEvent,
)
from operator_use.web.browser.page import Page
from operator_use.web.browser.session import Session
from operator_use.web.browser.views import BrowserState, Tab
from operator_use.web.dom.views import DOMElementNode
from collections import deque
from typing import Any, Optional, Callable
from pathlib import Path
import subprocess
import shutil
import os
import tempfile
import asyncio
import httpx
import sys
import json
import random
import re
import logging


_SPECIAL_KEYS: dict[str, dict] = {
    "Enter": {"key": "Enter", "code": "Enter", "keyCode": 13},
    "Escape": {"key": "Escape", "code": "Escape", "keyCode": 27},
    "Tab": {"key": "Tab", "code": "Tab", "keyCode": 9},
    "Backspace": {"key": "Backspace", "code": "Backspace", "keyCode": 8},
    "Delete": {"key": "Delete", "code": "Delete", "keyCode": 46},
    "PageUp": {"key": "PageUp", "code": "PageUp", "keyCode": 33},
    "PageDown": {"key": "PageDown", "code": "PageDown", "keyCode": 34},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "keyCode": 38},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "keyCode": 40},
    "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "keyCode": 37},
    "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "keyCode": 39},
    "Home": {"key": "Home", "code": "Home", "keyCode": 36},
    "End": {"key": "End", "code": "End", "keyCode": 35},
    "F5": {"key": "F5", "code": "F5", "keyCode": 116},
    " ": {"key": " ", "code": "Space", "keyCode": 32},
    "Space": {"key": " ", "code": "Space", "keyCode": 32},
}

_MODIFIER_KEYS: dict[str, dict] = {
    "Control": {"key": "Control", "code": "ControlLeft", "keyCode": 17, "bit": 2},
    "Ctrl": {"key": "Control", "code": "ControlLeft", "keyCode": 17, "bit": 2},
    "Shift": {"key": "Shift", "code": "ShiftLeft", "keyCode": 16, "bit": 8},
    "Alt": {"key": "Alt", "code": "AltLeft", "keyCode": 18, "bit": 1},
    "Meta": {"key": "Meta", "code": "MetaLeft", "keyCode": 91, "bit": 4},
    "Command": {"key": "Meta", "code": "MetaLeft", "keyCode": 91, "bit": 4},
}


logger = logging.getLogger(__name__)


def _parse_key_combo(keys_str: str):
    parts = [p.strip() for p in keys_str.split("+")]
    mods = [_MODIFIER_KEYS[p] for p in parts[:-1] if p in _MODIFIER_KEYS]
    return mods, parts[-1]


class Browser:
    def __init__(self, config: BrowserConfig = None):
        self.config = config if config else BrowserConfig()

        # CDP process / client state
        self._process: subprocess.Popen = None
        from operator_use.web.cdp import Client

        self._client: Client = None

        # Tab / session state
        self._session_manager = Session()
        self._targets: dict[str, dict] = self._session_manager.targets
        self._sessions: dict[str, str] = self._session_manager.sessions
        self._lifecycle: dict[str, deque] = {}
        self._page_started: dict[str, asyncio.Event] = {}
        self._page_ready: dict[str, asyncio.Event] = {}
        self._page_loading: dict[str, bool] = {}  # session_id -> True while navigation in progress
        self._current_target_id: str | None = None
        self._browser_event_handlers: dict[str, list[Callable[[BrowserEvent], Any]]] = {}
        self._special_keys = _SPECIAL_KEYS

        self._browser_state: BrowserState = None
        self._page = Page(self)
        self._state_watchdog = None
        self.crashed: bool = False

        # Last known mouse position (for trajectory simulation)
        self._mouse_x: int = 0
        self._mouse_y: int = 0

        # Resolved WebSocket URL when attach_to_existing uses DevToolsActivePort
        self._resolved_attach_ws_url: str | None = None

        # Watchdogs (attached during init_tabs)
        from operator_use.web.watchdog import (
            DOMWatchdog,
            DialogWatchdog,
            CrashWatchdog,
            DownloadWatchdog,
            PopupWatchdog,
            StateWatchdog,
        )

        self._watchdogs = [
            DOMWatchdog(self),
            DialogWatchdog(self),
            CrashWatchdog(self),
            DownloadWatchdog(self),
            PopupWatchdog(self),
            StateWatchdog(self),
        ]
        self._state_watchdog = next(
            (watchdog for watchdog in self._watchdogs if isinstance(watchdog, StateWatchdog)),
            None,
        )

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self):
        await self.init_browser()
        await self.init_tabs()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    # ------------------------------------------------------------------
    # Browser launch / connect
    # ------------------------------------------------------------------

    def _on_browser_disconnected(self):
        """Called when the CDP WebSocket connection drops (e.g. browser closed externally)."""
        self._client = None
        self._session_manager.clear()
        self._lifecycle.clear()
        self._page_started.clear()
        self._page_ready.clear()
        self._page_loading.clear()
        self._current_target_id = None

    async def init_browser(self):
        if self.config.wss_url:
            ws_url = (
                self.config.wss_url
                if not self.config.wss_url.startswith("http")
                else await self._fetch_ws_url(self.config.wss_url)
            )
            from operator_use.web.cdp import Client

            self._client = Client(ws_url)
            self._client.on_disconnect = self._on_browser_disconnected
            await self._client.__aenter__()
            return

        await self._resolve_ws_url()

        # attach_to_existing: if DevToolsActivePort gave us an exact ws URL, use it directly
        if self.config.attach_to_existing and self._resolved_attach_ws_url:
            from operator_use.web.cdp import Client

            self._client = Client(self._resolved_attach_ws_url)
            self._client.on_disconnect = self._on_browser_disconnected
            await self._client.__aenter__()
            return

        port = self.config.cdp_port
        for attempt in range(10):
            try:
                if not self.config.attach_to_existing and not await self._is_correct_browser(port):
                    # Wrong browser still on port — kill and re-launch
                    self._kill_on_port(port)
                    await asyncio.sleep(1.0)
                    self._process = self._launch_process()
                    await self._wait_for_browser(port=port, timeout=15.0)
                    continue
                ws_url = await self._fetch_ws_url(f"http://localhost:{port}")
                from operator_use.web.cdp import Client

                self._client = Client(ws_url)
                self._client.on_disconnect = self._on_browser_disconnected
                await self._client.__aenter__()
                return
            except Exception:
                await asyncio.sleep(1.0)
        raise RuntimeError(f"Could not establish WebSocket connection on port {port}")

    async def get_cdp_client(self):
        if self._client is None:
            await self.init_browser()
        return self._client

    async def ensure_open(self):
        """Lazily initialize the browser and tabs on first use."""
        if self._client is None:
            await self.init_browser()
            await self.init_tabs()

    def _read_devtools_active_port(self) -> str | None:
        """Read DevToolsActivePort file written by Chrome/Edge on startup.

        The file contains two lines: the CDP port number and the WebSocket path.
        e.g.:
            9222
            /devtools/browser/<id>

        Returns the full ws:// URL, or None if the file is absent or unreadable.
        """
        if not self.config.user_data_dir:
            return None
        port_file = Path(self.config.user_data_dir) / "DevToolsActivePort"
        try:
            lines = [
                line.strip()
                for line in port_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(lines) < 2:
                return None
            port, ws_path = lines[0], lines[1]
            if not port.isdigit():
                return None
            return f"ws://127.0.0.1:{port}{ws_path}"
        except Exception:
            return None

    async def _resolve_ws_url(self):
        if self.config.wss_url:
            return
        port = self.config.cdp_port
        if self.config.attach_to_existing:
            # Prefer the DevToolsActivePort file (exact ws URL, no polling needed).
            # Fall back to /json/version polling if user_data_dir is not set.
            ws_url = self._read_devtools_active_port()
            if ws_url:
                self._resolved_attach_ws_url = ws_url
                return
            if not await self._is_port_responsive(port):
                raise RuntimeError(
                    f"attach_to_existing=True but nothing is listening on port {port}. "
                    f"Launch your browser with --remote-debugging-port={port} first."
                )
            return
        if await self._is_port_responsive(port):
            if await self._is_correct_browser(port):
                return  # correct browser already running, just connect
            # Wrong browser on the port — kill entire process tree and wait for port to free
            self._kill_on_port(port)
            for _ in range(10):
                await asyncio.sleep(0.5)
                if not await self._is_port_responsive(port):
                    break
        self._process = self._launch_process()
        await self._wait_for_browser(port=port, timeout=15.0)

    async def _is_port_responsive(self, port: int) -> bool:
        try:
            async with httpx.AsyncClient() as http:
                await http.get(f"http://localhost:{port}/json/version", timeout=1.0)
                return True
        except Exception:
            return False

    async def _is_correct_browser(self, port: int) -> bool:
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(f"http://localhost:{port}/json/version", timeout=1.0)
                browser_str = resp.json().get("Browser", "").lower()
            if self.config.resolved_browser() == "chrome":
                return "chrome" in browser_str and "edg" not in browser_str
            elif self.config.resolved_browser() == "edge":
                return "edg" in browser_str
            return False
        except Exception:
            return False

    def _kill_on_port(self, port: int):
        try:
            if sys.platform == "win32":
                result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
                pids = set()
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        pid = line.strip().split()[-1]
                        if pid.isdigit():
                            pids.add(pid)
                for pid in pids:
                    # /T kills the entire process tree (all child processes too)
                    subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
            else:
                result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
                for pid in result.stdout.strip().splitlines():
                    subprocess.run(["kill", "-9", pid.strip()], capture_output=True)
        except Exception:
            pass

    def _copy_auth_files(self, src_profile_dir: str, dst_dir: str):
        """Copy auth-bearing files from src Chrome profile into dst_dir.

        Copies key files from src/Default/ into dst/Default/ and also copies
        Local State (DPAPI encryption key on Windows) from src/ into dst/.
        """
        src_default = Path(src_profile_dir) / "Default"
        dst_default = Path(dst_dir) / "Default"
        dst_default.mkdir(parents=True, exist_ok=True)

        auth_items = [
            "Cookies",
            "Local Storage",
            "Session Storage",
            "Network Persistent State",
            "Preferences",
        ]
        for item in auth_items:
            s = src_default / item
            d = dst_default / item
            try:
                if s.is_dir():
                    shutil.copytree(s, d, dirs_exist_ok=True)
                elif s.is_file():
                    shutil.copy2(s, d)
            except Exception:
                pass  # skip locked or missing files

        # Local State lives at the root of the profile dir (not inside Default/)
        # and holds the DPAPI key Chrome uses to decrypt the Cookies file on Windows.
        try:
            local_state = Path(src_profile_dir) / "Local State"
            if local_state.exists():
                shutil.copy2(local_state, Path(dst_dir) / "Local State")
        except Exception:
            pass

    def _resolve_user_data_dir(self) -> str:
        """Determine the user data directory to launch Chrome with.

        Three scenarios:
        1. use_system_profile=True
           → copy real Chrome profile auth files into a fresh temp dir each launch.
             Safe to use while Chrome is open; session data is discarded after.

        2. user_data_dir is a custom path (not the real Chrome profile)
           → persistent agent profile. On the very first run (Default/ absent),
             seed it with auth files from the real Chrome profile so the agent
             starts already logged in. Subsequent runs reuse what's already there.

        3. user_data_dir is None
           → completely fresh temp profile, no cookies, not logged into anything.
        """
        system_profile = self.config.get_system_profile_dir()

        if self.config.use_system_profile:
            # Always copy to a fresh temp dir — never touch the real profile
            tmp = tempfile.mkdtemp(prefix="web-use-profile-")
            if system_profile:
                self._copy_auth_files(system_profile, tmp)
            return tmp

        if self.config.user_data_dir:
            custom = Path(self.config.user_data_dir)
            is_real_profile = system_profile and custom.resolve() == Path(system_profile).resolve()
            if is_real_profile:
                # Treat the same as use_system_profile — avoid lock conflict
                tmp = tempfile.mkdtemp(prefix="web-use-profile-")
                self._copy_auth_files(str(custom), tmp)
                return tmp

            # Custom path: seed on first run only
            if not (custom / "Default").exists() and system_profile:
                self._copy_auth_files(system_profile, str(custom))

            custom.mkdir(parents=True, exist_ok=True)
            return str(custom)

        # No path given — fresh throwaway profile
        return tempfile.mkdtemp(prefix="web-use-browser-")

    def _launch_process(self) -> subprocess.Popen:
        exe = self._get_executable()
        port = self.config.cdp_port
        user_data_dir = self._resolve_user_data_dir()

        args = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            f"--download-default-directory={self.config.downloads_dir}",
        ] + BROWSER_ARGS

        if self.config.headless:
            args.append("--headless=new")

        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        return subprocess.Popen(args, **kwargs)

    def _get_executable(self) -> str:
        if self.config.browser_instance_dir:
            return self.config.browser_instance_dir

        browser = self.config.resolved_browser()
        if sys.platform == "win32":
            local = Path(os.environ.get("LOCALAPPDATA", ""))
            candidates = {
                "chrome": [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    str(local / "Google" / "Chrome" / "Application" / "chrome.exe"),
                ],
                "edge": [
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                ],
            }
            for path in candidates.get(browser, []):
                if Path(path).exists():
                    return path
            raise FileNotFoundError(
                f"{browser.capitalize()} executable not found. Set browser_instance_dir in BrowserConfig."
            )
        elif sys.platform == "darwin":
            paths = {
                "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            }
        else:
            paths = {
                "chrome": "google-chrome",
                "edge": "microsoft-edge",
            }
        return paths.get(browser, paths.get("chrome"))

    async def _wait_for_browser(self, port: int, timeout: float):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with httpx.AsyncClient() as http:
                    await http.get(f"http://localhost:{port}/json/version", timeout=2.0)
                    return
            except Exception:
                await asyncio.sleep(0.5)
        raise TimeoutError(f"Browser did not respond on port {port} within {timeout}s")

    async def _fetch_ws_url(self, http_url: str) -> str:
        async with httpx.AsyncClient() as http:
            resp = await http.get(f"{http_url.rstrip('/')}/json/version")
            return resp.json()["webSocketDebuggerUrl"]

    async def close(self):
        # Close all tabs
        try:
            for target_id, session_id in list(self._sessions.items()):
                try:
                    await self.send(
                        "Target.closeTarget", {"targetId": target_id}, session_id=session_id
                    )
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._session_manager.clear()
            self._lifecycle.clear()
            self._page_loading.clear()
            for ev in self._page_ready.values():
                ev.set()
            self._page_ready.clear()
            self._set_current_target_id(None)

        # Close CDP client
        try:
            if self._client:
                await self._client.__aexit__(None, None, None)
        except Exception:
            pass
        finally:
            self._client = None

        # Terminate browser process — skip if we attached to an existing browser
        # (we don't own that process, so we must not kill it)
        if not self.config.attach_to_existing:
            try:
                if self._process:
                    self._process.terminate()
                    self._process.wait(timeout=5)
            except Exception:
                try:
                    if self._process:
                        self._process.kill()
                except Exception:
                    pass
            finally:
                self._process = None

    # ------------------------------------------------------------------
    # CDP wrappers
    # ------------------------------------------------------------------

    async def send(
        self, method: str, params: Optional[dict] = None, session_id: Optional[str] = None
    ) -> Any:
        return await self._client.send(method, params or {}, session_id=session_id)

    def on(self, event: str, handler: Callable[[Any, Optional[str]], None]) -> None:
        self._client.on(event, handler)

    def on_browser_event(
        self, event: str | type[BrowserEvent], handler: Callable[[BrowserEvent], Any]
    ) -> None:
        key = event if isinstance(event, str) else event.event_name()
        self._browser_event_handlers.setdefault(key, []).append(handler)

    def emit_browser_event(self, event: BrowserEvent | str, payload: Optional[dict] = None) -> None:
        if isinstance(event, str):
            key = event
            event_obj = payload or {}
        else:
            key = event.event_name()
            event_obj = event
        for handler in self._browser_event_handlers.get(key, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event_obj))
                else:
                    handler(event_obj)
            except Exception as e:
                logger.debug("Browser event handler failed for %s: %s", event, e)

    # ------------------------------------------------------------------
    # Tab / session init
    # ------------------------------------------------------------------

    async def init_tabs(self):
        await self.get_cdp_client()

        self.on("Target.attachedToTarget", self._on_attached)
        self.on("Target.detachedFromTarget", self._on_detached)
        self.on("Target.targetInfoChanged", self._on_target_info_changed)
        self.on("Page.lifecycleEvent", self._on_lifecycle_event)

        for watchdog in self._watchdogs:
            await watchdog.attach()

        await self.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            },
        )
        await self.send(
            "Target.setDiscoverTargets",
            {
                "discover": True,
                "filter": [{"type": "page"}],
            },
        )

        result = await self.send("Target.getTargets", {"filter": [{"type": "page"}]})
        page_targets = result.get("targetInfos", [])

        if page_targets:
            self._set_current_target_id(page_targets[0]["targetId"])
            for info in page_targets:
                tid = info["targetId"]
                attach = await self.send(
                    "Target.attachToTarget", {"targetId": tid, "flatten": True}
                )
                sid = attach["sessionId"]
                self._session_manager.register_target(tid, sid, info["url"], info.get("title", ""))
                self._lifecycle[sid] = deque(maxlen=50)
                await self._init_tab_domains(sid)
        else:
            r = await self.send("Target.createTarget", {"url": "about:blank"})
            self._set_current_target_id(r["targetId"])
            attach = await self.send(
                "Target.attachToTarget",
                {
                    "targetId": self._current_target_id,
                    "flatten": True,
                },
            )
            sid = attach["sessionId"]
            self._session_manager.register_target(self._current_target_id, sid, "about:blank", "")
            self._lifecycle[sid] = deque(maxlen=50)
            await self._init_tab_domains(sid)

    async def _init_tab_domains(self, session_id: str):
        await asyncio.gather(
            self.send("DOM.enable", {}, session_id=session_id),
            self.send("Page.enable", {}, session_id=session_id),
            self.send("Runtime.enable", {}, session_id=session_id),
            self.send("Network.enable", {}, session_id=session_id),
        )
        await self.send("Page.setLifecycleEventsEnabled", {"enabled": True}, session_id=session_id)
        await self.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": False,
                "flatten": True,
            },
            session_id=session_id,
        )

        try:
            await self.send(
                "Emulation.setUserAgentOverride",
                {
                    "userAgent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
                    ),
                    "acceptLanguage": "en-US,en;q=0.9",
                    "platform": "Win32",
                },
                session_id=session_id,
            )
        except Exception:
            pass

        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "script.js")) as f:
            anti_detect = f.read()
        try:
            await self.send(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": anti_detect},
                session_id=session_id,
            )
        except Exception:
            pass
        try:
            await self.send(
                "Runtime.evaluate",
                {
                    "expression": anti_detect,
                    "returnByValue": False,
                },
                session_id=session_id,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CDP event handlers
    # ------------------------------------------------------------------

    async def _on_attached(self, event, _=None):
        info = event.get("targetInfo", {})
        target_id = info.get("targetId")
        session_id = event.get("sessionId")
        if not target_id or not session_id:
            return
        if target_id in self._sessions:
            return
        if info.get("type", "") != "page":
            return
        self._session_manager.register_target(
            target_id, session_id, info.get("url", ""), info.get("title", "")
        )
        self._lifecycle[session_id] = deque(maxlen=50)
        await self._init_tab_domains(session_id)

    def _on_detached(self, event, _=None):
        session_id = event.get("sessionId")
        target_id = self._session_manager.find_target_by_session(session_id)
        if target_id:
            self._session_manager.remove_by_target(target_id)
            self._lifecycle.pop(session_id, None)
            started = self._page_started.pop(session_id, None)
            if started:
                started.set()
            self._page_loading.pop(session_id, None)
            ready = self._page_ready.pop(session_id, None)
            if ready:
                ready.set()  # unblock any waiter on a closing tab
            self._set_current_target_id(self._session_manager.current_target_id)

    def _on_target_info_changed(self, event, _=None):
        info = event.get("targetInfo", {})
        tid = info.get("targetId")
        if tid in self._targets:
            self._session_manager.update_target(
                tid,
                url=info.get("url", ""),
                title=info.get("title", ""),
            )

    def _on_lifecycle_event(self, event, session_id=None):
        if not session_id:
            return
        name = event.get("name", "")
        if session_id in self._lifecycle:
            self._lifecycle[session_id].append(
                {
                    "name": name,
                    "loaderId": event.get("loaderId"),
                    "timestamp": event.get("timestamp"),
                }
            )
        # Track navigation lifecycle
        if name == "commit":
            self._page_loading[session_id] = True
            started = self._page_started.get(session_id)
            if started:
                started.set()
        elif name == "networkIdle":
            self._page_loading[session_id] = False

        # Signal any waiter on networkIdle or load
        if name in ("networkIdle", "load"):
            ready = self._page_ready.get(session_id)
            if ready:
                ready.set()
            self.emit_browser_event(NavigationSettledEvent(session_id=session_id, name=name))

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def _get_current_session_id(self) -> str | None:
        return self._session_manager.current_session_id()

    def _set_current_target_id(self, target_id: str | None) -> None:
        self._current_target_id = target_id
        self._session_manager.current_target_id = target_id

    def current_page(self) -> Page:
        return self._page

    def _parse_key_combo_impl(self, keys_str: str):
        return _parse_key_combo(keys_str)

    def _begin_navigation_tracking(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._page_started[session_id] = asyncio.Event()
        self._page_ready[session_id] = asyncio.Event()
        self._page_loading[session_id] = True
        self.emit_browser_event(NavigationStartedEvent(session_id=session_id))

    def is_navigation_pending(self) -> bool:
        sid = self._get_current_session_id()
        if not sid:
            return False
        if self._page_loading.get(sid, False):
            return True
        started = self._page_started.get(sid)
        ready = self._page_ready.get(sid)
        return bool(started and started.is_set() and not (ready and ready.is_set()))

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    async def get_all_tabs(self) -> list[Tab]:
        items = list(self._targets.items())
        sids = [self._sessions.get(tid, "") for tid, _ in items]

        async def _fetch(tid, sid, info):
            try:
                result = await asyncio.wait_for(
                    self.send(
                        "Runtime.evaluate",
                        {
                            "expression": "({url: document.URL, title: document.title})",
                            "returnByValue": True,
                        },
                        session_id=sid,
                    ),
                    timeout=1.5,
                )
                live = result.get("result", {}).get("value", {})
                url = live.get("url", info.get("url", ""))
                title = live.get("title", info.get("title", ""))
                self._session_manager.update_target(tid, url=url, title=title)
            except Exception:
                url = info.get("url", "")
                title = info.get("title", "")
            return url, title

        results = await asyncio.gather(
            *(_fetch(tid, sid, info) for (tid, info), sid in zip(items, sids))
        )
        return [
            Tab(id=i, url=url, title=title, target_id=tid, session_id=sid)
            for i, ((tid, _), sid, (url, title)) in enumerate(zip(items, sids, results))
        ]

    async def get_current_tab(self) -> Tab | None:
        if not self._current_target_id:
            return None
        tid = self._current_target_id
        sid = self._sessions.get(tid, "")
        info = self._targets.get(tid, {})
        try:
            result = await asyncio.wait_for(
                self.send(
                    "Runtime.evaluate",
                    {
                        "expression": "({url: document.URL, title: document.title})",
                        "returnByValue": True,
                    },
                    session_id=sid,
                ),
                timeout=1.5,
            )
            live = result.get("result", {}).get("value", {})
            url = live.get("url", info.get("url", ""))
            title = live.get("title", info.get("title", ""))
        except Exception:
            url = info.get("url", "")
            title = info.get("title", "")
        idx = next((i for i, t in enumerate(self._targets) if t == tid), 0)
        return Tab(id=idx, url=url, title=title, target_id=tid, session_id=sid)

    async def new_tab(self) -> Tab:
        r = await self.send("Target.createTarget", {"url": "about:blank"})
        tid = r["targetId"]
        attach = await self.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = attach["sessionId"]
        self._session_manager.register_target(tid, sid, "about:blank", "")
        self._lifecycle[sid] = deque(maxlen=50)
        await self._init_tab_domains(sid)
        self._set_current_target_id(tid)
        await self._activate_target(tid)
        return Tab(
            id=len(self._targets) - 1, url="about:blank", title="", target_id=tid, session_id=sid
        )

    async def close_tab(self, target_id: str = None):
        tid = target_id or self._current_target_id
        sid = self._sessions.get(tid)
        if len(self._sessions) <= 1:
            return
        try:
            await self.send("Target.closeTarget", {"targetId": tid}, session_id=sid)
        except Exception:
            pass
        remaining = self._session_manager.remaining_targets(tid)
        if remaining and self._current_target_id == tid:
            self._set_current_target_id(remaining[-1])
            await self._activate_target(remaining[-1])

    async def switch_tab(self, tab_index: int):
        tabs = await self.get_all_tabs()
        if tab_index < 0 or tab_index >= len(tabs):
            raise IndexError(f"Tab index {tab_index} out of range ({len(tabs)} tabs)")
        self._set_current_target_id(tabs[tab_index].target_id)
        await self._activate_target(self._current_target_id)

    async def _activate_target(self, target_id: str):
        try:
            await self.send("Target.activateTarget", {"targetId": target_id})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate(self, url: str):
        sid = self._get_current_session_id()
        # Clear stale lifecycle events and arm the ready event before navigating
        if sid:
            if sid in self._lifecycle:
                self._lifecycle[sid].clear()
            self._begin_navigation_tracking(sid)
        await self.send(
            "Page.navigate",
            {
                "url": url,
                "transitionType": "address_bar",
            },
            session_id=sid,
        )
        await self._wait_for_page(timeout=15.0)

    async def go_back(self):
        self._begin_navigation_tracking(self._get_current_session_id())
        await self.execute_script("history.back()")
        await self._wait_for_page(timeout=10.0)

    async def go_forward(self):
        self._begin_navigation_tracking(self._get_current_session_id())
        await self.execute_script("history.forward()")
        await self._wait_for_page(timeout=10.0)

    async def _wait_for_page(self, timeout: float = 10.0):
        sid = self._get_current_session_id()
        if not sid:
            return
        started = self._page_started.get(sid)
        ready = self._page_ready.get(sid)
        tracking_possible_navigation = False

        if not self._page_loading.get(sid, False):
            # Actions like clicks can trigger navigation asynchronously.
            if started is None or started.is_set():
                started = asyncio.Event()
                self._page_started[sid] = started
            if ready is None or ready.is_set():
                ready = asyncio.Event()
                self._page_ready[sid] = ready
            tracking_possible_navigation = True
            try:
                await asyncio.wait_for(started.wait(), timeout=min(timeout, 0.75))
            except asyncio.TimeoutError:
                if self._page_started.get(sid) is started:
                    self._page_started.pop(sid, None)
                if self._page_ready.get(sid) is ready:
                    self._page_ready.pop(sid, None)
                await asyncio.sleep(0.1)
                return

        if ready is None or ready.is_set():
            ready = asyncio.Event()
            self._page_ready[sid] = ready

        try:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if self._page_ready.get(sid) is ready:
                self._page_ready.pop(sid, None)
            if tracking_possible_navigation and self._page_started.get(sid) is started:
                self._page_started.pop(sid, None)
            self._page_loading.pop(sid, None)

        await asyncio.sleep(0.3)  # brief render buffer

    # ------------------------------------------------------------------
    # Script execution
    # ------------------------------------------------------------------

    @staticmethod
    def _repair_js(code: str) -> str:
        """Fix common escaping mistakes in LLM-generated JavaScript."""
        # 1. Double-escaped quotes produced by JSON round-trip
        code = re.sub(r'\\"', '"', code)

        # 2. Over-escaped regex escape sequences (\\\\X → \\X)
        code = re.sub(r"\\\\([dDsSwWbBnrtfv])", r"\\\1", code)
        code = re.sub(r"\\\\([.*+?^${}()|[\]\\])", r"\\\1", code)

        # 3. querySelector[All]("selector with 'single' quotes") → backtick form
        def _fix_selector(m: re.Match) -> str:
            fn, sel = m.group(1), m.group(2)
            if "'" in sel:
                return f"{fn}(`{sel}`)"
            return m.group(0)

        code = re.sub(
            r'(querySelector(?:All)?)\s*\(\s*"([^"]*)"\s*\)',
            _fix_selector,
            code,
        )

        # 4. document.evaluate("xpath with 'quotes'", ...) → backtick form
        def _fix_xpath(m: re.Match) -> str:
            xpath = m.group(1)
            if "'" in xpath:
                return f"document.evaluate(`{xpath}`,"
            return m.group(0)

        code = re.sub(
            r'document\.evaluate\s*\(\s*"([^"]*)"\s*,',
            _fix_xpath,
            code,
        )

        return code

    async def execute_script(
        self, script: str, truncate: bool = False, repair: bool = False
    ) -> Any:
        return await self.current_page().execute_script(script, truncate=truncate, repair=repair)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    async def _move_mouse(self, x: int, y: int):
        """Simulate human-like mouse movement via a quadratic bezier curve."""
        sid = self._get_current_session_id()
        x0, y0 = self._mouse_x, self._mouse_y
        steps = random.randint(5, 8)
        # Random control point offset for natural arc
        cx = (x0 + x) / 2 + random.randint(-80, 80)
        cy = (y0 + y) / 2 + random.randint(-40, 40)
        for i in range(1, steps + 1):
            t = i / steps
            px = int((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t**2 * x)
            py = int((1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t**2 * y)
            await self.send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": px,
                    "y": py,
                },
                session_id=sid,
            )
            await asyncio.sleep(random.uniform(0.002, 0.008))
        self._mouse_x, self._mouse_y = x, y

    async def click_at(self, x: int, y: int):
        await self.current_page().click_at(x, y)

    async def scroll_into_view(self, xpath: str):
        escaped = xpath.replace('"', '\\"')
        await self.execute_script(
            f"(function(){{"
            f'  var el = document.evaluate("{escaped}", document, null, 8, null).singleNodeValue;'
            f'  if (el) el.scrollIntoView({{block:"center", inline:"nearest"}});'
            f"}})()"
        )

    async def type_text(self, text: str, delay_ms: int = 50):
        await self.current_page().type_text(text, delay_ms=delay_ms)

    async def key_press(self, keys: str):
        await self.current_page().key_press(keys)

    async def scroll_page(self, direction: str, amount: int = 500):
        await self.current_page().scroll_page(direction, amount=amount)

    async def scroll_element(self, xpath: str, direction: str, amount: int = 500):
        escaped = xpath.replace('"', '\\"')
        delta = -amount if direction == "up" else amount
        await self.execute_script(
            f"(function(){{"
            f'  var el = document.evaluate("{escaped}", document, null, 8, null).singleNodeValue;'
            f"  if (el) el.scrollBy(0, {delta});"
            f"}})()"
        )

    async def get_scroll_position(self) -> dict:
        return await self.current_page().get_scroll_position()

    # ------------------------------------------------------------------
    # Screenshot / page info
    # ------------------------------------------------------------------

    async def get_screenshot(
        self, full_page: bool = False, save_screenshot: bool = False
    ) -> bytes | None:
        return await self.current_page().get_screenshot(
            full_page=full_page, save_screenshot=save_screenshot
        )

    async def get_page_content(self) -> str:
        return await self.current_page().get_page_content()

    async def get_viewport(self) -> tuple[int, int]:
        return await self.current_page().get_viewport()

    # ------------------------------------------------------------------
    # Element actions
    # ------------------------------------------------------------------

    async def scroll_at(self, x: int, y: int, direction: str, amount: int = 500):
        await self.current_page().scroll_at(x, y, direction, amount=amount)

    async def set_file_input_at(self, x: int, y: int, files: list[str]):
        await self.current_page().set_file_input_at(x, y, files)

    async def select_option_at(self, x: int, y: int, labels: list[str]):
        await self.current_page().select_option_at(x, y, labels)

    async def set_file_input(self, xpath: str, files: list[str]):
        sid = self._get_current_session_id()
        escaped = xpath.replace('"', '\\"')
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": f'document.evaluate("{escaped}", document, null, 8, null).singleNodeValue',
                "returnByValue": False,
            },
            session_id=sid,
        )
        obj_id = result.get("result", {}).get("objectId")
        if not obj_id:
            raise Exception(f"Could not resolve file input element at xpath: {xpath}")
        node = await self.send("DOM.describeNode", {"objectId": obj_id}, session_id=sid)
        backend_node_id = node["node"]["backendNodeId"]
        await self.send(
            "DOM.setFileInputFiles",
            {
                "files": files,
                "backendNodeId": backend_node_id,
            },
            session_id=sid,
        )

    async def select_option(self, xpath: str, labels: list[str]):
        escaped = xpath.replace('"', '\\"')
        labels_json = json.dumps(labels)
        await self.execute_script(
            f"(function(){{"
            f'  var el = document.evaluate("{escaped}", document, null, 8, null).singleNodeValue;'
            f"  if (!el) return false;"
            f"  var labels = {labels_json};"
            f"  for (var i = 0; i < el.options.length; i++) {{"
            f"    if (labels.includes(el.options[i].text.trim())) el.options[i].selected = true;"
            f"  }}"
            f'  el.dispatchEvent(new Event("change", {{bubbles: true}}));'
            f"  return true;"
            f"}})()"
        )

    # ------------------------------------------------------------------
    # DOM state
    # ------------------------------------------------------------------

    async def get_state(self, use_vision: bool = False) -> BrowserState:
        if self._state_watchdog is not None:
            state = await self._state_watchdog.get_state(use_vision=use_vision)
            if state is not None:
                return state
            if self._browser_state is not None:
                return self._browser_state

        from operator_use.web.dom import DOM

        dom = DOM(session=self)
        screenshot, dom_state = await dom.get_state(use_vision=use_vision)
        tabs = await self.get_all_tabs()
        current_tab = await self.get_current_tab()
        self._browser_state = BrowserState(
            current_tab=current_tab,
            tabs=tabs,
            screenshot=screenshot,
            dom_state=dom_state,
        )
        return self._browser_state

    # ------------------------------------------------------------------
    # Storage state (cookies as portable JSON)
    # ------------------------------------------------------------------

    async def export_storage_state(self, output_path: str | Path | None = None) -> dict:
        """Export all browser cookies as a plain JSON dict via CDP."""
        result = await self.send("Storage.getCookies", {})
        raw_cookies = result.get("cookies", [])
        cookies = [
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c.get("path", "/"),
                "expires": c.get("expires", -1),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            }
            for c in raw_cookies
        ]
        state = {"cookies": cookies}
        if output_path:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        return state

    async def import_storage_state(self, state: dict | str | Path):
        """Inject cookies from a previously exported storage state."""
        if isinstance(state, (str, Path)):
            state = json.loads(Path(state).read_text())

        cookies = []
        for c in state.get("cookies", []):
            cookie = dict(c)
            if cookie.get("expires", -1) in (0, 0.0, -1, -1.0):
                cookie.pop("expires", None)
            cookies.append(cookie)

        if cookies:
            await self.send("Network.setCookies", {"cookies": cookies})

    async def get_element_by_index(self, index: int) -> DOMElementNode:
        selector_map = self._browser_state.dom_state.selector_map
        if index not in selector_map:
            raise Exception(f"Element at index {index} not found in selector map")
        return selector_map[index]
