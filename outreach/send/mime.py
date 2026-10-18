"""Build raw RFC 5322 messages (plain text + optional PDF attachment)."""

from __future__ import annotations

import mimetypes
import uuid
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from pathlib import Path


def make_message_id(domain: str = "outreach.local") -> str:
    return f"<{uuid.uuid4().hex}.{uuid.uuid4().hex[:8]}@{domain}>"


def build_message(
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    message_id: str | None = None,
    from_name: str = "",
    in_reply_to: str = "",
    references: str = "",
    attachment_path: str | Path | None = None,
    reply_to: str = "",
) -> tuple[str, str]:
    """Return (raw_message, message_id)."""
    msg = EmailMessage(policy=SMTP)
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    mid = message_id or make_message_id(from_email.split("@", 1)[-1])
    msg["Message-ID"] = mid
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)

    if attachment_path:
        path = Path(attachment_path)
        if path.exists():
            ctype, _ = mimetypes.guess_type(str(path))
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(
                path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
            )
    return msg.as_string(), mid
