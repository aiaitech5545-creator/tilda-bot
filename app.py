import asyncio
import os
import json
import re
import secrets
import string
from typing import Optional, List
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import CommandStart, Command

import gspread
from google.oauth2.service_account import Credentials


# ========= ENV ============

BOT_TOKEN = os.getenv("BOT_TOKEN")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")

EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")
ACCESS_CODE_COLUMN_NAME = os.getenv("ACCESS_CODE_COLUMN_NAME", "AccessCode")
TELEGRAM_ID_COLUMN_NAME = os.getenv("TELEGRAM_ID_COLUMN_NAME", "TelegramID")

LESSONS_URL = os.getenv("LESSONS_URL")
PAGE_PASSWORD = os.getenv("PAGE_PASSWORD", "privet")

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")  # строкой


# ========= LINKS ============
COURSE_CHAT_URL = "https://t.me/+8u12vcEoLJc0YWFi"
ARTEM_CHANNEL_URL = "https://t.me/mnogomorya"
PROBLEM_URL = "https://t.me/ilinartem"


# ========= REQUIRED CHECK ============

required = [BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON]
if not all(required):
    print("❌ Не заданы необходимые переменные окружения!")
    print("Нужны: BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON")
    raise SystemExit(1)


bot = Bot(BOT_TOKEN)
dp = Dispatcher()

waiting_email: dict[int, bool] = {}
gs_lock = asyncio.Lock()


# ========= GOOGLE SHEETS ============

def get_gs_client() -> gspread.Client:
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


gs_client = get_gs_client()


def get_worksheet() -> gspread.Worksheet:
    sh = gs_client.open_by_key(SPREADSHEET_ID)
    return sh.worksheet(SHEET_NAME)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _looks_like_email(text: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", text.strip()))


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _get_headers(ws: gspread.Worksheet) -> List[str]:
    return ws.row_values(1)


def _col_index(headers: List[str], column_name: str) -> Optional[int]:
    if column_name not in headers:
        return None
    return headers.index(column_name) + 1


def _update_cell(ws: gspread.Worksheet, headers: List[str], row: int, column_name: str, value: str) -> None:
    col = _col_index(headers, column_name)
    if not col:
        return
    ws.update_cell(row, col, value)


def _get_row_dict(ws: gspread.Worksheet, headers: List[str], row: int) -> dict:
    values = ws.row_values(row)
    if len(values) < len(headers):
        values += [""] * (len(headers) - len(values))
    return dict(zip(headers, values))


def _find_row_by_email(ws: gspread.Worksheet, headers: List[str], email: str) -> Optional[int]:
    col = _col_index(headers, EMAIL_COLUMN_NAME)
    if not col:
        return None

    # find может находить подстроку, поэтому проверяем точное совпадение значения ячейки
    cell = ws.find(email, in_column=col)
    if not cell:
        return None

    found = ws.cell(cell.row, col).value or ""
    if _normalize_email(found) == _normalize_email(email):
        return cell.row
    return None


def _find_row_by_telegram_id(ws: gspread.Worksheet, headers: List[str], tg_id: int) -> Optional[int]:
    col = _col_index(headers, TELEGRAM_ID_COLUMN_NAME)
    if not col:
        return None

    tg_str = str(tg_id)
    cell = ws.find(tg_str, in_column=col)
    if not cell:
        return None

    found = (ws.cell(cell.row, col).value or "").strip()
    if found == tg_str:
        return cell.row
    return None


def generate_access_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def make_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Открыть уроки", url=LESSONS_URL)],
            [InlineKeyboardButton(text="💬 Вступить в чат курса", url=COURSE_CHAT_URL)],
            [InlineKeyboardButton(text="📣 Подписаться на мой канал", url=ARTEM_CHANNEL_URL)],
            [InlineKeyboardButton(text="⚠️ Сообщить о проблеме", url=PROBLEM_URL)],
        ]
    )


async def notify_admin(text: str) -> None:
    if not ADMIN_TELEGRAM_ID:
        return
    try:
        await bot.send_message(int(ADMIN_TELEGRAM_ID), text, parse_mode="Markdown")
    except Exception:
        pass


async def issue_access(message: Message, email: str) -> None:
    """
    1) ищем email в таблице
    2) если найден — выдаём/создаём код, записываем TG ID
    3) отвечаем пользователю + уведомляем админа
    """
    await message.answer(
        f"🔍 Проверяю оплату по адресу:\n`{email}`…",
        parse_mode="Markdown"
    )

    user_id = message.from_user.id

    try:
        async with gs_lock:
            ws = get_worksheet()
            headers = _get_headers(ws)

            row_index = _find_row_by_email(ws, headers, email)
            if not row_index:
                await message.answer(
                    "❌ Этот email не найден в списке оплат.\n"
                    "Проверь правильность.\n"
                    "Если всё верно — напиши @ilinartem."
                )
                return

            row = _get_row_dict(ws, headers, row_index)

            access_code = (row.get(ACCESS_CODE_COLUMN_NAME, "") or "").strip()
            if not access_code:
                access_code = generate_access_code()
                _update_cell(ws, headers, row_index, ACCESS_CODE_COLUMN_NAME, access_code)

            _update_cell(ws, headers, row_index, TELEGRAM_ID_COLUMN_NAME, str(user_id))

        waiting_email[user_id] = False

    except Exception as e:
        await message.answer(
            "❌ Ошибка при проверке оплаты.\n"
            "Если что-то не так — напиши @ilinartem."
        )
        await notify_admin(
            "⚠️ *Ошибка проверки email*\n\n"
            f"📧 `{email}`\n"
            f"🆔 TG: `{user_id}`\n"
            f"⏱ `{_ts_utc()}`\n"
            f"❗️ `{e}`"
        )
        return

    await message.answer(
        "✅ *Доступ подтверждён!*\n\n"
        "Вот твои данные для входа на страницу курса:\n\n"
        f"🔐 Пароль к странице:\n`{PAGE_PASSWORD}`\n"
        f"🔑 Твой индивидуальный код:\n`{access_code}`\n\n"
        "➡️ Нажми кнопку ниже, чтобы перейти к урокам.\n"
        "📣 Также вступи в чат курса и подпишись на новости.\n\n"
        "Если что-то не так — напиши мне в личку @ilinartem.",
        parse_mode="Markdown",
        reply_markup=make_keyboard()
    )

    await notify_admin(
        "📩 *Новый доступ к курсу!*\n\n"
        f"📧 Email: `{email}`\n"
        f"🆔 Telegram ID: `{user_id}`\n"
        f"🔑 Код доступа: `{access_code}`\n"
        f"⏱ Время: `{_ts_utc()}`"
    )


