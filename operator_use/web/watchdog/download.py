from __future__ import annotations
import logging
from operator_use.web.watchdog.base import BaseWatchdog

logger = logging.getLogger(__name__)


class DownloadWatchdog(BaseWatchdog):
    """Tracks browser-native downloads via CDP Browser events.

    Covers downloads triggered by clicking buttons/links, not just
    explicit URL fetches. Also sets the download directory at the
    browser level so Chrome saves files to the configured path.
    """

    def __init__(self, session) -> None:
        super().__init__(session)
        self.downloads: dict[str, dict] = {}  # guid -> info

    async def attach(self) -> None:
        try:
            await self.session.send(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": self.session.config.downloads_dir,
                    "eventsEnabled": True,
                },
            )
        except Exception:
            pass

        self.session.on("Browser.downloadWillBegin", self._on_begin)
        self.session.on("Browser.downloadProgress", self._on_progress)

    def _on_begin(self, event, session_id=None) -> None:
        guid = event.get("guid", "")
        filename = event.get("suggestedFilename", "")
        url = event.get("url", "")
        self.downloads[guid] = {"url": url, "filename": filename, "state": "started"}
        logger.info("Download started: %s (%s)", filename, url)

    def _on_progress(self, event, session_id=None) -> None:
        guid = event.get("guid", "")
        state = event.get("state", "")
        if guid not in self.downloads:
            return
        self.downloads[guid]["state"] = state
        filename = self.downloads[guid]["filename"]
        if state == "completed":
            logger.info("Download completed: %s", filename)
        elif state == "canceled":
            logger.info("Download canceled: %s", filename)
