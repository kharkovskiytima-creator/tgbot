"""Адмін-панель. Усі обробники доступні лише власнику (ADMIN_ID)."""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import (ADMIN_HORIZON_DAYS, MAX_DURATION, MIN_DURATION,
                        WORK_DAYS)
from app.database import BLOCKS, EXTRA, Appointment, Database
from app.keyboards import (admin_back_kb, admin_calendar_footer,
                           admin_cancel_confirm_kb, admin_menu_kb,
                           appointments_list_kb, build_calendar,
                           dayoff_confirm_kb, duration_kb, pagination_row,
                           rules_kb)
from app.states import AdminStates
from app.ui import AdminFilter, notify, render
from app.utils import (card_admin, esc, fmt_date_long, fmt_range,
                       now_local, parse_time_range, parse_year_month,
                       user_link)

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

PAGE_SIZE = 6
ADMIN_TEXT = "🛠 <b>Адмін-панель</b>\n\nОберіть дію:"

# Призначення календарів: префікс → заголовок
CAL_TITLES = {
    "av": "🔎 <b>Перегляд записів за датою</b>\n\nОберіть дату:",
    "do": "🏖 <b>Додати вихідний день</b>\n\nОберіть дату вихідного:",
    "bl": "⛔ <b>Блокування часу</b>\n\nОберіть дату:",
    "ex": "➕ <b>Додаткові години</b>\n\nОберіть дату, на яку потрібно відкрити додатковий час:",
}


def _pages(total: int) -> int:
    return max(1, math.ceil(total / PAGE_SIZE))


# ================================================================== меню
@router.callback_query(F.data == "adm:menu")
async def adm_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(cb, ADMIN_TEXT, admin_menu_kb())
    await cb.answer()


# ================================================================== списки записів
async def show_upcoming(event: Message | CallbackQuery, db: Database, page: int, mode: str) -> None:
    """mode = 'all' (перегляд) або 'cancel' (вибір запису для скасування)."""
    now = now_local()
    total = await db.count_upcoming(now)
    pages = _pages(total)
    page = min(max(page, 0), pages - 1)
    appts = await db.list_upcoming(now, PAGE_SIZE, page * PAGE_SIZE)

    if mode == "cancel":
        title = "❌ <b>Скасування запису</b>\nНатисніть на запис, який потрібно скасувати:"
    else:
        title = "📋 <b>Усі майбутні записи</b>"
    if not appts:
        text = f"{title}\n\n📭 Майбутніх записів немає."
    else:
        body = "\n\n".join(card_admin(a) for a in appts)
        text = f"{title}\nВсього: {total} · сторінка {page + 1}/{pages}\n\n{body}"
        if mode == "all":
            text += "\n\n<i>Щоб скасувати запис, натисніть ❌ нижче.</i>"
    prefix = "adm:all" if mode == "all" else "adm:cancel"
    await render(event, text, appointments_list_kb(appts, prefix, page, pages))


