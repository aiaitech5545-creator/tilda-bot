import asyncio
import os
import json
import secrets
import string
from typing import Tuple, List, Dict
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command

import gspread
from google.oauth2.service_account import Credentials


# ========= ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")
EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")

ACCESS_CODE_COLUMN_NAME = os.getenv("ACCESS_CODE_COLUMN_NAME", "AccessCode")
TELEGRAM_ID_COLUMN_NAME = os.getenv("TELEGRAM_ID_COLUMN_NAME", "TelegramID")

LESSONS_URL = os.getenv("LESSONS_URL")
PAGE_PASSWORD = os.getenv("PAGE_PASSWORD", "2025")  # пароль к странице Tilda по умолчанию

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")  # твой Telegram ID (строкой)


if not all([BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON]):
    print("❌ Не заданы необходимые переменные окружения!")
    print("Нужны: BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON")
    exit(1)


bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# от кого ждём email после /start?start=course_access
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


def find_row_by_email(email: str) -> Tuple[int | None, Dict | None, List[str] | None]:
    ws = get_worksheet()
    headers = ws.row_values(1)
    records = ws.get_all_records()

    email = email.strip().lower()
    for i, row in enumerate(records, start=2):
        if str(row.get(EMAIL_COLUMN_NAME, "")).strip().lower() == email:
            return i, row, headers
    return None, None, None


def find_row_by_telegram_id(tg_id: int) -> Tuple[int | None, Dict | None, List[str] | None]:
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
    """Шлём тебе в личку уведомление о новом доступе."""
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

    try:
        await bot.send_message(int(ADMIN_TELEGRAM_ID), msg, parse_mode="Markdown")
    except Exception as e:
        print("Не удалось отправить уведомление админу:", e)


# ========= DEBUG /debug ============

@dp.message(Command("debug"))
async def debug(message: Message):
    try:
        ws = get_worksheet()
        headers = ws.row_values(1)
        await message.answer(
            "🛠 *DEBUG*\n\n"
            f"📄 Лист: `{SHEET_NAME}`\n"
            "🔎 Заголовки колонок:\n"
            f"{', '.join(headers)}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(
            "❌ Ошибка при доступе к таблице.\n"
            f"`{e}`\n\n"
            "Если что-то не так — напиши мне в личку @ilinartem.",
            parse_mode="Markdown"
        )


# ========= /start ============

@dp.message(CommandStart())
async def start(message: Message):
    args = message.text.split()

    # пользователь пришёл по ссылке с параметром ?start=course_access
    if len(args) > 1 and args[1] == "course_access":
        waiting_email[message.from_user.id] = True
        await message.answer(
            "👋 Привет! Я — бот доступа к курсу для моряков.\n\n"
            "✉️ Напиши *email*, который ты указывал при оплате.",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "⚓ Привет! Я бот доступа к курсу для моряков.\n\n"
            "Чтобы получить доступ к урокам:\n"
            "1️⃣ Оплати курс на сайте\n"
            "2️⃣ Вернись в бота по кнопке со страницы «Спасибо за оплату».\n\n"
            "Если что-то не так — напиши мне в личку @ilinartem."
        )


# ========= /mycode — повторная выдача кода и пароля ============

@dp.message(Command("mycode"))
async def mycode(message: Message):
    tg_id = message.from_user.id

    try:
        row_index, row, headers = find_row_by_telegram_id(tg_id)
    except Exception as e:
        print("Ошибка при поиске по TelegramID в /mycode:", e)
        await message.answer(
            "❌ Произошла ошибка при поиске твоих данных.\n"
            "Попробуй позже. Если что-то не так — напиши мне в личку @ilinartem.",
            parse_mode="Markdown"
        )
        return

    if not row_index:
        await message.answer(
            "❗️ Я не нашёл твой Telegram ID в базе.\n"
            "Если оплата была — пройди проверку ещё раз через кнопку на странице «Спасибо за оплату».\n\n"
            "Если что-то не так — напиши мне в личку @ilinartem."
        )
        return

    access_code = row.get(ACCESS_CODE_COLUMN_NAME, "")
    if not access_code:
        access_code = generate_access_code()
        try:
            update_cell(row_index, ACCESS_CODE_COLUMN_NAME, access_code, headers)
        except Exception as e:
            print("Ошибка при обновлении кода в /mycode:", e)
            await message.answer(
                "⚠️ Не удалось обновить код в базе.\n"
                "Если что-то не так — напиши мне в личку @ilinartem.",
                parse_mode="Markdown"
            )

    await message.answer(
        "🔁 *Повторная выдача данных*\n\n"
        f"🔐 Пароль к странице:\n`{PAGE_PASSWORD}`\n\n"
        f"🔑 Твой код доступа:\n`{access_code}`\n\n"
        "Если что-то не так — напиши мне в личку @ilinartem.",
        parse_mode="Markdown"
    )


