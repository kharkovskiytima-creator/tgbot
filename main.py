"""Точка входу: python main.py"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from app.config import settings
from app.database import Database
from app.handlers import admin, booking, menu
from app.reminders import reminder_loop

logger = logging.getLogger("barbershop")


async def on_error(event: ErrorEvent) -> bool:
    """Глобальна обробка помилок: бот не падає, користувач бачить зрозуміле повідомлення."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and any(
        s in str(exc) for s in ("query is too old", "message is not modified",
                                "message to edit not found", "message can't be edited")
    ):
        return True

    logger.exception("Необроблена помилка", exc_info=exc)
    update = event.update
    with suppress(Exception):
        if update.callback_query:
            await update.callback_query.answer("⚠️ Сталася помилка. Спробуйте ще раз.", show_alert=True)
        elif update.message:
            await update.message.answer("⚠️ Сталася помилка. Спробуйте ще раз або натисніть /start")
    return True


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    db = Database(settings.db_path)
    await db.connect()
    logger.info("База даних: %s", settings.db_path)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db

    # Порядок важливий: меню → запис → адмін → запасний обробник.
    dp.include_router(menu.router)
    dp.include_router(booking.router)
    dp.include_router(admin.router)
    dp.include_router(menu.fallback_router)
    dp.errors.register(on_error)

    reminders_task = asyncio.create_task(reminder_loop(bot, db), name="reminders")
    try:
        me = await bot.get_me()
        logger.info("Бот запущено: @%s", me.username)
        await bot.set_my_commands([
            BotCommand(command="start", description="Головне меню"),
            BotCommand(command="menu", description="Показати меню"),
        ])
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        reminders_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminders_task
        await bot.session.close()
        await db.close()
        logger.info("Бот зупинено")


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
