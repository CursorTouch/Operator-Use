import platformdirs
import os
import platform
from dataclasses import dataclass
from typing import Literal
from pathlib import Path


def _get_browser_user_data_dir(browser: str) -> str:
    """Retrieve the standard user data directory for the specified browser."""
    system = platform.system()
    home = Path.home()

    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        if browser == "chrome":
            return (local / "Google" / "Chrome" / "User Data").as_posix()
        elif browser == "edge":
            return (local / "Microsoft" / "Edge" / "User Data").as_posix()
    elif system == "Darwin":
        support = home / "Library" / "Application Support"
        if browser == "chrome":
            return (support / "Google" / "Chrome").as_posix()
        elif browser == "edge":
            return (support / "Microsoft" / "Edge").as_posix()
    else:  # Linux/Unix
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        if browser == "chrome":
            return (config_home / "google-chrome").as_posix()
        elif browser == "edge":
            return (config_home / "microsoft-edge").as_posix()
    return None


def detect_installed_browser() -> Literal["chrome", "edge"]:
    """Return the first browser found on this machine, preferring Chrome then Edge."""
    system = platform.system()
    home = Path.home()

    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        candidates: list[tuple[Literal["chrome", "edge"], str]] = [
            ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            ("chrome", str(local / "Google" / "Chrome" / "Application" / "chrome.exe")),
            ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ]
        for browser, path in candidates:
            if Path(path).exists():
                return browser
    elif system == "Darwin":
        candidates = [
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
        for browser, path in candidates:
            if Path(path).exists():
                return browser
    else:
        import shutil

        for browser, cmd in [("chrome", "google-chrome"), ("edge", "microsoft-edge")]:
            if shutil.which(cmd):
                return browser
    return "edge"  # fallback


@dataclass
class BrowserConfig:
    headless: bool = False
    wss_url: str = None  # Remote CDP endpoint (ws:// or http:// for /json/version)
    cdp_port: int = 9222  # Local remote-debugging port
    device: str = None
    browser_instance_dir: str = None  # Path to browser executable (optional override)
    downloads_dir: str = platformdirs.user_downloads_dir()
    browser: Literal["chrome", "edge"] = None  # None = auto-detect on first use
    user_data_dir: str = None
    # use_system_profile=True: copy real Chrome profile to temp on every launch (safe when Chrome is open)
    # user_data_dir set to a custom path: seeds from real Chrome profile on first run, then persists
    # user_data_dir=None: fresh temporary profile with no auth
    use_system_profile: bool = False
    # copy_auth=False (default): never copy real Chrome cookies/sessions into the agent browser.
    # copy_auth=True: opt-in to copying auth files — agent gains access to all logged-in accounts.
    # Only enable if you explicitly want the agent to use your real browser sessions.
    copy_auth: bool = False
    # copy_auth_domains: when copy_auth=True, only copy auth data for these domains.
    # Empty list (default) = copy all domains. Example: ["github.com", "gitlab.com"]
    # Domain filtering implementation is Phase 2 — field available now for config compatibility.
    copy_auth_domains: list = None
    # attach_to_existing=True: connect to an already-running browser on cdp_port instead of launching one.
    # The browser must have been started with --remote-debugging-port=<cdp_port>.
    # No process is launched or killed. Raises RuntimeError if nothing is listening on the port.
    attach_to_existing: bool = False
    timeout: int = 60 * 1000
    slow_mo: int = 300
    minimum_wait_page_load_time: float = 0.3
    wait_for_network_idle_page_load_time: float = 0.5
    maximum_wait_page_load_time: float = 10.0

    def resolved_browser(self) -> Literal["chrome", "edge"]:
        """Return the configured browser, auto-detecting if not explicitly set."""
        if self.browser:
            return self.browser
        detected = detect_installed_browser()
        self.browser = detected
        return detected

    def get_system_profile_dir(self) -> str | None:
        return _get_browser_user_data_dir(self.resolved_browser())


BROWSER_ARGS = [
    "--enable-blink-features=IdleDetection",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-background-timer-throttling",
    "--disable-popup-blocking",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-window-activation",
    "--disable-focus-on-load",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-startup-window",
    "--window-position=0,0",
    "--disable-sync",
]

IGNORE_DEFAULT_ARGS = ["--enable-automation"]
