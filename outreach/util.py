"""Small shared helpers: time, ids, hashing, retries, text."""

from __future__ import annotations

import hashlib
import random
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, TypeVar
from urllib.parse import urlparse

T = TypeVar("T")

UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def new_id(prefix: str = "") -> str:
    stamp = now_utc().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{stamp}{secrets.token_hex(3)}"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def domain_of(url_or_email: str) -> str:
    if not url_or_email:
        return ""
    value = url_or_email.strip()
    if "@" in value and "://" not in value:
        return value.split("@", 1)[1].lower().lstrip(".")
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).netloc.lower()
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def registrable_domain(domain: str) -> str:
    """Best-effort eTLD+1 without pulling in a public-suffix list."""
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    # Common two-part public suffixes.
    two = {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go"}
    if parts[-2] in two and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def random_delay(low: int, high: int) -> float:
    return random.uniform(low, high)


def sleep_delay(low_s: int, high_s: int) -> None:
    time.sleep(random_delay(low_s, high_s))


def retry(
    fn: Callable[[], T],
    attempts: int = 5,
    base_delay: float = 1.5,
    exceptions: tuple[type[BaseException], ...] = (PermissionError, OSError),
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call ``fn`` with linear backoff; re-raise the last error."""
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except exceptions as exc:  # noqa: PERF203
            last = exc
            if on_retry:
                on_retry(i + 1, exc)
            if i < attempts - 1:
                time.sleep(base_delay * (i + 1))
    assert last is not None
    raise last


WORD_RE = re.compile(r"\S+")


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def first_name(contact_name: str) -> str:
    name = (contact_name or "").strip()
    if not name:
        return ""
    return name.split()[0]


def dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def days_between(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 86400.0


def utcnow_plus(**kwargs) -> datetime:
    return now_utc() + timedelta(**kwargs)
