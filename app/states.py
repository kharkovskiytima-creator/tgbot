"""Стани FSM."""
from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    name = State()
    phone = State()
    confirm = State()


class AdminStates(StatesGroup):
    block_range = State()      # введення проміжку часу для блокування
    extra_range = State()      # введення проміжку часу для додаткових годин
    duration_input = State()   # введення власної тривалості стрижки
