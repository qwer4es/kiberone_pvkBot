# bot.py
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder
import sqlite3
import os
import threading
from flask import Flask

# Получаем данные из переменных окружения
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID_STR = os.getenv("ADMIN_ID")

# Преобразуем ADMIN_ID в int с проверкой
ADMIN_ID = None
if ADMIN_ID_STR:
    try:
        ADMIN_ID = int(ADMIN_ID_STR)
    except ValueError:
        print(f"Warning: ADMIN_ID '{ADMIN_ID_STR}' is not a valid integer")
        ADMIN_ID = None

# Проверяем наличие токена
if not API_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Подключаем базу данных
conn = sqlite3.connect('applications.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_name TEXT,
    child_age_range TEXT,
    parent_name TEXT,
    parent_phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
conn.commit()


# Состояния для FSM
class ApplicationStates(StatesGroup):
    child_name = State()
    child_age_range = State()
    parent_name = State()
    parent_phone = State()


# Команда /start
@dp.message(Command("start"))
async def send_welcome(message: Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Записаться на пробное занятие")]],
        resize_keyboard=True)
    await message.answer(
        "Привет! Я помогу записаться на пробное занятие в KiberOne.",
        reply_markup=keyboard)


# Начало заполнения
@dp.message(F.text == "Записаться на пробное занятие")
async def start_application(message: Message, state: FSMContext):
    await message.answer("Введите имя ребенка:")
    await state.set_state(ApplicationStates.child_name)


# Имя ребёнка
@dp.message(ApplicationStates.child_name)
async def process_child_name(message: Message, state: FSMContext):
    await state.update_data(child_name=message.text)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="6-8 лет", callback_data="age_6_8")
    ], [
        InlineKeyboardButton(text="9-11 лет", callback_data="age_9_11")
    ], [InlineKeyboardButton(text="12-14 лет", callback_data="age_12_14")]])
    await message.answer("Выберите возрастную категорию ребенка:",
                         reply_markup=keyboard)
    await state.set_state(ApplicationStates.child_age_range)


# Выбор возраста
@dp.callback_query(F.data.startswith("age"), ApplicationStates.child_age_range)
async def process_age_callback(callback_query: CallbackQuery,
                               state: FSMContext):
    if callback_query.data:
        parts = callback_query.data.split("_")
        if len(parts) >= 3:
            age_range = f"{parts[1]}-{parts[2]}"
            await callback_query.answer()
            await state.update_data(child_age_range=age_range)

            builder = ReplyKeyboardBuilder()
            builder.add(
                KeyboardButton(text="Отправить номер телефона", request_contact=True))
            builder.adjust(1)

            if callback_query.message:
                await callback_query.message.answer(
                    "Введите имя родителя или нажмите кнопку 'Отправить номер телефона', чтобы поделиться контактом:",
                    reply_markup=builder.as_markup(resize_keyboard=True,
                                                   one_time_keyboard=True))
            await state.set_state(ApplicationStates.parent_name)


# Обработка имени или контакта
@dp.message(ApplicationStates.parent_name)
async def process_parent_name_or_contact(message: Message, state: FSMContext):
    if message.contact:
        parent_name = message.contact.first_name or "Родитель"
        parent_phone = message.contact.phone_number
        await state.update_data(parent_name=parent_name,
                                parent_phone=parent_phone)
    else:
        parent_name = message.text
        await state.update_data(parent_name=parent_name)
        await message.answer("Введите номер телефона родителя:")
        await state.set_state(ApplicationStates.parent_phone)
        return

    await save_application(message, state)


# Ввод телефона
@dp.message(ApplicationStates.parent_phone)
async def process_parent_phone(message: Message, state: FSMContext):
    await state.update_data(parent_phone=message.text)
    await save_application(message, state)


