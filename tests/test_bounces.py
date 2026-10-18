from __future__ import annotations

from outreach.mail import replies as rl
from outreach.mail.dsn import looks_like_bounce, parse_dsn

HARD = (
    "From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\n"
    "Subject: Delivery Status Notification (Failure)\r\n"
    "Message-ID: <bounce1@mail.gmail.com>\r\n"
    'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n'
    "\r\n"
    "--B\r\nContent-Type: text/plain\r\n\r\nAddress not found. Your message wasn't delivered.\r\n"
    "--B\r\nContent-Type: message/delivery-status\r\n\r\n"
    "Reporting-MTA: dns; googlemail.com\r\n\r\n"
    "Final-Recipient: rfc822; someone@example.com\r\n"
    "Action: failed\r\n"
    "Status: 5.1.1\r\n"
    "Diagnostic-Code: smtp; 550 5.1.1 The email account does not exist\r\n\r\n"
    "--B\r\nContent-Type: message/rfc822\r\n\r\n"
    "Message-ID: <oursent@mail.gmail.com>\r\n"
    "Subject: hello\r\n\r\nbody\r\n"
    "--B--\r\n"
)

SOFT = HARD.replace("Status: 5.1.1", "Status: 4.4.7").replace(
    "Delivery Status Notification (Failure)", "Delivery Status Notification (Delay)"
)

NON_BOUNCE = (
    "From: Jane <jane@acme.com>\r\nSubject: Re: hello\r\nMessage-ID: <r1@acme.com>\r\n"
    "In-Reply-To: <oursent@mail.gmail.com>\r\n\r\nYes, I'm interested!\r\n"
)


def test_hard_bounce_parse():
    r = parse_dsn(HARD)
    assert r.is_bounce and r.kind == "hard"
    assert r.final_recipient == "someone@example.com"
    assert r.status_code == "5.1.1"
    assert r.original_message_id == "<oursent@mail.gmail.com>"


def test_soft_bounce_parse():
    r = parse_dsn(SOFT)
    assert r.is_bounce and r.kind == "soft"
    assert r.final_recipient == "someone@example.com"


def test_non_bounce():
    r = parse_dsn(NON_BOUNCE)
    assert not r.is_bounce
    assert r.kind == "not_bounce"


def test_looks_like_bounce():
    assert looks_like_bounce("Delivery Status Notification (Failure)", "mailer-daemon@googlemail.com")
    assert not looks_like_bounce("Re: hello", "jane@acme.com")


def test_auto_reply_detection():
    info = rl.parse_message(
        "From: Jane <jane@acme.com>\r\nSubject: Automatic reply: hello\r\n"
        "Auto-Submitted: auto-replied\r\n\r\nI am out of office until 2026-04-01.\r\n"
    )
    assert rl.is_auto_reply(info)
    assert rl.extract_return_date(info.body) == "2026-04-01"


def test_classify_heuristic():
    neg = rl.ReplyInfo(body="Please unsubscribe me from this list.")
    pos = rl.ReplyInfo(body="I'm interested, can we schedule a call?")
    assert rl.classify_heuristic(neg) == "negative_or_unsubscribe"
    assert rl.classify_heuristic(pos) == "positive"


def test_references_us():
    info = rl.parse_message(NON_BOUNCE)
    assert rl.references_us(info, {"<oursent@mail.gmail.com>"})
    assert not rl.references_us(info, {"<other@x.com>"})
