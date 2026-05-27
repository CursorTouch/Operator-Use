from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator


def shutdown_debug() -> bool:
    """True when the user wants full shutdown diagnostics.

    Enabled by the OPERATOR_DEBUG env var or a root logger at DEBUG level.
    """
    return bool(os.environ.get("OPERATOR_DEBUG")) or \
        logging.getLogger().getEffectiveLevel() <= logging.DEBUG


@contextmanager
def quiet_library_logging(*logger_names: str) -> Generator[bool]:
    """Mute noisy third-party loggers during a channel's shutdown/teardown.

    Channel backends (python-telegram-bot, discord.py, slack_sdk, twitchio,
    and their aiohttp/asyncio plumbing) log the cancelled networking task as
    an ERROR traceback during a normal Ctrl-C shutdown even though they
    handle it gracefully. Raise those loggers to CRITICAL for the teardown
    window so users get a clean exit, unless they are debugging.

    Yields the resolved debug flag so callers can decide whether to log
    their own teardown exceptions.
    """
    debug = shutdown_debug()
    if debug:
        yield debug
        return
    saved: list[tuple[logging.Logger, int]] = []
    for name in logger_names:
        lg = logging.getLogger(name)
        saved.append((lg, lg.level))
        lg.setLevel(logging.CRITICAL)
    try:
        yield debug
    finally:
        for lg, level in saved:
            lg.setLevel(level)
