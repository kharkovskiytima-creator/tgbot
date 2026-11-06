"""Процес запису клієнта: дата → час → ім'я → телефон → підтвердження."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.config import BOOKING_HORIZON_DAYS, settings
from app.database import Database
from app.keyboards import (build_calendar, confirm_kb, main_menu_kb,
                           menu_footer, my_appointment_kb, name_step_kb,
                           phone_step_kb, times_kb)
from app.schedule import (AlreadyBooked, SlotUnavailable, book_slot,
                          days_with_slots, get_free_slots)
from app.states import BookingStates
from app.texts import BTN_BACK
from app.ui import is_admin, notify, render
from app.utils import (card_admin, card_client, esc, fmt_date_long,
                       normalize_name, normalize_phone, now_local,
                       parse_year_month)

logger = logging.getLogger(__name__)
router = Router(name="booking")


# ------------------------------------------------------------------ допоміжні
async def show_calendar(event: Message | CallbackQuery, db: Database,
                        year: int, month: int) -> None:
    now = now_local()
    min_ym = (now.year, now.month)
    last = now.date() + timedelta(days=BOOKING_HORIZON_DAYS)
    max_ym = (last.year, last.month)
    # Не виходимо за межі дозволених місяців.
    year, month = min(max((year, month), min_ym), max_ym)

    enabled = await days_with_slots(db, year, month, now, settings.min_lead_minutes)
    text = (
        "📅 <b>Оберіть дату</b>\n\n"
        "Натисніть на число, щоб побачити вільні години.\n"
        "<i>Символ «·» — вихідний або немає вільного часу.</i>"
    )
    kb = build_calendar("bk", year, month, enabled, min_ym, max_ym, footer=menu_footer())
    await render(event, text, kb)


async def show_times(event: Message | CallbackQuery, db: Database, day: date) -> bool:
    """Показує вільний час на день. False — якщо вільних годин немає."""
    slots = await get_free_slots(db, day, now_local(), settings.min_lead_minutes)
    if not slots:
        return False
    text = f"🕐 <b>Оберіть зручний час</b>\n\n📅 {fmt_date_long(day)}"
    await render(event, text, times_kb(day, slots))
    return True


async def begin_booking(event: Message | CallbackQuery, state: FSMContext, db: Database) -> None:
    """Початок запису (кнопка меню або інлайн-кнопка)."""
    await state.clear()
    existing = await db.get_user_upcoming(event.from_user.id, now_local())
    if existing:
        await render(
            event,
            "ℹ️ <b>У вас уже є активний запис</b>\n\n"
            f"{card_client(existing)}\n\n"
            "Щоб записатися на інший час, спочатку скасуйте поточний запис.",
            my_appointment_kb(existing.id),
        )
        return
    now = now_local()
    await show_calendar(event, db, now.year, now.month)


async def _ask_name(message: Message, state: FSMContext) -> None:
    await state.set_state(BookingStates.name)
    await message.answer(
        "👤 <b>Як до вас звертатися?</b>\n\nВведіть ваше ім’я:",
        reply_markup=name_step_kb(),
    )


async def _back_to_times(message: Message, state: FSMContext, db: Database, day: date) -> None:
    """Повернення з кроку введення даних до вибору часу."""
    await state.clear()
    await message.answer("↩️ Повертаємось до вибору часу…",
                         reply_markup=main_menu_kb(is_admin(message.from_user.id)))
    if not await show_times(message, db, day):
        await show_calendar(message, db, day.year, day.month)


# ------------------------------------------------------------------ вибір дати та часу
@router.callback_query(F.data == "bk:start")
async def cb_start(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await begin_booking(cb, state, db)
    await cb.answer()


@router.callback_query(F.data.startswith("bk:m:"))
async def cb_month(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    try:
        year, month = parse_year_month(cb.data.split(":", 2)[2])
    except ValueError:
        await cb.answer("Некоректна дата", show_alert=True)
        return
    await show_calendar(cb, db, year, month)
    await cb.answer()


@router.callback_query(F.data.startswith("bk:d:"))
async def cb_day(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    try:
        day = date.fromisoformat(cb.data.split(":", 2)[2])
    except ValueError:
        await cb.answer("Некоректна дата", show_alert=True)
        return
    if not await show_times(cb, db, day):
        await cb.answer("На цю дату вільних годин немає", show_alert=True)
        await show_calendar(cb, db, day.year, day.month)
        return
    await cb.answer()


@router.callback_query(F.data.startswith("bk:t:"))
async def cb_time(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    try:
        _, _, iso, compact = cb.data.split(":")
        day = date.fromisoformat(iso)
        hhmm = f"{compact[:2]}:{compact[2:]}"
    except (ValueError, IndexError):
        await cb.answer("Некоректні дані", show_alert=True)
        return

    now = now_local()
    if await db.get_user_upcoming(cb.from_user.id, now):
        await cb.answer("У вас уже є активний запис", show_alert=True)
        await begin_booking(cb, state, db)
        return

    if hhmm not in await get_free_slots(db, day, now, settings.min_lead_minutes):
        await cb.answer("Цей час щойно зайняли 😔 Оберіть інший.", show_alert=True)
        if not await show_times(cb, db, day):
            await show_calendar(cb, db, day.year, day.month)
        return

    await state.set_state(BookingStates.name)
    await state.update_data(day=day.isoformat(), time=hhmm)
    await render(cb, f"✅ <b>Ви обрали:</b>\n📅 {fmt_date_long(day)}\n🕙 {hhmm}")
    await cb.answer()
    await _ask_name(cb.message, state)


# ------------------------------------------------------------------ ім'я
@router.message(BookingStates.name, F.text == BTN_BACK)
async def name_back(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    try:
        day = date.fromisoformat(data["day"])
    except (KeyError, ValueError):
        await state.clear()
        await message.answer("Сесію запису завершено. Оберіть дію в меню.",
                             reply_markup=main_menu_kb(is_admin(message.from_user.id)))
        return
    await _back_to_times(message, state, db, day)


@router.message(BookingStates.name, F.text)
async def name_input(message: Message, state: FSMContext) -> None:
    name = normalize_name(message.text)
    if name is None:
        await message.answer(
            "⚠️ Ім’я має містити від 2 до 50 літер (без цифр і спеціальних символів).\n"
            "Спробуйте ще раз:"
        )
        return
    await state.update_data(name=name)
    await state.set_state(BookingStates.phone)
    await message.answer(
        f"Приємно познайомитись, <b>{esc(name)}</b>! 🙌\n\n"
        "📞 Тепер надішліть свій номер телефону — натисніть кнопку нижче "
        "«📱 Надіслати номер телефону».",
        reply_markup=phone_step_kb(),
    )


@router.message(BookingStates.name)
async def name_wrong_type(message: Message) -> None:
    await message.answer("⚠️ Будь ласка, введіть ім’я текстом.")


# ------------------------------------------------------------------ телефон
@router.message(BookingStates.phone, F.contact)
async def phone_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact
    if contact.user_id != message.from_user.id:
        await message.answer(
            "⚠️ Потрібно надіслати <b>саме ваш</b> номер.\n"
            "Натисніть кнопку «📱 Надіслати номер телефону»."
        )
        return
    phone = normalize_phone(contact.phone_number)
    if phone is None:
        await message.answer("⚠️ Не вдалося розпізнати номер. Спробуйте ще раз через кнопку.")
        return

    data = await state.get_data()
    try:
        day = date.fromisoformat(data["day"])
        hhmm = data["time"]
        name = data["name"]
    except (KeyError, ValueError):
        await state.clear()
        await message.answer("Сесію запису завершено. Почніть заново з головного меню.",
                             reply_markup=main_menu_kb(is_admin(message.from_user.id)))
        return

    await state.update_data(phone=phone)
    await state.set_state(BookingStates.confirm)
    await message.answer("✅ Номер отримано!", reply_markup=main_menu_kb(is_admin(message.from_user.id)))
    await message.answer(
        "📝 <b>Перевірте дані запису</b>\n\n"
        f"📅 <b>Дата:</b> {fmt_date_long(day)}\n"
        f"🕙 <b>Час:</b> {hhmm}\n"
        f"👤 <b>Ім’я:</b> {esc(name)}\n"
        f"📞 <b>Телефон:</b> {esc(phone)}\n\n"
        "Усе вірно? Натисніть «Підтвердити запис».",
        reply_markup=confirm_kb(day),
    )


@router.message(BookingStates.phone, F.text == BTN_BACK)
async def phone_back(message: Message, state: FSMContext) -> None:
    await _ask_name(message, state)


@router.message(BookingStates.phone)
async def phone_wrong(message: Message) -> None:
    await message.answer(
        "⚠️ Номер потрібно надіслати кнопкою «📱 Надіслати номер телефону» "
        "(вона під полем введення).",
        reply_markup=phone_step_kb(),
    )


# ------------------------------------------------------------------ підтвердження
@router.callback_query(F.data == "bk:ok")
async def cb_confirm(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    try:
        day = date.fromisoformat(data["day"])
        hhmm, name, phone = data["time"], data["name"], data["phone"]
    except (KeyError, ValueError):
        await state.clear()
        await render(cb, "⌛ Сесія запису застаріла. Будь ласка, почніть заново.",
                     InlineKeyboardMarkup(inline_keyboard=menu_footer()))
        await cb.answer()
        return

    user = cb.from_user
    try:
        appt = await book_slot(
            db, user_id=user.id, username=user.username, name=name, phone=phone,
            day=day, hhmm=hhmm, now=now_local(), min_lead_minutes=settings.min_lead_minutes,
        )
    except AlreadyBooked:
        await state.clear()
        await cb.answer("У вас уже є активний запис", show_alert=True)
        await begin_booking(cb, state, db)
        return
    except SlotUnavailable:
        await state.clear()
        await cb.answer("Цей час щойно зайняли 😔 Оберіть інший.", show_alert=True)
        if not await show_times(cb, db, day):
            await show_calendar(cb, db, day.year, day.month)
        return

    await state.clear()
    await render(cb, "✅ Запис підтверджено!")
    await cb.answer()

    where = f"\n📍 <b>Адреса:</b> {esc(settings.address)}" if settings.address else ""
    await cb.message.answer(
        "🎉 <b>Запис підтверджено!</b>\n\n"
        f"{card_client(appt)}{where}\n\n"
        "Ми нагадаємо вам про візит за 24 години та за 2 години.\n"
        "Переглянути або скасувати запис можна в меню. До зустрічі! ✂️",
        reply_markup=main_menu_kb(is_admin(user.id)),
    )

    await notify(bot, settings.admin_id, "🔔 <b>Новий запис!</b>\n\n" + card_admin(appt))
    logger.info("Новий запис №%s: %s %s", appt.id, appt.start_at, appt.name)

