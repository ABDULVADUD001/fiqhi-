
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

# Foydalanuvchi ayni paytda nima qilayotganini eslab turadi.
user_modes = {}

# Dastlabki manbali ma'lumotlar.
STARTER_ANSWERS = [
    {
        "question": "Tahoratning farzlari nechta?",
        "answer": (
            "Hanafiy mazhabida tahoratning to‘rtta farzi bor:\n\n"
            "1. Yuzni yuvish.\n"
            "2. Ikki qo‘lni tirsaklari bilan qo‘shib yuvish.\n"
            "3. Boshning to‘rtdan biriga mas'h tortish.\n"
            "4. Ikki oyoqni to‘piqlari bilan qo‘shib yuvish.\n\n"
            "Bu ro‘yxat tahoratning farzlariga tegishli. "
            "Tahoratning sunnatlari va boshqa masalalari alohida o‘rganiladi."
        ),
        "source": (
            "O‘zbekiston musulmonlari idorasi — "
            "https://muslim.uz/uz/e/post/41177-tahoratning-farzlari"
        ),
    },
    {
        "question": "Namoz va tahorat haqida boshlang‘ich ma’lumot",
        "answer": (
            "Namozdan oldin tahorat yoki zarur bo‘lsa, boshqa "
            "shar'iy poklanish usuli bilan poklanish muhimdir. "
            "Tahorat va namozga oid masalalar turli shartlar va "
            "hukmlarga ega. Aniq holat bo‘yicha savol yuboring."
        ),
        "source": (
            "O‘zbekiston musulmonlari idorasi — "
            "https://muslim.uz/uz/c/prayer"
        ),
    },
    {
        "question": "Ro‘za nima va Ramazon ro‘zasi haqida",
        "answer": (
            "Ro‘za — shar'iy ma'noda tong otishidan quyosh "
            "botishigacha niyat bilan yeyish, ichish va jinsiy "
            "yaqinlikdan tiyilishdir. Ramazon ro‘zasi Islomning "
            "asosiy ibodatlaridan biridir. Ayrim holatlarning "
            "hukmi tafsilotlarga bog‘liq bo‘lishi mumkin."
        ),
        "source": (
            "O‘zbekiston musulmonlari idorasi — "
            "https://www.muslim.uz/uz/c/fasting"
        ),
    },
]


def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
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
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ],
        ],
        resize_keyboard=True,
    )


async def init_db():
    global db_pool

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL topilmadi. Railway'dagi PostgreSQL "
            "ulanishini tekshiring."
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

        # Dastlabki ma'lumotlarni faqat bir marta qo'shish.
        for item in STARTER_ANSWERS:
            await conn.execute("""
                INSERT INTO questions
                    (user_id, question, answer, source, status)
                SELECT 0, $1, $2, $3, 'approved'
                WHERE NOT EXISTS (
                    SELECT 1 FROM questions
                    WHERE user_id = 0 AND question = $1
                )
            """, item["question"], item["answer"], item["source"])

    log.info("Ma'lumotlar bazasi tayyor.")


async def save_user(message: Message):
    if not message.from_user:
        return

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id)
            DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name
        """,
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
        )


async def find_answer(query: str):
    async with db_pool.acquire() as conn:
        return await conn.fetch("""
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


async def search_and_reply(message: Message, query: str):
    query = query.strip()

    if not query:
        await message.answer("Iltimos, fiqh mavzusini yozing.")
        return

    try:
        answers = await find_answer(query)
    except Exception:
        log.exception("Qidiruv xatosi")
        await message.answer(
            "Qidiruv vaqtida xato yuz berdi. Keyinroq urinib ko‘ring."
        )
        return

    if not answers:
        await message.answer(
            "Bu so‘rovga mos tasdiqlangan javob topilmadi.\n\n"
            "Savolingizni «❓ Savol berish» orqali yuborishingiz mumkin."
        )
        return

    for item in answers:
        text = f"📚 {item['question']}\n\n{item['answer']}"

        if item["source"]:
            text += f"\n\n📖 Manba: {item['source']}"

        await message.answer(text)


async def save_question(message: Message, question: str):
    question = question.strip()

    if not question:
        await message.answer("Savolingizni yozing.")
        return

    async with db_pool.acquire() as conn:
        question_id = await conn.fetchval("""
            INSERT INTO questions (user_id, question, status)
            VALUES ($1, $2, 'pending')
            RETURNING id
        """, message.from_user.id, question)

    await message.answer(
        f"✅ Savolingiz qabul qilindi.\n"
        f"Raqami: #{question_id}\n\n"
        "Javob manbalar tekshirilganidan keyin beriladi."
    )

    # Administratorlarga savol haqida xabar berish.
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"❓ Yangi fiqhiy savol #{question_id}\n\n"
                f"Foydalanuvchi ID: {message.from_user.id}\n"
                f"Savol: {question}\n\n"
                f"Javob berish:\n"
                f"/javob {question_id} | javob matni | manba URL"
            )
        except Exception:
            log.exception("Adminga xabar yuborilmadi: %s", admin_id)


