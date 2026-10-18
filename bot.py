
import os
import json
import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://abdulvadud001.github.io/fiqhi-/"
)
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8758611320").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida yo'q.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None

def main_keyboard():
    rows = [
        [
            KeyboardButton(text="📚 Fiqh bo‘limlari"),
            KeyboardButton(text="🔎 Qidiruv"),
        ],
        [
            KeyboardButton(text="❓ Savol berish"),
            KeyboardButton(text="👤 Profil"),
        ],
        [
            KeyboardButton(
                text="🌐 Fiqh Ensiklopediyasi",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True
    )

async def init_db():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL topilmadi. Railway'ga PostgreSQL qo'shing."
        )

    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                source TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

async def save_user(message: Message):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name
        """, message.from_user.id,
             message.from_user.username,
             message.from_user.full_name)

async def find_answer(query: str):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT question, answer, source
            FROM questions
            WHERE status = 'approved'
              AND answer IS NOT NULL
              AND (
                question ILIKE $1
                OR answer ILIKE $1
              )
            ORDER BY created_at DESC
            LIMIT 5
        """, f"%{query}%")

    return rows

async def search_and_reply(message: Message, query: str):
    query = query.strip()

    if not query:
        await message.answer("Savolingizni yozing.")
        return

    try:
        answers = await find_answer(query)
    except Exception:
        log.exception("Qidiruvda ma'lumotlar bazasi xatosi")
        await message.answer(
            "Qidiruv vaqtida xato yuz berdi. Keyinroq urinib ko‘ring."
        )
        return

    if not answers:
        await message.answer(
            "Bu mavzu bo‘yicha hali tasdiqlangan javob topilmadi.\n\n"
            "Savolingizni «❓ Savol berish» tugmasi orqali yuborishingiz mumkin."
        )
        return

    for item in answers:
        text = f"📚 {item['question']}\n\n{item['answer']}"
        if item["source"]:
            text += f"\n\n📖 Manba: {item['source']}"

        await message.answer(text)

@dp.message(CommandStart())
async def start(message: Message):
    await save_user(message)
    await message.answer(
        "Assalomu alaykum!\n\n"
        "Hanafiy Fiqhi Ensiklopediyasiga xush kelibsiz.\n"
        "Fiqh mavzularini qidiring yoki savolingizni yuboring.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "Qidirish uchun «🔎 Qidiruv» tugmasini bosing.\n"
        "Savol yuborish uchun «❓ Savol berish» tugmasini bosing."
    )

@dp.message(Command("cancel"))
async def cancel_command(message: Message):
    await message.answer(
        "Amal bekor qilindi.",
        reply_markup=main_keyboard()
    )

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat administrator uchun.")
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE status = 'pending'"
        )
        approved = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE status = 'approved'"
        )

    await message.answer(
        f"🛠 Admin statistikasi\n\n"
        f"Foydalanuvchilar: {users}\n"
        f"Kutilayotgan savollar: {pending}\n"
        f"Tasdiqlangan javoblar: {approved}"
    )

@dp.message(F.web_app_data)
async def web_app_data_handler(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
    except (json.JSONDecodeError, TypeError):
        await message.answer("Yuborilgan ma'lumotni o‘qib bo‘lmadi.")
        return

    action = data.get("action")
    query = str(data.get("query", "")).strip()

    if action == "search":
        await search_and_reply(message, query)
    elif action == "question":
        await message.answer(
            "Savolingizni oddiy xabar qilib shu chatga yuboring."
        )
    else:
        await message.answer("Noma'lum amal.")

@dp.message(F.text == "📚 Fiqh bo‘limlari")
async def sections(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💧 Tahorat", callback_data="topic:Tahorat"),
            InlineKeyboardButton(text="🕌 Namoz", callback_data="topic:Namoz"),
        ],
        [
            InlineKeyboardButton(text="🌙 Ro‘za", callback_data="topic:Ro‘za"),
            InlineKeyboardButton(text="🤲 Zakot", callback_data="topic:Zakot"),
        ],
    ])
    await message.answer("Fiqh bo‘limini tanlang:", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("topic:"))
async def topic_selected(callback):
    topic = callback.data.split(":", 1)[1]
    await callback.answer()
    await search_and_reply(callback.message, topic)

@dp.message(F.text == "🔎 Qidiruv")
async def search_button(message: Message):
    await message.answer(
        "Qidiriladigan fiqh mavzusini shu chatga yozing."
    )

@dp.message(F.text == "❓ Savol berish")
async def ask_question(message: Message):
    await message.answer(
        "Savolingizni bitta xabar qilib yozib yuboring."
    )

@dp.message(F.text == "👤 Profil")
async def profile(message: Message):
    await save_user(message)
    await message.answer(
        f"👤 Profil\nTelegram ID: {message.from_user.id}"
    )

@dp.message(F.text)
async def text_handler(message: Message):
    text = message.text.strip()

    if text.startswith("/"):
        return

    if text in {
        "📚 Fiqh bo‘limlari",
        "🔎 Qidiruv",
        "❓ Savol berish",
        "👤 Profil",
    }:
        return

    await save_user(message)
    await search_and_reply(message, text)

async def main():
    await init_db()
    try:
        log.info("Bot ishga tushmoqda...")
        await dp.start_polling(bot)
    finally:
        if db_pool:
            await db_pool.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
