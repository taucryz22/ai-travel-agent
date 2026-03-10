from __future__ import annotations

from datetime import datetime, timedelta

TIME_FMT = "%H:%M"


def hm_to_minutes(hm: str) -> int:
    dt = datetime.strptime(hm, TIME_FMT)
    return dt.hour * 60 + dt.minute


def minutes_to_hm(total: int) -> str:
    hours, minutes = divmod(total, 60)
    return f"{hours:02d}:{minutes:02d}"


def add_minutes(hm: str, minutes: int) -> str:
    total = hm_to_minutes(hm) + minutes
    return minutes_to_hm(total)


def fits_in_day(current_hm: str, duration_min: int, day_end_hm: str) -> bool:
    return hm_to_minutes(current_hm) + duration_min <= hm_to_minutes(day_end_hm)