@dp.message(CommandStart())
async def start(message: Message):
    await save_user(message)
    user_modes.pop(message.from_user.id, None)

    await message.answer(
        "Assalomu alaykum!\n\n"
        "Hanafiy Fiqhi Ensiklopediyasiga xush kelibsiz.\n"
        "Bo‘limlarni tanlang yoki fiqhiy savolingizni qidiring.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "📚 Bo‘limlarni oching yoki 🔎 Qidiruvni tanlang.\n"
        "❓ Savol berish orqali savolingizni yuborishingiz mumkin.\n\n"
        "Admin buyruqlari: /admin, /savollar"
    )


@dp.message(Command("cancel"))
async def cancel_command(message: Message):
    user_modes.pop(message.from_user.id, None)
    await message.answer(
        "Amal bekor qilindi.",
        reply_markup=main_keyboard(),
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
        "🛠 Admin statistikasi\n\n"
        f"Foydalanuvchilar: {users}\n"
        f"Kutilayotgan savollar: {pending}\n"
        f"Tasdiqlangan javoblar: {approved}\n\n"
        "Buyruqlar:\n"
        "/savollar — kutilayotgan savollar\n"
        "/javob ID | javob | manba — javobni tasdiqlash"
    )


@dp.message(Command("savollar"))
async def pending_questions(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat administrator uchun.")
        return

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, user_id, question, created_at
            FROM questions
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT 10
        """)

    if not rows:
        await message.answer("Kutilayotgan savollar yo‘q.")
        return

    for row in rows:
        await message.answer(
            f"❓ Savol #{row['id']}\n"
            f"Foydalanuvchi ID: {row['user_id']}\n"
            f"{row['question']}\n\n"
            f"Javob berish:\n"
            f"/javob {row['id']} | javob matni | manba URL"
        )


@dp.message(Command("javob"))
async def approve_answer(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat administrator uchun.")
        return

    # Misol: /javob 12 | Tahorat ... | https://muslim.uz/...
    raw = message.text.partition(" ")[2].strip()
    parts = [part.strip() for part in raw.split("|", 2)]

    if len(parts) != 3 or not parts[0].isdigit():
        await message.answer(
            "Buyruq formati:\n"
            "/javob ID | javob matni | manba URL"
        )
        return

    question_id = int(parts[0])
    answer = parts[1]
    source = parts[2]

    if not answer or not source.startswith(("https://", "http://")):
        await message.answer(
            "Javob matni va to‘liq manba URL manzilini kiriting."
        )
        return

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE questions
            SET answer = $1, source = $2, status = 'approved'
            WHERE id = $3 AND status = 'pending'
            RETURNING user_id, question
        """, answer, source, question_id)

    if not row:
        await message.answer(
            "Bunday kutilayotgan savol topilmadi. /savollar ni tekshiring."
        )
        return

    await message.answer(
        f"✅ #{question_id} savolga javob saqlandi va tasdiqlandi."
    )

    try:
        await bot.send_message(
            row["user_id"],
            f"📚 Savolingizga javob:\n\n{row['question']}\n\n"
            f"{answer}\n\n📖 Manba: {source}"
        )
    except Exception:
        log.exception("Foydalanuvchiga javob yuborilmadi")


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
        user_modes[message.from_user.id] = "question"
        await message.answer("Savolingizni shu chatga yozing.")
    else:
        await message.answer("Noma'lum amal.")


@dp.message(F.text == "📚 Fiqh bo‘limlari")
async def sections(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💧 Tahorat", callback_data="topic:Tahorat"
            ),
            InlineKeyboardButton(
                text="🕌 Namoz", callback_data="topic:Namoz"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌙 Ro‘za", callback_data="topic:Ro‘za"
            ),
            InlineKeyboardButton(
                text="🤲 Zakot", callback_data="topic:Zakot"
            ),
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
    user_modes[message.from_user.id] = "search"
    await message.answer("Qidiriladigan fiqh mavzusini yozing.")


@dp.message(F.text == "❓ Savol berish")
async def ask_question(message: Message):
    user_modes[message.from_user.id] = "question"
    await message.answer("Savolingizni bitta xabar qilib yozing.")


@dp.message(F.text == "👤 Profil")
async def profile(message: Message):
    await save_user(message)

    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM questions WHERE user_id = $1",
            message.from_user.id,
        )

    await message.answer(
        f"👤 Profil\n"
        f"Telegram ID: {message.from_user.id}\n"
        f"Yuborgan savollaringiz: {count}"
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

    mode = user_modes.pop(message.from_user.id, None)

    if mode == "question":
        await save_question(message, text)
    else:
        # Qidiruv rejimida ham, oddiy matnda ham qidiradi.
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
