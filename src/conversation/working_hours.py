"""
Working hours calculator for NE India business operations.

Computes working hours between two timestamps, excluding:
- Night hours (outside 9am-7pm IST)
- Sundays
- Indian gazetted holidays
- Assam state holidays

Used by conversation routing to determine how far back to look
for contextually related unassigned scraps.
"""

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Working hours: 9am - 7pm IST, Mon-Sat
WORK_START = time(9, 0)
WORK_END = time(19, 0)
WORK_HOURS_PER_DAY = 10  # 9am to 7pm

# Sunday = 6 in weekday()
WEEKLY_OFF = {6}  # Sunday only — Mon-Sat is working


# Indian gazetted holidays + Assam state holidays (2026)
# Dates approximate — some shift yearly based on lunar calendar
HOLIDAYS_2026 = {
    # National
    (1, 26),   # Republic Day
    (3, 14),   # Holi (approx)
    (3, 31),   # Id-ul-Fitr (approx)
    (4, 6),    # Ram Navami (approx)
    (4, 10),   # Mahavir Jayanti
    (4, 14),   # Dr Ambedkar Jayanti
    (5, 1),    # May Day
    (5, 12),   # Buddha Purnima (approx)
    (6, 7),    # Id-ul-Zuha (approx)
    (7, 6),    # Muharram (approx)
    (8, 15),   # Independence Day
    (9, 5),    # Milad-un-Nabi (approx)
    (10, 2),   # Gandhi Jayanti
    (10, 12),  # Dussehra (approx)
    (10, 20),  # Diwali (approx - could be Nov depending on year)
    (11, 1),   # Diwali day 2 / Govardhan Puja
    (11, 15),  # Guru Nanak Jayanti (approx)
    (12, 25),  # Christmas

    # Assam state holidays
    (1, 14),   # Magh Bihu / Bhogali Bihu
    (1, 15),   # Magh Bihu day 2
    (4, 14),   # Bohag Bihu (Rongali Bihu) — overlaps Ambedkar
    (4, 15),   # Bohag Bihu day 2
    (4, 16),   # Bohag Bihu day 3
    (10, 24),  # Kati Bihu (approx)
    (11, 2),   # Bhai Dooj
}


def is_working_day(dt: datetime) -> bool:
    """Check if a date is a working day (not Sunday, not holiday)."""
    if dt.weekday() in WEEKLY_OFF:
        return False
    if (dt.month, dt.day) in HOLIDAYS_2026:
        return False
    return True


def working_hours_between(ts1: int, ts2: int) -> float:
    """
    Compute working hours between two unix timestamps.

    ts1 should be earlier than ts2. Returns the number of 9am-7pm IST
    working hours between them, excluding Sundays and holidays.

    Examples:
        10am Tue → 2pm Tue = 4.0 working hours
        5pm Mon → 10am Tue = 1.0 working hours (5pm-7pm Mon = 2h, but
            we only count forward from ts1, so 5pm-7pm = 2h on Mon,
            then 9am-10am = 1h on Tue... wait, let me think properly)

    Actually: we iterate day by day, computing working minutes in each day
    between the two timestamps.
    """
    if ts1 > ts2:
        ts1, ts2 = ts2, ts1

    dt1 = datetime.fromtimestamp(ts1, tz=IST)
    dt2 = datetime.fromtimestamp(ts2, tz=IST)

    total_minutes = 0.0
    current = dt1

    while current.date() <= dt2.date():
        if is_working_day(current):
            # Working window for this day
            day_start = current.replace(hour=WORK_START.hour, minute=WORK_START.minute,
                                         second=0, microsecond=0)
            day_end = current.replace(hour=WORK_END.hour, minute=WORK_END.minute,
                                       second=0, microsecond=0)

            # Clamp to the actual range [ts1, ts2]
            effective_start = max(day_start, dt1)
            effective_end = min(day_end, dt2)

            if effective_start < effective_end:
                minutes = (effective_end - effective_start).total_seconds() / 60
                total_minutes += minutes

        # Next day
        current = (current + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    return total_minutes / 60.0


def working_hours_ago(ts: int, hours: float) -> int:
    """
    Given a timestamp, find the timestamp that is `hours` working hours
    before it. Used to compute the lookback window.

    Returns a unix timestamp.
    """
    dt = datetime.fromtimestamp(ts, tz=IST)
    remaining_minutes = hours * 60
    current = dt

    while remaining_minutes > 0:
        if is_working_day(current):
            day_start = current.replace(hour=WORK_START.hour, minute=WORK_START.minute,
                                         second=0, microsecond=0)
            day_end = current.replace(hour=WORK_END.hour, minute=WORK_END.minute,
                                       second=0, microsecond=0)

            # How much of today's working hours are before `current`?
            effective_end = min(day_end, current)
            effective_start = day_start

            if effective_start < effective_end:
                available = (effective_end - effective_start).total_seconds() / 60
                if available >= remaining_minutes:
                    # Answer is within this day
                    result = effective_end - timedelta(minutes=remaining_minutes)
                    return int(result.timestamp())
                else:
                    remaining_minutes -= available

        # Go to previous day end-of-work
        current = (current - timedelta(days=1)).replace(
            hour=WORK_END.hour, minute=WORK_END.minute,
            second=0, microsecond=0
        )

    return int(current.timestamp())
