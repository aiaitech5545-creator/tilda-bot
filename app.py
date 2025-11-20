import asyncio
import os
import json
import secrets
import string
from typing import Optional, Tuple, List, Dict
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command

import gspread
from google.oauth2.service_account import Credentials


# ========= ПЕРЕМЕННЫЕ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")
EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")

ACCESS_CODE_COLUMN_NAME = os.getenv("ACCESS_CODE_COLUMN_NAME", "AccessCode")
TELEGRAM_ID_COLUMN_NAME = os.getenv("TELEGRAM_ID_COLUMN_NAME", "TelegramID")

LESSONS_URL = os.getenv("LESSONS_URL")
PAGE_PASSWORD = os.getenv("PAGE_PASSWORD", "2025")   # <<< ИЗМЕНЁННЫЙ ПАРОЛЬ

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")


if not all([BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON]):
    print("❌ Не заданы необходимые переменные окружения!")
    exit(1)


bot = Bot(BOT_TOKEN)
dp = Dispatcher()

waiting_email: dict[int, bool] = {}


# ========= GOOGLE SHEETS ============

def get_gs_client() -> gspread.Client:
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


gs_client = get_gs_client()


def get_worksheet():
    sh = gs_client.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(SHEET_NAME)


def find_row_by_email(email: str):
    ws = get_worksheet()
    headers = ws.row_values(1)
    records = ws.get_all_records()

    email = email.strip().lower()
    for i, row in enumerate(records, start=2):
        if str(row.get(EMAIL_COLUMN_NAME, "")).strip().lower() == email:
            return i, row, headers

    return None, None, None


def find_row_by_telegram_id(tg_id: int):
    ws = get_worksheet()
    headers = ws.row_values(1)
    records = ws.get_all_records()

    tg_id = str(tg_id)
    for i, row in enumerate(records, start=2):
        if str(row.get(TELEGRAM_ID_COLUMN_NAME, "")).strip() == tg_id:
            return i, row, headers

    return None, None, None


def update_cell(row: int, column_name: str, value: str, headers: List[str]):
    if column_name not in headers:
        return
    col_index = headers.index(column_name) + 1
    ws = get_worksheet()
    ws.update_cell(row, col_index, value)


def generate_access_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def notify_admin(email: str, tg_id: int, access_code: str):
    if not ADMIN_TELEGRAM_ID:
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg = (
        "📩 *Новый доступ к курсу!*\n\n"
        f"📧 Email: `{email}`\n"
        f"🆔 Telegram ID: `{tg_id}`\n"
        f"🔑 Код доступа: `{access_code}`\n"
        f"⏱ Время: {ts}"
    )

    await bot.send_message(int(ADMIN_TELEGRAM_ID), msg, parse_mode="Markdown")


# ========= DEBUG ============

@dp.message(Command("debug"))
async def debug(message: Message):
    try:
        ws = get_worksheet()
        headers = ws.row_values(1)

        await message.answer(
            f"🛠 DEBUG\n\nЗаголовки колонок:\n{', '.join(headers)}"
        )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка: `{e}`\nЕсли что-то не так — напиши @ilinartem",
            parse_mode="Markdown"
        )


# ========= START ============

@dp.message(CommandStart())
async def start(message: Message):
    args = message.text.split()

    if len(args) > 1 and args[1] == "course_access":
        waiting_email[message.from_user.id] = True
        await message.answer(
            "👋 Привет! Я — бот доступа к курсу для моряков.\n\n"
            "✉️ Напиши *email*, который ты указывал при оплате.",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "⚓ Привет! Чтобы получить доступ к урокам:\n\n"
            "1️⃣ Оплати курс на сайте\n"
            "2️⃣ Вернись в бота по кнопке «Спасибо за оплату».\n\n"
            "Если что-то не так — напиши @ilinartem."
        )


# ========= /mycode =========

@dp.message(Command("mycode"))
async def mycode(message: Message):
    tg_id = message.from_user.id

    row_index, row, headers = find_row_by_telegram_id(tg_id)

    if not row_index:
        await message.answer(
            "❗️ Я не нашёл твой Telegram ID в базе.\n"
            "Если оплата была — заново введи email.\n\n"
            "Если что-то не так — напиши @ilinartem."
        )
        return

    access_code = row.get(ACCESS_CODE_COLUMN_NAME, "")
    if not access_code:
        access_code = generate_access_code()
        update_cell(row_index, ACCESS_CODE_COLUMN_NAME, access_code, headers)

    await message.answer(
        "🔁 *Повторная выдача*\n\n"
        f"🔐 Пароль к странице:\n`{PAGE_PASSWORD}`\n\n"
        f"🔑 Твой код доступа:\n`{access_code}`\n\n"
        "Нажми и удерживай, чтобы скопировать.\n"
        "Если что-то не так — напиши @ilinartem.",
        parse_mode="Markdown"
    )


# ========= ОБРАБОТКА EMAIL ============

@dp.message(F.text)
async def handle_email(message: Message):
    user_id = message.from_user.id

    if not waiting_email.get(user_id):
        await message.answer(
            "ℹ️ Чтобы получить доступ — вернись на сайт и нажми кнопку "
            "со страницы «Спасибо за оплату».\n\n"
            "Если что-то не так — напиши @ilinartem."
        )
        return

    email = message.text.strip()

    await message.answer(
        f"🔍 Проверяю оплату по адресу:\n`{email}`…",
        parse_mode="Markdown"
    )

    row_index, row, headers = find_row_by_email(email)

    if not row_index:
        await message.answer(
            "❌ Этот email не найден в списке оплат.\n"
            "Проверь правильность.\n"
            "Если всё верно — напиши @ilinartem."
        )
        return

    # Код доступа
    access_code = row.get(ACCESS_CODE_COLUMN_NAME, "")
    if not access_code:
        access_code = generate_access_code()
        update_cell(row_index, ACCESS_CODE_COLUMN_NAME, access_code, headers)

    # Telegram ID
    update_cell(row_index, TELEGRAM_ID_COLUMN_NAME, str(user_id), headers)

    waiting_email[user_id] = False

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Открыть уроки", url=LESSONS_URL)],
            [InlineKeyboardButton(text="⚠️ Сообщить о проблеме", url="https://t.me/ilinartem")]
        ]
    )

    await message.answer(
        "✅ *Доступ подтверждён!*\n\n"
        f"🔐 Пароль к странице:\n`{PAGE_PASSWORD}`\n"
        f"🔑 Твой индивидуальный код:\n`{access_code}`\n\n"
        "Нажми кнопку ниже, чтобы перейти к урокам.\n\n"
        "Если что-то не так — напиши @ilinartem.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    await message.answer(
        f"🔑 *Скопируй код доступа:*\n`{access_code}`",
        parse_mode="Markdown"
    )

    await notify_admin(email, user_id, access_code)


# ========= RUN ============

async def main():
    print("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
