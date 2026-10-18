"""Send-window scheduling: recipient-local slots, weekends, holidays, DST."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

_holiday_cache: dict[tuple[str, int], object] = {}


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5  # Sat=5, Sun=6


def is_holiday(iso_code: str, day: date) -> bool:
    if not iso_code:
        return False
    key = (iso_code, day.year)
    if key not in _holiday_cache:
        try:
            import holidays

            _holiday_cache[key] = holidays.country_holidays(iso_code, years=[day.year])
        except Exception:  # noqa: BLE001
            _holiday_cache[key] = {}
    cal = _holiday_cache[key]
    try:
        return day in cal  # type: ignore[operator]
    except Exception:  # noqa: BLE001
        return False


def next_send_slot(
    now_utc: datetime,
    tz_name: str,
    hours: list[int],
    jitter_minutes: int = 0,
    iso_code: str = "",
    rng: random.Random | None = None,
    max_days: int = 14,
) -> datetime:
    """Next weekday slot at one of ``hours`` recipient-local, in UTC.

    A random hour is chosen from ``hours`` and a random jitter (0..jitter_min)
    is added to the minutes so emails do not all land on :00.
    """
    rng = rng or random
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = UTC

    local_now = now_utc.astimezone(tz)
    hour = rng.choice(hours)
    minute = rng.randint(0, jitter_minutes) if jitter_minutes > 0 else 0

    for offset in range(max_days):
        day = (local_now + timedelta(days=offset)).date()
        if is_weekend(day):
            continue
        if iso_code and is_holiday(iso_code, day):
            continue
        cand = datetime.combine(day, time(hour, minute), tzinfo=tz)
        if offset == 0 and cand <= local_now + timedelta(minutes=1):
            continue
        return cand.astimezone(UTC)

    return (local_now + timedelta(days=1)).astimezone(UTC)


def describe_local(dt_utc: datetime | None, tz_name: str) -> str:
    if dt_utc is None:
        return ""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = UTC
    local = dt_utc.astimezone(tz)
    return f"{local.strftime('%Y-%m-%d %H:%M')} ({tz_name})"


def add_business_days(start_utc: datetime, days: int, iso_code: str = "") -> datetime:
    """Add ``days`` business days to a UTC datetime (skips weekends/holidays)."""
    tz = UTC
    try:
        # keep it in the recipient-independent UTC calendar for follow-ups
        day = start_utc.astimezone(tz).date()
    except Exception:  # noqa: BLE001
        day = start_utc.date()
    added = 0
    while added < days:
        day = day + timedelta(days=1)
        if is_weekend(day):
            continue
        if iso_code and is_holiday(iso_code, day):
            continue
        added += 1
    return datetime.combine(day, start_utc.astimezone(tz).timetz(), tzinfo=tz)


def due_or_reschedule(
    send_after_utc: datetime | None,
    now_utc: datetime,
    tz_name: str,
    hours: list[int],
    jitter_minutes: int = 0,
    iso_code: str = "",
    overdue_hours: int = 3,
    rng: random.Random | None = None,
) -> tuple[datetime, bool]:
    """Return (slot, rescheduled).

    * future slot -> unchanged, not rescheduled
    * due and overdue by <= overdue_hours -> send now (unchanged)
    * due and overdue by more than overdue_hours -> next valid slot
    """
    if send_after_utc is None:
        return next_send_slot(now_utc, tz_name, hours, jitter_minutes, iso_code, rng), True
    if send_after_utc > now_utc:
        return send_after_utc, False
    if (now_utc - send_after_utc) > timedelta(hours=overdue_hours):
        return next_send_slot(now_utc, tz_name, hours, jitter_minutes, iso_code, rng), True
    return send_after_utc, False
