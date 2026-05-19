from __future__ import annotations

import asyncio
import imaplib
import logging
import smtplib
import ssl
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as email_policy
from typing import TYPE_CHECKING

from program.gateway.types import BaseChannel
from program.bus.types import (
    IncomingMessage, OutgoingMessage, StreamPhase,
    TextPart, FilePart, text_from_parts,
)
from program.gateway.channels.email.utils import (
    is_noreply, extract_address, extract_text,
    thread_id_from, message_id_from, build_email,
)

logger = logging.getLogger(__name__)


class EmailChannel(BaseChannel):
    """
    Email channel via IMAP (polling) + SMTP (sending).

    Incoming: polls IMAP INBOX for UNSEEN messages at a configurable interval.
    Outgoing: sends via SMTP with full attachment and reply-threading support.

    chat_id  = thread root Message-ID (stable across all messages in a thread)
    message_id = per-message Message-ID (used for reply threading)

    Supported send modes:
      file         — email with attachment(s)
      intermediate — email with plain-text body mid-turn
      react        — not supported (silently ignored)
      reply=True   — adds In-Reply-To / References headers

    Requires: no extra packages (uses stdlib imaplib + smtplib + email).
    """

    def __init__(
        self,
        username: str,
        password: str,
        imap_host: str,
        smtp_host: str,
        imap_port: int = 993,
        smtp_port: int = 587,
        poll_interval: int = 30,
        allow_from: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._username = username
        self._password = password
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._poll_interval = poll_interval
        self._allow_from = [a.lower() for a in (allow_from or [])]
        self._buffers: dict[str, str] = {}
        # thread_id → last seen Message-ID (for reply headers)
        self._last_message_id: dict[str, str] = {}
        # thread_id → original sender address (reply-to target)
        self._thread_sender: dict[str, str] = {}
        # thread_id → subject
        self._thread_subject: dict[str, str] = {}

    @property
    def channel_id(self) -> str:
        return 'email'

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Poll IMAP for new messages until cancelled."""
        logger.info('EmailChannel: started polling %s every %ds', self._imap_host, self._poll_interval)
        while True:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._poll_imap)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('EmailChannel: IMAP poll error')
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    async def disconnect(self) -> None:
        pass  # polling loop exits on CancelledError; no persistent connection to close

    # ── IMAP polling ──────────────────────────────────────────────────────────

    def _poll_imap(self) -> None:
        """Fetch UNSEEN messages from INBOX and publish them as IncomingMessages."""
        try:
            ctx = ssl.create_default_context()
            with imaplib.IMAP4_SSL(self._imap_host, self._imap_port, ssl_context=ctx) as imap:
                imap.login(self._username, self._password)
                imap.select('INBOX')
                _, data = imap.search(None, 'UNSEEN')
                uids = data[0].split() if data[0] else []

                for uid in uids:
                    _, msg_data = imap.fetch(uid, '(RFC822)')
                    if not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                    if not raw:
                        continue

                    msg: EmailMessage = message_from_bytes(raw, policy=email_policy)  # type: ignore[assignment]
                    self._handle_imap_message(msg)
        except imaplib.IMAP4.error:
            logger.exception('EmailChannel: IMAP error during poll')

    def _handle_imap_message(self, msg: EmailMessage) -> None:
        from_header = msg.get('From', '')
        sender = extract_address(from_header)

        if is_noreply(sender):
            logger.debug('EmailChannel: skipping no-reply message from %r', sender)
            return

        if self._allow_from and sender.lower() not in self._allow_from:
            logger.debug('EmailChannel: sender %r not in allow_from list, skipping', sender)
            return

        text = extract_text(msg)
        if not text:
            return

        thread_id = thread_id_from(msg)
        msg_id = message_id_from(msg)
        subject = msg.get('Subject', '').strip()

        # Store thread context for outgoing replies
        if thread_id:
            self._last_message_id[thread_id] = msg_id
            self._thread_sender[thread_id] = sender
            if thread_id not in self._thread_subject:
                self._thread_subject[thread_id] = subject

        chat_id = thread_id or sender

        asyncio.get_event_loop().call_soon_threadsafe(
            asyncio.ensure_future,
            self.receive(IncomingMessage(
                channel='email',
                chat_id=chat_id,
                parts=[TextPart(text)],
                user_id=sender,
                message_id=msg_id,
            )),
        )

    # ── SMTP sending ──────────────────────────────────────────────────────────

    async def send(self, msg: OutgoingMessage) -> None:
        """Deliver an outgoing message via SMTP."""
        chat_id = msg.chat_id
        phase = msg.stream_phase
        metadata = msg.metadata

        if phase == StreamPhase.START:
            self._buffers[chat_id] = ''

        elif phase == StreamPhase.CHUNK:
            kind = metadata.get('kind')
            if kind not in ('tool_start', 'tool_end'):
                self._buffers[chat_id] = self._buffers.get(chat_id, '') + text_from_parts(msg.parts)

        elif phase == StreamPhase.END:
            buffered = self._buffers.pop(chat_id, '')
            if buffered.strip():
                await self._send_email(chat_id, buffered)

        elif phase == StreamPhase.ERROR:
            err = text_from_parts(msg.parts) or 'Unknown error'
            await self._send_email(chat_id, f'Error: {err}')

        elif phase is None:
            kind = metadata.get('kind')
            if kind == 'react':
                return  # email has no reactions

            reply_to_mid = metadata.get('reply_to')
            attachments: list[str] = []
            text_parts: list[str] = []

            for p in msg.parts:
                match p:
                    case FilePart(path=fp):
                        attachments.append(fp)
                    case TextPart(content=t):
                        text_parts.append(t)

            body = '\n'.join(text_parts)
            await self._send_email(chat_id, body, attachments=attachments, reply_to_mid=reply_to_mid)

    async def _send_email(
        self,
        chat_id: str,
        body: str,
        attachments: list[str] | None = None,
        reply_to_mid: str | None = None,
    ) -> None:
        to_addr = self._thread_sender.get(chat_id, chat_id)
        subject = self._thread_subject.get(chat_id, 'Re: (no subject)')
        if not subject.lower().startswith('re:'):
            subject = f'Re: {subject}'

        # Build reply headers using the most recent message in the thread
        target_mid = reply_to_mid or self._last_message_id.get(chat_id)
        reply_headers: dict[str, str] = {}
        if target_mid:
            reply_headers = {
                'In-Reply-To': f'<{target_mid}>',
                'References': f'<{target_mid}>',
            }

        email_msg = build_email(
            from_addr=self._username,
            to_addr=to_addr,
            subject=subject,
            body=body or '(no content)',
            attachments=attachments,
            reply_headers=reply_headers if reply_headers else None,
        )

        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._smtp_send, email_msg
            )
        except Exception:
            logger.exception('EmailChannel: SMTP send failed to %r', to_addr)

    def _smtp_send(self, msg: EmailMessage) -> None:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(self._smtp_host, self._smtp_port) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.login(self._username, self._password)
            smtp.send_message(msg)
