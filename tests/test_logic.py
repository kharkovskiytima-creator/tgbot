"""Тести логіки (без Telegram): розклад, бронювання, нагадування, збереження даних.

Запуск:  python -m unittest discover -s tests -v
Для reminders/keyboards використовуються мінімальні заглушки aiogram, тож тести
працюють навіть без встановленого aiogram.
"""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("ADMIN_ID", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- заглушки aiogram (лише якщо бібліотеки немає) ----
try:
    import aiogram  # noqa: F401
except ImportError:
    class _Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Err(Exception):
        pass

    class _Retry(_Err):
        retry_after = 1

    aio = types.ModuleType("aiogram")
    aio.Bot = _Obj
    exc = types.ModuleType("aiogram.exceptions")
    exc.TelegramBadRequest = type("TelegramBadRequest", (_Err,), {})
    exc.TelegramForbiddenError = type("TelegramForbiddenError", (_Err,), {})
    exc.TelegramNetworkError = type("TelegramNetworkError", (_Err,), {})
    exc.TelegramRetryAfter = _Retry
    ty = types.ModuleType("aiogram.types")
    for n in ("InlineKeyboardButton", "InlineKeyboardMarkup", "KeyboardButton", "ReplyKeyboardMarkup"):
        setattr(ty, n, type(n, (_Obj,), {}))
    sys.modules.update({"aiogram": aio, "aiogram.exceptions": exc, "aiogram.types": ty})

from app import schedule  # noqa: E402
from app.database import BLOCKS, EXTRA, Database  # noqa: E402
from app.keyboards import build_calendar, times_kb  # noqa: E402
from app.schedule import AlreadyBooked, SlotUnavailable, book_slot, get_free_slots  # noqa: E402
from app.utils import (normalize_name, normalize_phone, parse_time_range,  # noqa: E402
                       parse_year_month)

# Фіксований «зараз»: понеділок 2026-11-09 09:00
NOW = datetime(2026, 11, 9, 9, 0)
MON = date(2026, 11, 9)
TUE = date(2026, 11, 10)
SAT = date(2026, 11, 14)
SUN = date(2026, 11, 15)


def run(coro):
    return asyncio.run(coro)


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        self.db = Database(self.path)
        run(self._connect())

    async def _connect(self):
        await self.db.connect()

    def tearDown(self):
        run(self.db.close())
        self.tmp.cleanup()

    async def book(self, uid, day, hhmm, now=NOW, name="Іван", phone="+380501234567"):
        return await book_slot(self.db, user_id=uid, username=None, name=name, phone=phone,
                               day=day, hhmm=hhmm, now=now)


class TestSlots(DbTestCase):
    def test_weekday_default_slots(self):
        slots = run(get_free_slots(self.db, TUE, NOW))
        self.assertEqual(slots[0], "10:00")
        self.assertEqual(slots[-1], "19:00")   # остання стрижка закінчується о 20:00
        self.assertEqual(len(slots), 10)

    def test_weekend_empty(self):
        self.assertEqual(run(get_free_slots(self.db, SAT, NOW)), [])
        self.assertEqual(run(get_free_slots(self.db, SUN, NOW)), [])

    def test_past_and_far_future_empty(self):
        self.assertEqual(run(get_free_slots(self.db, MON - timedelta(days=1), NOW)), [])
        self.assertEqual(run(get_free_slots(self.db, MON + timedelta(days=200), NOW)), [])

    def test_today_hides_past_hours_and_lead(self):
        now = datetime(2026, 11, 9, 13, 10)
        slots = run(get_free_slots(self.db, MON, now, min_lead_minutes=30))
        self.assertEqual(slots[0], "14:00")     # 13:00 уже минуло, 13:40 — це ще не слот
        slots = run(get_free_slots(self.db, MON, datetime(2026, 11, 9, 21, 0)))
        self.assertEqual(slots, [])

    def test_booking_removes_slot(self):
        run(self.book(10, TUE, "12:00"))
        self.assertNotIn("12:00", run(get_free_slots(self.db, TUE, NOW)))

    def test_double_booking_same_slot_rejected(self):
        run(self.book(10, TUE, "12:00"))
        with self.assertRaises(SlotUnavailable):
            run(self.book(11, TUE, "12:00"))

    def test_concurrent_booking_only_one_wins(self):
        async def go():
            res = await asyncio.gather(
                *[self.book(100 + i, TUE, "15:00") for i in range(8)], return_exceptions=True)
            return res
        res = run(go())
        ok = [r for r in res if not isinstance(r, Exception)]
        bad = [r for r in res if isinstance(r, SlotUnavailable)]
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(bad), 7)

    def test_one_active_booking_per_client(self):
        run(self.book(10, TUE, "12:00"))
        with self.assertRaises(AlreadyBooked):
            run(self.book(10, TUE, "13:00"))

    def test_cancel_frees_slot_and_allows_rebook(self):
        a = run(self.book(10, TUE, "12:00"))
        self.assertTrue(run(self.db.cancel_appointment(a.id, "client")))
        self.assertFalse(run(self.db.cancel_appointment(a.id, "client")))  # вдруге — вже ні
        self.assertIn("12:00", run(get_free_slots(self.db, TUE, NOW)))
        run(self.book(10, TUE, "13:00"))

    def test_day_off(self):
        run(self.db.add_day_off(TUE))
        self.assertEqual(run(get_free_slots(self.db, TUE, NOW)), [])
        run(self.db.remove_day_off(TUE))
        self.assertTrue(run(get_free_slots(self.db, TUE, NOW)))

    def test_blocked_time(self):
        run(self.db.add_range(BLOCKS, TUE, 14 * 60, 16 * 60))
        slots = run(get_free_slots(self.db, TUE, NOW))
        self.assertNotIn("14:00", slots)
        self.assertNotIn("15:00", slots)
        self.assertIn("13:00", slots)
        self.assertIn("16:00", slots)

    def test_extra_hours_on_weekend_and_after_close(self):
        run(self.db.add_range(EXTRA, SAT, 10 * 60, 13 * 60))
        self.assertEqual(run(get_free_slots(self.db, SAT, NOW)), ["10:00", "11:00", "12:00"])
        run(self.db.add_range(EXTRA, TUE, 20 * 60, 22 * 60))
        slots = run(get_free_slots(self.db, TUE, NOW))
        self.assertIn("20:00", slots)
        self.assertIn("21:00", slots)

    def test_day_off_overrides_extra(self):
        run(self.db.add_range(EXTRA, SAT, 10 * 60, 13 * 60))
        run(self.db.add_day_off(SAT))
        self.assertEqual(run(get_free_slots(self.db, SAT, NOW)), [])

    def test_duration_change(self):
        self.assertEqual(run(self.db.get_duration()), 60)
        run(self.db.set_duration(90))
        slots = run(get_free_slots(self.db, TUE, NOW))
        self.assertEqual(slots[:3], ["10:00", "11:30", "13:00"])
        self.assertEqual(slots[-1], "17:30")    # 17:30 + 90 хв = 19:00; 19:00 + 90 хв вже виходить за 20:00
        a = run(self.book(10, TUE, "11:30"))
        self.assertEqual((a.end_at - a.start_at), timedelta(minutes=90))

    def test_long_appointment_blocks_following_slots_after_duration_change(self):
        run(self.book(10, TUE, "10:00"))            # 60 хв
        run(self.db.set_duration(30))
        slots = run(get_free_slots(self.db, TUE, NOW))
        self.assertNotIn("10:00", slots)
        self.assertNotIn("10:30", slots)            # перетинається з 10:00–11:00
        self.assertIn("11:00", slots)

    def test_reminder_flags_on_late_booking(self):
        far = run(self.book(1, TUE + timedelta(days=3), "12:00"))
        self.assertFalse(far.remind24_sent or far.remind2_sent)
        soon = run(self.book(2, TUE, "10:00", now=datetime(2026, 11, 9, 20, 0)))   # <24 год
        self.assertTrue(soon.remind24_sent)
        self.assertFalse(soon.remind2_sent)
        very_soon = run(self.book(3, MON, "19:00", now=datetime(2026, 11, 9, 18, 0)))  # <2 год
        self.assertTrue(very_soon.remind24_sent and very_soon.remind2_sent)

    def test_data_persists_after_restart(self):
        a = run(self.book(10, TUE, "12:00"))
        run(self.db.set_duration(45))
        run(self.db.add_day_off(SUN))
        run(self.db.close())
        db2 = Database(self.path)
        run(db2.connect())
        try:
            self.assertEqual(run(db2.get_duration()), 45)
            self.assertTrue(run(db2.is_day_off(SUN)))
            got = run(db2.get_appointment(a.id))
            self.assertEqual(got.name, "Іван")
            self.assertEqual(got.start_at, datetime(2026, 11, 10, 12, 0))
        finally:
            run(db2.close())
            self.db = db2  # tearDown закриє вдруге без помилки

    def test_admin_queries(self):
        run(self.book(1, TUE, "10:00", name="Анна"))
        run(self.book(2, TUE, "11:00", name="Борис"))
        run(self.book(3, TUE + timedelta(days=1), "10:00", name="Віктор"))
        self.assertEqual(run(self.db.count_upcoming(NOW)), 3)
        self.assertEqual([a.name for a in run(self.db.list_for_day(TUE))], ["Анна", "Борис"])
        self.assertEqual(len(run(self.db.list_upcoming(NOW, 2, 0))), 2)
        self.assertEqual(len(run(self.db.list_upcoming(NOW, 2, 2))), 1)
        cancelled = run(self.db.cancel_day(TUE, "admin"))
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(run(self.db.count_upcoming(NOW)), 1)
        clients = run(self.db.list_clients(10, 0))
        self.assertEqual(run(self.db.count_clients()), 3)
        self.assertEqual({c.name for c in clients}, {"Анна", "Борис", "Віктор"})

    def test_days_with_slots(self):
        run(self.db.add_day_off(TUE))
        days = run(schedule.days_with_slots(self.db, 2026, 11, NOW))
        self.assertIn(MON, days)
        self.assertNotIn(TUE, days)
        self.assertNotIn(SAT, days)
        self.assertNotIn(date(2026, 11, 2), days)   # минуле


