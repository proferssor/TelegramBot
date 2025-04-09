# -*- coding: utf-8 -*-
import sqlite3
import logging
import asyncio
import configparser
# --- Используем полный импорт typing для диагностики ---
import typing
# ---

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import math

# --- Конфигурация ---
config = configparser.ConfigParser()
CONFIG_FILE = 'config.ini'

# (Функции load_config, save_config без изменений)
def load_config():
    """Загружает конфигурацию из файла."""
    global TOKEN, ADMIN_IDS, SUPER_ADMIN_ID, GROUP_CHAT_ID, TOPIC_ID
    try:
        config.read(CONFIG_FILE, encoding='utf-8')

        TOKEN = config.get('Telegram', 'token')
        ADMIN_IDS_STR = config.get('Telegram', 'admin_ids', fallback='')
        ADMIN_IDS = set(map(int, filter(str.isdigit, ADMIN_IDS_STR.split(',')))) if ADMIN_IDS_STR else set()
        SUPER_ADMIN_ID = int(config.get('Telegram', 'super_admin_id'))
        GROUP_CHAT_ID = config.get('Telegram', 'group_chat_id', fallback=None)
        TOPIC_ID_STR = config.get('Telegram', 'topic_id', fallback=None)
        TOPIC_ID = int(TOPIC_ID_STR) if TOPIC_ID_STR and TOPIC_ID_STR.isdigit() else None

        logging.info("Конфигурация загружена.")

    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        logging.error(f"Ошибка чтения {CONFIG_FILE}: {e}. Убедитесь, что файл существует и содержит все необходимые параметры.")
        print(f"Ошибка чтения {CONFIG_FILE}: {e}. Убедитесь, что файл существует и содержит все необходимые параметры.")
        exit()
    except ValueError as e:
        logging.error(f"Ошибка преобразования значения в {CONFIG_FILE}: {e}. Проверьте правильность ID.")
        print(f"Ошибка преобразования значения в {CONFIG_FILE}: {e}. Проверьте правильность ID.")
        exit()

def save_config():
    """Сохраняет текущую конфигурацию в файл."""
    try:
        if not config.has_section('Telegram'):
            config.add_section('Telegram')
        config.set('Telegram', 'token', TOKEN)
        config.set('Telegram', 'admin_ids', ','.join(map(str, ADMIN_IDS)))
        config.set('Telegram', 'super_admin_id', str(SUPER_ADMIN_ID))
        if GROUP_CHAT_ID:
            config.set('Telegram', 'group_chat_id', str(GROUP_CHAT_ID))
        else:
            if config.has_option('Telegram', 'group_chat_id'):
                config.remove_option('Telegram', 'group_chat_id')
        if TOPIC_ID:
            config.set('Telegram', 'topic_id', str(TOPIC_ID))
        else:
            if config.has_option('Telegram', 'topic_id'):
                config.remove_option('Telegram', 'topic_id')

        with open(CONFIG_FILE, 'w', encoding='utf-8') as configfile:
            config.write(configfile)
        logging.info(f"Конфигурация сохранена в {CONFIG_FILE}.")
    except IOError as e:
        logging.error(f"Ошибка записи в {CONFIG_FILE}: {e}")
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при сохранении конфигурации: {e}")

load_config()

# --- Логирование ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- База данных ---
DB_NAME = "reports.db"

# (Функции initialize_database, get_db_connection без изменений)
def initialize_database():
    """Инициализирует базу данных и создает таблицы, если они не существуют."""
    with sqlite3.connect(DB_NAME, check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            role TEXT DEFAULT 'user' CHECK(role IN ('user', 'admin', 'super_admin'))
        )
        """)
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'role' not in columns:
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user' CHECK(role IN ('user', 'admin', 'super_admin'))")
                logging.info("Столбец 'role' добавлен в таблицу 'users'.")
            except sqlite3.OperationalError as e:
                logging.warning(f"Не удалось добавить столбец 'role': {e}")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            completed_task TEXT,
            next_task TEXT,
            timestamp TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER UNIQUE,
            topic_name TEXT
        )
        """)

        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (SUPER_ADMIN_ID,))
        cursor.execute("UPDATE users SET role = 'super_admin' WHERE user_id = ?", (SUPER_ADMIN_ID,))
        if ADMIN_IDS:
            for admin_id in ADMIN_IDS:
                cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (admin_id,))
                cursor.execute("UPDATE users SET role = 'admin' WHERE user_id = ? AND role != 'super_admin'", (admin_id,))

        conn.commit()
        logging.info("База данных инициализирована.")

