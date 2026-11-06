@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist venv (
    echo Створюємо віртуальне середовище...
    python -m venv venv || (echo Python не знайдено. Встановіть Python 3.10+ з python.org & pause & exit /b 1)
)
call venv\Scripts\activate
pip install -r requirements.txt

if not exist .env (
    copy .env.example .env >nul
    echo.
    echo Створено файл .env. Впишіть BOT_TOKEN та ADMIN_ID, збережіть і закрийте Блокнот.
    notepad .env
)

python main.py
pause
