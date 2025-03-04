import asyncio
import logging
from aiogram import types

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram.types import Message
import os
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiogram.types import BotCommand
from dotenv import load_dotenv
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT
)''')
cur.execute('''CREATE TABLE IF NOT EXISTS weekly_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    year INTEGER,
    week INTEGER,
    hours REAL,
    UNIQUE(user_id, year, week),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
)''')
cur.execute('''CREATE TABLE IF NOT EXISTS monthly_hours (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    year INTEGER,
    month INTEGER,
    hours REAL,
    UNIQUE(user_id, year, month),
    FOREIGN KEY(user_id) REFERENCES users(user_id)
)''')
conn.commit()

class Register(StatesGroup):
    waiting_for_name = State()
    waiting_for_surname = State()

class InputHours(StatesGroup):
    waiting_for_week_hours = State()
    waiting_for_month_hours = State()
    waiting_for_week_hours_edit = State()


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    cur = conn.cursor()
    cur.execute("SELECT first_name, last_name FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    if user:
        first_name, last_name = user
        await message.answer(f"Привет, {first_name}! Вы уже зарегистрированы в системе.")
    else:
        await message.answer("Здравствуйте! Пожалуйста, представьтесь – введите ваше имя:")
        await state.set_state(Register.waiting_for_name)

@dp.message(Command("notify"))
async def manual_notify(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав на выполнение этой команды.")
        return
    await send_weekly_prompt()
    await send_monthly_prompt()
    await message.answer("✅ Уведомления отправлены пользователям.")


@dp.message(Command("week"))
async def manual_week_hours(message: Message, state: FSMContext):
    user_id = message.from_user.id
    now = datetime.now()
    year, week_num, _ = now.isocalendar()

    cur = conn.cursor()
    cur.execute("""
        SELECT hours FROM weekly_hours 
        WHERE user_id = ? AND year = ? AND week = ?
    """, (user_id, year, week_num))
    existing_data = cur.fetchone()

    if existing_data:
        await message.answer(
            f"⛔ Вы уже ввели {existing_data[0]} часов за эту неделю ({week_num}-я неделя {year} года).")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) == 2 and parts[1].isdigit():
        hours = float(parts[1])
        cur.execute("""
            INSERT INTO weekly_hours (user_id, year, week, hours) 
            VALUES (?, ?, ?, ?)
        """, (user_id, year, week_num, hours))
        conn.commit()
        await message.answer(f"✅ Записано {hours} часов за текущую неделю ({week_num}-я неделя {year} года).")
        return

    await message.answer("Введите количество рабочих часов за текущую неделю:")
    await state.set_state(InputHours.waiting_for_week_hours)
    await state.update_data(target_year=year, target_week=week_num)


@dp.message(Command("month"))
async def manual_month_hours(message: Message, state: FSMContext):
    user_id = message.from_user.id
    now = datetime.now()
    year, month = now.year, now.month

    cur = conn.cursor()
    cur.execute("""
        SELECT hours FROM monthly_hours 
        WHERE user_id = ? AND year = ? AND month = ?
    """, (user_id, year, month))
    existing_data = cur.fetchone()

    if existing_data:
        await message.answer(f"⛔ Вы уже ввели {existing_data[0]} часов за этот месяц ({month:02d}.{year}).")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) == 2 and parts[1].isdigit():
        hours = float(parts[1])
        cur.execute("""
            INSERT INTO monthly_hours (user_id, year, month, hours) 
            VALUES (?, ?, ?, ?)
        """, (user_id, year, month, hours))
        conn.commit()
        await message.answer(f"✅ Записано {hours} часов за текущий месяц ({month:02d}.{year}).")
        return

    await message.answer("Введите количество рабочих часов за текущий месяц:")
    await state.set_state(InputHours.waiting_for_month_hours)
    await state.update_data(target_year=year, target_month=month)

@dp.message(Register.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(first_name=message.text.strip())
    await message.answer("Спасибо! Теперь введите вашу фамилию:")
    await state.set_state(Register.waiting_for_surname)

@dp.message(Register.waiting_for_surname)
async def process_surname(message: types.Message, state: FSMContext):
    data = await state.get_data()
    first_name = data.get("first_name", "").strip()
    last_name = message.text.strip()
    user_id = message.from_user.id
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO users (user_id, first_name, last_name) VALUES (?, ?, ?)",
                (user_id, first_name, last_name))
    conn.commit()
    await state.clear()
    await message.answer(f"Спасибо, {first_name}! Вы зарегистрированы. "
                         f"Бот будет напоминать вам вводить рабочие часы каждую неделю и месяц."
                         f"Для того чтобы узнать больше, введи /help")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🤖 *Этот бот помогает собирать данные о ваших рабочих часах.*\n\n"
        "📌 /start – регистрация в системе\n"
        "📌 /help – показать справку по командам\n"
        "📌 /week – ввести рабочие часы за неделю (если ещё не введены)\n"
        "📌 /weekchange – изменить рабочие часы за текущую неделю\n"
        "📌 /month – ввести запланированные часы на месяц (если ещё не введены)\n"
    )

    if message.from_user.id in ADMIN_IDS:
        help_text += (
            "\n*🔧 Админ-команды:*\n"
            "📊 /users – список всех пользователей\n"
            "📊 /export – экспорт всех данных в Excel\n"
            "🔧 /editname <user_id> <новое_имя> – изменить имя пользователя\n"
            "🔧 /editusername <user_id> <новая_фамилия> – изменить фамилию пользователя\n"
            "🔧 /remove_user <user_id> – удалить пользователя\n"
            "📢 /notify – отправить напоминание пользователям о вводе рабочих часов\n"
        )

    await message.answer(help_text, parse_mode="Markdown")




#АДМИНКА
@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    cur = conn.cursor()
    cur.execute("SELECT user_id, first_name, last_name FROM users")
    users_list = cur.fetchall()
    if not users_list:
        await message.answer("Пока нет ни одного зарегистрированного пользователя.")
        return
    text_lines = ["📋 *Список пользователей:*"]
    for user_id, first_name, last_name in users_list:
        cur.execute("SELECT year, month, hours FROM monthly_hours WHERE user_id=? ORDER BY year DESC, month DESC LIMIT 1", (user_id,))
        mon_data = cur.fetchone()
        if mon_data:
            mon_year, mon_month, mon_hours = mon_data
            mon_info = f"{mon_hours} ч. (план {mon_month:02d}.{mon_year})"
        else:
            mon_info = "нет данных"
        cur.execute("SELECT year, week, hours FROM weekly_hours WHERE user_id=? ORDER BY year DESC, week DESC LIMIT 1", (user_id,))
        week_data = cur.fetchone()
        if week_data:
            week_year, week_num, week_hours = week_data
            week_info = f"{week_hours} ч. (нед. {week_num} {week_year}г.)"
        else:
            week_info = "нет данных"
        text_lines.append(f"{user_id}: *{first_name} {last_name}* — Месяц: {mon_info}, Неделя: {week_info}")
    await message.answer("\n".join(text_lines), parse_mode="Markdown")

@dp.message(Command("export"))
async def cmd_export(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    df_users = pd.read_sql_query("SELECT * FROM users", conn)
    df_weekly = pd.read_sql_query("SELECT * FROM weekly_hours", conn)
    df_monthly = pd.read_sql_query("SELECT * FROM monthly_hours", conn)

    excel_file = "work_hours.xlsx"

    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        sheet_count = 0

        if not df_weekly.empty:
            df_weekly = df_weekly.merge(df_users, on='user_id', how='left')
            df_weekly = df_weekly[['first_name', 'last_name', 'year', 'week', 'hours']]
            df_weekly.rename(columns={
                'first_name': 'First Name', 'last_name': 'Last Name',
                'year': 'Year', 'week': 'Week', 'hours': 'Hours'
            }, inplace=True)
            df_weekly.to_excel(writer, sheet_name="WeeklyHours", index=False)
            sheet_count += 1

        if not df_monthly.empty:
            df_monthly = df_monthly.merge(df_users, on='user_id', how='left')
            df_monthly = df_monthly[['first_name', 'last_name', 'year', 'month', 'hours']]
            df_monthly.rename(columns={
                'first_name': 'First Name', 'last_name': 'Last Name',
                'year': 'Year', 'month': 'Month', 'hours': 'Hours'
            }, inplace=True)
            df_monthly.to_excel(writer, sheet_name="MonthlyHours", index=False)
            sheet_count += 1
        if sheet_count == 0:
            df_empty = pd.DataFrame({"Сообщение": ["Нет данных для экспорта"]})
            df_empty.to_excel(writer, sheet_name="Empty", index=False)

    try:
        file = FSInputFile(excel_file)
        await message.answer_document(file, caption="📊 Отчет по рабочим часам")
    finally:
        os.remove(excel_file) #


@dp.message(Command("editusername"))
async def cmd_edit_surname(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав на выполнение этой команды.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("❗ Использование: `/editusername <user_id> <новая_фамилия>`", parse_mode="Markdown")
        return

    user_id_str, new_surname = parts[1], parts[2]

    if not user_id_str.isdigit():
        await message.answer("❗ Ошибка: `user_id` должен быть числом.", parse_mode="Markdown")
        return

    user_id = int(user_id_str)

    cur = conn.cursor()
    cur.execute("UPDATE users SET last_name=? WHERE user_id=?", (new_surname.strip(), user_id))

    if cur.rowcount == 0:
        await message.answer(f"❌ Пользователь с ID `{user_id}` не найден.", parse_mode="Markdown")
    else:
        conn.commit()
        await message.answer(f"✅ Фамилия пользователя `{user_id}` изменена на `{new_surname}`.", parse_mode="Markdown")


@dp.message(Command("editname"))
async def cmd_edit_name(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав на выполнение этой команды.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("❗ Использование: `/editname <user_id> <новое_имя>`", parse_mode="Markdown")
        return

    user_id_str, new_name = parts[1], parts[2]

    if not user_id_str.isdigit():
        await message.answer("❗ Ошибка: `user_id` должен быть числом.", parse_mode="Markdown")
        return

    user_id = int(user_id_str)

    cur = conn.cursor()
    cur.execute("UPDATE users SET first_name=? WHERE user_id=?", (new_name.strip(), user_id))

    if cur.rowcount == 0:
        await message.answer(f"❌ Пользователь с ID `{user_id}` не найден.", parse_mode="Markdown")
    else:
        conn.commit()
        await message.answer(f"✅ Имя пользователя `{user_id}` изменено на `{new_name}`.", parse_mode="Markdown")


@dp.message(Command("removeuser"))
async def cmd_remove_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав на выполнение этой команды.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❗ Использование: `/remove_user <user_id>`", parse_mode="Markdown")
        return

    user_id_str = parts[1]
    if not user_id_str.isdigit():
        await message.answer("❗ Ошибка: `user_id` должен быть числом.", parse_mode="Markdown")
        return

    user_id = int(user_id_str)

    cur = conn.cursor()

    cur.execute("SELECT first_name, last_name FROM users WHERE user_id=?", (user_id,))
    user = cur.fetchone()
    if not user:
        await message.answer(f"❌ Пользователь с ID `{user_id}` не найден.", parse_mode="Markdown")
        return

    cur.execute("DELETE FROM weekly_hours WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM monthly_hours WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    conn.commit()

    await message.answer(f"✅ Пользователь `{user_id}` ({user[0]} {user[1]}) и все его данные удалены.", parse_mode="Markdown")

@dp.message(Command("edit_name"))
async def cmd_edit_name(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply("Использование: `/edit_name <user_id> <новое имя>`", parse_mode="Markdown")
        return
    _, uid_str, new_name = parts
    if not uid_str.isdigit():
        await message.reply("Ошибка: user_id должен быть числом.")
        return
    user_id = int(uid_str)
    cur = conn.cursor()
    cur.execute("UPDATE users SET first_name=? WHERE user_id=?", (new_name.strip(), user_id))
    if cur.rowcount == 0:
        await message.reply("Пользователь с ID {} не найден.".format(user_id))
    else:
        conn.commit()
        await message.reply("Имя пользователя обновлено успешно.")

@dp.message(Command("edit_surname"))
async def cmd_edit_surname(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply("Использование: `/edit_surname <user_id> <новая фамилия>`", parse_mode="Markdown")
        return
    _, uid_str, new_surname = parts
    if not uid_str.isdigit():
        await message.reply("Ошибка: user_id должен быть числом.")
        return
    user_id = int(uid_str)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_name=? WHERE user_id=?", (new_surname.strip(), user_id))
    if cur.rowcount == 0:
        await message.reply(f"Пользователь с ID {user_id} не найден.")
    else:
        conn.commit()
        await message.reply("Фамилия пользователя обновлена успешно.")

@dp.message(Command("remove_user"))
async def cmd_remove_user(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Использование: `/remove_user <user_id>`", parse_mode="Markdown")
        return
    _, uid_str = parts
    if not uid_str.isdigit():
        await message.reply("Ошибка: user_id должен быть числом.")
        return
    user_id = int(uid_str)
    cur = conn.cursor()
    cur.execute("DELETE FROM weekly_hours WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM monthly_hours WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    if cur.rowcount == 0:
        await message.reply(f"Пользователь с ID {user_id} не найден.")
    else:
        conn.commit()
        await message.reply(f"Пользователь {user_id} и все его данные удалены.")


@dp.message(Command("weekchange"))
async def change_week_hours(message: Message, state: FSMContext):
    user_id = message.from_user.id
    now = datetime.now()
    year, week_num, _ = now.isocalendar()
    cur = conn.cursor()
    cur.execute("""
        SELECT hours FROM weekly_hours 
        WHERE user_id = ? AND year = ? AND week = ?
    """, (user_id, year, week_num))
    existing_data = cur.fetchone()

    if not existing_data:
        await message.answer(f"❌ У вас еще не записаны часы за эту неделю ({week_num}-я неделя {year}). Используйте /weekh для добавления.")
        return
    await state.set_state(InputHours.waiting_for_week_hours_edit)
    await state.update_data(target_year=year, target_week=week_num)

    await message.answer(f"📝 Введите новое количество часов за текущую неделю ({week_num}-я неделя {year} года):")

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Показать справку"),
        BotCommand(command="week", description="Ввести рабочие часы за неделю"),
        BotCommand(command="weekchange", description="Изменить рабочие часы за неделю"),
        BotCommand(command="month", description="Ввести рабочие часы за месяц"),
    ]
    await bot.set_my_commands(commands)


@dp.message(InputHours.waiting_for_week_hours)
async def process_week_hours(message: Message, state: FSMContext):
    text = message.text.strip().replace(',', '.')

    try:
        hours = float(text)
    except ValueError:
        await message.reply("❗ Пожалуйста, введите число часов (например, 40).")
        return

    data = await state.get_data()
    year = data.get("target_year")
    week_num = data.get("target_week")
    user_id = message.from_user.id

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO weekly_hours (user_id, year, week, hours) 
        VALUES (?, ?, ?, ?)
    """, (user_id, year, week_num, hours))
    conn.commit()

    await state.clear()
    await message.reply(f"✅ Записано {hours} часов за текущую неделю ({week_num}-я неделя {year} года).")


@dp.message(InputHours.waiting_for_month_hours)
async def process_month_hours(message: Message, state: FSMContext):
    text = message.text.strip().replace(',', '.')

    try:
        hours = float(text)
    except ValueError:
        await message.reply("❗ Пожалуйста, введите число часов (например, 160).")
        return

    data = await state.get_data()
    year = data.get("target_year")
    month = data.get("target_month")
    user_id = message.from_user.id

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO monthly_hours (user_id, year, month, hours) 
        VALUES (?, ?, ?, ?)
    """, (user_id, year, month, hours))
    conn.commit()

    await state.clear()
    await message.reply(f"✅ Записано {hours} часов за текущий месяц ({month:02d}.{year}).")

@dp.message(InputHours.waiting_for_week_hours_edit)
async def process_week_hours_edit(message: Message, state: FSMContext):
    text = message.text.strip().replace(',', '.')

    try:
        hours = float(text)
    except ValueError:
        await message.reply("❗ Пожалуйста, введите число часов (например, 40).")
        return

    data = await state.get_data()
    year = data.get("target_year")
    week_num = data.get("target_week")
    user_id = message.from_user.id

    if year is None or week_num is None:
        await message.reply("⚠ Ошибка: Не удалось определить неделю. Попробуйте снова.")
        return

    cur = conn.cursor()
    cur.execute("""
        UPDATE weekly_hours 
        SET hours = ? 
        WHERE user_id = ? AND year = ? AND week = ?
    """, (hours, user_id, year, week_num))
    conn.commit()

    await state.clear()
    await message.reply(f"✅ Обновлено: {hours} часов за текущую неделю ({week_num}-я неделя {year}).")

#еженедельные и ежемесячные напоминания
async def send_weekly_prompt():
    now = datetime.now()
    monday_this_week = now - timedelta(days=now.weekday())
    last_week_monday = monday_this_week - timedelta(days=7)
    year, week_num, _ = last_week_monday.isocalendar()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    for (uid,) in users:
        try:
            await bot.send_message(uid, "⏱ Пожалуйста, введите часы работы за текущую неделю.")
            state = dp.current_state(chat=uid, user=uid)
            await state.set_state(InputHours.waiting_for_week_hours.state)
            await state.update_data(target_year=year, target_week=week_num)
        except Exception as e:
            logging.error(f"Ошибка отправки еженедельного напоминания для {uid}: {e}")

async def send_monthly_prompt():
    now = datetime.now()
    year = now.year
    month = now.month
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    for (uid,) in users:
        try:
            await bot.send_message(uid, "📅 Пожалуйста, введите запланированные часы на текущий месяц.")
            state = dp.current_state(chat=uid, user=uid)
            await state.set_state(InputHours.waiting_for_month_hours.state)
            await state.update_data(target_year=year, target_month=month)
        except Exception as e:
            logging.error(f"Ошибка отправки ежемесячного напоминания для {uid}: {e}")


scheduler = AsyncIOScheduler()
scheduler.add_job(send_weekly_prompt, CronTrigger(day_of_week='mon', hour=9, minute=0))
scheduler.add_job(send_monthly_prompt, CronTrigger(day='1', hour=9, minute=0))

async def on_startup(dp):
    scheduler.start()
    logging.info("Scheduler started. Bot is up and running.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await set_bot_commands(bot)
    dp["bot"] = bot
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

