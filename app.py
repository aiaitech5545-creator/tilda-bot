import asyncio
import os
import json

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command

import gspread
from google.oauth2.service_account import Credentials

# ========= НАСТРОЙКИ ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =========

BOT_TOKEN = os.getenv("BOT_TOKEN")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")              # ID таблицы Google Sheets
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")              # Имя листа (вкладки), по умолчанию "КУРС"
EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")  # Имя колонки с email

LESSONS_URL = os.getenv("LESSONS_URL")                    # Ссылка на страницу с уроками (Tilda)

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")  # JSON сервисного аккаунта

# Проверяем, что заданы основные переменные
if not all([BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON]):
    print("❌ Не заданы необходимые переменные окружения!")
    print("Нужны: BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON")
    exit(1)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# храним, от кого ждём email
waiting_email: dict[int, bool] = {}


def get_gs_client():
    """
    Подключаемся к Google Sheets, используя JSON из переменной окружения.
    Режим только чтения.
    """
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client


gs_client = get_gs_client()


def check_email_paid(email: str) -> bool:
    """
    Ищем email в таблице.
    Если нашли — считаем, что оплата была.
    """
    sh = gs_client.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)

    records = ws.get_all_records()  # список dict по строкам
    email = email.strip().lower()

    for row in records:
        # Берём значение из колонки с email
        value = str(row.get(EMAIL_COLUMN_NAME, "")).strip().lower()
        if value == email:
            return True

    return False


# ================== DEBUG КОМАНДА ====================

@dp.message(Command("debug"))
async def cmd_debug(message: Message):
    """
    Показывает, видит ли бот таблицу и лист,
    какие заголовки колонок и какие email.
    Помогает понять, где ошибка: ID, имя листа или доступ.
    """
    try:
        await message.answer("Пробую открыть таблицу и лист…")

        # 1. Пробуем открыть таблицу по ID
        try:
            sh = gs_client.open_by_key(SPREADSHEET_ID)
        except Exception as e:
            await message.answer(
                "❌ Не смог открыть таблицу по SPREADSHEET_ID.\n\n"
                f"SPREADSHEET_ID: `{SPREADSHEET_ID}`\n\n"
                f"Текст ошибки Google:\n`{e}`",
                parse_mode="Markdown"
            )
            return

        # 2. Пробуем открыть лист по имени
        try:
            ws = sh.worksheet(SHEET_NAME)
        except Exception as e:
            await message.answer(
                "❌ Таблица открылась, но не нашёл лист с таким именем.\n\n"
                f"SHEET_NAME: `{SHEET_NAME}`\n\n"
                f"Текст ошибки Google:\n`{e}`",
                parse_mode="Markdown"
            )
            return

        # 3. Если сюда дошли – читаем заголовки и первые email
        headers = ws.row_values(1)
        records = ws.get_all_records()
        emails = [str(r.get(EMAIL_COLUMN_NAME, "")) for r in records[:10]]

        text = "✅ Успешно прочитал таблицу.\n\n"
        text += f"Лист: *{SHEET_NAME}*\n"
        text += "Заголовки колонок:\n"
        text += (", ".join(headers) or "(пусто)")
        text += "\n\nПримеры значений в колонке *{0}*:\n".format(EMAIL_COLUMN_NAME)
        if emails:
            text += "\n".join(f"- {e}" for e in emails)
        else:
            text += "(нет строк с данными)"

        await message.answer(text, parse_mode="Markdown")

    except Exception as e:
        await message.answer(
            "❌ Неизвестная ошибка в /debug.\n"
            f"`{e}`",
            parse_mode="Markdown"
        )


# ================== ОСНОВНЫЕ ХЭНДЛЕРЫ ====================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()

    # Если человек пришёл по ссылке с сайта (?start=course_access)
    if len(args) > 1 and args[1] == "course_access":
        waiting_email[message.from_user.id] = True
        await message.answer(
            "Привет! 👋\n\n"
            "Спасибо за оплату доступа к курсу.\n"
            "Напиши, пожалуйста, *email*, который ты указал при оплате.",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "Привет! Это бот курса для моряков.\n\n"
            "Чтобы получить доступ к урокам, сначала оплати курс на сайте,\n"
            "а потом вернись сюда по кнопке со страницы «Спасибо за оплату»."
        )


@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id

    # Если мы ждём от пользователя email
    if waiting_email.get(user_id):
        email = message.text.strip()

        await message.answer(
            f"Проверяю оплату по адресу:\n`{email}`\n\n"
            "Подожди пару секунд…",
            parse_mode="Markdown",
        )

        try:
            paid = check_email_paid(email)
        except Exception as e:
            print("Ошибка при работе с Google Sheets:", e)
            await message.answer(
                "Произошла ошибка при проверке оплаты 😔\n"
                "Попробуй ещё раз чуть позже или напиши мне напрямую."
            )
            return

        if paid:
            waiting_email[user_id] = False

            # Красивая кнопка "Открыть уроки"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть уроки 📚", url=LESSONS_URL)],
                ]
            )

            await message.answer(
                "Оплата найдена ✅\n\n"
                "Нажми на кнопку ниже, чтобы открыть страницу с уроками.",
                reply_markup=keyboard
            )
        else:
            await message.answer(
                "Я не нашёл этот email в списке оплат 😕\n\n"
                "Проверь, пожалуйста, что написал тот же адрес,\n"
                "который указывал при оплате.\n\n"
                "Если уверен, что всё верно — напиши мне в личку."
            )
    else:
        await message.answer(
            "Если ты оплатил курс, вернись на сайт и зайди в бота по кнопке "
            "со страницы «Спасибо за оплату»."
        )


async def main():
    print("Бот запущен 🚢")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