class TestReminders(DbTestCase):
    def test_reminders_sent_once_each(self):
        from app import reminders
        from app.utils import now_local
        import app.reminders as rem

        sent = []

        class FakeBot:
            async def send_message(self, chat_id, text, reply_markup=None):
                sent.append((chat_id, text))

        # Підміняємо «зараз» усередині модуля нагадувань.
        clock = {"now": datetime(2026, 11, 9, 9, 0)}
        rem.now_local = lambda: clock["now"]

        async def scenario():
            a = await self.book(7, date(2026, 11, 11), "12:00")        # середа 12:00
            for now, expected in [
                (datetime(2026, 11, 10, 9, 0), 0),     # ще за 27 год — нічого
                (datetime(2026, 11, 10, 12, 0), 1),    # рівно 24 год — перше
                (datetime(2026, 11, 10, 12, 5), 1),    # повторно не шлемо
                (datetime(2026, 11, 11, 10, 0), 2),    # за 2 год — друге
                (datetime(2026, 11, 11, 10, 5), 2),    # повторно не шлемо
            ]:
                clock["now"] = now
                await rem.process_reminders(FakeBot(), self.db)
                self.assertEqual(len(sent), expected, now)
            return a

        run(scenario())
        self.assertIn("24 години", sent[0][1])
        self.assertIn("2 години", sent[1][1])
        rem.now_local = now_local
        self.assertEqual(reminders.CHECK_INTERVAL_SECONDS, 60)

    def test_cancelled_appointment_gets_no_reminder(self):
        import app.reminders as rem
        sent = []

        class FakeBot:
            async def send_message(self, *a, **k):
                sent.append(1)

        rem.now_local = lambda: datetime(2026, 11, 10, 12, 0)

        async def scenario():
            a = await self.book(7, date(2026, 11, 11), "12:00")
            await self.db.cancel_appointment(a.id, "client")
            await rem.process_reminders(FakeBot(), self.db)

        run(scenario())
        self.assertEqual(sent, [])


