import sqlite3
import logging
import asyncio
import configparser
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta, time

# Чтение конфигурационного файла
config = configparser.ConfigParser()
config.read('config.ini')

# Настройки из config.ini
TOKEN = config.get('Telegram', 'token')
ADMIN_IDS = set(map(int, config.get('Telegram', 'admin_ids').split(',')))
# Добавляем настройки для группового чата и топика
GROUP_CHAT_ID = config.get('Telegram', 'group_chat_id', fallback=None)
TOPIC_ID = config.get('Telegram', 'topic_id', fallback=None)

# Логирование
logging.basicConfig(level=logging.INFO)

# База данных
conn = sqlite3.connect("reports.db", check_same_thread=False)
cursor = conn.cursor()

# Создание таблицы users для хранения информации о зарегистрированных пользователях
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    user_name TEXT
)
""")

# Создание таблицы reports для хранения отчетов
cursor.execute("""
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    completed_task TEXT,
    next_task TEXT,
    timestamp TEXT,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
)
""")
conn.commit()

# Инициализация бота
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Планировщик задач
scheduler = AsyncIOScheduler()

# Состояния для FSM
class RegistrationState(StatesGroup):
    name = State()  # Состояние для ввода имени пользователя

class ReportState(StatesGroup):
    completed_task = State()
    next_task = State()

# Клавиатура с кнопками
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📝 Начать отчёт")],
        [KeyboardButton(text="📜 Просмотреть отчёты")]
    ], resize_keyboard=True)
    return keyboard

# Проверка, зарегистрирован ли пользователь
def is_user_registered(user_id):
    cursor.execute("SELECT user_name FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    return result is not None and result[0] is not None

# Команда /start
@dp.message(Command("start"))
async def start_command(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if is_user_registered(user_id):
        # Если пользователь уже зарегистрирован, показываем основное меню
        await message.answer("С возвращением! Используйте кнопки ниже для управления.", reply_markup=get_main_keyboard())
    else:
        # Если пользователь не зарегистрирован, запрашиваем имя
        await message.answer("Привет! Пожалуйста, введите ваше имя для регистрации.")
        await state.set_state(RegistrationState.name)

# Принимаем имя пользователя
@dp.message(RegistrationState.name)
async def get_user_name(message: Message, state: FSMContext):
    user_name = message.text
    user_id = message.from_user.id

    # Сохраняем имя пользователя в таблице users
    cursor.execute("INSERT OR REPLACE INTO users (user_id, user_name) VALUES (?, ?)", (user_id, user_name))
    conn.commit()

    await message.answer(f"✅ Спасибо, {user_name}! Теперь вы зарегистрированы.", reply_markup=get_main_keyboard())
    await state.clear()

# Начало отчёта
@dp.message(lambda message: message.text == "📝 Начать отчёт")
async def start_report(message: Message, state: FSMContext):
    await message.answer("📌 Опишите, что вы уже сделали.", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(ReportState.completed_task)

# Принимаем выполненную работу
@dp.message(ReportState.completed_task)
async def get_completed_task(message: Message, state: FSMContext):
    current_state = await state.get_state()
    logging.info(f"Текущее состояние: {current_state}")  # Лог состояния
    await state.update_data(completed_task=message.text)
    await message.answer("📅 Теперь укажите, чем собираетесь заняться.")
    await state.set_state(ReportState.next_task)

# Принимаем предстоящую работу и сохраняем отчёт
@dp.message(ReportState.next_task)
async def get_next_task(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_name = cursor.execute("SELECT user_name FROM users WHERE user_id = ?", (user_id,)).fetchone()[0]
    data = await state.get_data()
    completed_task = data["completed_task"]
    next_task = message.text

    # Получаем текущее локальное время (UTC+6)
    local_time = datetime.now() + timedelta(hours=6)
    timestamp = local_time.strftime("%Y-%m-%d %H:%M:%S")

    # Записываем в таблицу reports
    cursor.execute("""INSERT INTO reports (user_id, completed_task, next_task, timestamp)
                  VALUES (?, ?, ?, ?)""",
               (user_id, completed_task, next_task, timestamp))
    conn.commit()

    await message.answer("✅ Отчёт сохранён!", reply_markup=get_main_keyboard())
    await state.clear()
    
    # Отправляем отчет в топик группы, если настроено
    if GROUP_CHAT_ID and TOPIC_ID:
        report_text = f"📊 *Новый отчёт*\n\n👤 {user_name}\n🕒 {timestamp}\n✅ *Выполнено:* {completed_task}\n⏭ *Планы:* {next_task}"
        try:
            # Используем message_thread_id для отправки сообщения в конкретный топик
            await bot.send_message(
                chat_id=GROUP_CHAT_ID, 
                text=report_text, 
                message_thread_id=int(TOPIC_ID),
                parse_mode="Markdown"
            )
            logging.info(f"Отчет от {user_name} отправлен в топик {TOPIC_ID} группы {GROUP_CHAT_ID}")
        except Exception as e:
            logging.error(f"Ошибка при отправке отчета в топик группы: {e}")

# Просмотр всех отчётов (только для админов)
@dp.message(lambda message: message.text == "📜 Просмотреть отчёты")
async def view_reports(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав для просмотра отчётов.", reply_markup=get_main_keyboard())
        return
    
    cursor.execute("""
        SELECT users.user_name, reports.completed_task, reports.next_task, reports.timestamp
        FROM reports
        JOIN users ON reports.user_id = users.user_id
        ORDER BY reports.timestamp DESC
    """)
    reports = cursor.fetchall()
    if not reports:
        await message.answer("📭 Отчётов пока нет.", reply_markup=get_main_keyboard())
        return

    response = "📜 Отчёты:\n\n" + "\n\n".join(
        [f"👤 {r[0]}\n🕒 {r[3]}\n✅ {r[1]}\n⏭ {r[2]}" for r in reports]
    )
    await message.answer(response, reply_markup=get_main_keyboard())

# Функция для отправки отчётов администраторам и в топик группы
async def send_reports_to_admins():
    # Получаем только отчеты за сегодняшний день
    today = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d")
    
    cursor.execute("""
        SELECT users.user_name, reports.completed_task, reports.next_task, reports.timestamp
        FROM reports
        JOIN users ON reports.user_id = users.user_id
        WHERE reports.timestamp LIKE ?
        ORDER BY reports.timestamp DESC
    """, (f"{today}%",))
    
    reports = cursor.fetchall()
    if not reports:
        logging.info("Нет отчетов за сегодня")
        return

    response = "📜 Отчёты за сегодня:\n\n" + "\n\n".join(
        [f"👤 {r[0]}\n🕒 {r[3]}\n✅ {r[1]}\n⏭ {r[2]}" for r in reports]
    )

    # Отправка отчетов администраторам в личку
    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, response)
    
    # Отправка сводки отчетов в топик группы
    if GROUP_CHAT_ID and TOPIC_ID:
        try:
            await bot.send_message(
                chat_id=GROUP_CHAT_ID, 
                text=f"📊 *Сводка отчетов*\n\n{response}", 
                message_thread_id=int(TOPIC_ID),
                parse_mode="Markdown"
            )
            logging.info(f"Сводка отчетов отправлена в топик {TOPIC_ID} группы {GROUP_CHAT_ID}")
        except Exception as e:
            logging.error(f"Ошибка при отправке сводки отчетов в топик группы: {e}")

# Новая функция: Проверка, отправлял ли пользователь отчёт сегодня
def has_user_reported_today(user_id):
    # Получаем текущую дату в формате UTC+6
    today = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d")
    
    cursor.execute("""
        SELECT COUNT(*) FROM reports 
        WHERE user_id = ? AND timestamp LIKE ?
    """, (user_id, f"{today}%"))
    
    count = cursor.fetchone()[0]
    return count > 0

# Функция отправки утреннего напоминания пользователям
async def send_morning_reminder():
    cursor.execute("SELECT user_id, user_name FROM users")
    users = cursor.fetchall()
    
    for user_id, user_name in users:
        try:
            message_text = f"Доброе утро, {user_name}! 🌞\nНе забудьте отправить утренний отчёт о планах на сегодня."
            await bot.send_message(user_id, message_text, reply_markup=get_main_keyboard())
            logging.info(f"Утреннее напоминание отправлено пользователю {user_name} (ID: {user_id})")
        except Exception as e:
            logging.error(f"Ошибка при отправке утреннего напоминания пользователю {user_id}: {e}")

# Функция отправки вечернего напоминания пользователям, которые не отправили отчёт
async def send_evening_reminder():
    cursor.execute("SELECT user_id, user_name FROM users")
    users = cursor.fetchall()
    
    for user_id, user_name in users:
        if not has_user_reported_today(user_id):
            try:
                message_text = f"Добрый вечер, {user_name}! 🌙\nНе забудьте отправить отчёт о проделанной работе за сегодня."
                await bot.send_message(user_id, message_text, reply_markup=get_main_keyboard())
                logging.info(f"Вечернее напоминание отправлено пользователю {user_name} (ID: {user_id})")
            except Exception as e:
                logging.error(f"Ошибка при отправке вечернего напоминания пользователю {user_id}: {e}")

# Планирование отправки отчётов и напоминаний
def schedule_reports():
    # Отправка отчётов администраторам и в топик группы
    # Отправка в 9:00 каждый будний день
    scheduler.add_job(send_reports_to_admins, 'cron', day_of_week='mon-fri', hour=9, minute=0)
    
    # Отправка в 17:30 каждый будний день
    scheduler.add_job(send_reports_to_admins, 'cron', day_of_week='mon-fri', hour=17, minute=30)
    
    # Утреннее напоминание пользователям (в 8:00 каждый будний день)
    scheduler.add_job(send_morning_reminder, 'cron', day_of_week='mon-fri', hour=8, minute=0)
    
    # Вечернее напоминание пользователям (в 16:00 каждый будний день)
    scheduler.add_job(send_evening_reminder, 'cron', day_of_week='mon-fri', hour=16, minute=0)

# Функция запуска бота
async def main():
    schedule_reports()  # Запуск планировщика
    scheduler.start()
    await dp.start_polling(bot)

# Запуск
if __name__ == "__main__":
    asyncio.run(main())