"""Робота з SQLite.

Використовується стандартний sqlite3; блокуючі виклики виконуються у потоці
(asyncio.to_thread), тому бот не «зависає». Усі дані зберігаються у файлі і
не втрачаються після перезапуску.

Час зберігається як локальний київський рядок 'YYYY-MM-DD HH:MM'.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import DEFAULT_DURATION

DT_FMT = "%Y-%m-%d %H:%M"

BLOCKS = "blocked_slots"   # заблокований час
EXTRA = "extra_hours"      # додаткові години
_RANGE_TABLES = {BLOCKS, EXTRA}


def dt_to_str(dt: datetime) -> str:
    return dt.strftime(DT_FMT)


def str_to_dt(value: str) -> datetime:
    return datetime.strptime(value, DT_FMT)


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    username       TEXT,
    name           TEXT    NOT NULL,
    phone          TEXT    NOT NULL,
    start_at       TEXT    NOT NULL,
    end_at         TEXT    NOT NULL,
    duration       INTEGER NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'cancelled')),
    cancelled_by   TEXT,
    remind24_sent  INTEGER NOT NULL DEFAULT 0,
    remind2_sent   INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_start ON appointments (start_at, status);
CREATE INDEX IF NOT EXISTS idx_app_user  ON appointments (user_id, status);

CREATE TABLE IF NOT EXISTS days_off (
    day TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS blocked_slots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    day       TEXT    NOT NULL,
    start_min INTEGER NOT NULL,
    end_min   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blocked_day ON blocked_slots (day);

CREATE TABLE IF NOT EXISTS extra_hours (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    day       TEXT    NOT NULL,
    start_min INTEGER NOT NULL,
    end_min   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extra_day ON extra_hours (day);
"""


@dataclass(slots=True)
class Appointment:
    id: int
    user_id: int
    username: str | None
    name: str
    phone: str
    start_at: datetime
    end_at: datetime
    duration: int
    status: str
    cancelled_by: str | None
    remind24_sent: bool
    remind2_sent: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Appointment":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            username=row["username"],
            name=row["name"],
            phone=row["phone"],
            start_at=str_to_dt(row["start_at"]),
            end_at=str_to_dt(row["end_at"]),
            duration=row["duration"],
            status=row["status"],
            cancelled_by=row["cancelled_by"],
            remind24_sent=bool(row["remind24_sent"]),
            remind2_sent=bool(row["remind2_sent"]),
        )


