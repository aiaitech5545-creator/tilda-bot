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

# ========= НАСТРОЙКИ ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =========

BOT_TOKEN = os.getenv("BOT_TOKEN")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")                      # ID таблицы Google Sheets
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")                      # Имя листа (вкладки)
EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")       # Имя колонки с email
ACCESS_CODE_COLUMN_NAME = os.getenv("ACCESS_CODE_COLUMN_NAME", "AccessCode")  # Колонка с индивидуальным кодом
TELEGRAM_ID_COLUMN_NAME = os.getenv("TELEGRAM_ID_COLUMN_NAME", "TelegramID")  # Колонка с Telegram ID

LESSONS_URL = os.getenv("LESSONS_URL")                            # Страница с уроками (Tilda)
PAGE_PASSWORD = os.getenv("PAGE_PASSWORD", "море2025")            # Пароль к странице Tilda

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")  # твой Telegram ID, например "211779388"

if not all([BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON]):
    print("❌ Не заданы необходимые переменные окружения!")
    print("Нужны: BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON")
    exit(1)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Храним, от кого ждём email
waiting_email: dict[int, bool] = {}


# ========= GOOGLE SHEETS =========

def get_gs_client() -> gspread.Client:
    """
    Подключаемся к Google Sheets, используя JSON из переменной окружения.
    Даём права на чтение/запись (нужно, чтобы сохранять индивидуальные коды и Telegram ID).
    """
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)


gs_client = get_gs_client()


def get_worksheet():
    sh = gs_client.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    return ws


def find_row_by_email(email: str) -> Tuple[Optional[int], Optional[Dict], Optional[List[str]]]:
    """
    Ищем строку по email.
    Возвращаем:
    - номер строки (1-based),
    - dict с данными строки,
    - список заголовков (headers)
    """
    ws = get_worksheet()
    headers = ws.row_values(1)
    records = ws.get_all_records()  # начинается со 2-й строки

    email = email.strip().lower()

    for i, row in enumerate(records, start=2):  # строки: 2,3,4,...
        value = str(row.get(EMAIL_COLUMN_NAME, "")).strip().lower()
        if value == email:
            return i, row, headers

    return None, None, None


def find_row_by_telegram_id(telegram_id: int) -> Tuple[Optional[int], Optional[Dict], Optional[List[str]]]:
    """
    Ищем строку по Telegram ID.
    """
    ws = get_worksheet()
    headers = ws.row_values(1)
    records = ws.get_all_records()

    tid = str(telegram_id).strip()

    for i, row in enumerate(records, start=2):
        value = str(row.get(TELEGRAM_ID_COLUMN_NAME, "")).strip()
        if value == tid:
            return i, row, headers

    return None, None, None


def update_cell(row_index: int, column_name: str, value: str, headers: List[str]) -> None:
    """
    Обновляем одну ячейку по названию колонки и номеру строки.
    Если колонки нет в таблице — тихо выходим.
    """
    if column_name not in headers:
        return
    col_index = headers.index(column_name) + 1  # индексы колонок с 1
    ws = get_worksheet()
    ws.update_cell(row_index, col_index, value)


def generate_access_code(length: int = 8) -> str:
    """
    Генерируем код из заглавных букв и цифр, например: "PPE4MSEA".
    """
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def notify_admin_new_access(email: str, tg_id: int, access_code: str):
    """
    Шлём тебе в личку уведомление о новом доступе.
    """
    if not ADMIN_TELEGRAM_ID:
        return

    try:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            "📩 *Новый доступ к курсу*\n\n"
            f"Email: `{email}`\n"
            f"Telegram ID: `{tg_id}`\n"
            f"Код доступа: `{access_code}`\n"
            f"Время: {now_utc}"
        )
        await bot.send_message(int(ADMIN_TELEGRAM_ID), text, parse_mode="Markdown")
    except Exception as e:
        print("Не удалось отправить уведомление админу:", e)


# ========= DEBUG =========

