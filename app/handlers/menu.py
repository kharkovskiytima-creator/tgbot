"""Головне меню, /start, «Мій запис», скасування запису клієнтом, контакти."""
from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from app.config import settings
from app.database import Database
from app.handlers.booking import begin_booking
from app.keyboards import (NOOP, admin_menu_kb, book_again_kb,
                           cancel_confirm_kb, main_menu_kb, my_appointment_kb)
from app.texts import (BTN_ADMIN, BTN_BOOK, BTN_CANCEL, BTN_CONTACTS, BTN_MENU,
                       BTN_MY)
from app.ui import AdminFilter, is_admin, notify, render
from app.utils import card_admin, card_client, esc, now_local, work_hours_text

logger = logging.getLogger(__name__)

# Цей роутер підключається ПЕРШИМ: кнопки меню працюють у будь-якому стані
# (і скидають незавершений діалог).
router = Router(name="menu")
# Цей — ОСТАННІМ: відповідає на «зайвий» текст, коли діалог не активний.
fallback_router = Router(name="fallback")

ADMIN_TEXT = "🛠 <b>Адмін-панель</b>\n\nОберіть дію:"


def greeting_text() -> str:
    return (
        f"👋 <b>Вітаємо у «{esc(settings.shop_name)}»!</b>\n\n"
        "Тут можна легко записатися на чоловічу стрижку — без дзвінків та очікування.\n\n"
        f"🕙 <b>Графік:</b> {work_hours_text()}\n"
        "🌴 Субота та неділя — вихідні\n\n"
        "Оберіть дію в меню нижче ⬇️"
    )


def contacts_text() -> str:
    lines = [f"📞 <b>Контакти — {esc(settings.shop_name)}</b>\n"]
    if settings.address:
        lines.append(f"📍 <b>Адреса:</b> {esc(settings.address)}")
    if settings.phone:
        lines.append(f"☎️ <b>Телефон:</b> {esc(settings.phone)}")
    if settings.instagram:
        lines.append(f"📷 <b>Instagram:</b> {esc(settings.instagram)}")
    if settings.map_url:
        lines.append(f'🗺 <a href="{esc(settings.map_url)}">Відкрити на карті</a>')
    lines.append(f"🕙 <b>Графік:</b> {work_hours_text()}")
    lines.append("🌴 Субота та неділя — вихідні")
    return "\n".join(lines)


async def send_main_menu(message: Message, text: str | None = None) -> None:
    await message.answer(text or greeting_text(), reply_markup=main_menu_kb(is_admin(message.from_user.id)))


# ------------------------------------------------------------------ команди та головне меню
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_main_menu(message)


@router.message(Command("menu"))
@router.message(F.text == BTN_MENU)
async def to_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await send_main_menu(message, "🏠 <b>Головне меню</b>\n\nОберіть дію ⬇️")


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(cb.message, Message):
        with suppress(TelegramAPIError):
            await cb.message.delete()
    await cb.bot.send_message(
        cb.from_user.id, "🏠 <b>Головне меню</b>\n\nОберіть дію ⬇️",
        reply_markup=main_menu_kb(is_admin(cb.from_user.id)),
    )
    await cb.answer()


@router.callback_query(F.data == NOOP)
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ------------------------------------------------------------------ кнопки меню клієнта
@router.message(F.text == BTN_BOOK)
async def menu_book(message: Message, state: FSMContext, db: Database) -> None:
    await begin_booking(message, state, db)


@router.message(F.text == BTN_MY)
async def menu_my(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    appt = await db.get_user_upcoming(message.from_user.id, now_local())
    if appt is None:
        await message.answer(
            "📭 <b>У вас поки немає активних записів.</b>\n\nХочете записатися на стрижку?",
            reply_markup=book_again_kb(),
        )
        return
    await message.answer(f"✂️ <b>Ваш запис</b>\n\n{card_client(appt)}", reply_markup=my_appointment_kb(appt.id))


@router.message(F.text == BTN_CANCEL)
async def menu_cancel(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    appt = await db.get_user_upcoming(message.from_user.id, now_local())
    if appt is None:
        await message.answer("📭 У вас немає активних записів, які можна скасувати.", reply_markup=book_again_kb())
        return
    await message.answer(
        f"❓ <b>Скасувати цей запис?</b>\n\n{card_client(appt)}",
        reply_markup=cancel_confirm_kb(appt.id),
    )


@router.message(F.text == BTN_CONTACTS)
async def menu_contacts(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(contacts_text(), link_preview_options=LinkPreviewOptions(is_disabled=True))


# ------------------------------------------------------------------ адмін-панель (вхід)
@router.message(AdminFilter(), F.text == BTN_ADMIN)
@router.message(AdminFilter(), Command("admin"))
async def open_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(ADMIN_TEXT, reply_markup=admin_menu_kb())


# ------------------------------------------------------------------ скасування запису клієнтом
async def _own_active(cb: CallbackQuery, db: Database, appt_id: int):
    appt = await db.get_appointment(appt_id)
    if (appt is None or appt.user_id != cb.from_user.id
            or appt.status != "active" or appt.end_at <= now_local()):
        return None
    return appt


@router.callback_query(F.data.startswith("cl:ask:"))
async def cb_cancel_ask(cb: CallbackQuery, db: Database) -> None:
    appt = await _own_active(cb, db, int(cb.data.split(":")[2]))
    if appt is None:
        await cb.answer("Запис не знайдено або його вже скасовано", show_alert=True)
        return
    await render(cb, f"❓ <b>Скасувати цей запис?</b>\n\n{card_client(appt)}", cancel_confirm_kb(appt.id))
    await cb.answer()


@router.callback_query(F.data.startswith("cl:yes:"))
async def cb_cancel_yes(cb: CallbackQuery, db: Database, bot: Bot) -> None:
    appt = await _own_active(cb, db, int(cb.data.split(":")[2]))
    if appt is None or not await db.cancel_appointment(appt.id, "client"):
        await cb.answer("Запис не знайдено або його вже скасовано", show_alert=True)
        return
    await render(
        cb,
        "✅ <b>Запис скасовано.</b>\n\nМайстер отримав повідомлення. "
        "Будемо раді бачити вас в інший час!",
        book_again_kb(),
    )
    await cb.answer()
    await notify(bot, settings.admin_id, "🚫 <b>Клієнт скасував запис</b>\n\n" + card_admin(appt))
    logger.info("Запис №%s скасовано клієнтом", appt.id)


@router.callback_query(F.data == "cl:no")
async def cb_cancel_no(cb: CallbackQuery, db: Database) -> None:
    appt = await db.get_user_upcoming(cb.from_user.id, now_local())
    if appt is None:
        await render(cb, "ℹ️ Активних записів немає.", book_again_kb())
    else:
        await render(cb, f"👍 Добре, запис залишається в силі.\n\n{card_client(appt)}",
                     my_appointment_kb(appt.id))
    await cb.answer()


# ------------------------------------------------------------------ запасний обробник
@fallback_router.message(StateFilter(None), F.text)
async def unknown_text(message: Message) -> None:
    await message.answer("🤔 Не зрозумів вас. Скористайтеся кнопками меню нижче ⬇️",
                         reply_markup=main_menu_kb(is_admin(message.from_user.id)))
