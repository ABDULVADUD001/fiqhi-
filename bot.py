
import os
import asyncio
import logging
from datetime import datetime, timezone

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

if not BOT_TOKEN:
    raise RuntimeError("Railway Variables ichida BOT_TOKEN kiritilmagan.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None


def main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📚 Fiqh savol-javob"),
        KeyboardButton(text="🔎 Qidiruv"),
    )
    builder.row(
        KeyboardButton(text="👤 Profil"),
        KeyboardButton(text="💎 Premium"),
    )
    builder.row(
        KeyboardButton(text="📖 Manbalar"),
        KeyboardButton(text="ℹ️ Yordam"),
    )

    if WEBAPP_URL.startswith("https://"):
        builder.row(
            KeyboardButton(
                text="🌙 Mini App ochish",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        )

    return builder.as_markup(resize_keyboard=True)


async def init_database():
    global db_pool

    if not DATABASE_URL:
        logger.warning(
            "DATABASE_URL sozlanmagan. Bot vaqtinchalik "
            "ma'lumotlar bazasisiz ishlaydi."
        )
        return

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                source_name TEXT,
                verification_status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS premium_subscriptions (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                starts_at TIMESTAMPTZ,
                expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

    logger.info("PostgreSQL jadvallari tayyor.")


async def save_user(message: Message):
    if not db_pool or not message.from_user:
        return

    user = message.from_user

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users
                (telegram_id, username, full_name, last_seen)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name,
                last_seen = NOW()
            """,
            user.id,
            user.username,
            user.full_name or "",
        )


@dp.message(CommandStart())
async def start_handler(message: Message):
    await save_user(message)

    await message.answer(
        "🕌 Hanafiy Fiqhi Ensiklopediyasiga xush kelibsiz!\n\n"
        "Bu loyiha Hanafiy fiqhi bo‘yicha ma'lumotlarni "
        "ishonchli manbalar asosida taqdim etish uchun yaratilgan.\n\n"
        "📚 Savolingizni yuboring yoki menyudan bo‘limni tanlang.\n\n"
        "⚠️ Tekshirilmagan ma'lumot tasdiqlangan fatvo "
        "sifatida taqdim etilmaydi.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Yordam")
async def help_handler(message: Message):
    await message.answer(
        "ℹ️ YORDAM\n\n"
        "/start — bosh menyu\n"
        "/profile — profilingiz\n"
        "/help — yordam\n"
        "/admin — administrator menyusi\n\n"
        "Fiqh savolingizni oddiy matn shaklida yuboring."
    )


@dp.message(Command("profile"))
@dp.message(F.text == "👤 Profil")
async def profile_handler(message: Message):
    await save_user(message)

    user_id = message.from_user.id

    question_count = 0
    premium_status = "Faol emas"

    if db_pool:
        async with db_pool.acquire() as conn:
            question_count = await conn.fetchval(
                "SELECT COUNT(*) FROM questions WHERE telegram_id = $1",
                user_id,
            )

            active_premium = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM premium_subscriptions
                    WHERE telegram_id = $1
                      AND status = 'active'
                      AND expires_at > NOW()
                )
                """,
                user_id,
            )

            if active_premium:
                premium_status = "Faol"

    await message.answer(
        "👤 PROFIL\n\n"
        f"🆔 ID: {user_id}\n"
        f"💬 Savollar soni: {question_count}\n"
        f"💎 Premium: {premium_status}"
    )


@dp.message(F.text == "📚 Fiqh savol-javob")
async def question_prompt(message: Message):
    await message.answer(
        "📚 Fiqh savolingizni bitta xabarda yozing.\n\n"
        "Masalan: Tahoratning farzlari nimalar?"
    )


@dp.message(F.text == "🔎 Qidiruv")
async def search_prompt(message: Message):
    await message.answer(
        "🔎 Qidirish uchun fiqh mavzusini yoki savolingizni yozing."
    )


@dp.message(F.text == "📖 Manbalar")
async def sources_handler(message: Message):
    await message.answer(
        "📖 MANBALAR\n\n"
        "Loyiha Hanafiy fiqhi bo‘yicha ishonchli manbalarga "
        "asoslanishi ko‘zda tutilgan.\n\n"
        "Manba bilan tekshirilgan javoblar alohida belgilanadi. "
        "Hozircha tashqi manbalar bilan avtomatik tekshiruv "
        "to‘liq sozlanmagan."
    )


@dp.message(F.text == "💎 Premium")
async def premium_handler(message: Message):
    await message.answer(
        "💎 HANAFIY FIQHI ENSIKLOPEDIYASI — PREMIUM\n\n"
        "Premium bo‘limi tayyorlanmoqda.\n"
        "To‘lov amalga oshirishdan oldin tarif, muddat va "
        "to‘lov tizimi administrator tomonidan sozlanishi kerak.\n\n"
        "⚠️ Hozircha bu xabar obuna sotib olmaydi."
    )


@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Bu buyruq faqat administrator uchun.")
        return

    total_users = 0
    total_questions = 0
    pending_questions = 0

    if db_pool:
        async with db_pool.acquire() as conn:
            total_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users"
            )
            total_questions = await conn.fetchval(
                "SELECT COUNT(*) FROM questions"
            )
            pending_questions = await conn.fetchval(
                """
                SELECT COUNT(*) FROM questions
                WHERE verification_status = 'pending'
                """
            )

    await message.answer(
        "🛠 ADMIN PANEL\n\n"
        f"👥 Foydalanuvchilar: {total_users}\n"
        f"❓ Jami savollar: {total_questions}\n"
        f"🕓 Tekshirilmagan savollar: {pending_questions}\n\n"
        "Bu dastlabki statistika paneli. "
        "Savollarni tasdiqlash va obunalarni boshqarish "
        "funksiyalari hali qo‘shilmagan."
    )


@dp.message(F.text)
async def question_handler(message: Message):
    await save_user(message)

    if not message.from_user:
        return

    question = (message.text or "").strip()

    if not question:
        return

    if len(question) > 3000:
        await message.answer(
            "Savol juda uzun. Iltimos, 3000 belgidan oshirmang."
        )
        return

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO questions
                    (telegram_id, question, verification_status)
                VALUES ($1, $2, 'pending')
                """,
                message.from_user.id,
                question,
            )

    await message.answer(
        "📝 Savolingiz qabul qilindi.\n\n"
        "Hozircha bu botda fatvo manbalarini avtomatik "
        "tekshirish tizimi ulanmagan. Shu sababli savolingizga "
        "tekshirilmagan diniy hukmni uydirib javob bermayman.\n\n"
        "Savol bazaga saqlandi, agar PostgreSQL ulangan bo‘lsa. "
        "Keyingi bosqichda manbalar va tekshirilgan javoblar "
        "tizimini qo‘shamiz.",
        reply_markup=main_keyboard(),
    )


async def main():
    await init_database()

    try:
        logger.info("Bot ishga tushmoqda...")
        await dp.start_polling(bot)
    finally:
        if db_pool:
            await db_pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