initialize_database()

def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_NAME, check_same_thread=False)

# --- Вспомогательные функции БД с использованием typing.* ---
def get_user_role(user_id: int) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else 'user'

def get_user_name(user_id: int) -> typing.Optional[str]: # Изменено
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_name FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None

def has_user_reported_today(user_id: int) -> bool:
    today = (datetime.now() + timedelta(hours=6)).strftime("%Y-%m-%d")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM reports WHERE user_id = ? AND timestamp LIKE ?", (user_id, f"{today}%"))
        return cursor.fetchone()[0] > 0

def get_users_by_role(role: str) -> typing.List[typing.Tuple[int, str]]: # Изменено
    """Возвращает список пользователей (ID, Имя) с указанной ролью."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, user_name FROM users WHERE role = ? ORDER BY user_name", (role,))
        users = cursor.fetchall()
        return [(uid, name) for uid, name in users if name]

def get_users_with_reports() -> typing.List[typing.Tuple[int, str]]: # Изменено
    """Возвращает список пользователей (ID, Имя), у которых есть хотя бы один отчет."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT u.user_id, u.user_name
            FROM users u
            JOIN reports r ON u.user_id = r.user_id
            WHERE u.user_name IS NOT NULL
            ORDER BY u.user_name
        """)
        users = cursor.fetchall()
        return users

def get_all_users_except_super_admin() -> typing.List[typing.Tuple[int, str]]:
    """Возвращает список всех пользователей (ID, Имя), кроме супер-админа."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, user_name FROM users WHERE user_id != ? ORDER BY user_name", (SUPER_ADMIN_ID,))
        users = cursor.fetchall()
        return [(uid, name) for uid, name in users]


# --- Инициализация aiogram ---
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler(timezone="Asia/Bishkek")

# --- Состояния FSM ---
class RegistrationState(StatesGroup):
    name = State()

class ReportState(StatesGroup):
    completed_task = State()
    next_task = State()

# --- Константы для Callback Data ---
CALLBACK_PREFIX_ADD_ADMIN = "add_admin_"
CALLBACK_PREFIX_REMOVE_ADMIN = "rem_admin_"
CALLBACK_PREFIX_VIEW_REPORTS = "view_rep_"
CALLBACK_PREFIX_DELETE_USER = "del_user_" # Новый префикс
ACTION_SELECT = "select"
ACTION_PAGE = "page"
ACTION_CANCEL = "cancel"
USERS_PER_PAGE = 5

