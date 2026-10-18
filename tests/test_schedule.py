import random
from datetime import datetime, timedelta, timezone

import pytest

from outreach.schedule.slots import (
    add_business_days,
    due_or_reschedule,
    is_weekend,
    next_send_slot,
)
from outreach.schedule.tz import country_code, recipient_timezone

UTC = timezone.utc
HOURS = [9, 10, 14]


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_weekend_skip():
    # 2026-10-17 is a Saturday
    assert is_weekend(datetime(2026, 10, 17).date())
    slot = next_send_slot(utc(2026, 10, 16, 22, 0), "Asia/Singapore", HOURS, 0, rng=random.Random(1))
    # Friday 22:00 UTC is Saturday 06:00 in Singapore -> must move to Monday
    local = slot.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Singapore"))
    assert local.weekday() < 5


def test_several_countries():
    c1, g1 = recipient_timezone("Japan", "Tokyo")
    assert c1 == "Asia/Tokyo" and g1 is False
    c2, g2 = recipient_timezone("United States", "San Francisco")
    assert c2 == "America/Los_Angeles" and g2 is False
    c3, g3 = recipient_timezone("United States")  # multi-timezone, no city
    assert c3 == "America/New_York" and g3 is True
    c4, g4 = recipient_timezone("Neverland")
    assert c4 == "UTC" and g4 is True
    assert country_code("Germany") == "DE"


def test_dst_boundary_new_york():
    # US DST springs forward 2026-03-08. A slot on 03-06 09:00 local is EST (UTC-5).
    from zoneinfo import ZoneInfo

    now = utc(2026, 3, 6, 12, 0)  # Friday 07:00 EST
    slot = next_send_slot(now, "America/New_York", [9], 0, rng=random.Random(0))
    local = slot.astimezone(ZoneInfo("America/New_York"))
    assert local.hour == 9
    assert local.utcoffset().total_seconds() == -5 * 3600


def test_dst_after_transition():
    from zoneinfo import ZoneInfo

    now = utc(2026, 3, 9, 12, 0)  # Monday 08:00 EDT, after spring forward
    slot = next_send_slot(now, "America/New_York", [9], 0, rng=random.Random(0))
    local = slot.astimezone(ZoneInfo("America/New_York"))
    assert local.hour == 9
    assert local.utcoffset().total_seconds() == -4 * 3600


def test_jitter_within_bounds():
    rng = random.Random(3)
    slot = next_send_slot(utc(2026, 5, 4, 0, 0), "Asia/Kolkata", HOURS, 20, rng=rng)
    assert 0 <= slot.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).minute <= 20


def test_overdue_within_threshold_sends_now():
    now = utc(2026, 5, 4, 12, 0)
    send_after = now - timedelta(hours=1)
    slot, resched = due_or_reschedule(send_after, now, "Europe/Warsaw", HOURS, 0, overdue_hours=3)
    assert slot == send_after and resched is False


def test_overdue_beyond_threshold_reschedules():
    now = utc(2026, 5, 4, 12, 0)  # Monday
    send_after = now - timedelta(hours=8)
    slot, resched = due_or_reschedule(send_after, now, "Europe/Warsaw", HOURS, 0, overdue_hours=3, rng=random.Random(2))
    assert resched is True
    assert slot > now


def test_business_days_skip_weekend():
    friday = utc(2026, 5, 8, 9, 0)  # Friday
    result = add_business_days(friday, 1, "US")
    assert result.weekday() == 0  # Monday
