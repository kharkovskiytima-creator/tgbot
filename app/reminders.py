"""Фонові нагадування клієнтам: за 24 години та за 2 години до запису."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramNetworkError, TelegramRetryAfter)

from app.database import Appointment, Database
from app.keyboards import reminder_kb
from app.utils import card_client, now_local

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


def _text_24h(a: Appointment) -> str:
    return (
        "⏰ <b>Нагадування про запис</b>\n\n"
        "Чекаємо на вас завтра! Ваш запис відбудеться через 24 години.\n\n"
        f"{card_client(a)}"
    )


def _text_2h(a: Appointment) -> str:
    return (
        "⏰ <b>Скоро ваша стрижка!</b>\n\n"
        "Нагадуємо: ваш запис відбудеться через 2 години.\n\n"
        f"{card_client(a)}"
    )


async def process_reminders(bot: Bot, db: Database) -> None:
    now = now_local()
    for appt in await db.pending_reminders(now):
        until = appt.start_at - now

        if not appt.remind2_sent and until <= timedelta(hours=2):
            kind, text = "2h", _text_2h(appt)
        elif not appt.remind24_sent and timedelta(hours=2) < until <= timedelta(hours=24):
            kind, text = "24h", _text_24h(appt)
        else:
            if not appt.remind24_sent and until <= timedelta(hours=2):
                # Бот був вимкнений — 24-годинне нагадування вже не актуальне.
                await db.mark_reminders(appt.id, r24=True)
            continue

        try:
            await bot.send_message(appt.user_id, text, reply_markup=reminder_kb(appt.id))
        except TelegramRetryAfter as exc:
            logger.warning("Ліміт Telegram, чекаємо %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            continue
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            # Клієнт заблокував бота / чат недоступний — повторювати немає сенсу.
            logger.warning("Нагадування для запису №%s не доставлено: %s", appt.id, exc)
        except TelegramNetworkError as exc:
            logger.warning("Мережева помилка під час нагадування №%s: %s", appt.id, exc)
            continue

        if kind == "2h":
            await db.mark_reminders(appt.id, r24=True, r2=True)
        else:
            await db.mark_reminders(appt.id, r24=True)
        logger.info("Нагадування %s для запису №%s оброблено", kind, appt.id)


async def reminder_loop(bot: Bot, db: Database) -> None:
    logger.info("Службу нагадувань запущено")
    while True:
        try:
            await process_reminders(bot, db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл не має падати
            logger.exception("Помилка у службі нагадувань")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