# ========= COMMANDS ============

@dp.message(Command("debug"))
async def debug(message: Message):
    try:
        async with gs_lock:
            ws = get_worksheet()
            headers = _get_headers(ws)
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
            "Если что-то не так — напиши @ilinartem.",
            parse_mode="Markdown"
        )


@dp.message(Command("access"))
async def access_cmd(message: Message):
    waiting_email[message.from_user.id] = True
    await message.answer(
        "🔑 Ок, давай выдадим доступ.\n\n"
        "✉️ Напиши *email*, который ты указывал при оплате.",
        parse_mode="Markdown"
    )


@dp.message(Command("mycode"))
async def mycode(message: Message):
    tg_id = message.from_user.id

    try:
        async with gs_lock:
            ws = get_worksheet()
            headers = _get_headers(ws)

            row_index = _find_row_by_telegram_id(ws, headers, tg_id)
            if not row_index:
                await message.answer(
                    "❗️ Я не нашёл твой Telegram ID в базе.\n"
                    "Если оплата была — пройди проверку ещё раз.\n\n"
                    "Если что-то не так — напиши мне в личку @ilinartem."
                )
                return

            row = _get_row_dict(ws, headers, row_index)
            access_code = (row.get(ACCESS_CODE_COLUMN_NAME, "") or "").strip()

            if not access_code:
                access_code = generate_access_code()
                _update_cell(ws, headers, row_index, ACCESS_CODE_COLUMN_NAME, access_code)

    except Exception as e:
        await message.answer(
            "❌ Ошибка при получении кода.\n"
            "Если что-то не так — напиши @ilinartem."
        )
        await notify_admin(
            "⚠️ *Ошибка /mycode*\n\n"
            f"🆔 TG: `{tg_id}`\n"
            f"⏱ `{_ts_utc()}`\n"
            f"❗️ `{e}`"
        )
        return

    await message.answer(
        "🔁 *Повторная выдача данных*\n\n"
        f"🔐 Пароль к странице:\n`{PAGE_PASSWORD}`\n\n"
        f"🔑 Твой код доступа:\n`{access_code}`\n\n"
        "Если что-то не так — напиши @ilinartem.",
        parse_mode="Markdown"
    )


# ========= START + BUTTON ============

@dp.message(CommandStart())
async def start(message: Message):
    args = message.text.split(maxsplit=1)

    # ✅ вариант со ссылкой после оплаты: /start course_access
    if len(args) > 1 and args[1].strip() == "course_access":
        waiting_email[message.from_user.id] = True
        await message.answer(
            "👋 Привет! Я — бот доступа к курсу для моряков.\n\n"
            "✉️ Напиши *email*, который ты указывал при оплате.",
            parse_mode="Markdown"
        )
        return

    # ✅ вариант без ссылки: обычный /start
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Получить доступ", callback_data="get_access")],
            [InlineKeyboardButton(text="⚠️ Сообщить о проблеме", url=PROBLEM_URL)],
        ]
    )
    await message.answer(
        "⚓ Привет! Я — бот доступа к курсу для моряков.\n\n"
        "Нажми **«Получить доступ»** или введи команду /access.\n"
        "Также можно просто отправить сюда свой email.",
        parse_mode="Markdown",
        reply_markup=kb
    )


@dp.callback_query(F.data == "get_access")
async def cb_get_access(callback: CallbackQuery):
    waiting_email[callback.from_user.id] = True
    await callback.message.answer(
        "✉️ Напиши *email*, который ты указывал при оплате.",
        parse_mode="Markdown"
    )
    await callback.answer()


# ========= TEXT HANDLER (EMAIL) ============

@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    # если человек просто прислал email — начинаем проверку даже без режима
    if _looks_like_email(text):
        waiting_email[user_id] = True

    if not waiting_email.get(user_id):
        await message.answer(
            "ℹ️ Чтобы получить доступ — нажми **«Получить доступ»** или введи /access.\n\n"
            "Если что-то не так — напиши @ilinartem.",
            parse_mode="Markdown"
        )
        return

    # режим ожидания email включен
    email = _normalize_email(text)

    if not _looks_like_email(text):
        await message.answer(
            "❗️ Похоже, это не email.\n"
            "Напиши, пожалуйста, email в формате `name@example.com`.",
            parse_mode="Markdown"
        )
        return

    await issue_access(message, email)


# ========= RUN ============

async def main():
    print("🚀 Бот запущен (курс моряков)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