@dp.message(Command("debug"))
async def cmd_debug(message: Message):
    """
    Проверяем доступ к таблице + показываем заголовки и примеры email/кодов.
    """
    try:
        await message.answer("Проверяю доступ к таблице…")

        try:
            sh = gs_client.open_by_key(SPREADSHEET_ID)
        except Exception as e:
            await message.answer(
                f"❌ Не смог открыть таблицу.\n"
                f"SPREADSHEET_ID: `{SPREADSHEET_ID}`\n\n"
                f"Ошибка:\n`{e}`",
                parse_mode="Markdown",
            )
            return

        try:
            ws = sh.worksheet(SHEET_NAME)
        except Exception as e:
            await message.answer(
                f"❌ Таблица открылась, но лист не найден.\n"
                f"SHEET_NAME: `{SHEET_NAME}`\n\n"
                f"Ошибка:\n`{e}`",
                parse_mode="Markdown",
            )
            return

        headers = ws.row_values(1)
        records = ws.get_all_records()

        emails = [str(r.get(EMAIL_COLUMN_NAME, "")) for r in records[:5]]
        codes = [str(r.get(ACCESS_CODE_COLUMN_NAME, "")) for r in records[:5]]
        tids = [str(r.get(TELEGRAM_ID_COLUMN_NAME, "")) for r in records[:5]]

        text = "✅ Доступ к таблице есть.\n\n"
        text += f"*Лист:* `{SHEET_NAME}`\n"
        text += "Заголовки колонок:\n"
        text += (", ".join(headers) or "(пусто)")
        text += f"\n\nПервые email из '{EMAIL_COLUMN_NAME}':\n"
        text += "\n".join(f"- {e}" for e in emails) if emails else "(нет данных)"
        text += f"\n\nПервые коды из '{ACCESS_CODE_COLUMN_NAME}':\n"
        text += "\n".join(f"- {c}" for c in codes) if codes else "(нет данных)"
        text += f"\n\nПервые TelegramID из '{TELEGRAM_ID_COLUMN_NAME}':\n"
        text += "\n".join(f"- {t}" for t in tids) if tids else "(нет данных)"

        await message.answer(text, parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"❌ Ошибка в /debug\n`{e}`", parse_mode="Markdown")


# ========= ОСНОВНАЯ ЛОГИКА =========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()

    # Если человек пришёл по ссылке вида ?start=course_access
    if len(args) > 1 and args[1] == "course_access":
        waiting_email[message.from_user.id] = True
        await message.answer(
            "Привет! 👋\n\n"
            "Это PPE* бот доступа к курсу.\n\n"
            "Напиши *email*, который ты указывал при оплате — я проверю доступ.",
            parse_mode="Markdown",
        )
    else:
        await message.answer(
            "Привет! Это PPE* бот курса для моряков.\n\n"
            "Чтобы получить доступ к урокам:\n"
            "1️⃣ Оплати курс на сайте\n"
            "2️⃣ Вернись сюда по кнопке со страницы «Спасибо за оплату».\n\n"
            "Тогда я смогу выдать тебе доступ и персональный код.",
        )


@dp.message(Command("mycode"))
async def cmd_mycode(message: Message):
    """
    Студент может запросить свой код ещё раз.
    Ищем по TelegramID → отдаём код + ссылку.
    """
    user_id = message.from_user.id

    try:
        row_index, row, headers = find_row_by_telegram_id(user_id)
    except Exception as e:
        print("Ошибка при поиске по TelegramID:", e)
        await message.answer(
            "Произошла ошибка при поиске твоего кода 😔\n"
            "Попробуй позже или напиши Артёму: @ilinartem"
        )
        return

    if not row_index or not row:
        await message.answer(
            "Я пока не нахожу тебя в базе по Telegram ID.\n\n"
            "Если ты уже оплатил курс, зайди ещё раз по кнопке со страницы "
            "«Спасибо за оплату» или отправь свой email заново."
        )
        return

    access_code = str(row.get(ACCESS_CODE_COLUMN_NAME, "")).strip()
    email = str(row.get(EMAIL_COLUMN_NAME, "")).strip()

    if not access_code:
        access_code = generate_access_code()
        try:
            update_cell(row_index, ACCESS_CODE_COLUMN_NAME, access_code, headers)
        except Exception as e:
            print("Не удалось обновить код при /mycode:", e)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть уроки 📚", url=LESSONS_URL)],
            [InlineKeyboardButton(text="Написать Артёму ✉️", url="https://t.me/ilinartem")],
        ]
    )

    await message.answer(
        "🧰 *PPE* ACCESS — повторная выдача кода\n\n"
        f"Email в базе: `{email}`\n\n"
        f"▶️ *Пароль к странице:*\n`{PAGE_PASSWORD}`\n\n"
        f"▶️ *Твой персональный код PPE*: `{access_code}`\n\n"
        "Нажми кнопку ниже, чтобы перейти к урокам.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    await message.answer(
        f"🔑 *Скопируй свой персональный код PPE:*\n"
        f"`{access_code}`\n\n"
        "Нажми и удерживай это сообщение, чтобы скопировать.",
        parse_mode="Markdown",
    )