@router.callback_query(F.data.startswith("adm:all:"))
async def cb_all(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await show_upcoming(cb, db, int(cb.data.split(":")[2]), "all")
    await cb.answer()


@router.callback_query(F.data.startswith("adm:cancel:"))
async def cb_cancel_list(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await show_upcoming(cb, db, int(cb.data.split(":")[2]), "cancel")
    await cb.answer()


async def show_day(event: Message | CallbackQuery, db: Database, day: date) -> None:
    appts = await db.list_for_day(day)
    lines = [f"📆 <b>{fmt_date_long(day)}</b>"]
    if await db.is_day_off(day):
        lines.append("🏖 Позначено як вихідний")
    elif day.weekday() not in WORK_DAYS:
        lines.append("🌴 Вихідний за графіком")
    for _, s, e in await db.list_ranges(BLOCKS, day):
        lines.append(f"⛔ Заблоковано: {fmt_range(s, e)}")
    for _, s, e in await db.list_ranges(EXTRA, day):
        lines.append(f"➕ Додатково відкрито: {fmt_range(s, e)}")
    if appts:
        lines.append(f"\nЗаписів: {len(appts)}\n")
        lines.append("\n\n".join(card_admin(a, with_date=False) for a in appts))
    else:
        lines.append("\n📭 Записів на цей день немає.")
    await render(event, "\n".join(lines),
                 appointments_list_kb(appts, None, 0, 1) if appts else admin_back_kb())


@router.callback_query(F.data == "adm:today")
async def cb_today(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await show_day(cb, db, now_local().date())
    await cb.answer()


@router.callback_query(F.data == "adm:tomorrow")
async def cb_tomorrow(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await show_day(cb, db, now_local().date() + timedelta(days=1))
    await cb.answer()


# ================================================================== календарі адміністратора
async def show_admin_calendar(event: CallbackQuery, db: Database, prefix: str,
                              year: int, month: int) -> None:
    today = now_local().date()
    last = today + timedelta(days=ADMIN_HORIZON_DAYS)
    if prefix == "av":  # перегляд: можна й минулі дати
        first_allowed = today - timedelta(days=ADMIN_HORIZON_DAYS)
    else:
        first_allowed = today
    min_ym = (first_allowed.year, first_allowed.month)
    max_ym = (last.year, last.month)
    year, month = min(max((year, month), min_ym), max_ym)

    month_start = date(year, month, 1)
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    days_off = set(await db.list_days_off(month_start, next_month - timedelta(days=1))) if prefix == "do" else set()

    enabled: set[date] = set()
    d = month_start
    while d < next_month:
        if first_allowed <= d <= last and d not in days_off:
            enabled.add(d)
        d += timedelta(days=1)

    await render(event, CAL_TITLES[prefix],
                 build_calendar(prefix, year, month, enabled, min_ym, max_ym, admin_calendar_footer()))


async def open_calendar(cb: CallbackQuery, state: FSMContext, db: Database, prefix: str) -> None:
    await state.clear()
    today = now_local().date()
    await show_admin_calendar(cb, db, prefix, today.year, today.month)
    await cb.answer()


@router.callback_query(F.data == "adm:date")
async def cb_open_view(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await open_calendar(cb, state, db, "av")


@router.callback_query(F.data == "adm:dayoff")
async def cb_open_dayoff(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await open_calendar(cb, state, db, "do")


@router.callback_query(F.data == "adm:block")
async def cb_open_block(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await open_calendar(cb, state, db, "bl")


@router.callback_query(F.data == "adm:extra")
async def cb_open_extra(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await open_calendar(cb, state, db, "ex")


@router.callback_query(F.data.regexp(r"^(av|do|bl|ex):m:"))
async def cb_cal_month(cb: CallbackQuery, db: Database) -> None:
    prefix, _, raw = cb.data.split(":", 2)
    try:
        year, month = parse_year_month(raw)
    except ValueError:
        await cb.answer("Некоректна дата", show_alert=True)
        return
    await show_admin_calendar(cb, db, prefix, year, month)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^(av|do|bl|ex):d:"))
async def cb_cal_day(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    prefix, _, raw = cb.data.split(":", 2)
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        await cb.answer("Некоректна дата", show_alert=True)
        return

    if prefix == "av":
        await show_day(cb, db, day)

    elif prefix == "do":
        existing = await db.list_for_day(day)
        if existing:
            await render(
                cb,
                f"⚠️ На <b>{fmt_date_long(day)}</b> є активні записи ({len(existing)}).\n\n"
                "Що зробити?\n"
                "• «Скасувати записи» — клієнти отримають повідомлення про скасування;\n"
                "• «Записи залишити» — нові записи на цей день стануть недоступні, "
                "але наявні залишаться.",
                dayoff_confirm_kb(day),
            )
        else:
            await db.add_day_off(day)
            await render(cb, f"🏖 Вихідний додано: <b>{fmt_date_long(day)}</b>", admin_back_kb())

    elif prefix in ("bl", "ex"):
        await state.set_state(AdminStates.block_range if prefix == "bl" else AdminStates.extra_range)
        await state.update_data(day=day.isoformat())
        what = ("закрити для запису" if prefix == "bl" else "відкрити для запису")
        await render(
            cb,
            f"{'⛔ <b>Блокування часу</b>' if prefix == 'bl' else '➕ <b>Додаткові години</b>'}\n"
            f"📅 {fmt_date_long(day)}\n\n"
            f"Надішліть проміжок часу, який потрібно {what}, у форматі:\n"
            "<code>14:00-16:00</code>",
            admin_back_kb(),
        )
    await cb.answer()


# ================================================================== вихідний: підтвердження
@router.callback_query(F.data.regexp(r"^adm:do[01]:"))
async def cb_dayoff_confirm(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    _, action, raw = cb.data.split(":", 2)
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        await cb.answer("Некоректна дата", show_alert=True)
        return

    await db.add_day_off(day)
    cancelled_count = informed = 0
    if action == "do1":
        for appt in await db.cancel_day(day, "admin"):
            cancelled_count += 1
            if await notify_client_cancelled(bot, appt, reason="цього дня майстер не працює"):
                informed += 1

    text = f"🏖 Вихідний додано: <b>{fmt_date_long(day)}</b>"
    if action == "do1":
        text += f"\n🚫 Скасовано записів: {cancelled_count}\n📨 Клієнтів сповіщено: {informed}"
    await render(cb, text, admin_back_kb())
    await cb.answer()


# ================================================================== введення діапазону часу
async def _overlap_note(db: Database, day: date, start: int, end: int) -> str:
    conflicts = [
        a for a in await db.list_for_day(day)
        if (a.start_at.hour * 60 + a.start_at.minute) < end
        and (a.end_at.hour * 60 + a.end_at.minute) > start
    ]
    if not conflicts:
        return ""
    lines = "\n".join(f"• №{a.id} {a.start_at:%H:%M} — {esc(a.name)}, {esc(a.phone)}" for a in conflicts)
    return ("\n\n⚠️ <b>У цей проміжок уже є записи</b> (вони не скасовані автоматично):\n"
            f"{lines}\nСкасуйте їх вручну через «❌ Скасувати запис», якщо потрібно.")


@router.message(AdminStates.block_range, F.text)
async def input_block(message: Message, state: FSMContext, db: Database) -> None:
    rng = parse_time_range(message.text)
    data = await state.get_data()
    if rng is None:
        await message.answer("⚠️ Невірний формат. Приклад: <code>14:00-16:00</code> "
                             "(початок має бути раніше за кінець).")
        return
    try:
        day = date.fromisoformat(data["day"])
    except (KeyError, ValueError):
        await state.clear()
        await message.answer("Сесію завершено.", reply_markup=None)
        await message.answer(ADMIN_TEXT, reply_markup=admin_menu_kb())
        return
    start, end = rng
    await db.add_range(BLOCKS, day, start, end)
    await state.clear()
    note = await _overlap_note(db, day, start, end)
    await message.answer(
        f"⛔ Час заблоковано: <b>{fmt_date_long(day)}, {fmt_range(start, end)}</b>{note}",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminStates.extra_range, F.text)
async def input_extra(message: Message, state: FSMContext, db: Database) -> None:
    rng = parse_time_range(message.text)
    data = await state.get_data()
    if rng is None:
        await message.answer("⚠️ Невірний формат. Приклад: <code>10:00-14:00</code> "
                             "(початок має бути раніше за кінець).")
        return
    try:
        day = date.fromisoformat(data["day"])
    except (KeyError, ValueError):
        await state.clear()
        await message.answer(ADMIN_TEXT, reply_markup=admin_menu_kb())
        return
    start, end = rng
    await db.add_range(EXTRA, day, start, end)
    await state.clear()
    note = ""
    if await db.is_day_off(day):
        note = ("\n\n⚠️ На цю дату встановлено вихідний — додаткові години не діятимуть, "
                "доки вихідний не буде прибрано («🗂 Вихідні та блокування»).")
    await message.answer(
        f"➕ Додаткові години відкрито: <b>{fmt_date_long(day)}, {fmt_range(start, end)}</b>{note}",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminStates.block_range)
@router.message(AdminStates.extra_range)
@router.message(AdminStates.duration_input)
async def input_wrong_type(message: Message) -> None:
    await message.answer("⚠️ Надішліть відповідь текстом у потрібному форматі.")


# ================================================================== скасування запису адміністратором
async def notify_client_cancelled(bot: Bot, appt: Appointment, reason: str = "") -> bool:
    extra = f" Причина: {reason}." if reason else ""
    text = (
        "😔 <b>Ваш запис скасовано майстром</b>\n\n"
        f"📅 {fmt_date_long(appt.start_at.date())}, {appt.start_at:%H:%M}\n\n"
        f"Приносимо вибачення за незручності.{extra}\n"
        "Ви можете записатися на інший час через «✂️ Записатися на стрижку»."
    )
    return await notify(bot, appt.user_id, text)


@router.callback_query(F.data.startswith("adm:cx:"))
async def cb_cancel_ask(cb: CallbackQuery, db: Database) -> None:
    appt = await db.get_appointment(int(cb.data.split(":")[2]))
    if appt is None or appt.status != "active":
        await cb.answer("Запис не знайдено або вже скасовано", show_alert=True)
        return
    await render(cb, f"❓ <b>Скасувати цей запис?</b>\n\n{card_admin(appt)}\n\n"
                     "Клієнт отримає повідомлення про скасування.",
                 admin_cancel_confirm_kb(appt.id))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:cy:"))
async def cb_cancel_yes(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    appt = await db.get_appointment(int(cb.data.split(":")[2]))
    if appt is None or not await db.cancel_appointment(appt.id, "admin"):
        await cb.answer("Запис не знайдено або вже скасовано", show_alert=True)
        return
    informed = await notify_client_cancelled(bot, appt)
    note = ("📨 Клієнта сповіщено." if informed else
            f"⚠️ Не вдалося сповістити клієнта (можливо, він заблокував бота). "
            f"Зв’яжіться з ним: {esc(appt.phone)} · {user_link(appt.user_id, appt.username)}")
    await render(cb, f"✅ <b>Запис №{appt.id} скасовано.</b>\n{note}", admin_back_kb())
    await cb.answer()
    logger.info("Запис №%s скасовано адміністратором", appt.id)


# ================================================================== контакти клієнтів
@router.callback_query(F.data.startswith("adm:clients:"))
async def cb_clients(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    total = await db.count_clients()
    pages = _pages(total)
    page = min(max(int(cb.data.split(":")[2]), 0), pages - 1)
    clients = await db.list_clients(PAGE_SIZE + 4, page * (PAGE_SIZE + 4))
    pages = max(1, math.ceil(total / (PAGE_SIZE + 4)))

    if not clients:
        text = "👥 <b>Контакти клієнтів</b>\n\n📭 Клієнтів поки немає."
    else:
        body = "\n\n".join(
            f"👤 <b>{esc(c.name)}</b>\n📞 {esc(c.phone)}\n💬 {user_link(c.user_id, c.username)}\n"
            f"✂️ Активних записів: {c.visits} · останній: {c.last_visit:%d.%m.%Y}"
            for c in clients
        )
        text = f"👥 <b>Контакти клієнтів</b>\nВсього: {total} · сторінка {page + 1}/{pages}\n\n{body}"
    rows = [pagination_row("adm:clients", page, pages)] if pages > 1 else []
    await render(cb, text, admin_back_kb(rows))
    await cb.answer()


# ================================================================== тривалість стрижки
@router.callback_query(F.data == "adm:duration")
async def cb_duration(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    current = await db.get_duration()
    await render(
        cb,
        f"⏱ <b>Тривалість стрижки</b>\n\nПоточне значення: <b>{current} хв</b>\n\n"
        "Нова тривалість діє для нових записів; наявні записи не змінюються.",
        duration_kb(current),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:dur:"))
async def cb_duration_set(cb: CallbackQuery, db: Database) -> None:
    minutes = int(cb.data.split(":")[2])
    if not MIN_DURATION <= minutes <= MAX_DURATION:
        await cb.answer("Недопустиме значення", show_alert=True)
        return
    await db.set_duration(minutes)
    await render(cb, f"✅ Тривалість стрижки змінено: <b>{minutes} хв</b>", duration_kb(minutes))
    await cb.answer("Збережено")


@router.callback_query(F.data == "adm:durcustom")
async def cb_duration_custom(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.duration_input)
    await render(
        cb,
        f"✏️ Введіть тривалість стрижки в хвилинах (число від {MIN_DURATION} до {MAX_DURATION}):",
        admin_back_kb(),
    )
    await cb.answer()


@router.message(AdminStates.duration_input, F.text)
async def input_duration(message: Message, state: FSMContext, db: Database) -> None:
    raw = message.text.strip()
    if not raw.isdigit() or not MIN_DURATION <= int(raw) <= MAX_DURATION:
        await message.answer(f"⚠️ Введіть ціле число від {MIN_DURATION} до {MAX_DURATION}.")
        return
    minutes = int(raw)
    await db.set_duration(minutes)
    await state.clear()
    await message.answer(f"✅ Тривалість стрижки змінено: <b>{minutes} хв</b>", reply_markup=admin_back_kb())


# ================================================================== вихідні, блокування, додаткові години
async def show_rules(event: CallbackQuery, db: Database) -> None:
    today = now_local().date()
    days_off = (await db.list_days_off(today))[:10]
    blocks = (await db.list_ranges_from(BLOCKS, today))[:10]
    extras = (await db.list_ranges_from(EXTRA, today))[:10]

    if not (days_off or blocks or extras):
        text = "🗂 <b>Вихідні та блокування</b>\n\nДодаткових налаштувань розкладу немає."
    else:
        lines = ["🗂 <b>Вихідні та блокування</b>", "<i>Натисніть на пункт, щоб видалити його.</i>\n"]
        if days_off:
            lines.append("🏖 <b>Вихідні дні:</b>")
            lines += [f"• {fmt_date_long(d)}" for d in days_off]
        if blocks:
            lines.append("\n⛔ <b>Заблокований час:</b>")
            lines += [f"• {fmt_date_long(d)}, {fmt_range(s, e)}" for _, d, s, e in blocks]
        if extras:
            lines.append("\n➕ <b>Додаткові години:</b>")
            lines += [f"• {fmt_date_long(d)}, {fmt_range(s, e)}" for _, d, s, e in extras]
        text = "\n".join(lines) + "\n\n<i>Показано найближчі записи (до 10 у кожній категорії).</i>"
    await render(event, text, rules_kb(days_off, blocks, extras))


@router.callback_query(F.data == "adm:rules")
async def cb_rules(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await show_rules(cb, db)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_remove_rule(cb: CallbackQuery, db: Database) -> None:
    _, _, kind, raw = cb.data.split(":", 3)
    ok = False
    try:
        if kind == "do":
            ok = await db.remove_day_off(date.fromisoformat(raw))
        elif kind == "bl":
            ok = await db.remove_range(BLOCKS, int(raw))
        elif kind == "ex":
            ok = await db.remove_range(EXTRA, int(raw))
    except ValueError:
        ok = False
    await cb.answer("🗑 Видалено" if ok else "Уже видалено")
    await show_rules(cb, db)
