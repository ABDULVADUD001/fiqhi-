
import os
import asyncio
import logging

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramAPIError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8758611320").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida mavjud emas.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool = None
admin_states = {}


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📚 Fiqh savol-javob"),
                KeyboardButton(text="🔎 Qidiruv"),
            ],
            [
                KeyboardButton(text="👤 Profil"),
                KeyboardButton(text="💎 Premium"),
            ],
            [
                KeyboardButton(text="📖 Manbalar"),
                KeyboardButton(text="ℹ️ Yordam"),
            ],
        ],
        resize_keyboard=True,
    )


def review_keyboard(question_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✍️ Javob berish",
                    callback_data=f"answer:{question_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"reject:{question_id}",
                ),
            ]
        ]
    )


async def init_database():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL yo‘q. Railway PostgreSQL xizmatini ulang."
        )

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
            );

            CREATE TABLE IF NOT EXISTS questions (
                id BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT,
                source_name TEXT,
                source_url TEXT,
                verification_status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                answered_at TIMESTAMPTZ
            );

            CREATE INDEX IF NOT EXISTS questions_user_idx
            ON questions (telegram_id);

            CREATE INDEX IF NOT EXISTS questions_status_idx
            ON questions (verification_status);
        """)

    logger.info("PostgreSQL ulandi va jadvallar tayyor.")


async def save_user(message: Message):
    if not db_pool or not message.from_user:
        return

    user = message.from_user

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, username, full_name)
            VALUES ($1, $2, $3)
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


async def notify_admins(question_id, user_id, question):
    text = (
        "📝 YANGI FIQH SAVOLI\n\n"
        f"🔢 Savol ID: {question_id}\n"
        f"👤 Foydalanuvchi ID: {user_id}\n\n"
        f"❓ Savol:\n{question}\n\n"
        "Javob berishdan oldin manbani tekshiring."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                text,
                reply_markup=review_keyboard(question_id),
            )
        except TelegramAPIError:
            logger.exception("Adminga xabar yuborilmadi: %s", admin_id)


@dp.message(CommandStart())
async def start_handler(message: Message):
    await save_user(message)

    await message.answer(
        "🕌 Hanafiy Fiqhi Ensiklopediyasiga xush kelibsiz!\n\n"
        "📚 Hanafiy fiqhi bo‘yicha savolingizni yuboring.\n"
        "🔎 Tekshirilgan javoblarni qidirishingiz mumkin.\n\n"
        "⚠️ Manbasi tekshirilmagan ma’lumot fatvo sifatida "
        "taqdim etilmaydi.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Yordam")
async def help_handler(message: Message):
    await message.answer(
        "ℹ️ YORDAM\n\n"
        "/start — bosh menyu\n"
        "/profile — profil\n"
        "/help — yordam\n"
        "/admin — administrator statistikasi\n\n"
        "Savolingizni oddiy matn ko‘rinishida yuboring."
    )


@dp.message(Command("profile"))
@dp.message(F.text == "👤 Profil")
async def profile_handler(message: Message):
    await save_user(message)
    user_id = message.from_user.id

    async with db_pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE telegram_id = $1",
            user_id,
        )
        pending = await conn.fetchval(
            """
            SELECT COUNT(*) FROM questions
            WHERE telegram_id = $1
              AND verification_status = 'pending'
            """,
            user_id,
        )

    await message.answer(
        "👤 PROFIL\n\n"
        f"🆔 Telegram ID: {user_id}\n"
        f"❓ Savollar: {total}\n"
        f"🕓 Tekshirilayotgan savollar: {pending}\n"
        "💎 Premium: hozircha ulanmagan"
    )


@dp.message(F.text == "📚 Fiqh savol-javob")
async def question_prompt(message: Message):
    await message.answer("Savolingizni yuboring. Masalan: Tahoratning farzlari nimalar?")


@dp.message(F.text == "🔎 Qidiruv")
async def search_prompt(message: Message):
    await message.answer("Qidirish uchun mavzu yoki savolingizni yozing.")


@dp.message(F.text == "📖 Manbalar")
async def sources_handler(message: Message):
    await message.answer(
        "📖 MANBALAR\n\n"
        "Savolga javob berishda manba administrator tomonidan "
        "tekshirilishi kerak.\n"
        "Fatvo.uz va qo‘lyozmalarni avtomatik tekshirish hali ulanmagan."
    )


@dp.message(F.text == "💎 Premium")
async def premium_handler(message: Message):
    await message.answer(
        "💎 PREMIUM\n\n"
        "Obuna va to‘lov tizimi hali sozlanmagan. "
        "Hozircha bu bo‘lim orqali to‘lov olinmaydi."
    )


@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Bu buyruq faqat administrator uchun.")
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total = await conn.fetchval("SELECT COUNT(*) FROM questions")
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM questions "
            "WHERE verification_status = 'pending'"
        )
        verified = await conn.fetchval(
            "SELECT COUNT(*) FROM questions "
            "WHERE verification_status = 'verified'"
        )

    await message.answer(
        "🛠 ADMIN PANEL\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"❓ Jami savollar: {total}\n"
        f"🕓 Kutilmoqda: {pending}\n"
        f"✅ Tekshirilgan javoblar: {verified}"
    )


@dp.callback_query(F.data.startswith("answer:"))
async def begin_answer(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    question_id = int(callback.data.split(":")[1])

    async with db_pool.acquire() as conn:
        question = await conn.fetchrow(
            """
            SELECT id FROM questions
            WHERE id = $1 AND verification_status = 'pending'
            """,
            question_id,
        )

    if not question:
        await callback.answer("Savol topilmadi yoki ko‘rib chiqilgan.")
        return

    admin_states[callback.from_user.id] = {
        "question_id": question_id,
        "step": "answer",
    }

    await callback.message.answer(
        f"{question_id}-savol uchun tekshirilgan javobni yuboring."
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reject:"))
async def reject_question(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    question_id = int(callback.data.split(":")[1])

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE questions
            SET verification_status = 'rejected'
            WHERE id = $1 AND verification_status = 'pending'
            """,
            question_id,
        )

    if result == "UPDATE 1":
        await callback.message.answer(f"❌ {question_id}-savol rad etildi.")
        await callback.answer("Rad etildi.")
    else:
        await callback.answer("Savol topilmadi yoki oldin ko‘rib chiqilgan.")


