"""Логіка розкладу: вільні слоти та безпечне бронювання."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from app.config import (BOOKING_HORIZON_DAYS, WORK_DAYS, WORK_END_MIN,
                        WORK_START_MIN)
from app.database import EXTRA, Appointment, Database


class BookingError(Exception):
    """Базовий клас помилок бронювання."""


class SlotUnavailable(BookingError):
    """Обраний час уже зайнятий або недоступний."""


class AlreadyBooked(BookingError):
    """У клієнта вже є активний запис."""


def _overlaps(s: int, e: int, intervals: list[tuple[int, int]]) -> bool:
    return any(s < be and e > bs for bs, be in intervals)


async def get_free_slots(db: Database, day: date, now: datetime,
                         min_lead_minutes: int = 0) -> list[str]:
    """Список вільних часів дня у форматі 'HH:MM'.

    Враховує: минулі дати, горизонт запису, вихідні (субота/неділя та дні,
    додані адміністратором), додаткові години, заблокований час, уже
    зайняті слоти та мінімальний «запас» до початку.
    """
    if day < now.date() or day > now.date() + timedelta(days=BOOKING_HORIZON_DAYS):
        return []
    if await db.is_day_off(day):
        return []

    duration = await db.get_duration()

    windows: list[tuple[int, int]] = []
    if day.weekday() in WORK_DAYS:
        windows.append((WORK_START_MIN, WORK_END_MIN))
    windows.extend((s, e) for _, s, e in await db.list_ranges(EXTRA, day))
    if not windows:
        return []

    starts: set[int] = set()
    for w_start, w_end in windows:
        t = w_start
        while t + duration <= w_end:
            starts.add(t)
            t += duration

    busy = await db.busy_intervals(day)
    earliest = None
    if day == now.date():
        earliest = now.hour * 60 + now.minute + min_lead_minutes

    result = []
    for start in sorted(starts):
        if earliest is not None and start < earliest:
            continue
        if _overlaps(start, start + duration, busy):
            continue
        result.append(f"{start // 60:02d}:{start % 60:02d}")
    return result


async def days_with_slots(db: Database, year: int, month: int, now: datetime,
                          min_lead_minutes: int = 0) -> set[date]:
    """Дні місяця, на які є хоча б один вільний час."""
    first = date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    result: set[date] = set()
    for offset in range(days_in_month):
        d = first + timedelta(days=offset)
        if await get_free_slots(db, d, now, min_lead_minutes):
            result.add(d)
    return result


async def book_slot(
    db: Database, *, user_id: int, username: str | None, name: str, phone: str,
    day: date, hhmm: str, now: datetime, min_lead_minutes: int = 0,
) -> Appointment:
    """Створює запис. Усередині блокування перевіряє, що час досі вільний —
    тому два клієнти не можуть забронювати один і той самий слот."""
    async with db.booking_lock:
        if await db.get_user_upcoming(user_id, now):
            raise AlreadyBooked()

        free = await get_free_slots(db, day, now, min_lead_minutes)
        if hhmm not in free:
            raise SlotUnavailable()

        duration = await db.get_duration()
        hour, minute = (int(x) for x in hhmm.split(":"))
        start = datetime(day.year, day.month, day.day, hour, minute)
        end = start + timedelta(minutes=duration)

        until = start - now
        appt_id = await db.insert_appointment(
            user_id=user_id, username=username, name=name, phone=phone,
            start_at=start, end_at=end, duration=duration,
            # Якщо до візиту вже менше 24 год / 2 год — відповідне нагадування не потрібне.
            remind24_sent=until <= timedelta(hours=24),
            remind2_sent=until <= timedelta(hours=2),
            created_at=now,
        )
        appt = await db.get_appointment(appt_id)
        assert appt is not None
        return appt
