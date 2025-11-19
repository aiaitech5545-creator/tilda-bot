import asyncio
import os
import json

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart

import gspread
from google.oauth2.service_account import Credentials

# ========= НАСТРОЙКИ ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =========

BOT_TOKEN = os.getenv("BOT_TOKEN")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")
EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")

LESSONS_URL = os.getenv("LESSONS_URL")

GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

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
    Только чтение.
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


# ================== Хэндлеры бота ====================

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
                    # Можно добавить кнопку поддержки, если захочешь:
                    # [InlineKeyboardButton(text="Написать в поддержку", url="https://t.me/ТВОЙ_ЮЗЕР")]
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
