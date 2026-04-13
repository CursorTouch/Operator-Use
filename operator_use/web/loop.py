"""LoopGuard: detects agent looping patterns (action repetition, stagnation, cycles, failed retries)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque

_EXEMPT = {"done_tool", "wait_tool"}
_IGNORE_PARAMS = {"thought"}

_REPEAT_NUDGES = {
    3: "You have repeated the same action {n} times — make sure it is actually making progress.",
    5: "Same action repeated {n} times. If you are stuck, try a different approach.",
    8: "Same action repeated {n} times. Stop and take a completely different strategy.",
}


class LoopGuard:
    """Watches for signs that the agent is looping:

    - **Action repetition**: the same tool + params appearing too often
      in the last `window` steps.
    - **Page stagnation**: the page DOM and URL not changing across
      consecutive steps, meaning actions have no visible effect.
    - **Page cycle**: the agent returning to a page state it has already
      visited (e.g. A→B→A), which consecutive-stagnation cannot catch.
    - **Failed action retry**: the agent calling the exact same action
      that just failed, unchanged, which will produce the same failure.

    Call ``check()`` before each LLM step to get a warning string (or
    None), ``record_action()`` after executing a tool, and
    ``record_page()`` after capturing browser state.
    """

    def __init__(self, window: int = 15) -> None:
        self._hashes: deque[str] = deque(maxlen=window)
        self._last_page: tuple[str, str] | None = None  # (url, content_hash)
        self._stagnant = 0
        # Page cycle detection
        self._visited_pages: dict[str, int] = {}  # fingerprint → visit count
        self._cycle_warning: str = ""
        # Failed action retry detection
        self._last_action_key: str | None = None
        self._last_action_failed: bool = False
        self._failed_retry_warning: str = ""

    def record_action(self, tool: str, params: dict, is_success: bool = True) -> None:
        if tool in _EXEMPT:
            return
        filtered = {k: v for k, v in params.items() if k not in _IGNORE_PARAMS}
        normalised = {
            k: v.strip().lower() if isinstance(v, str) else v for k, v in filtered.items()
        }
        raw = json.dumps({tool: normalised}, sort_keys=True).encode()
        key = hashlib.sha256(raw).hexdigest()[:12]
        self._hashes.append(key)

        # Failed action retry: same key as last action and last action failed
        if not is_success and self._last_action_key == key and self._last_action_failed:
            self._failed_retry_warning = (
                f"You are retrying '{tool}' with the same parameters after it already failed. "
                "The same action will produce the same failure — change your approach, "
                "try different parameters, or use a different tool."
            )
        else:
            self._failed_retry_warning = ""

        self._last_action_key = key
        self._last_action_failed = not is_success

    def record_page(self, url: str, dom_text: str) -> None:
        digest = hashlib.sha256(dom_text.encode("utf-8", errors="replace")).hexdigest()[:16]
        page = (url, digest)
        if page == self._last_page:
            self._stagnant += 1
        else:
            self._stagnant = 0
        self._last_page = page

        # Page cycle detection: track all-time visits to this url+dom fingerprint
        fingerprint = f"{url}|{digest}"
        count = self._visited_pages.get(fingerprint, 0) + 1
        self._visited_pages[fingerprint] = count
        if count == 2:
            self._cycle_warning = (
                "You have returned to a page you already visited. "
                "You may be cycling between pages. Try a completely different approach "
                "instead of repeating the same steps."
            )
        elif count >= 3:
            self._cycle_warning = (
                f"You have returned to this page {count} times. "
                "You are stuck in a loop — stop and reconsider your strategy entirely. "
                "Try an alternative method or a different URL."
            )
        else:
            self._cycle_warning = ""

    def check(self) -> str | None:
        warnings: list[str] = []

        if self._hashes:
            top = Counter(self._hashes).most_common(1)[0][1]
            for threshold in sorted(_REPEAT_NUDGES, reverse=True):
                if top >= threshold:
                    warnings.append(_REPEAT_NUDGES[threshold].format(n=top))
                    break

        if self._stagnant >= 4:
            warnings.append(
                f"The page has not changed for {self._stagnant} steps. "
                "Your actions may not be having any effect. "
                "If waiting for something external (OTP, email, slow load) "
                "use wait_tool first, then re-check."
            )

        if self._cycle_warning:
            warnings.append(self._cycle_warning)

        if self._failed_retry_warning:
            warnings.append(self._failed_retry_warning)

        return "\n\n".join(warnings) or None

    def reset(self) -> None:
        self._hashes.clear()
        self._last_page = None
        self._stagnant = 0
        self._visited_pages = {}
        self._cycle_warning = ""
        self._last_action_key = None
        self._last_action_failed = False
        self._failed_retry_warning = ""