# --- Клавиатуры ---
# (get_main_keyboard без изменений)
def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру в зависимости от роли пользователя."""
    role = get_user_role(user_id)
    buttons = []
    if role == 'user':
        buttons = [
            [KeyboardButton(text="📝 Начать отчёт")],
            [KeyboardButton(text="📜 Просмотреть мои отчёты")]
        ]
    elif role == 'admin':
        buttons = [
            [KeyboardButton(text="📜 Просмотреть все отчёты")],
            [KeyboardButton(text="👥 Просмотреть отчёты по пользователю")]
        ]
    elif role == 'super_admin':
        buttons = [
            [KeyboardButton(text="📝 Начать отчёт")],
            [KeyboardButton(text="📜 Просмотреть все отчёты")],
            [KeyboardButton(text="👥 Просмотреть отчёты по пользователю")],
            [KeyboardButton(text="➕ Добавить админа")],
            [KeyboardButton(text="➖ Удалить админа")],
            [KeyboardButton(text="🗑️ Удалить пользователя")], # Новая кнопка
        ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=False)

# (create_user_selection_keyboard без изменений)
def create_user_selection_keyboard(users: typing.List[typing.Tuple[int, str]], page: int, callback_prefix: str) -> InlineKeyboardMarkup: # Изменено
    """Создает инлайн-клавиатуру для выбора пользователя с пагинацией."""
    keyboard = []
    total_users = len(users)
    total_pages = math.ceil(total_users / USERS_PER_PAGE)
    start_index = (page - 1) * USERS_PER_PAGE
    end_index = start_index + USERS_PER_PAGE
    users_on_page = users[start_index:end_index]

    for user_id, user_name in users_on_page:
        button_text = f"{user_name or 'Имя не указано'} ({user_id})"
        callback_data = f"{callback_prefix}{ACTION_SELECT}:{user_id}"
        keyboard.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])

    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{callback_prefix}{ACTION_PAGE}:{page-1}"))
    if page < total_pages:
        pagination_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{callback_prefix}{ACTION_PAGE}:{page+1}"))

    if pagination_buttons:
        keyboard.append(pagination_buttons)

    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"{callback_prefix}{ACTION_CANCEL}")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# --- Обработчики команд и сообщений ---
# (start_command, process_name, start_report, process_completed_task, process_next_task без изменений)
@dp.message(Command("start"))
async def start_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, user_name) VALUES (?, ?)", (user_id, user_name))
        cursor.execute("UPDATE users SET user_name = ? WHERE user_id = ? AND (role = 'user' OR role IS NULL)", (user_name, user_id))
        conn.commit()

    current_name = get_user_name(user_id)
    if not current_name:
        await message.answer("👋 Привет! Пожалуйста, введите ваше имя для завершения регистрации.")
        await state.set_state(RegistrationState.name)
    else:
        await message.answer(f"👋 С возвращением, {current_name}! Используйте кнопки ниже.",
                            reply_markup=get_main_keyboard(user_id))
        await state.clear()

@dp.message(RegistrationState.name)
async def process_name(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_name = message.text
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET user_name = ? WHERE user_id = ?", (user_name, user_id))
        conn.commit()
    await message.answer(f"✅ Спасибо, {user_name}! Регистрация завершена.",
                        reply_markup=get_main_keyboard(user_id))
    await state.clear()

@dp.message(F.text == "📝 Начать отчёт")
async def start_report(message: Message, state: FSMContext):
    user_id = message.from_user.id
    role = get_user_role(user_id)
    if role == 'user' or role == 'super_admin':
        await message.answer("📌 Опишите, что вы уже сделали (выполненные задачи).", reply_markup=ReplyKeyboardRemove())
        await state.set_state(ReportState.completed_task)
    elif role == 'admin':
        await message.answer("Администраторы не могут отправлять отчеты.", reply_markup=get_main_keyboard(user_id))
    else:
        await message.answer("Не удалось определить вашу роль. Обратитесь к администратору.", reply_markup=get_main_keyboard(user_id))

@dp.message(ReportState.completed_task)
async def process_completed_task(message: Message, state: FSMContext):
    await state.update_data(completed_task=message.text)
    await message.answer("📅 Теперь укажите, чем планируете заняться дальше.")
    await state.set_state(ReportState.next_task)

@dp.message(ReportState.next_task)
async def process_next_task(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_name = get_user_name(user_id) or f"User_{user_id}"
    data = await state.get_data()
    completed_task = data.get("completed_task", "N/A")
    next_task = message.text
    local_time = datetime.now() + timedelta(hours=6)
    timestamp = local_time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO reports (user_id, completed_task, next_task, timestamp) VALUES (?, ?, ?, ?)",
                            (user_id, completed_task, next_task, timestamp))
            conn.commit()
        await message.answer("✅ Отчёт успешно сохранён!", reply_markup=get_main_keyboard(user_id))
        logging.info(f"Отчет сохранен для пользователя {user_name} (ID: {user_id})")
    except sqlite3.Error as e:
        logging.error(f"Ошибка сохранения отчета в БД для user_id {user_id}: {e}")
        await message.answer("❌ Произошла ошибка при сохранении отчета.", reply_markup=get_main_keyboard(user_id))
        await state.clear()
        return
    await state.clear()

    global GROUP_CHAT_ID, TOPIC_ID
    if GROUP_CHAT_ID and TOPIC_ID:
        report_text = (
            f"📊 *Новый отчёт*\n\n"
            f"👤 *Пользователь:* {user_name}\n"
            f"🕒 *Время:* {timestamp}\n"
            f"✅ *Выполнено:* {completed_task}\n"
            f"⏭ *Планы:* {next_task}"
        )
        try:
            await bot.send_message(chat_id=GROUP_CHAT_ID, text=report_text, message_thread_id=TOPIC_ID, parse_mode="Markdown")
            logging.info(f"Отчет от {user_name} отправлен в топик {TOPIC_ID} группы {GROUP_CHAT_ID}")
        except Exception as e:
            logging.error(f"Ошибка при отправке отчета в топик группы {GROUP_CHAT_ID} (топик {TOPIC_ID}): {e}")
            try:
                await bot.send_message(SUPER_ADMIN_ID, f"⚠️ Не удалось отправить отчет от {user_name} в группу {GROUP_CHAT_ID}, топик {TOPIC_ID}. Ошибка: {e}")
            except Exception as admin_e:
                logging.error(f"Не удалось уведомить суперадмина об ошибке отправки отчета: {admin_e}")

# --- Просмотр отчётов ---
# (view_reports_handler без изменений)
@dp.message(F.text.in_(["📜 Просмотреть все отчёты", "📜 Просмотреть мои отчёты", "👥 Просмотреть отчёты по пользователю"]))
async def view_reports_handler(message: Message, state: FSMContext):
    """Обрабатывает кнопки просмотра отчетов."""
    user_id = message.from_user.id
    role = get_user_role(user_id)
    command = message.text

    await state.clear()

    if command == "📜 Просмотреть все отчёты":
        if role in ['admin', 'super_admin']:
            await send_all_reports(message) # <--- Вызываем обновленную функцию
        else:
            await message.answer("⛔ У вас нет прав для просмотра всех отчётов.", reply_markup=get_main_keyboard(user_id))

    elif command == "📜 Просмотреть мои отчёты":
        await send_user_reports(message, user_id)

    elif command == "👥 Просмотреть отчёты по пользователю":
        if role in ['admin', 'super_admin']:
            users_with_reports = get_users_with_reports()
            if not users_with_reports:
                await message.answer("Нет пользователей с отчетами для просмотра.")
                return
            keyboard = create_user_selection_keyboard(users_with_reports, 1, CALLBACK_PREFIX_VIEW_REPORTS)
            await message.answer("Выберите пользователя для просмотра отчетов:", reply_markup=keyboard)
        else:
            await message.answer("⛔ У вас нет прав для просмотра отчетов других пользователей.", reply_markup=get_main_keyboard(user_id))

# --- ИЗМЕНЕННАЯ ФУНКЦИЯ ---
async def send_all_reports(message: Message):
    """Отправляет последние отчеты, сгруппированные по пользователям."""
    user_id = message.from_user.id
    logging.info(f"Пользователь {user_id} запросил все отчеты.")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Получаем N последних отчетов, сортируем по пользователю и времени
            # LIMIT 100 - берем последние 100 отчетов для группировки
            cursor.execute("""
                SELECT u.user_name, r.completed_task, r.next_task, r.timestamp
                FROM reports r JOIN users u ON r.user_id = u.user_id
                WHERE r.id IN (SELECT id FROM reports ORDER BY timestamp DESC LIMIT 100)
                ORDER BY u.user_name ASC, r.timestamp DESC
            """)
            reports = cursor.fetchall()

        if not reports:
            await message.answer("📭 Отчётов пока нет.", reply_markup=get_main_keyboard(user_id))
            return

        # --- ЛОГИКА ГРУППИРОВКИ (как в send_daily_summary) ---
        grouped_reports: typing.Dict[str, typing.List[typing.Tuple[str, str, str]]] = {} # Используем typing.Dict и т.д.
        processed_report_count = 0
        for user_name, completed_task, next_task, timestamp in reports:
            # Используем имя пользователя как ключ
            uname = user_name if user_name else f"User_ID_{timestamp.split('-')[0]}" # Обработка случая без имени
            if uname not in grouped_reports:
                grouped_reports[uname] = []
            # Преобразуем timestamp в H:M формат для отображения
            time_str = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M") # Добавим дату для ясности
            grouped_reports[uname].append((completed_task, next_task, time_str))
            processed_report_count += 1


        # --- ФОРМАТИРОВАНИЕ С ГРУППИРОВКОЙ ---
        user_blocks = []
        # Сортируем пользователей по имени для стабильного порядка
        for uname in sorted(grouped_reports.keys()):
            u_reports = grouped_reports[uname]
            # Форматируем отчеты одного пользователя, разделяя их ОДНИМ переносом строки
            # Отчеты уже отсортированы по времени DESC из-за ORDER BY в SQL
            reports_text = "\n".join(
                [f"  🕒 {t}\n  ✅ {comp}\n  ⏭ {nxt}" for comp, nxt, t in u_reports]
            )
            # Экранируем имя пользователя
            safe_uname = uname.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')
            user_blocks.append(f"👤 *{safe_uname}*:\n{reports_text}")

        # Собираем итоговый текст, разделяя блоки пользователей ДВУМЯ переносами строки
        response = f"📜 *Последние {processed_report_count} отчётов (сгруппировано по пользователям):*\n\n" + "\n\n".join(user_blocks)
        # --- КОНЕЦ ФОРМАТИРОВАНИЯ ---


        # Отправка сообщения (возможно, частями)
        for i in range(0, len(response), 4096):
            # Отправляем с reply_markup=None, чтобы клавиатура не мешала просмотру
            await message.answer(response[i:i + 4096], parse_mode="Markdown", reply_markup=None)

        # После отправки последнего блока возвращаем клавиатуру
        # Проверяем, было ли сообщение отправлено (на случай пустого response)
        if response and len(response) % 4096 != 0:
            await message.answer("--- Конец списка ---", reply_markup=get_main_keyboard(user_id))
        elif not response: # Если response пустой (маловероятно, но все же)
            await message.answer("Не удалось сформировать список отчетов.", reply_markup=get_main_keyboard(user_id))


    except sqlite3.Error as e:
        logging.error(f"Ошибка БД при получении всех отчетов: {e}")
        await message.answer("❌ Произошла ошибка базы данных при получении отчетов.", reply_markup=get_main_keyboard(user_id))
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при отправке всех отчетов: {e}")
        await message.answer("❌ Произошла непредвиденная ошибка при получении отчетов.", reply_markup=get_main_keyboard(user_id))
# --- КОНЕЦ ИЗМЕНЕННОЙ ФУНКЦИИ ---


# (send_user_reports без изменений)
async def send_user_reports(message_or_callback: typing.Union[Message, CallbackQuery], target_user_id: int): # Используем typing.Union
    """Отправляет отчеты конкретного пользователя. Может вызываться из Message или CallbackQuery."""
    is_callback = isinstance(message_or_callback, CallbackQuery)
    requesting_user_id = message_or_callback.from_user.id
    reply_target = message_or_callback.message if is_callback else message_or_callback
    keyboard_to_return = get_main_keyboard(requesting_user_id)

    target_user_name = get_user_name(target_user_id)
    if not target_user_name:
        await reply_target.answer(f"❌ Пользователь с ID {target_user_id} не найден.", reply_markup=keyboard_to_return)
        if is_callback: await message_or_callback.answer()
        return

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT completed_task, next_task, timestamp FROM reports WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50", (target_user_id,))
            reports = cursor.fetchall()
        if not reports:
            await reply_target.answer(f"📭 У пользователя {target_user_name} (ID: {target_user_id}) нет отчётов.", reply_markup=keyboard_to_return)
            if is_callback: await message_or_callback.answer()
            return

        safe_target_name = target_user_name.replace('*', '\\*').replace('_', '\\_').replace('`', '\\`')
        report_lines = [f"🕒 *{ts}*\n✅ {comp}\n⏭ {nxt}" for comp, nxt, ts in reports]
        response = f"📜 *Отчёты {safe_target_name} (ID: {target_user_id}) (последние 50):*\n\n" + "\n\n".join(report_lines)

        if is_callback:
            await reply_target.edit_text("Отправка отчетов...")

        for i in range(0, len(response), 4096):
            await bot.send_message(reply_target.chat.id, response[i:i + 4096], parse_mode="Markdown", reply_markup=None)

        await bot.send_message(reply_target.chat.id, f"--- Конец отчетов {safe_target_name} ---", reply_markup=keyboard_to_return)

        if is_callback:
            await message_or_callback.answer()

    except Exception as e:
        logging.error(f"Ошибка при отправке отчетов пользователя {target_user_id}: {e}")
        error_message = "❌ Произошла ошибка при получении отчетов."
        if is_callback:
            await reply_target.edit_text(error_message)
            await message_or_callback.answer("Произошла ошибка", show_alert=True)
        else:
            await reply_target.answer(error_message, reply_markup=keyboard_to_return)


# (process_view_reports_callback без изменений)
@dp.callback_query(F.data.startswith(CALLBACK_PREFIX_VIEW_REPORTS))
async def process_view_reports_callback(callback: CallbackQuery):
    """Обрабатывает выбор пользователя для просмотра отчетов."""
    requesting_user_id = callback.from_user.id
    role = get_user_role(requesting_user_id)

    if role not in ['admin', 'super_admin']:
        await callback.answer("⛔ Действие доступно только администраторам.", show_alert=True)
        return

    action_part = callback.data[len(CALLBACK_PREFIX_VIEW_REPORTS):]

    if action_part == ACTION_CANCEL:
        await callback.message.edit_text("Просмотр отчетов отменен.")
        await callback.answer()
        return

    try:
        action, value = action_part.split(":", 1)
    except ValueError:
        logging.warning(f"Некорректный формат callback data (view_rep): {callback.data}")
        await callback.answer("Ошибка обработки данных.", show_alert=True)
        await callback.message.edit_text("Произошла внутренняя ошибка.")
        return

    if action == ACTION_SELECT:
        try:
            user_id_to_view = int(value)
            await send_user_reports(callback, user_id_to_view)
        except ValueError:
            await callback.answer("Ошибка: Некорректный ID.", show_alert=True)
            await callback.message.edit_text("Произошла ошибка выбора пользователя.")
        except Exception as e:
            logging.error(f"Непредвиденная ошибка при выборе пользователя для просмотра отчетов {value}: {e}")
            await callback.answer("Непредвиденная ошибка.", show_alert=True)
            await callback.message.edit_text("Произошла непредвиденная ошибка.")

    elif action == ACTION_PAGE:
        try:
            page = int(value)
            users_with_reports = get_users_with_reports()
            keyboard = create_user_selection_keyboard(users_with_reports, page, CALLBACK_PREFIX_VIEW_REPORTS)
            await callback.message.edit_reply_markup(reply_markup=keyboard)
            await callback.answer()
        except ValueError:
            logging.warning(f"Некорректный номер страницы (view_rep): {value}")
            await callback.answer("Ошибка: Некорректный номер страницы.", show_alert=True)
        except Exception as e:
            logging.error(f"Ошибка обновления клавиатуры просмотра отчетов: {e}")
            await callback.answer("Не удалось обновить список.")


# --- Администрирование (Выбор из списка) ---
# (add_admin_start, remove_admin_start, process_add_admin_callback, process_remove_admin_callback без изменений)
@dp.message(F.text == "➕ Добавить админа")
async def add_admin_start(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        await message.answer("⛔ Эта команда доступна только супер-администратору.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    users_to_add = get_users_by_role('user')
    if not users_to_add:
        await message.answer("Нет пользователей для назначения администраторами.")
        return
    keyboard = create_user_selection_keyboard(users_to_add, 1, CALLBACK_PREFIX_ADD_ADMIN)
    await message.answer("Выберите пользователя для назначения администратором:", reply_markup=keyboard)

@dp.message(F.text == "➖ Удалить админа")
async def remove_admin_start(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        await message.answer("⛔ Эта команда доступна только супер-администратору.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    admins_to_remove = get_users_by_role('admin')
    if not admins_to_remove:
        await message.answer("Нет администраторов для понижения.")
        return
    keyboard = create_user_selection_keyboard(admins_to_remove, 1, CALLBACK_PREFIX_REMOVE_ADMIN)
    await message.answer("Выберите администратора для понижения:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith(CALLBACK_PREFIX_ADD_ADMIN))
async def process_add_admin_callback(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("⛔ Действие доступно только супер-администратору.", show_alert=True)
        return
    action_part = callback.data[len(CALLBACK_PREFIX_ADD_ADMIN):]
    if action_part == ACTION_CANCEL:
        await callback.message.edit_text("Действие отменено.")
        await callback.answer()
        return
    try: action, value = action_part.split(":", 1)
    except ValueError:
        logging.warning(f"Некорректный формат callback data (add_admin): {callback.data}")
        await callback.answer("Ошибка обработки данных.", show_alert=True); await callback.message.edit_text("Произошла внутренняя ошибка."); return
    if action == ACTION_SELECT:
        try:
            user_id_to_add = int(value); user_name = get_user_name(user_id_to_add) or f"ID: {user_id_to_add}"
            with get_db_connection() as conn: cursor = conn.cursor(); cursor.execute("UPDATE users SET role = 'admin' WHERE user_id = ?", (user_id_to_add,)); conn.commit()
            ADMIN_IDS.add(user_id_to_add); save_config()
            await callback.message.edit_text(f"✅ Пользователь {user_name} назначен администратором.")
            logging.info(f"{user_name} ({user_id_to_add}) назначен админом {callback.from_user.id}")
            try: await bot.send_message(user_id_to_add, "🎉 Поздравляем! Вас назначили администратором.", reply_markup=get_main_keyboard(user_id_to_add))
            except Exception as e: logging.warning(f"Не удалось уведомить нового админа {user_id_to_add}: {e}")
        except ValueError: await callback.answer("Ошибка: Некорректный ID.", show_alert=True); await callback.message.edit_text("Произошла ошибка.")
        except sqlite3.Error as e: logging.error(f"Ошибка БД при назначении админа {value}: {e}"); await callback.answer("Ошибка базы данных.", show_alert=True); await callback.message.edit_text("Произошла ошибка БД.")
        except Exception as e: logging.error(f"Ошибка при назначении админа {value}: {e}"); await callback.answer("Непредвиденная ошибка.", show_alert=True); await callback.message.edit_text("Произошла ошибка.")
    elif action == ACTION_PAGE:
        try:
            page = int(value); users_to_add = get_users_by_role('user'); keyboard = create_user_selection_keyboard(users_to_add, page, CALLBACK_PREFIX_ADD_ADMIN)
            await callback.message.edit_reply_markup(reply_markup=keyboard); await callback.answer()
        except ValueError: logging.warning(f"Некорректный номер стр. (add_admin): {value}"); await callback.answer("Ошибка: Некорректный номер стр.", show_alert=True)
        except Exception as e: logging.error(f"Ошибка обновления клав. добавления админа: {e}"); await callback.answer("Не удалось обновить список.")

@dp.callback_query(F.data.startswith(CALLBACK_PREFIX_REMOVE_ADMIN))
async def process_remove_admin_callback(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("⛔ Действие доступно только супер-администратору.", show_alert=True); return
    action_part = callback.data[len(CALLBACK_PREFIX_REMOVE_ADMIN):]
    if action_part == ACTION_CANCEL: await callback.message.edit_text("Действие отменено."); await callback.answer(); return
    try: action, value = action_part.split(":", 1)
    except ValueError: logging.warning(f"Некорректный формат callback data (rem_admin): {callback.data}"); await callback.answer("Ошибка обработки данных.", show_alert=True); await callback.message.edit_text("Внутр. ошибка."); return
    if action == ACTION_SELECT:
        try:
            user_id_to_remove = int(value); user_name = get_user_name(user_id_to_remove) or f"ID: {user_id_to_remove}"
            with get_db_connection() as conn: cursor = conn.cursor(); cursor.execute("UPDATE users SET role = 'user' WHERE user_id = ?", (user_id_to_remove,)); conn.commit()
            if user_id_to_remove in ADMIN_IDS: ADMIN_IDS.remove(user_id_to_remove)
            save_config()
            await callback.message.edit_text(f"✅ Администратор {user_name} понижен до пользователя.")
            logging.info(f"Админ {user_name} ({user_id_to_remove}) понижен супер-админом {callback.from_user.id}")
            try: await bot.send_message(user_id_to_remove, "ℹ️ Вас понизили до обычного пользователя.", reply_markup=get_main_keyboard(user_id_to_remove))
            except Exception as e: logging.warning(f"Не удалось уведомить пользователя о понижении {user_id_to_remove}: {e}")
        except ValueError: await callback.answer("Ошибка: Некорректный ID.", show_alert=True); await callback.message.edit_text("Произошла ошибка.")
        except sqlite3.Error as e: logging.error(f"Ошибка БД при удалении админа {value}: {e}"); await callback.answer("Ошибка базы данных.", show_alert=True); await callback.message.edit_text("Произошла ошибка БД.")
        except Exception as e: logging.error(f"Ошибка при удалении админа {value}: {e}"); await callback.answer("Непредвиденная ошибка.", show_alert=True); await callback.message.edit_text("Произошла ошибка.")
    elif action == ACTION_PAGE:
        try:
            page = int(value); admins_to_remove = get_users_by_role('admin'); keyboard = create_user_selection_keyboard(admins_to_remove, page, CALLBACK_PREFIX_REMOVE_ADMIN)
            await callback.message.edit_reply_markup(reply_markup=keyboard); await callback.answer()
        except ValueError: logging.warning(f"Некорректный номер стр. (rem_admin): {value}"); await callback.answer("Ошибка: Некорректный номер стр.", show_alert=True)
        except Exception as e: logging.error(f"Ошибка обновления клав. удаления админа: {e}"); await callback.answer("Не удалось обновить список.")

# --- Удаление пользователя (Выбор из списка) ---
@dp.message(F.text == "🗑️ Удалить пользователя")
async def delete_user_start(message: Message):
    if message.from_user.id != SUPER_ADMIN_ID:
        await message.answer("⛔ Эта команда доступна только супер-администратору.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    users_to_delete = get_all_users_except_super_admin()
    if not users_to_delete:
        await message.answer("Нет пользователей для удаления.")
        return
    keyboard = create_user_selection_keyboard(users_to_delete, 1, CALLBACK_PREFIX_DELETE_USER)
    await message.answer("Выберите пользователя для удаления:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith(CALLBACK_PREFIX_DELETE_USER))
async def process_delete_user_callback(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("⛔ Действие доступно только супер-администратору.", show_alert=True)
        return
    action_part = callback.data[len(CALLBACK_PREFIX_DELETE_USER):]
    if action_part == ACTION_CANCEL:
        await callback.message.edit_text("Удаление пользователя отменено.")
        await callback.answer()
        return
    try: action, value = action_part.split(":", 1)
    except ValueError:
        logging.warning(f"Некорректный формат callback data (del_user): {callback.data}")
        await callback.answer("Ошибка обработки данных.", show_alert=True); await callback.message.edit_text("Произошла внутренняя ошибка."); return
    if action == ACTION_SELECT:
        try:
            user_id_to_delete = int(value); user_name = get_user_name(user_id_to_delete) or f"ID: {user_id_to_delete}"
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id_to_delete,))
                conn.commit()
            await callback.message.edit_text(f"🗑️ Пользователь {user_name} (ID: {user_id_to_delete}) был удален.")
            logging.info(f"Пользователь {user_name} ({user_id_to_delete}) удален супер-админом {callback.from_user.id}")
            try:
                await bot.send_message(user_id_to_delete, "ℹ️ Ваша учетная запись была удалена администратором. Пожалуйста, свяжитесь с администратором для получения дополнительной информации.", reply_markup=ReplyKeyboardRemove())
            except Exception as e:
                logging.warning(f"Не удалось уведомить удаленного пользователя {user_id_to_delete}: {e}")
        except ValueError:
            await callback.answer("Ошибка: Некорректный ID.", show_alert=True); await callback.message.edit_text("Произошла ошибка.")
        except sqlite3.Error as e:
            logging.error(f"Ошибка БД при удалении пользователя {value}: {e}")
            await callback.answer("Ошибка базы данных.", show_alert=True); await callback.message.edit_text("Произошла ошибка БД.")
        except Exception as e:
            logging.error(f"Ошибка при удалении пользователя {value}: {e}")
            await callback.answer("Непредвиденная ошибка.", show_alert=True); await callback.message.edit_text("Произошла ошибка.")
    elif action == ACTION_PAGE:
        try:
            page = int(value); users_to_delete = get_all_users_except_super_admin(); keyboard = create_user_selection_keyboard(users_to_delete, page, CALLBACK_PREFIX_DELETE_USER)
            await callback.message.edit_reply_markup(reply_markup=keyboard); await callback.answer()
        except ValueError:
            logging.warning(f"Некорректный номер стр. (del_user): {value}")
            await callback.answer("Ошибка: Некорректный номер стр.", show_alert=True)
        except Exception as e:
            logging.error(f"Ошибка обновления клавиатуры удаления пользователя: {e}")
            await callback.answer("Не удалось обновить список.")

# --- Запуск бота ---
async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())