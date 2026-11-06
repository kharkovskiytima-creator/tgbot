# ✂️ Telegram-бот для запису в барбершоп

Український бот для автоматичного запису клієнтів на чоловічу стрижку.
**Python 3.10+ · aiogram 3 · SQLite**

## Можливості

**Клієнт:** запис через календар → вільні години → ім’я → номер телефону кнопкою Telegram → підтвердження;
перегляд свого запису; скасування; контакти; нагадування за 24 год і за 2 год.

**Адміністратор (лише ваш Telegram ID):** усі записи, сьогодні / завтра / за датою, скасування запису
(клієнт отримує повідомлення), додавання вихідного, блокування часу, додаткові години, зміна тривалості
стрижки, контакти клієнтів, перегляд і видалення вихідних/блоків/додаткових годин.
Адміністратор отримує повідомлення про кожен новий запис і кожне скасування клієнтом.

## Структура проєкту

```
barber_bot/
├── main.py                  # точка входу
├── requirements.txt
├── .env.example             # шаблон налаштувань (копіюється у .env)
├── run_windows.bat          # запуск у Windows одним кліком
├── deploy/
│   └── barbershop-bot.service   # systemd-сервіс для Linux-сервера
├── app/
│   ├── config.py            # змінні середовища, графік роботи (10:00–20:00, Пн–Пт)
│   ├── database.py          # SQLite: записи, вихідні, блоки, додаткові години, налаштування
│   ├── schedule.py          # вільні слоти та безпечне бронювання
│   ├── reminders.py         # нагадування за 24 год і 2 год
│   ├── keyboards.py         # усі клавіатури та календар
│   ├── states.py            # стани діалогів (FSM)
│   ├── texts.py             # тексти кнопок
│   ├── ui.py                # фільтр адміністратора, допоміжні функції
│   ├── utils.py             # форматування, валідація
│   └── handlers/
│       ├── menu.py          # /start, меню, «Мій запис», скасування, контакти
│       ├── booking.py       # процес запису
│       └── admin.py         # адмін-панель
└── tests/test_logic.py      # автотести логіки розкладу
```

## 1. Створення бота через BotFather

1. У Telegram знайдіть **@BotFather** (з синьою галочкою) і натисніть **Start**.
2. Надішліть `/newbot`.
3. Введіть **назву** бота (відображається в чаті), наприклад: `Барбершоп Запис`.
4. Введіть **username** — унікальне ім’я, що закінчується на `bot`, наприклад: `my_barbershop_zapys_bot`.
5. BotFather надішле **токен** вигляду `123456789:AAH...xyz`. Збережіть його і нікому не показуйте.
6. (Необов’язково) `/setdescription`, `/setabouttext`, `/setuserpic` — опис і фото бота.

## 2. Налаштування токена та ID адміністратора

1. Дізнайтесь свій Telegram ID: напишіть боту **@userinfobot** — він надішле число (`Id: 123456789`).
2. Скопіюйте `.env.example` у файл `.env` (у цій самій папці).
3. Заповніть:

```env
BOT_TOKEN=123456789:AAH...xyz
ADMIN_ID=123456789
SHOP_NAME=Назва вашого барбершопу
SHOP_ADDRESS=м. Львів, вул. Прикладна, 1
SHOP_PHONE=+380XXXXXXXXX
SHOP_INSTAGRAM=@your_barbershop
SHOP_MAP_URL=https://maps.google.com/...
```

`SHOP_*` — для кнопки «📞 Контакти»; порожні поля просто не показуються.
Адмін-панель відкривається кнопкою «🛠 Адмін-панель» (або командою `/admin`) і працює **тільки** для `ADMIN_ID`.

## 3. Запуск на Windows

**Швидкий спосіб:** встановіть Python 3.10+ з [python.org](https://www.python.org/downloads/)
(під час встановлення поставте галочку **Add Python to PATH**), далі двічі клікніть `run_windows.bat`.

**Вручну** (PowerShell / командний рядок у папці проєкту):

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env
python main.py
```

У консолі з’явиться `Бот запущено: @ваш_бот`. Не закривайте вікно — бот працює, поки воно відкрите.
Зупинка: `Ctrl+C`.

## 4. Розміщення на сервері для роботи 24/7 (Ubuntu/Debian VPS)

```bash
# 1. Підготовка
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
sudo useradd -r -m -s /bin/bash botuser

# 2. Завантажте проєкт у /opt/barber_bot (scp, git або sftp)
sudo mkdir -p /opt/barber_bot && sudo chown botuser:botuser /opt/barber_bot
#    ... скопіюйте файли проєкту в /opt/barber_bot ...

# 3. Середовище та залежності
sudo -u botuser bash -c 'cd /opt/barber_bot && python3 -m venv venv && venv/bin/pip install -r requirements.txt'

# 4. Налаштування
sudo -u botuser cp /opt/barber_bot/.env.example /opt/barber_bot/.env
sudo -u botuser nano /opt/barber_bot/.env        # впишіть BOT_TOKEN, ADMIN_ID
sudo chmod 600 /opt/barber_bot/.env

# 5. Автозапуск через systemd
sudo cp /opt/barber_bot/deploy/barbershop-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now barbershop-bot

# 6. Перевірка та логи
sudo systemctl status barbershop-bot
sudo journalctl -u barbershop-bot -f
```

Бот перезапускається автоматично після збою чи перезавантаження сервера, а записи зберігаються у
`data/barbershop.db`. Оновлення коду: замініть файли → `sudo systemctl restart barbershop-bot`.
Резервна копія: періодично копіюйте `/opt/barber_bot/data/barbershop.db`.

> Запускайте лише **один** екземпляр бота з одним токеном (не одночасно на ПК і на сервері).

## Правила роботи розкладу

- Графік: Пн–Пт 10:00–20:00; субота й неділя — вихідні; часовий пояс **Europe/Kyiv**.
- Слоти йдуть із кроком, що дорівнює тривалості стрижки (початково 60 хв: 10:00, 11:00 … 19:00).
- Минулі дати та час не показуються; запис можливий мінімум за 30 хв до початку (`BOOKING_MIN_LEAD_MINUTES`) і не далі ніж на 60 днів уперед.
- Один клієнт може мати **один** активний запис (щоб нова бронь не накопичувалась).
- Подвійне бронювання виключене: перед збереженням час перевіряється ще раз під блокуванням.
- Зміна тривалості діє для нових записів; наявні записи не змінюються.
- Додаткові години можна відкрити і на вихідний, і після 20:00. Вихідний день, доданий адміністратором, має пріоритет над додатковими годинами.
- Нагадування: за 24 год і за 2 год. Якщо клієнт записався менше ніж за 24 год (або 2 год) до візиту, відповідне нагадування не надсилається.

## Тести

```bash
python -m unittest discover -s tests -v
```
