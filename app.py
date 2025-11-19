
import asyncio
import os
import json

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

import gspread
from google.oauth2.service_account import Credentials

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "КУРС")
EMAIL_COLUMN_NAME = os.getenv("EMAIL_COLUMN_NAME", "Email")
LESSONS_URL = os.getenv("LESSONS_URL")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not all([BOT_TOKEN, SPREADSHEET_ID, LESSONS_URL, GOOGLE_SERVICE_ACCOUNT_JSON]):
    print("❌ Missing environment variables!")
    exit(1)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

waiting_email = {}

def get_gs_client():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client

gs_client = get_gs_client()

def check_email_paid(email: str) -> bool:
    sh = gs_client.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    records = ws.get_all_records()
    email = email.strip().lower()
    for row in records:
        value = str(row.get(EMAIL_COLUMN_NAME, "")).strip().lower()
        if value == email:
            return True
    return False

@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    if len(args) > 1 and args[1] == "course_access":
        waiting_email[message.from_user.id] = True
        await message.answer(
            "Привет! 👋\n\nСпасибо за оплату доступа к курсу.\n"
            "Напиши, пожалуйста, *email*, который ты указал при оплате.",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "Привет! Это бот курса.\n"
            "Чтобы получить доступ, оплати курс и вернись сюда по кнопке со страницы «Спасибо за оплату»."
        )

@dp.message(F.text)
async def handle_text(message: Message):
    user_id = message.from_user.id
    if waiting_email.get(user_id):
        email = message.text.strip()
        await message.answer(
            f"Проверяю оплату по адресу:\n`{email}`\n\nПодожди пару секунд…",
            parse_mode="Markdown",
        )
        try:
            paid = check_email_paid(email)
        except Exception as e:
            print("Google Sheets error:", e)
            await message.answer("Ошибка при проверке оплаты 😔")
            return
        if paid:
            waiting_email[user_id] = False
            await message.answer("Оплата найдена ✅\n\nВот твой доступ к курсу:")
            await message.answer(f"🔗 {LESSONS_URL}")
        else:
            await message.answer(
                "Я не нашёл этот email в списке оплат 😕\nПроверь адрес."
            )
    else:
        await message.answer("Используй кнопку на странице «Спасибо за оплату».")

async def main():
    print("Bot started 🚢")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
