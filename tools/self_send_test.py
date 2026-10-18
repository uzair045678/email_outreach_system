"""One-off: send a real test email to an address you own, with an attachment.

Usage:  python tools/self_send_test.py you@example.com
This is only used for the Phase 7 acceptance check.
"""

from __future__ import annotations

import sys
from pathlib import Path

from outreach.config import load_config
from outreach.logging_setup import setup_logging
from outreach.mail.himalaya import Himalaya
from outreach.send.mime import build_message


def make_min_pdf(path: Path) -> None:
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n160\n%%EOF\n"
    )
    path.write_bytes(content)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tools/self_send_test.py you@example.com")
        return 1
    to_email = sys.argv[1]
    cfg = load_config()
    logger = setup_logging(cfg.path("log_dir"), command="self-send-test")

    pdf = cfg.path("cache_dir") / "test-attachment.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    make_min_pdf(pdf)

    from_email = cfg.profile_raw().get("email") or ""
    if not from_email or from_email == "TODO":
        from_email = to_email
    raw, mid = build_message(
        from_email=from_email,
        to_email=to_email,
        subject="outreach pipeline self-send test",
        body=(
            "This is a one-time test from the outreach pipeline to confirm that sending "
            "through Himalaya works and that a Message-ID is assigned. No action needed.\n"
        ),
        from_name="Outreach Pipeline Test",
        attachment_path=pdf,
    )
    hima = Himalaya(cfg.raw.himalaya.account.raw, logger=logger)
    ok, err = hima.send_raw(raw, save_mailbox=cfg.raw.himalaya.folders.sent.raw)
    if ok:
        print(f"SENT ok  message_id={mid}")
        return 0
    print(f"SEND FAILED: {err}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