class TestValidationAndUI(unittest.TestCase):
    def test_name(self):
        self.assertEqual(normalize_name("  Іван   Петренко "), "Іван Петренко")
        self.assertEqual(normalize_name("Дем'ян"), "Дем'ян")
        self.assertIsNone(normalize_name("A"))
        self.assertIsNone(normalize_name("12345"))
        self.assertIsNone(normalize_name("<b>x</b>"))
        self.assertIsNone(normalize_name("а" * 51))

    def test_phone(self):
        self.assertEqual(normalize_phone("380501234567"), "+380501234567")
        self.assertEqual(normalize_phone("+38 (050) 123-45-67"), "+380501234567")
        self.assertIsNone(normalize_phone("12345"))

    def test_time_range(self):
        self.assertEqual(parse_time_range("14:00-16:00"), (840, 960))
        self.assertEqual(parse_time_range(" 9:30 – 11.00 "), (570, 660))
        self.assertIsNone(parse_time_range("16:00-14:00"))
        self.assertIsNone(parse_time_range("25:00-26:00"))
        self.assertIsNone(parse_time_range("abc"))

    def test_year_month(self):
        self.assertEqual(parse_year_month("2026-11"), (2026, 11))
        with self.assertRaises(ValueError):
            parse_year_month("2026-13")

    def test_calendar_structure(self):
        kb = build_calendar("bk", 2026, 11, {date(2026, 11, 10)}, (2026, 11), (2027, 1), footer=None)
        rows = kb.inline_keyboard
        flat = [b for r in rows for b in r]
        datas = [b.callback_data for b in flat]
        self.assertIn("bk:d:2026-11-10", datas)
        self.assertNotIn("bk:d:2026-11-11", datas)
        self.assertIn("bk:m:2026-12", datas)
        self.assertNotIn("bk:m:2026-10", datas)       # до минулого місяця навігації немає
        for r in rows[2:-1]:
            self.assertEqual(len(r), 7)
        for b in flat:
            self.assertTrue(len(b.callback_data.encode()) <= 64 and b.text)

    def test_times_kb_callback_roundtrip(self):
        kb = times_kb(TUE, ["10:00", "11:00", "12:00", "13:00"])
        first = kb.inline_keyboard[0][0].callback_data
        self.assertEqual(first, "bk:t:2026-11-10:1000")
        _, _, iso, compact = first.split(":")
        self.assertEqual((date.fromisoformat(iso), f"{compact[:2]}:{compact[2:]}"), (TUE, "10:00"))


if __name__ == "__main__":
    unittest.main()
