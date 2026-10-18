"""Parse delivery-status notifications (bounces) from raw RFC 5322 messages."""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass

MSGID_RE = re.compile(r"<[^<>@\s]+@[^<>\s]+>")

BOUNCE_SUBJECT_HINTS = (
    "undelivered", "undeliverable", "delivery status notification", "delivery failure",
    "address not found", "mail delivery failed", "returned mail", "failure notice",
    "delivery has failed", "could not be delivered", "message not delivered",
    "undelivered mail returned",
)
BOUNCE_SENDER_HINTS = ("mailer-daemon", "postmaster", "mail delivery subsystem", "mailerdaemon")


@dataclass
class DsnResult:
    is_bounce: bool = False
    kind: str = "not_bounce"          # hard | soft | unknown | not_bounce
    final_recipient: str = ""
    status_code: str = ""
    original_message_id: str = ""
    subject: str = ""
    from_addr: str = ""
    message_id: str = ""
    diagnostic: str = ""
    parseable: bool = True


def _status_kind(code: str) -> str:
    code = (code or "").strip()
    if not code:
        return "unknown"
    if code.startswith("5"):
        return "hard"
    if code.startswith("4"):
        return "soft"
    return "unknown"


def looks_like_bounce(subject: str, from_addr: str) -> bool:
    s = (subject or "").lower()
    f = (from_addr or "").lower()
    return any(h in s for h in BOUNCE_SUBJECT_HINTS) or any(h in f for h in BOUNCE_SENDER_HINTS)


def parse_dsn(raw: str) -> DsnResult:
    result = DsnResult()
    try:
        msg = email.message_from_string(raw, policy=email.policy.default)
    except Exception:  # noqa: BLE001
        result.parseable = False
        return result

    subject = str(msg.get("Subject") or "")
    from_hdr = msg.get("From") or ""
    from_addr = ""
    try:
        from_addr = email.utils.parseaddr(str(from_hdr))[1]
    except Exception:  # noqa: BLE001
        from_addr = str(from_hdr)
    result.subject = subject
    result.from_addr = from_addr
    result.message_id = str(msg.get("Message-ID") or "")

    if not looks_like_bounce(subject, from_addr):
        # still try to read a DSN body
        has_dsn = any(p.get_content_type() == "message/delivery-status" for p in msg.walk())
        if not has_dsn:
            result.kind = "not_bounce"
            return result

    result.is_bounce = True
    statuses: list[str] = []
    recipients: list[str] = []
    diagnostics: list[str] = []

    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "message/delivery-status":
            payload = part.get_payload()
            if isinstance(payload, list):
                for sub in payload:
                    fr = sub.get("Final-Recipient") or sub.get("Original-Recipient")
                    if fr:
                        recipients.append(str(fr))
                    st = sub.get("Status")
                    if st:
                        statuses.append(str(st))
                    dg = sub.get("Diagnostic-Code")
                    if dg:
                        diagnostics.append(str(dg))
        elif ctype == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                inner = payload[0]
                mid = inner.get("Message-ID")
                if mid and not result.original_message_id:
                    result.original_message_id = str(mid)

    # headers first, then body text for a message-id
    text = _text_body(msg)
    for header in ("In-Reply-To", "References", "X-Original-Message-ID"):
        for mid in MSGID_RE.findall(str(msg.get(header) or "")):
            if not result.original_message_id:
                result.original_message_id = mid
    if not result.original_message_id:
        found = MSGID_RE.findall(text)
        if found:
            result.original_message_id = found[0]

    if recipients:
        result.final_recipient = _clean_recipient(recipients[0])
    if statuses:
        result.status_code = statuses[0]
        result.kind = _status_kind(statuses[0])
    else:
        result.kind = "unknown"
    if diagnostics:
        result.diagnostic = diagnostics[0]

    if result.kind == "unknown" and not result.final_recipient:
        result.parseable = False
    return result


def _clean_recipient(value: str) -> str:
    m = re.search(r"[\w.+\-]+@[\w.\-]+", value or "")
    return m.group(0).lower() if m else (value or "").strip().lower()


def _text_body(msg) -> str:
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                parts.append(part.get_content())
            except Exception:  # noqa: BLE001
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode("utf-8", "replace"))
    return "\n".join(parts)