@dp.message(F.text)
async def handle_email(message: Message):
    user_id = message.from_user.id

    if waiting_email.get(user_id):
        email = message.text.strip()

        await message.answer(
            f"Проверяю оплату по адресу:\n`{email}`…",
            parse_mode="Markdown",
        )

        try:
            row_index, row, headers = find_row_by_email(email)
        except Exception as e:
            print("Ошибка при работе с Google Sheets:", e)
            await message.answer(
                "Произошла ошибка при проверке оплаты 😔\n"
                "Попробуй позже или напиши Артёму: @ilinartem",
            )
            return

        if not row_index or not row:
            await message.answer(
                "Я не нашёл этот email в списке оплат ❗️\n\n"
                "Проверь, точно ли указал тот же адрес.\n"
                "Если уверен, что оплата была — напиши Артёму: @ilinartem",
            )
            return

        # Получаем или создаём индивидуальный код
        access_code = ""
        if ACCESS_CODE_COLUMN_NAME in row and row.get(ACCESS_CODE_COLUMN_NAME):
            access_code = str(row.get(ACCESS_CODE_COLUMN_NAME)).strip()
        else:
            access_code = generate_access_code()
            try:
                update_cell(row_index, ACCESS_CODE_COLUMN_NAME, access_code, headers)
            except Exception as e:
                print("Не удалось сохранить индивидуальный код в таблицу:", e)

        # Сохраняем Telegram ID в таблицу
        try:
            update_cell(row_index, TELEGRAM_ID_COLUMN_NAME, str(user_id), headers)
        except Exception as e:
            print("Не удалось сохранить TelegramID:", e)

        waiting_email[user_id] = False

        # Клавиатура
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть уроки 📚", url=LESSONS_URL)],
                [InlineKeyboardButton(text="Написать Артёму ✉️", url="https://t.me/ilinartem")],
            ]
        )

        # Основное сообщение
        await message.answer(
            "🧰 *PPE* ACCESS\n\n"
            "Твой доступ к курсу активирован.\n\n"
            "🔐 Страница с уроками защищена паролем.\n"
            "Используй данные ниже:\n\n"
            f"▶️ *Пароль к странице:*\n`{PAGE_PASSWORD}`\n\n"
            f"▶️ *Твой персональный код PPE*: `{access_code}`\n\n"
            "Нажми кнопку ниже, чтобы перейти к урокам.\n"
            "Если что-то пойдёт не так — пиши напрямую.",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        # Отдельное сообщение только с кодом — удобно копировать
        await message.answer(
            f"🔑 *Скопируй свой персональный код PPE:*\n"
            f"`{access_code}`\n\n"
            "Нажми и удерживай это сообщение, чтобы скопировать.",
            parse_mode="Markdown",
        )

        # Уведомление админу о новом доступе
        await notify_admin_new_access(email=email, tg_id=user_id, access_code=access_code)

    else:
        await message.answer(
            "Если ты уже оплатил курс — вернись на сайт и нажми кнопку "
            "со страницы «Спасибо за оплату», чтобы я понял, что это ты.",
        )


# ========= START =========

async def main():
    print("Бот запущен 🚢 PPE* access online")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
