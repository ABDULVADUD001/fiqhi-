
import os
import re
import json
import asyncio
import logging
from urllib.parse import urlparse

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
    WebAppInfo,
)
from aiogram.exceptions import TelegramAPIError

# =====================================
# SOZLAMALAR
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "8758611320").split(",")
    if value.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN sozlamasi topilmadi.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db_pool = None
user_states = {}
admin_states = {}


# =====================================
# TUGMALAR
# =====================================

def main_keyboard():
    rows = [
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
    ]

    if WEBAPP_URL:
        rows.insert(
            0,
            [
                KeyboardButton(
                    text="🌐 Mini App ochish",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ],
        )

    return ReplyKeyboardMarkup(
        keyboard=rows,
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
        ],
    )


def confirmation_keyboard(question_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash va yuborish",
                    callback_data=f"confirm:{question_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=f"cancel:{question_id}",
                )
            ],
        ],
    )


# =====================================
# MA'LUMOTLAR BAZASI
# =====================================

async def init_database():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL topilmadi. PostgreSQL ulanishini sozlang."
        )

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )

    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            username TEXT,
            full_name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS questions (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            source_name TEXT,
            source_url TEXT,
            verification_status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            answered_at TIMESTAMPTZ,
            reviewed_by BIGINT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS questions_user_idx
        ON questions (telegram_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS questions_status_idx
        ON questions (verification_status)
        """,
    ]

    async with db_pool.acquire() as conn:
        for statement in statements:
            await conn.execute(statement)

        await conn.execute(
            """
            ALTER TABLE questions
            ADD COLUMN IF NOT EXISTS reviewed_by BIGINT
            """
        )

    logger.info("PostgreSQL bazasi tayyor.")


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


# =====================================
# YORDAMCHI FUNKSIYALAR
# =====================================

def valid_source_url(url):
    try:
        parsed = urlparse(url.strip())
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and " " not in url
        )
    except (ValueError, AttributeError):
        return False


def tokenize(text):
    return set(
        re.findall(
            r"[a-zA-Zа-яА-ЯёЁўқғҳЎҚҒҲ0-9]+",
            text.lower(),
        )
    )


async def search_verified_answer(query):
    query_words = tokenize(query)

    if not query_words:
        return None

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, question, answer, source_name, source_url
            FROM questions
            WHERE verification_status = 'verified'
              AND answer IS NOT NULL
            ORDER BY answered_at DESC
            LIMIT 500
            """
        )

    best_row = None
    best_score = 0

    for row in rows:
        question_words = tokenize(row["question"] or "")
        answer_words = tokenize(row["answer"] or "")

        score = (
            3 * len(query_words & question_words)
            + len(query_words & answer_words)
        )

        if score > best_score:
            best_score = score
            best_row = row

    if best_row and best_score >= 3:
        return best_row

    return None


async def notify_admins(question_id, user_id, question):
    text = (
        "📝 YANGI FIQH SAVOLI\n\n"
        f"🔢 Savol ID: {question_id}\n"
        f"👤 Foydalanuvchi ID: {user_id}\n\n"
        f"❓ Savol:\n{question}\n\n"
        "Javob va ishonchli manbani tekshiring."
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


async def submit_question(message: Message, question_text: str):
    if not message.from_user:
        return

    question_text = question_text.strip()

    if not question_text:
        await message.answer("Iltimos, savolingizni yozing.")
        return

    if len(question_text) > 3000:
        await message.answer("Savol 3000 belgidan oshmasin.")
        return

    async with db_pool.acquire() as conn:
        question_id = await conn.fetchval(
            """
            INSERT INTO questions (telegram_id, question)
            VALUES ($1, $2)
            RETURNING id
            """,
            message.from_user.id,
            question_text,
        )

    await message.answer(
        f"📝 Savolingiz qabul qilindi!\n"
        f"🔢 Savol raqami: {question_id}\n\n"
        "Administrator javob va manbani tekshirganidan keyin "
        "javob sizga yuboriladi.",
        reply_markup=main_keyboard(),
    )

    await notify_admins(
        question_id,
        message.from_user.id,
        question_text,
    )


# =====================================
# BOSHLASH VA YORDAM
# =====================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    if message.from_user:
        user_states.pop(message.from_user.id, None)

    await save_user(message)

    await message.answer(
        "🕌 Hanafiy Fiqhi Ensiklopediyasiga xush kelibsiz!\n\n"
        "📚 Hanafiy fiqhi bo‘yicha savol yuboring.\n"
        "🔎 Tekshirilgan javoblarni qidiring.\n\n"
        "⚠️ Javoblar manba asosida tekshirilishi kerak.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Yordam")
async def help_handler(message: Message):
    await message.answer(
        "ℹ️ YORDAM\n\n"
        "/start — bosh menyu\n"
        "/profile — profil\n"
        "/admin — administrator statistikasi\n"
        "/cancel — joriy amalni bekor qilish\n\n"
        "Savolingizni oddiy matn ko‘rinishida yuborishingiz mumkin."
    )


@dp.message(Command("cancel"))
async def cancel_handler(message: Message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    user_states.pop(user_id, None)

    if user_id in ADMIN_IDS:
        admin_states.pop(user_id, None)

    await message.answer(
        "Joriy amal bekor qilindi.",
        reply_markup=main_keyboard(),
    )


# =====================================
# PROFIL
# =====================================

@dp.message(Command("profile"))
@dp.message(F.text == "👤 Profil")
async def profile_handler(message: Message):
    await save_user(message)

    if not message.from_user:
        return

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
        verified = await conn.fetchval(
            """
            SELECT COUNT(*) FROM questions
            WHERE telegram_id = $1
              AND verification_status = 'verified'
            """,
            user_id,
        )

    await message.answer(
        "👤 PROFIL\n\n"
        f"🆔 Telegram ID: {user_id}\n"
        f"❓ Jami savollar: {total}\n"
        f"🕓 Kutilmoqda: {pending}\n"
        f"✅ Tekshirilgan javoblar: {verified}\n"
        "💎 Premium: hali ulanmagan"
    )


# =====================================
# QIDIRUV VA SAVOL REJIMLARI
# =====================================

@dp.message(F.text == "📚 Fiqh savol-javob")
async def question_prompt(message: Message):
    if message.from_user:
        user_states[message.from_user.id] = "question"

    await message.answer(
        "📚 Savol yuborish rejimi yoqildi.\n"
        "Hanafiy fiqhi bo‘yicha savolingizni yozing."
    )


@dp.message(F.text == "🔎 Qidiruv")
async def search_prompt(message: Message):
    if message.from_user:
        user_states[message.from_user.id] = "search"

    await message.answer(
        "🔎 Qidiruv rejimi yoqildi.\n"
        "Masalan: tahorat farzlari"
    )


@dp.message(F.text == "📖 Manbalar")
async def sources_handler(message: Message):
    await message.answer(
        "📖 MANBALAR\n\n"
        "Bot faqat administrator tasdiqlagan javoblarni "
        "qidiruv natijasi sifatida ko‘rsatadi.\n"
        "Manbaning haqiqiyligini administrator tekshirishi kerak.\n"
        "Tashqi saytlardan avtomatik tekshiruv hali ulanmagan."
    )


@dp.message(F.text == "💎 Premium")
async def premium_handler(message: Message):
    await message.answer(
        "💎 PREMIUM\n\n"
        "Obuna va to‘lov tizimi hali ulanmagan. "
        "Hozircha to‘lov olinmaydi."
    )


# =====================================
# MINI APP'DAN KELGAN QIDIRUV
# =====================================

@dp.message(F.web_app_data)
async def mini_app_data_handler(message: Message):
    if not message.from_user or not message.web_app_data:
        return

    try:
        data = json.loads(message.web_app_data.data)
    except (json.JSONDecodeError, TypeError):
        await message.answer("Yuborilgan ma’lumotni o‘qib bo‘lmadi.")
        return

    if not isinstance(data, dict):
        await message.answer("So‘rov formati noto‘g‘ri.")
        return

    if data.get("action") != "search":
        await message.answer("Noma’lum amal.")
        return

    query = str(data.get("query", "")).strip()

    if not query or len(query) > 200:
        await message.answer("Qidiruv so‘rovi noto‘g‘ri.")
        return

    result = await search_verified_answer(query)

    if result:
        await message.answer(
            "🔎 TEKSHIRILGAN JAVOB\n\n"
            f"❓ Savol: {result['question']}\n\n"
            f"✅ Javob:\n{result['answer']}\n\n"
            f"📖 Manba: {result['source_name']}\n"
            f"🔗 Havola: {result['source_url']}"
        )
    else:
        await message.answer(
            "Bu mavzu bo‘yicha tekshirilgan javob topilmadi."
        )


# =====================================
# ADMIN STATISTIKASI
# =====================================

@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Bu buyruq faqat administrator uchun.")
        return

    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        total = await conn.fetchval("SELECT COUNT(*) FROM questions")
        pending = await conn.fetchval(
            """
            SELECT COUNT(*) FROM questions
            WHERE verification_status = 'pending'
            """
        )
        verified = await conn.fetchval(
            """
            SELECT COUNT(*) FROM questions
            WHERE verification_status = 'verified'
            """
        )
        rejected = await conn.fetchval(
            """
            SELECT COUNT(*) FROM questions
            WHERE verification_status = 'rejected'
            """
        )

    await message.answer(
        "🛠 ADMIN STATISTIKASI\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"❓ Jami savollar: {total}\n"
        f"🕓 Kutilmoqda: {pending}\n"
        f"✅ Tasdiqlangan: {verified}\n"
        f"❌ Rad etilgan: {rejected}"
    )


# =====================================
# ADMIN JAVOB BERISH
# =====================================

@dp.callback_query(F.data.startswith("answer:"))
async def begin_answer(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        question_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol raqami noto‘g‘ri.")
        return

    async with db_pool.acquire() as conn:
        question = await conn.fetchrow(
            """
            SELECT id, question FROM questions
            WHERE id = $1 AND verification_status = 'pending'
            """,
            question_id,
        )

    if not question:
        await callback.answer(
            "Savol topilmadi yoki avval ko‘rib chiqilgan.",
            show_alert=True,
        )
        return

    admin_states[callback.from_user.id] = {
        "question_id": question_id,
        "step": "answer",
    }

    await callback.message.answer(
        f"📝 Savol: {question['question']}\n\n"
        "Tekshirilgan javobni yuboring.\n"
        "Bekor qilish uchun /cancel yozing."
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reject:"))
async def reject_question(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        question_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol raqami noto‘g‘ri.")
        return

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE questions
            SET verification_status = 'rejected',
                reviewed_by = $2
            WHERE id = $1 AND verification_status = 'pending'
            """,
            question_id,
            callback.from_user.id,
        )

    if result == "UPDATE 1":
        if callback.message:
            await callback.message.answer(
                f"❌ {question_id}-savol rad etildi."
            )
        await callback.answer("Rad etildi.")
    else:
        await callback.answer(
            "Savol topilmadi yoki ko‘rib chiqilgan.",
            show_alert=True,
        )


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_review(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        question_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol raqami noto‘g‘ri.")
        return

    state = admin_states.get(callback.from_user.id)

    if state and state.get("question_id") == question_id:
        admin_states.pop(callback.from_user.id, None)

    await callback.answer("Bekor qilindi.")

    if callback.message:
        await callback.message.answer(
            "Javob tasdiqlanmadi. Savol kutilayotgan holatda qoldi."
        )


@dp.callback_query(F.data.startswith("confirm:"))
async def confirm_answer(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        question_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol raqami noto‘g‘ri.")
        return

    state = admin_states.get(callback.from_user.id)

    if (
        not state
        or state.get("step") != "confirm"
        or state.get("question_id") != question_id
    ):
        await callback.answer(
            "Tasdiqlash ma’lumoti topilmadi.",
            show_alert=True,
        )
        return

    answer = state.get("answer", "").strip()
    source_name = state.get("source_name", "").strip()
    source_url = state.get("source_url", "").strip()

    if not answer or not source_name or not valid_source_url(source_url):
        await callback.answer(
            "Javob yoki manba ma’lumoti noto‘g‘ri.",
            show_alert=True,
        )
        return

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            question = await conn.fetchrow(
                """
                SELECT telegram_id, question
                FROM questions
                WHERE id = $1 AND verification_status = 'pending'
                FOR UPDATE
                """,
                question_id,
            )

            if not question:
                admin_states.pop(callback.from_user.id, None)
                await callback.answer(
                    "Savol topilmadi yoki avval ko‘rib chiqilgan.",
                    show_alert=True,
                )
                return

            await conn.execute(
                """
                UPDATE questions
                SET answer = $1,
                    source_name = $2,
                    source_url = $3,
                    verification_status = 'verified',
                    answered_at = NOW(),
                    reviewed_by = $4
                WHERE id = $5
                """,
                answer,
                source_name,
                source_url,
                callback.from_user.id,
                question_id,
            )

    admin_states.pop(callback.from_user.id, None)

    try:
        await bot.send_message(
            question["telegram_id"],
            "📚 TEKSHIRILGAN JAVOB\n\n"
            f"❓ Savol: {question['question']}\n\n"
            f"✅ Javob:\n{answer}\n\n"
            f"📖 Manba: {source_name}\n"
            f"🔗 Havola: {source_url}\n\n"
            "Murakkab shaxsiy holatlarda malakali olim bilan maslahatlashing.",
            reply_markup=main_keyboard(),
        )
    except TelegramAPIError:
        logger.exception("Foydalanuvchiga javob yuborilmadi.")
        if callback.message:
            await callback.message.answer(
                "Javob bazaga saqlandi, lekin foydalanuvchiga "
                "Telegram orqali yuborilmadi."
            )

    if callback.message:
        await callback.message.answer(
            f"✅ {question_id}-savol tasdiqlandi."
        )

    await callback.answer("Tasdiqlandi.")


# =====================================
# MATNLI XABARLAR
# =====================================

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

    # Administratorning javob berish bosqichlari
    if state and user_id in ADMIN_IDS:
        if state["step"] == "answer":
            if len(text) > 3500:
                await message.answer(
                    "Javob 3500 belgidan oshmasin."
                )
                return

            state["answer"] = text
            state["step"] = "source"

            await message.answer(
                "Manbani shu formatda yuboring:\n\n"
                "MANBA NOMI | https://manba.uz/sahifa\n\n"
                "Manba sahifasini o‘zingiz tekshiring."
            )
            return

        if state["step"] == "source":
            parts = text.split("|", 1)

            if len(parts) != 2:
                await message.answer(
                    "Format: MANBA NOMI | https://manba.uz/sahifa"
                )
                return

            source_name = parts[0].strip()
            source_url = parts[1].strip()

            if not source_name:
                await message.answer("Manba nomini kiriting.")
                return

            if not valid_source_url(source_url):
                await message.answer(
                    "Havola http:// yoki https:// bilan boshlanishi "
                    "va to‘g‘ri URL formatida bo‘lishi kerak."
                )
                return

            state["source_name"] = source_name
            state["source_url"] = source_url
            state["step"] = "confirm"

            await message.answer(
                "🔍 JAVOBNI TEKSHIRING\n\n"
                f"❓ Savol ID: {state['question_id']}\n\n"
                f"✅ Javob:\n{state['answer']}\n\n"
                f"📖 Manba: {source_name}\n"
                f"🔗 Havola: {source_url}\n\n"
                "Ma’lumotni o‘zingiz tekshirgach, tasdiqlash tugmasini bosing.",
                reply_markup=confirmation_keyboard(state["question_id"]),
            )
            return

        if state["step"] == "confirm":
            await message.answer(
                "Javobni tasdiqlash yoki bekor qilish tugmasini bosing."
            )
            return

    mode = user_states.get(user_id)

    if mode == "search":
        result = await search_verified_answer(text)

        if result:
            await message.answer(
                "🔎 TEKSHIRILGAN JAVOB\n\n"
                f"❓ Savol: {result['question']}\n\n"
                f"✅ Javob:\n{result['answer']}\n\n"
                f"📖 Manba: {result['source_name']}\n"
                f"🔗 Havola: {result['source_url']}"
            )
        else:
            await message.answer(
                "Bu mavzu bo‘yicha tekshirilgan javob topilmadi.\n\n"
                "Boshqa kalit so‘z bilan qidiring yoki "
                "Fiqh savol-javob bo‘limidan yangi savol yuboring."
            )
        return

    if mode == "question":
        await submit_question(message, text)
        return

    await submit_question(message, text)


# =====================================
# ISHGA TUSHIRISH
# =====================================

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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to‘xtatildi.")
