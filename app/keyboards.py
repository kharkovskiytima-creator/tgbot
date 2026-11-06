"""Усі клавіатури бота."""
from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)

from app.database import Appointment
from app.texts import (BTN_ADMIN, BTN_BACK, BTN_BOOK, BTN_CANCEL, BTN_CONTACTS,
                       BTN_MENU, BTN_MY, BTN_PHONE)
from app.utils import MONTHS_NOM, WEEKDAYS_SHORT, fmt_range

NOOP = "noop"


def _ib(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =============================================================== reply-клавіатури
def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_BOOK)],
        [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_CANCEL)],
        [KeyboardButton(text=BTN_CONTACTS)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, input_field_placeholder="Оберіть дію ⬇️"
    )


def name_step_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_MENU)]],
        resize_keyboard=True, input_field_placeholder="Введіть ваше ім’я",
    )


def phone_step_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PHONE, request_contact=True)],
            [KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True, input_field_placeholder="Натисніть кнопку «Надіслати номер»",
    )


# =============================================================== календар
def build_calendar(
    prefix: str, year: int, month: int, enabled: set[date],
    min_ym: tuple[int, int], max_ym: tuple[int, int],
    footer: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    """Інлайн-календар. Дні з `enabled` — натискні, решта показані як «·».

    callback_data: '<prefix>:d:YYYY-MM-DD' (день) та '<prefix>:m:YYYY-MM' (місяць).
    """
    rows: list[list[InlineKeyboardButton]] = [
        [_ib(f"📅 {MONTHS_NOM[month - 1]} {year}", NOOP)],
        [_ib(w, NOOP) for w in WEEKDAYS_SHORT],
    ]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        row = []
        for d in week:
            if d.month != month:
                row.append(_ib(" ", NOOP))
            elif d in enabled:
                row.append(_ib(str(d.day), f"{prefix}:d:{d.isoformat()}"))
            else:
                row.append(_ib("·", NOOP))
        rows.append(row)

    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    nav = [
        _ib("◀️", f"{prefix}:m:{prev_y}-{prev_m:02d}") if (prev_y, prev_m) >= min_ym else _ib(" ", NOOP),
        _ib("▶️", f"{prefix}:m:{next_y}-{next_m:02d}") if (next_y, next_m) <= max_ym else _ib(" ", NOOP),
    ]
    rows.append(nav)
    if footer:
        rows.extend(footer)
    return _kb(rows)


# =============================================================== клієнт
def menu_footer() -> list[list[InlineKeyboardButton]]:
    return [[_ib(BTN_MENU, "menu:main")]]


def times_kb(day: date, slots: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot in slots:
        row.append(_ib(f"🕙 {slot}", f"bk:t:{day.isoformat()}:{slot.replace(':', '')}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_ib(BTN_BACK, f"bk:m:{day.year}-{day.month:02d}"), _ib(BTN_MENU, "menu:main")])
    return _kb(rows)


def confirm_kb(day: date) -> InlineKeyboardMarkup:
    return _kb([
        [_ib("✅ Підтвердити запис", "bk:ok")],
        [_ib(BTN_BACK, f"bk:d:{day.isoformat()}"), _ib(BTN_MENU, "menu:main")],
    ])


def my_appointment_kb(appt_id: int) -> InlineKeyboardMarkup:
    return _kb([[_ib(BTN_CANCEL, f"cl:ask:{appt_id}")], [_ib(BTN_MENU, "menu:main")]])


def reminder_kb(appt_id: int) -> InlineKeyboardMarkup:
    return _kb([[_ib("❌ Скасувати запис", f"cl:ask:{appt_id}")]])


def cancel_confirm_kb(appt_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [_ib("✅ Так, скасувати", f"cl:yes:{appt_id}")],
        [_ib("↩️ Ні, залишити", "cl:no"), _ib(BTN_MENU, "menu:main")],
    ])


def book_again_kb() -> InlineKeyboardMarkup:
    return _kb([[_ib("✂️ Записатися на стрижку", "bk:start")], [_ib(BTN_MENU, "menu:main")]])


# =============================================================== адміністратор
def admin_menu_kb() -> InlineKeyboardMarkup:
    return _kb([
        [_ib("📋 Усі записи", "adm:all:0"), _ib("❌ Скасувати запис", "adm:cancel:0")],
        [_ib("📆 Сьогодні", "adm:today"), _ib("📆 Завтра", "adm:tomorrow")],
        [_ib("🔎 За датою", "adm:date"), _ib("👥 Контакти клієнтів", "adm:clients:0")],
        [_ib("🏖 Додати вихідний", "adm:dayoff"), _ib("⛔ Заблокувати час", "adm:block")],
        [_ib("➕ Додаткові години", "adm:extra"), _ib("⏱ Тривалість стрижки", "adm:duration")],
        [_ib("🗂 Вихідні та блокування", "adm:rules")],
        [_ib(BTN_MENU, "menu:main")],
    ])


def admin_back_kb(extra_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([_ib("⬅️ Адмін-меню", "adm:menu")])
    return _kb(rows)


def admin_calendar_footer() -> list[list[InlineKeyboardButton]]:
    return [[_ib("⬅️ Адмін-меню", "adm:menu")]]


def pagination_row(prefix: str, page: int, pages: int) -> list[InlineKeyboardButton]:
    prev_btn = _ib("◀️", f"{prefix}:{page - 1}") if page > 0 else _ib(" ", NOOP)
    next_btn = _ib("▶️", f"{prefix}:{page + 1}") if page < pages - 1 else _ib(" ", NOOP)
    return [prev_btn, _ib(f"{page + 1}/{pages}", NOOP), next_btn]


def appointments_list_kb(
    appts: list[Appointment], pagination_prefix: str | None, page: int, pages: int,
) -> InlineKeyboardMarkup:
    rows = [
        [_ib(f"❌ №{a.id} · {a.start_at:%d.%m %H:%M} · {a.name[:14]}", f"adm:cx:{a.id}")]
        for a in appts
    ]
    if pagination_prefix and pages > 1:
        rows.append(pagination_row(pagination_prefix, page, pages))
    return admin_back_kb(rows)


def admin_cancel_confirm_kb(appt_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [_ib("✅ Так, скасувати запис", f"adm:cy:{appt_id}")],
        [_ib("↩️ Ні", "adm:menu")],
    ])


def dayoff_confirm_kb(day: date) -> InlineKeyboardMarkup:
    iso = day.isoformat()
    return _kb([
        [_ib("✅ Скасувати записи й додати вихідний", f"adm:do1:{iso}")],
        [_ib("📌 Додати вихідний, записи залишити", f"adm:do0:{iso}")],
        [_ib("↩️ Не додавати", "adm:menu")],
    ])


def duration_kb(current: int) -> InlineKeyboardMarkup:
    presets = [30, 45, 60, 90, 120]
    buttons = [_ib(f"{'✅ ' if m == current else ''}{m} хв", f"adm:dur:{m}") for m in presets]
    rows = [buttons[:3], buttons[3:]]
    rows.append([_ib("✏️ Інше значення", "adm:durcustom")])
    return admin_back_kb(rows)


def rules_kb(
    days_off: list[date],
    blocks: list[tuple[int, date, int, int]],
    extras: list[tuple[int, date, int, int]],
) -> InlineKeyboardMarkup:
    rows = [[_ib(f"🗑 Вихідний {d:%d.%m.%Y}", f"adm:rm:do:{d.isoformat()}")] for d in days_off]
    rows += [[_ib(f"🗑 Блок {d:%d.%m} {fmt_range(s, e)}", f"adm:rm:bl:{rid}")] for rid, d, s, e in blocks]
    rows += [[_ib(f"🗑 Додатково {d:%d.%m} {fmt_range(s, e)}", f"adm:rm:ex:{rid}")] for rid, d, s, e in extras]
    return admin_back_kb(rows)