@dataclass(slots=True)
class ClientInfo:
    user_id: int
    name: str
    phone: str
    username: str | None
    visits: int
    last_visit: datetime


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._thread_lock = threading.Lock()
        # Блокування, яке гарантує, що два клієнти не забронюють один час.
        self.booking_lock = asyncio.Lock()

    # ------------------------------------------------------------ інфраструктура
    async def connect(self) -> None:
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self) -> None:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('duration', ?)",
            (str(DEFAULT_DURATION),),
        )
        conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    def _run(self, sql: str, params: tuple[Any, ...], mode: str) -> Any:
        assert self._conn is not None, "База даних не підключена"
        with self._thread_lock:
            cur = self._conn.execute(sql, params)
            if mode == "all":
                return cur.fetchall()
            if mode == "one":
                return cur.fetchone()
            self._conn.commit()
            return cur.lastrowid, cur.rowcount

    async def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._run, sql, params, "all")

    async def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._run, sql, params, "one")

    async def _write(self, sql: str, params: tuple[Any, ...] = ()) -> tuple[int, int]:
        """Повертає (lastrowid, rowcount)."""
        return await asyncio.to_thread(self._run, sql, params, "write")

    # ------------------------------------------------------------ налаштування
    async def get_duration(self) -> int:
        row = await self._one("SELECT value FROM settings WHERE key = 'duration'")
        try:
            return int(row["value"]) if row else DEFAULT_DURATION
        except (TypeError, ValueError):
            return DEFAULT_DURATION

    async def set_duration(self, minutes: int) -> None:
        await self._write(
            "INSERT INTO settings (key, value) VALUES ('duration', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(minutes),),
        )

    # ------------------------------------------------------------ записи
    async def insert_appointment(
        self, *, user_id: int, username: str | None, name: str, phone: str,
        start_at: datetime, end_at: datetime, duration: int,
        remind24_sent: bool, remind2_sent: bool, created_at: datetime,
    ) -> int:
        rowid, _ = await self._write(
            "INSERT INTO appointments (user_id, username, name, phone, start_at, end_at, "
            "duration, remind24_sent, remind2_sent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, name, phone, dt_to_str(start_at), dt_to_str(end_at),
             duration, int(remind24_sent), int(remind2_sent), dt_to_str(created_at)),
        )
        return rowid

    async def get_appointment(self, appt_id: int) -> Appointment | None:
        row = await self._one("SELECT * FROM appointments WHERE id = ?", (appt_id,))
        return Appointment.from_row(row) if row else None

    async def get_user_upcoming(self, user_id: int, now: datetime) -> Appointment | None:
        """Активний майбутній запис клієнта (не скасований і ще не завершений)."""
        row = await self._one(
            "SELECT * FROM appointments WHERE user_id = ? AND status = 'active' "
            "AND end_at > ? ORDER BY start_at LIMIT 1",
            (user_id, dt_to_str(now)),
        )
        return Appointment.from_row(row) if row else None

    async def list_for_day(self, day: date) -> list[Appointment]:
        rows = await self._all(
            "SELECT * FROM appointments WHERE status = 'active' "
            "AND substr(start_at, 1, 10) = ? ORDER BY start_at",
            (day.isoformat(),),
        )
        return [Appointment.from_row(r) for r in rows]

    async def count_upcoming(self, now: datetime) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS c FROM appointments WHERE status = 'active' AND end_at > ?",
            (dt_to_str(now),),
        )
        return int(row["c"]) if row else 0

    async def list_upcoming(self, now: datetime, limit: int, offset: int) -> list[Appointment]:
        rows = await self._all(
            "SELECT * FROM appointments WHERE status = 'active' AND end_at > ? "
            "ORDER BY start_at LIMIT ? OFFSET ?",
            (dt_to_str(now), limit, offset),
        )
        return [Appointment.from_row(r) for r in rows]

    async def cancel_appointment(self, appt_id: int, by: str) -> bool:
        """Скасовує активний запис. True, якщо запис справді було скасовано."""
        _, count = await self._write(
            "UPDATE appointments SET status = 'cancelled', cancelled_by = ? "
            "WHERE id = ? AND status = 'active'",
            (by, appt_id),
        )
        return count > 0

    async def cancel_day(self, day: date, by: str) -> list[Appointment]:
        """Скасовує всі активні записи за день і повертає їх список."""
        appts = await self.list_for_day(day)
        cancelled = []
        for appt in appts:
            if await self.cancel_appointment(appt.id, by):
                cancelled.append(appt)
        return cancelled

    # ------------------------------------------------------------ нагадування
    async def pending_reminders(self, now: datetime) -> list[Appointment]:
        rows = await self._all(
            "SELECT * FROM appointments WHERE status = 'active' AND start_at > ? "
            "AND (remind24_sent = 0 OR remind2_sent = 0) ORDER BY start_at",
            (dt_to_str(now),),
        )
        return [Appointment.from_row(r) for r in rows]

    async def mark_reminders(self, appt_id: int, *, r24: bool | None = None,
                             r2: bool | None = None) -> None:
        if r24:
            await self._write("UPDATE appointments SET remind24_sent = 1 WHERE id = ?", (appt_id,))
        if r2:
            await self._write("UPDATE appointments SET remind2_sent = 1 WHERE id = ?", (appt_id,))

    # ------------------------------------------------------------ клієнти
    async def count_clients(self) -> int:
        row = await self._one("SELECT COUNT(DISTINCT user_id) AS c FROM appointments")
        return int(row["c"]) if row else 0

    async def list_clients(self, limit: int, offset: int) -> list[ClientInfo]:
        # Ім'я/телефон беруться з найновішого запису клієнта (bare columns + MAX у SQLite).
        rows = await self._all(
            "SELECT user_id, name, phone, username, MAX(start_at) AS last_visit, "
            "SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS visits "
            "FROM appointments GROUP BY user_id "
            "ORDER BY last_visit DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [
            ClientInfo(
                user_id=r["user_id"], name=r["name"], phone=r["phone"],
                username=r["username"], visits=int(r["visits"] or 0),
                last_visit=str_to_dt(r["last_visit"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------ вихідні
    async def is_day_off(self, day: date) -> bool:
        row = await self._one("SELECT 1 FROM days_off WHERE day = ?", (day.isoformat(),))
        return row is not None

    async def add_day_off(self, day: date) -> None:
        await self._write("INSERT OR IGNORE INTO days_off (day) VALUES (?)", (day.isoformat(),))

    async def remove_day_off(self, day: date) -> bool:
        _, count = await self._write("DELETE FROM days_off WHERE day = ?", (day.isoformat(),))
        return count > 0

    async def list_days_off(self, from_day: date, to_day: date | None = None) -> list[date]:
        if to_day is None:
            rows = await self._all(
                "SELECT day FROM days_off WHERE day >= ? ORDER BY day", (from_day.isoformat(),)
            )
        else:
            rows = await self._all(
                "SELECT day FROM days_off WHERE day >= ? AND day <= ? ORDER BY day",
                (from_day.isoformat(), to_day.isoformat()),
            )
        return [date.fromisoformat(r["day"]) for r in rows]

    # ------------------------------------------------------------ блокування / додаткові години
    async def add_range(self, table: str, day: date, start_min: int, end_min: int) -> int:
        assert table in _RANGE_TABLES
        rowid, _ = await self._write(
            f"INSERT INTO {table} (day, start_min, end_min) VALUES (?, ?, ?)",
            (day.isoformat(), start_min, end_min),
        )
        return rowid

    async def remove_range(self, table: str, range_id: int) -> bool:
        assert table in _RANGE_TABLES
        _, count = await self._write(f"DELETE FROM {table} WHERE id = ?", (range_id,))
        return count > 0

    async def list_ranges(self, table: str, day: date) -> list[tuple[int, int, int]]:
        """[(id, start_min, end_min)] для конкретного дня."""
        assert table in _RANGE_TABLES
        rows = await self._all(
            f"SELECT id, start_min, end_min FROM {table} WHERE day = ? ORDER BY start_min",
            (day.isoformat(),),
        )
        return [(r["id"], r["start_min"], r["end_min"]) for r in rows]

    async def list_ranges_from(self, table: str, from_day: date,
                               limit: int = 50) -> list[tuple[int, date, int, int]]:
        """[(id, day, start_min, end_min)] починаючи з дати."""
        assert table in _RANGE_TABLES
        rows = await self._all(
            f"SELECT id, day, start_min, end_min FROM {table} WHERE day >= ? "
            "ORDER BY day, start_min LIMIT ?",
            (from_day.isoformat(), limit),
        )
        return [(r["id"], date.fromisoformat(r["day"]), r["start_min"], r["end_min"]) for r in rows]

    async def busy_intervals(self, day: date) -> list[tuple[int, int]]:
        """Зайняті проміжки дня (хвилини від 00:00): активні записи + заблокований час."""
        rows = await self._all(
            "SELECT start_at, end_at FROM appointments "
            "WHERE status = 'active' AND substr(start_at, 1, 10) = ?",
            (day.isoformat(),),
        )
        intervals: list[tuple[int, int]] = []
        for r in rows:
            s, e = str_to_dt(r["start_at"]), str_to_dt(r["end_at"])
            end_min = e.hour * 60 + e.minute if e.date() == s.date() else 24 * 60
            intervals.append((s.hour * 60 + s.minute, end_min))
        blocks = await self.list_ranges(BLOCKS, day)
        intervals.extend((s, e) for _, s, e in blocks)
        return intervals