# ========= ОБРАБОТКА ЛЮБОГО ТЕКСТА (email) ============

@dp.message(F.text)
async def handle_email(message: Message):
    user_id = message.from_user.id

    # если мы не ждём от этого пользователя email — отправляем подсказку
    if not waiting_email.get(user_id):
        await message.answer(
            "ℹ️ Чтобы получить доступ к курсу — вернись на сайт и нажми кнопку "
            "со страницы «Спасибо за оплату».\n\n"
            "Если что-то не так — напиши мне в личку @ilinartem."
        )
        return

    email = message.text.strip()

    await message.answer(
        f"🔍 Проверяю оплату по адресу:\n`{email}`…",
        parse_mode="Markdown"
    )

    try:
        row_index, row, headers = find_row_by_email(email)
    except Exception as e:
        print("Ошибка при чтении таблицы:", e)
        await message.answer(
            "❌ Произошла ошибка при проверке оплаты.\n\n"
            "Попробуй ещё раз чуть позже.\n"
            "Если что-то не так — напиши мне в личку @ilinartem.",
            parse_mode="Markdown"
        )
        return

    if not row_index:
        await message.answer(
            "❌ Я не нашёл этот email в списке оплат.\n\n"
            "Проверь, правильно ли ты ввёл адрес.\n"
            "Если ты уверен, что оплата была — напиши мне в личку @ilinartem."
        )
        return

    # Генерируем или берём существующий код доступа
    access_code = row.get(ACCESS_CODE_COLUMN_NAME, "")
    if not access_code:
        access_code = generate_access_code()
        try:
            update_cell(row_index, ACCESS_CODE_COLUMN_NAME, access_code, headers)
        except Exception as e:
            print("Ошибка при сохранении кода:", e)
            await message.answer(
                "⚠️ Не удалось сохранить код в базу, но я всё равно покажу его тебе.\n"
                "Если что-то не так — напиши мне в личку @ilinartem.",
                parse_mode="Markdown"
            )

    # Сохраняем Telegram ID
    try:
        update_cell(row_index, TELEGRAM_ID_COLUMN_NAME, str(user_id), headers)
    except Exception as e:
        print("Ошибка при сохранении TelegramID:", e)
        await message.answer(
            "⚠️ Не удалось записать твой Telegram ID в базу.\n"
            "Если что-то не так — напиши мне в личку @ilinartem.",
            parse_mode="Markdown"
        )

    waiting_email[user_id] = False

    # Клавиатура: уроки + сообщить о проблеме
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Открыть уроки", url=LESSONS_URL)],
            [InlineKeyboardButton(text="⚠️ Сообщить о проблеме", url="https://t.me/ilinartem")]
        ]
    )

    # Одно основное сообщение (без отдельного "скопируй код")
    await message.answer(
        "✅ *Доступ подтверждён!*\n\n"
        "Вот твои данные для входа на страницу курса:\n\n"
        f"🔐 Пароль к странице:\n`{PAGE_PASSWORD}`\n"
        f"🔑 Твой индивидуальный код доступа:\n`{access_code}`\n\n"
        "➡️ Нажми кнопку ниже, чтобы перейти к урокам.\n\n"
        "Если что-то не так — напиши мне в личку @ilinartem.",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    # Уведомление админу о новом доступе
    await notify_admin(email, user_id, access_code)


# ========= RUN ============

async def main():
    print("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