# Сохранение заявки и отправка в канал
async def save_application(message: Message, state: FSMContext):
    user_data = await state.get_data()

    # Сохраняем в базу
    cursor.execute(
        '''
    INSERT INTO applications (child_name, child_age_range, parent_name, parent_phone)
    VALUES (?, ?, ?, ?)
    ''', (user_data['child_name'], user_data['child_age_range'],
          user_data['parent_name'], user_data['parent_phone']))
    conn.commit()

    # Подтверждение пользователю
    await message.answer(
        f"Спасибо! Ваша заявка принята.\n\n"
        f"Ребёнок: {user_data['child_name']}, Возраст: {user_data['child_age_range']}\n"
        f"Родитель: {user_data['parent_name']}, Телефон: {user_data['parent_phone']}"
    )

    # Отправка в канал (если настроен)
    if CHANNEL_ID:
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"🔔 Новая заявка на пробное занятие\n"
                f"👤 Ребёнок: {user_data['child_name']}\n"
                f"📅 Возраст: {user_data['child_age_range']} лет\n"
                f"👨‍👩‍👧 Родитель: {user_data['parent_name']}\n"
                f"📞 Телефон: {user_data['parent_phone']}",
            )
        except Exception as e:
            print(f"❌ Ошибка отправки в канал: {e}")

    await state.clear()


# ================
# АДМИН-ПАНЕЛЬ
# ================


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not ADMIN_ID or not message.from_user or message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет доступа к админ-панели.")
        return

    # Получаем статистику
    cursor.execute("SELECT COUNT(*) FROM applications")
    total = cursor.fetchone()[0]

    cursor.execute(
        "SELECT child_name, child_age_range, parent_name, parent_phone FROM applications ORDER BY id DESC LIMIT 10"
    )
    applications = cursor.fetchall()

    # Клавиатура админа
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Посмотреть все заявки",
                                 callback_data="admin_view_all")
        ],
                         [
                             InlineKeyboardButton(
                                 text="Скачать базу данных",
                                 callback_data="admin_download_db")
                         ]])

    response = f"🔐 <b>Админ-панель</b>\n\n"
    response += f"📊 Всего заявок: <b>{total}</b>\n\n"
    if applications:
        response += "<b>Последние 10 заявок:</b>\n"
        for app in applications:
            response += f"🔹 {app[0]} ({app[1]} лет) — {app[2]}, {app[3]}\n"
    else:
        response += "📭 Нет заявок."

    await message.answer(response, reply_markup=keyboard, parse_mode="HTML")


# Обработка кнопок админ-панели
@dp.callback_query(F.data == "admin_view_all")
async def admin_view_all(callback_query: CallbackQuery):
    if not ADMIN_ID or not callback_query.from_user or callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ Доступ запрещён.")
        return

    cursor.execute(
        "SELECT child_name, child_age_range, parent_name, parent_phone FROM applications ORDER BY id DESC"
    )
    apps = cursor.fetchall()

    if not apps:
        if callback_query.message:
            await callback_query.message.answer("📭 Нет ни одной заявки.")
        await callback_query.answer()
        return

    message_text = "📋 <b>Все заявки:</b>\n\n"
    for i, app in enumerate(apps, 1):
        message_text += f"{i}. {app[0]} ({app[1]} лет) — {app[2]}, {app[3]}\n"

    # Разбиваем, если слишком длинное сообщение
    if len(message_text) > 4096:
        parts = [
            message_text[i:i + 4000] for i in range(0, len(message_text), 4000)
        ]
        for part in parts:
            if callback_query.message:
                await callback_query.message.answer(part, parse_mode="HTML")
    else:
        if callback_query.message:
            await callback_query.message.answer(message_text, parse_mode="HTML")

    await callback_query.answer()


@dp.callback_query(F.data == "admin_download_db")
async def admin_download_db(callback_query: CallbackQuery):
    if not ADMIN_ID or not callback_query.from_user or callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("❌ Доступ запрещён.")
        return

    try:
        if callback_query.message:
            with open('applications.db', 'rb') as db_file:
                await callback_query.message.answer_document(document=db_file)
        await callback_query.answer("✅ База данных отправлена.")
    except Exception as e:
        await callback_query.answer(f"❌ Ошибка: {e}")

# Создаем Flask приложение
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Запускаем Flask в отдельном потоке
threading.Thread(target=run_flask, daemon=True).start()
# === КОНЕЦ ВЕБ-СЕРВЕРА ===
# Запуск бота
if __name__ == '__main__':
    import asyncio
    asyncio.run(dp.start_polling(bot))
