"""Спільні UI-помічники."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import (CallbackQuery, InlineKeyboardMarkup, Message,
                           TelegramObject)

from app.config import settings

logger = logging.getLogger(__name__)


class AdminFilter(BaseFilter):
    """Пропускає лише власника бота (за Telegram ID з .env)."""

    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and user.id == settings.admin_id)


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id == settings.admin_id


async def render(event: Message | CallbackQuery, text: str,
                 markup: InlineKeyboardMarkup | None = None) -> Message | None:
    """Для callback — редагує повідомлення, для message — надсилає нове."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message):
            try:
                return await msg.edit_text(text, reply_markup=markup)  # type: ignore[return-value]
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc):
                    return msg
                logger.debug("Не вдалося відредагувати повідомлення: %s", exc)
            return await msg.answer(text, reply_markup=markup)
        return await event.bot.send_message(event.from_user.id, text, reply_markup=markup)
    return await event.answer(text, reply_markup=markup)


async def notify(bot: Bot, chat_id: int, text: str,
                 markup: InlineKeyboardMarkup | None = None) -> bool:
    """Надсилає повідомлення і не падає при помилці. True — якщо доставлено."""
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
        return True
    except TelegramAPIError as exc:
        logger.warning("Не вдалося надіслати повідомлення %s: %s", chat_id, exc)
        return False
