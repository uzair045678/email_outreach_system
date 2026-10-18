"""Optional SMTP ``RCPT TO`` probe (free ZeroBounce fallback).

Opt-in via ``EMAIL_VERIFY_SMTP=1``. One probe per domain per process (never
hammer a single mail server). Returns:
    (True,  detail)  mailbox accepted   -> deliverable
    (False, detail)  mailbox rejected   -> undeliverable
    (None,  detail)  unknown / timeout / greylisting -> treat as uncertain
"""

from __future__ import annotations

import smtplib
import socket

from . import verify as verify_mod

HELO = "verifier.example.com"
MAX_PROBES_PER_DOMAIN = 1
DEFAULT_TIMEOUT = 12


def _mx_hosts(domain: str, resolver) -> list[str]:
    try:
        answers = resolver.resolve(domain, "MX")
        hosts = sorted(
            ((r.preference, str(r.exchange).rstrip(".")) for r in answers),
            key=lambda kv: kv[0],
        )
        return [h for _, h in hosts if h]
    except Exception:  # noqa: BLE001
        return [domain]


def probe_email(
    email_addr: str,
    resolver,
    mail_from: str = "probe@verifier.example.com",
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool | None, str]:
    """Return (ok, detail); ok in {True, False, None}."""
    if not verify_mod.is_valid_syntax(email_addr):
        return False, "bad-syntax"
    domain = verify_mod.util.domain_of(email_addr)

    last: tuple[bool | None, str] = (None, "smtp:no-mx-hosts")
    for host in _mx_hosts(domain, resolver):
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(host, 25)
                code, _ = smtp.ehlo(HELO)
                if code not in (250, 0):
                    smtp.helo(HELO)
                smtp.mail(mail_from)
                code, _msg = smtp.rcpt(email_addr)
                smtp.quit()
                if code == 250:
                    return True, f"smtp:{host}:250"
                if code in (550, 551, 552, 553, 554, 501, 511):
                    return False, f"smtp:{host}:{code}"
                last = (None, f"smtp:{host}:{code}")
        except smtplib.SMTPResponseException as exc:
            if exc.smtp_code in (550, 551, 552, 553, 554):
                return False, f"smtp:{host}:{exc.smtp_code}"
            last = (None, f"smtp:{host}:{exc.smtp_code}")
        except (smtplib.SMTPException, socket.timeout, OSError) as exc:
            last = (None, f"smtp:{host}:{type(exc).__name__}")
    return last
