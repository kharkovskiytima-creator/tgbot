"""Налаштування проєкту: читаються зі змінних середовища (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---------- Фіксований графік роботи ----------
TIMEZONE_NAME = "Europe/Kyiv"
WORK_DAYS = (0, 1, 2, 3, 4)          # понеділок – п'ятниця (0 = понеділок)
WORK_START_MIN = 10 * 60             # 10:00
WORK_END_MIN = 20 * 60               # 20:00

# ---------- Тривалість стрижки ----------
DEFAULT_DURATION = 60                # початкове значення, хвилин
MIN_DURATION = 15
MAX_DURATION = 240

# ---------- Горизонти планування ----------
BOOKING_HORIZON_DAYS = 60            # на скільки днів уперед може записатися клієнт
ADMIN_HORIZON_DAYS = 365             # на скільки днів уперед адміністратор налаштовує розклад


class ConfigError(SystemExit):
    """Помилка конфігурації: зупиняє програму з зрозумілим повідомленням."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    db_path: Path
    tz: ZoneInfo
    shop_name: str
    address: str
    phone: str
    instagram: str
    map_url: str
    min_lead_minutes: int

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or ":" not in token:
            raise ConfigError(
                "❌ Не вказано BOT_TOKEN у файлі .env (або він має неправильний формат).\n"
                "   Отримайте токен у @BotFather і впишіть його у .env"
            )

        admin_raw = os.getenv("ADMIN_ID", "").strip()
        if not admin_raw.lstrip("-").isdigit():
            raise ConfigError(
                "❌ Не вказано ADMIN_ID у файлі .env (потрібне число).\n"
                "   Свій Telegram ID можна дізнатися у бота @userinfobot"
            )

        try:
            tz = ZoneInfo(TIMEZONE_NAME)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(
                "❌ Не знайдено базу часових поясів. Виконайте: pip install tzdata"
            ) from exc

        db_path = Path(os.getenv("DB_PATH", "data/barbershop.db").strip() or "data/barbershop.db")
        if not db_path.is_absolute():
            db_path = BASE_DIR / db_path

        lead_raw = os.getenv("BOOKING_MIN_LEAD_MINUTES", "30").strip()
        lead = int(lead_raw) if lead_raw.isdigit() else 30

        return cls(
            bot_token=token,
            admin_id=int(admin_raw),
            db_path=db_path,
            tz=tz,
            shop_name=os.getenv("SHOP_NAME", "Барбершоп").strip() or "Барбершоп",
            address=os.getenv("SHOP_ADDRESS", "").strip(),
            phone=os.getenv("SHOP_PHONE", "").strip(),
            instagram=os.getenv("SHOP_INSTAGRAM", "").strip(),
            map_url=os.getenv("SHOP_MAP_URL", "").strip(),
            min_lead_minutes=lead,
        )


settings = Settings.from_env()
