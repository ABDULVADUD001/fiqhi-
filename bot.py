import os
import re
import asyncio
import logging
from urllib.parse import urlparse

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramAPIError

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/fiqhi.db")
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8758611320").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("Railway Variables ichida BOT_TOKEN yo‘q!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = None
user_states = {}
admin_states = {}


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Fiqh savol-javob")],
            [KeyboardButton(text="🔎 Qidiruv")],
            [KeyboardButton(text="👤 Profil"),
             KeyboardButton(text="📖 Manbalar")],
            [KeyboardButton(text="ℹ️ Yordam")],
        ],
        resize_keyboard=True
    )


def review_buttons(qid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✍️ Javob berish",
            callback_data=f"answer:{qid}"
        ),
        InlineKeyboardButton(
            text="❌ Rad etish",
            callback_data=f"reject:{qid}"
        )
    ]])


def confirm_buttons(qid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Tasdiqlash va yuborish",
            callback_data=f"confirm:{qid}"
        ),
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data=f"cancel:{qid}"
        )
    ]])


async def init_database():
    global db
    folder = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(folder, exist_ok=True)

    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row

    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            source_name TEXT,
            source_url TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_by INTEGER
        )
    """)

    await db.commit()
    logging.info("SQLite bazasi tayyor: %s", DB_PATH)


async def save_user(message: Message):
    if not message.from_user:
        return

    user = message.from_user

    await db.execute("""
        INSERT INTO users (telegram_id, username, full_name)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
        username=excluded.username,
        full_name=excluded.full_name
    """, (user.id, user.username, user.full_name or ""))

    await db.commit()


def valid_url(url):
    try:
        parsed = urlparse(url.strip())
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and " " not in url
        )
    except Exception:
        return False


def words(text):
    return set(re.findall(
        r"[a-zA-Zа-яА-ЯёЁўқғҳЎҚҒҲ0-9]+",
        text.lower()
    ))


async def search_answer(query):
    query_words = words(query)
    cursor = await db.execute("""
        SELECT question, answer, source_name, source_url
        FROM questions
        WHERE status='verified'
        ORDER BY id DESC
        LIMIT 500
    """)
    rows = await cursor.fetchall()

    best = None
    best_score = 0

    for row in rows:
        question_words = words(row["question"])
        answer_words = words(row["answer"] or "")
        score = (
            3 * len(query_words & question_words)
            + len(query_words & answer_words)
        )

        if score > best_score:
            best = row
            best_score = score

    return best if best_score >= 3 else None


async def send_to_admins(qid, uid, question):
    text = (
        "📝 YANGI FIQH SAVOLI\n\n"
        f"🔢 Savol: #{qid}\n"
        f"👤 Foydalanuvchi ID: {uid}\n\n"
        f"❓ {question}\n\n"
        "Javob va manbani tekshiring."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                text,
                reply_markup=review_buttons(qid)
            )
        except TelegramAPIError:
            logging.exception("Adminga xabar yuborilmadi")


async def submit_question(message: Message, question):
    if not message.from_user:
        return

    question = question.strip()

    if not question:
        await message.answer("Savolingizni yozing.")
        return

    if len(question) > 3000:
        await message.answer("Savol 3000 belgidan oshmasin.")
        return

    cursor = await db.execute(
        "INSERT INTO questions (telegram_id, question) VALUES (?, ?)",
        (message.from_user.id, question)
    )
    await db.commit()

    qid = cursor.lastrowid

    await message.answer(
        f"✅ Savolingiz qabul qilindi!\nSavol raqami: #{qid}\n\n"
        "Administrator javob va manbani tekshirganidan "
        "keyin javob sizga yuboriladi.",
        reply_markup=menu()
    )

    await send_to_admins(qid, message.from_user.id, question)


@dp.message(CommandStart())
async def start(message: Message):
    await save_user(message)

    if message.from_user:
        user_states.pop(message.from_user.id, None)

    await message.answer(
        "🕌 Hanafiy Fiqhi Ensiklopediyasiga xush kelibsiz!\n\n"
        "📚 Fiqh bo‘yicha savol yuboring.\n"
        "🔎 Tasdiqlangan javoblarni qidiring.\n\n"
        "Eslatma: javoblar ishonchli manbadan tekshirilishi kerak.",
        reply_markup=menu()
    )


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Yordam")
async def help_command(message: Message):
    await message.answer(
        "ℹ️ YORDAM\n\n"
        "/start — bosh menyu\n"
        "/profile — profilingiz\n"
        "/admin — admin statistikasi\n"
        "/savollar — kutilayotgan savollar\n"
        "/cancel — amalni bekor qilish\n\n"
        "Savolingizni yozib yuborishingiz mumkin."
    )


@dp.message(Command("cancel"))
async def cancel(message: Message):
    if message.from_user:
        user_states.pop(message.from_user.id, None)
        admin_states.pop(message.from_user.id, None)

    await message.answer(
        "Amal bekor qilindi.",
        reply_markup=menu()
    )


@dp.message(F.text == "📚 Fiqh savol-javob")
async def ask_prompt(message: Message):
    if message.from_user:
        user_states[message.from_user.id] = "question"

    await message.answer(
        "Hanafiy fiqhi bo‘yicha savolingizni yozing."
    )


@dp.message(F.text == "🔎 Qidiruv")
async def search_prompt(message: Message):
    if message.from_user:
        user_states[message.from_user.id] = "search"

    await message.answer(
        "Qidiruv uchun kalit so‘z yozing.\n"
        "Masalan: tahorat farzlari"
    )


@dp.message(F.text == "📖 Manbalar")
async def sources(message: Message):
    await message.answer(
        "📖 MANBALAR\n\n"
        "Bot faqat administrator tasdiqlagan javoblarni "
        "qidiruvda ko‘rsatadi. Har bir manbani javob berishdan "
        "oldin tekshirish zarur."
    )


@dp.message(Command("profile"))
@dp.message(F.text == "👤 Profil")
async def profile(message: Message):
    await save_user(message)

    if not message.from_user:
        return

    uid = message.from_user.id

    cursor = await db.execute(
        "SELECT COUNT(*) FROM questions WHERE telegram_id=?",
        (uid,)
    )
    total = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM questions "
        "WHERE telegram_id=? AND status='pending'",
        (uid,)
    )
    pending = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM questions "
        "WHERE telegram_id=? AND status='verified'",
        (uid,)
    )
    verified = (await cursor.fetchone())[0]

    await message.answer(
        "👤 PROFIL\n\n"
        f"🆔 ID: {uid}\n"
        f"❓ Jami savollar: {total}\n"
        f"🕓 Kutilmoqda: {pending}\n"
        f"✅ Javob berilgan: {verified}"
    )


@dp.message(Command("admin"))
async def admin_stats(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Bu buyruq faqat admin uchun.")
        return

    cursor = await db.execute("SELECT COUNT(*) FROM users")
    users = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM questions")
    total = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM questions WHERE status='pending'"
    )
    pending = (await cursor.fetchone())[0]

    cursor = await db.execute(
        "SELECT COUNT(*) FROM questions WHERE status='verified'"
    )
    verified = (await cursor.fetchone())[0]

    await message.answer(
        "🛠 ADMIN STATISTIKASI\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"❓ Jami savollar: {total}\n"
        f"🕓 Kutilmoqda: {pending}\n"
        f"✅ Tasdiqlangan: {verified}\n\n"
        "/savollar — kutilayotgan savollar"
    )


@dp.message(Command("savollar"))
async def pending_questions(message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Faqat admin uchun.")
        return

    cursor = await db.execute("""
        SELECT id, telegram_id, question
        FROM questions
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT 10
    """)
    rows = await cursor.fetchall()

    if not rows:
        await message.answer("Kutilayotgan savollar yo‘q.")
        return

    for row in rows:
        await message.answer(
            f"❓ Savol #{row['id']}\n"
            f"Foydalanuvchi ID: {row['telegram_id']}\n\n"
            f"{row['question']}",
            reply_markup=review_buttons(row["id"])
        )


@dp.callback_query(F.data.startswith("answer:"))
async def begin_answer(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    qid = int(callback.data.split(":")[1])

    cursor = await db.execute(
        "SELECT question FROM questions WHERE id=? AND status='pending'",
        (qid,)
    )
    row = await cursor.fetchone()

    if not row:
        await callback.answer("Savol topilmadi.", show_alert=True)
        return

    admin_states[callback.from_user.id] = {
        "qid": qid,
        "step": "answer"
    }

    await callback.message.answer(
        f"Savol: {row['question']}\n\n"
        "Tekshirilgan javobni yuboring.\n"
        "Bekor qilish: /cancel"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("reject:"))
async def reject(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    qid = int(callback.data.split(":")[1])

    cursor = await db.execute(
        "UPDATE questions SET status='rejected', reviewed_by=? "
        "WHERE id=? AND status='pending'",
        (callback.from_user.id, qid)
    )
    await db.commit()

    if cursor.rowcount:
        await callback.answer("Savol rad etildi.")
        await callback.message.answer(f"❌ #{qid} rad etildi.")
    else:
        await callback.answer("Savol topilmadi.", show_alert=True)


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_review(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    qid = int(callback.data.split(":")[1])
    state = admin_states.get(callback.from_user.id)

    if state and state.get("qid") == qid:
        admin_states.pop(callback.from_user.id, None)

    await callback.answer("Bekor qilindi.")
    await callback.message.answer(
        "Javob tasdiqlanmadi. Savol kutilayotgan holatda qoldi."
    )


@dp.callback_query(F.data.startswith("confirm:"))
async def confirm_answer(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    qid = int(callback.data.split(":")[1])
    state = admin_states.get(callback.from_user.id)

    if not state or state.get("step") != "confirm" or state.get("qid") != qid:
        await callback.answer("Tasdiqlash ma’lumoti topilmadi.", show_alert=True)
        return

    cursor = await db.execute(
        "SELECT telegram_id, question FROM questions "
        "WHERE id=? AND status='pending'",
        (qid,)
    )
    row = await cursor.fetchone()

    if not row:
        await callback.answer("Savol topilmadi.", show_alert=True)
        return

    answer = state["answer"]
    source_name = state["source_name"]
    source_url = state["source_url"]

    await db.execute("""
        UPDATE questions
        SET answer=?, source_name=?, source_url=?,
            status='verified', reviewed_by=?
        WHERE id=?
    """, (
        answer, source_name, source_url,
        callback.from_user.id, qid
    ))
    await db.commit()
    admin_states.pop(callback.from_user.id, None)

    try:
        await bot.send_message(
            row["telegram_id"],
            "📚 TEKSHIRILGAN JAVOB\n\n"
            f"❓ Savol: {row['question']}\n\n"
            f"✅ Javob:\n{answer}\n\n"
            f"📖 Manba: {source_name}\n"
            f"🔗 Havola: {source_url}\n\n"
            "Murakkab shaxsiy holatlarda malakali olim bilan maslahatlashing.",
            reply_markup=menu()
        )
    except TelegramAPIError:
        logging.exception("Foydalanuvchiga xabar yuborilmadi")
        await callback.message.answer(
            "Javob saqlandi, lekin foydalanuvchiga yuborilmadi."
        )

    await callback.message.answer(f"✅ #{qid} tasdiqlandi.")
    await callback.answer("Tasdiqlandi.")


@dp.message(F.text)
async def text_handler(message: Message):
    await save_user(message)

    if not message.from_user:
        return

    uid = message.from_user.id
    text = (message.text or "").strip()

    if not text or text.startswith("/"):
        return

    state = admin_states.get(uid)

    if state and uid in ADMIN_IDS:
        if state["step"] == "answer":
            if len(text) > 3500:
                await message.answer("Javob 3500 belgidan oshmasin.")
                return

            state["answer"] = text
            state["step"] = "source"

            await message.answer(
                "Manbani quyidagi formatda yuboring:\n\n"
                "MANBA NOMI | https://manba.uz/sahifa"
            )
            return

        if state["step"] == "source":
            parts = text.split("|", 1)

            if len(parts) != 2:
                await message.answer(
                    "Format: MANBA NOMI | https://manba.uz/sahifa"
                )
                return

            name = parts[0].strip()
            url = parts[1].strip()

            if not name or not valid_url(url):
                await message.answer(
                    "Manba nomi yoki havola noto‘g‘ri."
                )
                return

            state["source_name"] = name
            state["source_url"] = url
            state["step"] = "confirm"

            await message.answer(
                "🔍 JAVOBNI TEKSHIRING\n\n"
                f"Javob:\n{state['answer']}\n\n"
                f"Manba: {name}\n"
                f"Havola: {url}\n\n"
                "Manbani o‘zingiz tekshirgach tasdiqlang.",
                reply_markup=confirm_buttons(state["qid"])
            )
            return

        if state["step"] == "confirm":
            await message.answer(
                "Tasdiqlash yoki bekor qilish tugmasini bosing."
            )
            return

    mode = user_states.get(uid)

    if mode == "search":
        result = await search_answer(text)

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
                "Bu mavzu bo‘yicha tasdiqlangan javob topilmadi."
            )
        return

    await submit_question(message, text)


async def main():
    await init_database()

    try:
        logging.info("Bot ishga tushmoqda...")
        await dp.start_polling(bot)
    finally:
        if db:
            await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
