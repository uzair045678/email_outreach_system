"""Reply detection + classification helpers."""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass

from ..draft import prompts

RETURN_DATE_RES = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(
        r"\b(?:return|back|available|resume|until|on)\b[^\n.]{0,30}?"
        r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2})",
        re.IGNORECASE,
    ),
]

OOO_SUBJECT_HINTS = (
    "out of office", "out-of-office", "automatic reply", "auto-reply", "autoreply",
    "automatic response", "away", "on vacation", "maternity", "annual leave", "ooo:",
)


@dataclass
class ReplyInfo:
    from_addr: str = ""
    from_name: str = ""
    subject: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: str = ""
    auto_submitted: str = ""
    x_autoreply: str = ""
    date: str = ""
    body: str = ""


def parse_message(raw: str) -> ReplyInfo:
    info = ReplyInfo()
    try:
        msg = email.message_from_string(raw, policy=email.policy.default)
    except Exception:  # noqa: BLE001
        return info
    info.from_name, info.from_addr = email.utils.parseaddr(str(msg.get("From") or ""))
    info.from_addr = (info.from_addr or "").lower()
    info.subject = str(msg.get("Subject") or "")
    info.message_id = str(msg.get("Message-ID") or "")
    info.in_reply_to = str(msg.get("In-Reply-To") or "")
    info.references = str(msg.get("References") or "")
    info.auto_submitted = str(msg.get("Auto-Submitted") or "")
    info.x_autoreply = str(msg.get("X-Autoreply") or msg.get("X-Autorespond") or "")
    info.date = str(msg.get("Date") or "")
    info.body = _text_body(msg)
    return info


def _text_body(msg) -> str:
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                parts.append(part.get_content())
            except Exception:  # noqa: BLE001
                pass
    if not parts:
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    parts.append(re.sub(r"<[^>]+>", " ", part.get_content()))
                except Exception:  # noqa: BLE001
                    pass
    return "\n".join(parts)


def is_auto_reply(info: ReplyInfo) -> bool:
    if info.auto_submitted and info.auto_submitted.lower() not in ("no", ""):
        return True
    if info.x_autoreply:
        return True
    subj = info.subject.lower()
    return any(h in subj for h in OOO_SUBJECT_HINTS)


def references_us(info: ReplyInfo, sent_message_ids: set[str]) -> bool:
    blob = f"{info.in_reply_to} {info.references} {info.body[:2000]}"
    return any(mid and mid in blob for mid in sent_message_ids)


def extract_return_date(text: str) -> str:
    for pattern in RETURN_DATE_RES:
        m = pattern.search(text or "")
        if m:
            return m.group(1)
    return ""


def classify_heuristic(info: ReplyInfo) -> str:
    if is_auto_reply(info):
        return "auto_reply_out_of_office"
    body = (info.body or "").lower()
    negatives = (
        "unsubscribe", "remove me", "stop emailing", "don't contact", "do not contact",
        "not interested", "no thanks", "no thank you", "please stop", "take me off",
    )
    positives = (
        "interested", "would love", "let's talk", "lets talk", "available for a call",
        "schedule a call", "happy to chat", "send your cv", "share your portfolio",
        "tell me more", "sounds good",
    )
    if any(n in body for n in negatives):
        return "negative_or_unsubscribe"
    if any(p in body for p in positives):
        return "positive"
    return "other"


def classify_with_model(client, info: ReplyInfo, no_think: bool = True) -> dict:
    messages = prompts.reply_messages(info.subject, info.body)
    if no_think:
        from ..llm.client import add_no_think

        messages = add_no_think(messages)
    try:
        return client.chat_json(messages, schema=prompts.REPLY_SCHEMA)
    except Exception:  # noqa: BLE001
        return {"category": classify_heuristic(info)}
