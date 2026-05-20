from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EmailChannelConfig(BaseModel):
    model_config = ConfigDict(extra='ignore')
    enabled: bool = False
    imap_host: str = ''
    imap_port: int = 993
    smtp_host: str = ''
    smtp_port: int = 587
    poll_interval: int = 30   # seconds between IMAP checks
    allow_from: list[str] = []  # whitelist of sender addresses; empty = allow all


