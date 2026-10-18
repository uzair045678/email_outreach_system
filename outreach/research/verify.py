"""Email verification: syntax + MX (dnspython) + optional verification API.

The API backend (ZeroBounce etc.) is preferred. When no API is usable, an
OPT-IN SMTP ``RCPT TO`` probe (``EMAIL_VERIFY_SMTP=1``) may be used as a free
fallback; it is capped at one probe per domain per process.
"""

from __future__ import annotations

import re

import dns.exception
import dns.resolver
import requests

from .. import util

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

GENERIC_PREFIXES = {
    "info", "hello", "hi", "contact", "careers", "jobs", "job", "hr",
    "support", "sales", "admin", "office", "mail", "team", "enquiries",
    "enquiry", "inquiries", "inquiry", "business", "general", "press",
    "marketing", "recruitment", "apply", "work", "join",
}

ROLE_PREFIXES = {
    "founder", "ceo", "cto", "coo", "director", "creative", "producer",
    "lead", "manager", "recruiter", "talent", "hiring", "studio", "tech",
    "technical", "art", "design",
}


def is_valid_syntax(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_RE.match(email))


def email_type(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    base = re.split(r"[._\-+]", local)[0]
    if base in GENERIC_PREFIXES:
        return "generic"
    if base in ROLE_PREFIXES:
        return "role"
    return "personal"


class EmailVerifier:
    def __init__(self, api_key: str = "", base_url: str = "", logger=None, smtp_probe: bool = False):
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.logger = logger
        self.smtp_probe = smtp_probe
        self.resolver = dns.resolver.Resolver()
        self.resolver.timeout = 4
        self.resolver.lifetime = 6
        self._mx_cache: dict[str, tuple[bool, str]] = {}
        self._probed_domains: set[str] = set()

    def mx_ok(self, domain: str) -> tuple[bool, str]:
        domain = domain.lower().strip()
        if domain in self._mx_cache:
            return self._mx_cache[domain]
        result = self._lookup_mx(domain)
        self._mx_cache[domain] = result
        return result

    def _lookup_mx(self, domain: str) -> tuple[bool, str]:
        try:
            answers = self.resolver.resolve(domain, "MX")
            if len(answers) > 0:
                return True, "mx"
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            pass
        except (dns.resolver.NoNameservers, dns.exception.Timeout):
            return True, "dns-timeout (assumed ok)"
        except Exception:  # noqa: BLE001
            pass
        # Some domains accept mail on the A record.
        try:
            self.resolver.resolve(domain, "A")
            return True, "a-record"
        except dns.resolver.NXDOMAIN:
            return False, "nxdomain"
        except Exception:  # noqa: BLE001
            return False, "no-mx"

    def _api_check(self, email: str) -> tuple[str | None, str]:
        if not (self.api_key and self.base_url):
            return None, ""
        try:
            key_param = "api_key" if "zerobounce" in self.base_url else "apikey"
            resp = requests.get(
                self.base_url,
                params={"email": email, key_param: self.api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            status = str(data.get("status") or data.get("result") or data.get("state") or "").lower()
            if status in ("valid", "deliverable", "ok"):
                return "high", f"api:{status}"
            if status in ("invalid", "undeliverable", "disposable", "spamtrap", "abuse", "toxic", "do_not_mail"):
                return "low", f"api:{status}"
            return "medium", f"api:{status or 'unknown'}"
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.debug("verify api failed for %s: %s", email, exc)
            return None, ""

    def _smtp_check(self, email: str) -> tuple[str | None, str]:
        if not self.smtp_probe:
            return None, ""
        from . import smtp_probe

        domain = util.domain_of(email)
        if domain in self._probed_domains:
            return None, "smtp:already-probed"
        self._probed_domains.add(domain)
        try:
            ok, detail = smtp_probe.probe_email(email, self.resolver)
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.debug("smtp probe failed for %s: %s", email, exc)
            return None, ""
        if ok is True:
            return "high", detail
        if ok is False:
            return "low", detail
        return None, detail

    def verify(self, email: str, found_on_site: bool = True) -> tuple[str, str]:
        """Return (confidence, detail) where confidence in {high, medium, low}."""
        if not is_valid_syntax(email):
            return "low", "bad-syntax"
        domain = util.domain_of(email)
        ok, detail = self.mx_ok(domain)
        if not ok:
            return "low", f"mx:{detail}"

        api_conf, api_detail = self._api_check(email)
        if api_conf == "low":
            return "low", api_detail

        smtp_conf, smtp_detail = (None, "")
        if api_conf is None:
            smtp_conf, smtp_detail = self._smtp_check(email)
            if smtp_conf == "low":
                return "low", smtp_detail

        kind = email_type(email)
        if api_conf == "high":
            return "high", api_detail
        if smtp_conf == "high":
            return "high", smtp_detail
        if found_on_site and kind == "personal":
            return "high", "on-site-mx"
        if found_on_site and kind == "role":
            return "medium", "on-site-role"
        if found_on_site:
            return "medium", "on-site-generic"
        if kind == "personal":
            return "medium", "pattern-mx"
        return "low", "pattern-generic"
