from __future__ import annotations

import mimetypes
import re
from email.message import EmailMessage, Message
from pathlib import Path


_NOREPLY_RE = re.compile(
    r'(no.?reply|bounce|mailer.daemon|postmaster|do.?not.?reply)',
    re.IGNORECASE,
)


def is_noreply(address: str) -> bool:
    """Return True if the address looks like a no-reply / bounce address."""
    return bool(_NOREPLY_RE.search(address))


def extract_address(header: str) -> str:
    """Pull bare email address from a 'Name <addr>' header value."""
    m = re.search(r'<([^>]+)>', header)
    return m.group(1).strip() if m else header.strip()


def extract_text(msg: Message) -> str:
    """Extract plain-text body from an email, preferring text/plain over text/html."""
    text = ''
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == 'text/plain' and not part.get_filename():
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or 'utf-8'
                text = payload.decode(charset, errors='replace')
                break
    if not text:
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/html' and not part.get_filename():
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or 'utf-8'
                    raw = payload.decode(charset, errors='replace')
                    text = re.sub(r'<[^>]+>', ' ', raw)
                    text = re.sub(r'\s+', ' ', text).strip()
                    break
    return text.strip()


def build_reply_headers(orig: Message) -> dict[str, str]:
    """Build In-Reply-To and References headers for a reply to orig."""
    msg_id = orig.get('Message-ID', '').strip()
    existing_refs = orig.get('References', '').strip()
    refs = ' '.join(filter(None, [existing_refs, msg_id]))
    return {'In-Reply-To': msg_id, 'References': refs}


def thread_id_from(msg: Message) -> str:
    """Derive a stable thread ID from the email.

    Uses the root Message-ID: the first item in References if present,
    otherwise the message's own Message-ID.
    """
    refs = msg.get('References', '').strip().split()
    if refs:
        return refs[0].strip('<>')
    msg_id = msg.get('Message-ID', '').strip()
    return msg_id.strip('<>') if msg_id else ''


def message_id_from(msg: Message) -> str:
    """Return the bare Message-ID (without angle brackets)."""
    mid = msg.get('Message-ID', '').strip()
    return mid.strip('<>')


def build_email(
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[str] | None = None,
    reply_headers: dict[str, str] | None = None,
) -> EmailMessage:
    """Construct an EmailMessage ready to send via SMTP."""
    msg = EmailMessage()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    if reply_headers:
        for k, v in reply_headers.items():
            if v:
                msg[k] = v

    msg.set_content(body)

    for path_str in (attachments or []):
        path = Path(path_str)
        if not path.exists():
            continue
        mime_type, _ = mimetypes.guess_type(str(path))
        maintype, subtype = (mime_type or 'application/octet-stream').split('/', 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    return msg