@dp.message(F.text)
async def text_handler(message: Message):
    await save_user(message)

    if not message.from_user:
        return

    user_id = message.from_user.id
    text = (message.text or "").strip()

    if not text or text.startswith("/"):
        return

    state = admin_states.get(user_id)

    if state and user_id in ADMIN_IDS:
        if state["step"] == "answer":
            state["answer"] = text
            state["step"] = "source"

            await message.answer(
                "Endi manbani quyidagi shaklda yuboring:\n"
                "MANBA NOMI | MANBA URL\n\n"
                "Manbani mustaqil tekshirib, haqiqiy ma’lumot kiriting."
            )
            return

        if state["step"] == "source":
            parts = text.split("|", 1)

            if len(parts) != 2 or not all(p.strip() for p in parts):
                await message.answer(
                    "Format: MANBA NOMI | MANBA URL\n"
                    "Ikkala qismini ham kiriting."
                )
                return

            question_id = state["question_id"]
            answer = state["answer"]
            source_name, source_url = [p.strip() for p in parts]

            async with db_pool.acquire() as conn:
                question = await conn.fetchrow(
                    """
                    SELECT telegram_id, question FROM questions
                    WHERE id = $1 AND verification_status = 'pending'
                    """,
                    question_id,
                )

                if not question:
                    admin_states.pop(user_id, None)
                    await message.answer("Savol topilmadi yoki ko‘rib chiqilgan.")
                    return

                await conn.execute(
                    """
                    UPDATE questions
                    SET answer = $1, source_name = $2, source_url = $3,
                        verification_status = 'verified',
                        answered_at = NOW()
                    WHERE id = $4
                    """,
                    answer, source_name, source_url, question_id,
                )

            admin_states.pop(user_id, None)

            try:
                await bot.send_message(
                    question["telegram_id"],
                    "📚 TEKSHIRILGAN JAVOB\n\n"
                    f"❓ Savol: {question['question']}\n\n"
                    f"✅ Javob:\n{answer}\n\n"
                    f"📖 Manba: {source_name}\n"
                    f"🔗 Manba ma’lumoti: {source_url}\n\n"
                    "Murakkab shaxsiy holatlarda malakali olim bilan maslahatlashing.",
                )
            except TelegramAPIError:
                logger.exception("Foydalanuvchiga javob yuborilmadi.")

            await message.answer(f"✅ {question_id}-savol saqlandi.")
            return

    if len(text) > 3000:
        await message.answer("Savol 3000 belgidan oshmasin.")
        return

    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT question, answer, source_name, source_url
            FROM questions
            WHERE verification_status = 'verified'
              AND LOWER(question) LIKE '%' || LOWER($1) || '%'
            ORDER BY answered_at DESC
            LIMIT 1
            """,
            text,
        )

        if existing:
            await message.answer(
                "📚 OLDIN TEKSHIRILGAN JAVOB\n\n"
                f"❓ Savol: {existing['question']}\n\n"
                f"✅ Javob:\n{existing['answer']}\n\n"
                f"📖 Manba: {existing['source_name']}\n"
                f"🔗 Manba ma’lumoti: {existing['source_url']}"
            )
            return

        question_id = await conn.fetchval(
            """
            INSERT INTO questions (telegram_id, question)
            VALUES ($1, $2)
            RETURNING id
            """,
            user_id, text,
        )

    await message.answer(
        f"📝 Savolingiz qabul qilindi!\n"
        f"🔢 Savol raqami: {question_id}\n\n"
        "Savol tekshirish uchun administratorga yuboriladi."
    )

    await notify_admins(question_id, user_id, text)


async def main():
    await init_database()

    try:
        logger.info("Hanafiy Fiqhi Ensiklopediyasi ishga tushdi.")
        await dp.start_polling(bot)
    finally:
        if db_pool:
            await db_pool.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
