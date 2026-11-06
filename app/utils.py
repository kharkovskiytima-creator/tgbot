"""Допоміжні функції: час, форматування, валідація."""
from __future__ import annotations

import html
import re
from datetime import date, datetime

from app.config import WORK_END_MIN, WORK_START_MIN, settings
from app.database import Appointment

MONTHS_NOM = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
              "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня",
              "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
WEEKDAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "П’ятниця", "Субота", "Неділя"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def esc(text: object) -> str:
    """Екранування тексту для HTML-розмітки Telegram."""
    return html.escape(str(text), quote=False)


def now_local() -> datetime:
    """Поточний час у Києві (без tzinfo, з точністю до хвилини)."""
    return datetime.now(settings.tz).replace(tzinfo=None, second=0, microsecond=0)


# ---------------------------------------------------------------- форматування
def fmt_date_long(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]} {d.year}"


def fmt_min(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_range(start_min: int, end_min: int) -> str:
    return f"{fmt_min(start_min)}–{fmt_min(end_min)}"


def work_hours_text() -> str:
    return f"Пн–Пт, {fmt_min(WORK_START_MIN)}–{fmt_min(WORK_END_MIN)}"


def user_link(user_id: int, username: str | None) -> str:
    if username:
        return f"@{esc(username)}"
    return f'<a href="tg://user?id={user_id}">профіль у Telegram</a>'


def card_client(a: Appointment) -> str:
    return (
        f"📅 <b>Дата:</b> {fmt_date_long(a.start_at.date())}\n"
        f"🕙 <b>Час:</b> {a.start_at:%H:%M}–{a.end_at:%H:%M}\n"
        f"⏱ <b>Тривалість:</b> {a.duration} хв\n"
        f"👤 <b>Ім’я:</b> {esc(a.name)}\n"
        f"📞 <b>Телефон:</b> {esc(a.phone)}"
    )


def card_admin(a: Appointment, *, with_date: bool = True) -> str:
    lines = [f"🔖 <b>Запис №{a.id}</b>"]
    if with_date:
        lines.append(f"📅 {fmt_date_long(a.start_at.date())}")
    lines += [
        f"🕙 {a.start_at:%H:%M}–{a.end_at:%H:%M} ({a.duration} хв)",
        f"👤 {esc(a.name)}",
        f"📞 {esc(a.phone)}",
        f"💬 {user_link(a.user_id, a.username)}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- валідація
_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яІіЇїЄєҐґ'’ʼ`\-. ]+$")


def normalize_name(raw: str) -> str | None:
    """Повертає охайне ім'я або None, якщо ім'я некоректне."""
    name = re.sub(r"\s+", " ", raw).strip()
    if not 2 <= len(name) <= 50:
        return None
    if not _NAME_RE.match(name):
        return None
    if sum(ch.isalpha() for ch in name) < 2:
        return None
    return name


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


_RANGE_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})\s*$")


def parse_time_range(raw: str) -> tuple[int, int] | None:
    """'14:00-16:00' -> (840, 960). None, якщо формат неправильний."""
    m = _RANGE_RE.match(raw)
    if not m:
        return None
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start >= end:
        return None
    return start, end


def parse_year_month(raw: str) -> tuple[int, int]:
    """'2026-11' -> (2026, 11). ValueError при помилці."""
    y_str, m_str = raw.split("-")
    year, month = int(y_str), int(m_str)
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        raise ValueError("bad year-month")
    return year, month
