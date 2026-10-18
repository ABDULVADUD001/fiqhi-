
import asyncio
import logging
import os
import re
import sqlite3
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# =========================================================
# 1. SOZLAMALAR
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "8758611320").split(",")
    if x.strip().isdigit()
}

DB_PATH = os.getenv("DB_PATH", "fiqhi.db")
FATVO_API = "https://api.fatvo.uz/v1/questions/public"

APP_NAME = "Hanafiy Fiqhi Ensiklopediyasi"
FATVO_HOME = "https://fatvo.uz/uz"
MUSLIM_HOME = "https://muslim.uz/uz"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("fiqhi_bot")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi. Hosting sozlamalarida BOT_TOKEN o'zgaruvchisini kiriting."
    )

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# Foydalanuvchi holatlari va vaqtinchalik qidiruv natijalari.
user_states = {}
admin_states = {}
fatvo_choices = {}
pending_choices = {}

http_session = None


# =========================================================
# 2. MENYULAR
# =========================================================

def main_menu(is_admin=False):
    rows = [
        [
            KeyboardButton(text="🔎 Javob izlash"),
            KeyboardButton(text="❓ Savol yuborish"),
        ],
        [
            KeyboardButton(text="📚 Manbalar"),
            KeyboardButton(text="👤 Profilim"),
        ],
        [
            KeyboardButton(text="ℹ️ Yordam"),
            KeyboardButton(text="📖 Fiqh haqida"),
        ],
    ]

    if is_admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Savol yoki menyuni tanlang...",
    )


def admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Tekshirilmagan savollar",
                    callback_data="admin:pending",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin:stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Menyuga qaytish",
                    callback_data="admin:home",
                )
            ],
        ]
    )


def fatvo_results_keyboard(results):
    buttons = []

    for index, item in enumerate(results):
        title = item.get("title") or item.get("question") or "Mavzu"
        title = re.sub(r"\s+", " ", title).strip()

        if len(title) > 55:
            title = title[:52] + "..."

        buttons.append([
            InlineKeyboardButton(
                text=f"📖 {title}",
                callback_data=f"fatvo:{index}",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def pending_keyboard(question_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Javob berish",
                    callback_data=f"review:answer:{question_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"review:reject:{question_id}",
                ),
            ]
        ]
    )


# =========================================================
# 3. MA'LUMOTLAR BAZASI
# =========================================================

async def init_db():
    folder = Path(DB_PATH).parent

    if str(folder) not in ("", "."):
        folder.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                full_name TEXT DEFAULT '',
                joined_at INTEGER NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                admin_id INTEGER,
                admin_answer TEXT,
                source_name TEXT,
                source_url TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS verified_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                source_name TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                admin_id INTEGER
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_questions_status
            ON questions(status)
        """)

        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_answers_question
            ON verified_answers(question)
        """)

        await db.commit()

    logger.info("Ma'lumotlar bazasi tayyor: %s", DB_PATH)


async def register_user(user):
    if not user:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users(user_id, username, full_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
        """, (
            user.id,
            user.username or "",
            user.full_name or "",
            int(time.time()),
        ))
        await db.commit()


async def log_activity(user_id, action):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_activity(user_id, action, created_at) VALUES (?, ?, ?)",
            (user_id, action, int(time.time())),
        )
        await db.commit()


async def submit_question(message: Message, question: str):
    uid = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO questions(user_id, question, status, created_at)
            VALUES (?, ?, 'pending', ?)
        """, (uid, question.strip(), int(time.time())))

        question_id = cursor.lastrowid
        await db.commit()

    await log_activity(uid, "question_submitted")

    await message.answer(
        "📨 <b>Savolingiz tekshiruvga yuborildi.</b>\n\n"
        "Hozircha manbadan mos, javobi mavjud natija topilmadi. "
        "Savolingiz admin tomonidan ko‘rib chiqiladi.",
        reply_markup=main_menu(uid in ADMIN_IDS),
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "📥 <b>Yangi savol</b>\n\n"
                f"🆔 Savol ID: <code>{question_id}</code>\n"
                f"👤 Foydalanuvchi: <code>{uid}</code>\n\n"
                f"❓ {html_escape(question)}",
                reply_markup=pending_keyboard(question_id),
            )
        except TelegramAPIError:
            logger.exception("Admin %s ga xabar yuborilmadi", admin_id)

    return question_id


async def search_answer(query):
    """Avval admin tasdiqlagan javoblar bazasidan qidiradi."""
    query_norm = normalize_text(query)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT id, question, answer, source_name, source_url
            FROM verified_answers
            ORDER BY id DESC
        """)

        rows = await cursor.fetchall()

    best = None
    best_score = 0.0

    for row in rows:
        score = similarity_score(query_norm, normalize_text(row["question"]))

        if score > best_score:
            best = dict(row)
            best_score = score

    # O'xshash savolning o'zi yetarli emas; aniqroq moslik talab qilinadi.
    if best and best_score >= 0.78:
        return best

    return None


async def get_profile(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE user_id = ?",
            (user_id,),
        )
        sent = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE user_id = ? AND status = 'answered'",
            (user_id,),
        )
        answered = (await cursor.fetchone())[0]

    return sent, answered


# =========================================================
# 4. MATNNI TOZALASH VA QIDIRUV VARIANTLARI
# =========================================================

def html_escape(text):
    text = str(text or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = text.replace("ʻ", "'").replace("’", "'").replace("ʼ", "'")
    text = text.replace("`", "'").replace("o‘", "o").replace("g‘", "g")
    text = re.sub(r"[^a-z0-9а-яёўқғҳ\s'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def words(text):
    return [
        word.strip("'-")
        for word in normalize_text(text).split()
        if len(word.strip("'-")) > 1
    ]


def similarity_score(a, b):
    wa = set(words(a))
    wb = set(words(b))

    if not wa or not wb:
        return 0.0

    overlap = len(wa & wb) / max(1, len(wa))
    sequence = SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()

    return 0.75 * overlap + 0.25 * sequence


def make_search_variants(query):
    """
    Qidiruv uchun bir nechta variant:
    - asl matn;
    - tinish belgilaridan tozalangan matn;
    - keng tarqalgan o'zbekcha yozuv variantlari.
    """
    original = str(query or "").strip()
    normalized = normalize_text(original)

    variants = []

    def add(value):
        value = re.sub(r"\s+", " ", value).strip()

        if value and value not in variants:
            variants.append(value)

    add(original)
    add(normalized)

    # Apostrof va lotincha yozuv farqlarini yumshatish.
    add(normalized.replace("'", ""))
    add(normalized.replace("q", "k"))
    add(normalized.replace("x", "h"))

    # Keng tarqalgan so'z variantlari.
    replacements = [
        (r"\btaxorat\b", "tahorat"),
        (r"\btahoratlar\b", "tahorat"),
        (r"\bnamozlar\b", "namoz"),
        (r"\broza\b", "ro'za"),
        (r"\bruza\b", "ro'za"),
        (r"\bgusl\b", "g'usl"),
        (r"\bghusl\b", "g'usl"),
        (r"\bzakot\b", "zakot"),
        (r"\bzakah\b", "zakot"),
        (r"\bfarzlari\b", "farz"),
        (r"\bfarzlar\b", "farz"),
    ]

    for pattern, replacement in replacements:
        add(re.sub(pattern, replacement, normalized))

    return variants[:5]


def candidate_score(query, item):
    title = get_uz(item.get("title"))
    question = get_uz(item.get("question"))
    combined = f"{title} {question}".strip()

    return similarity_score(query, combined)


# =========================================================
# 5. FATVO.UZ API
# =========================================================

def get_uz(value):
    """API qiymati oddiy matn yoki til kalitlari bo'lgan lug'at bo'lishi mumkin."""
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        for key in ("uz", "uz-Latn", "uz-Cyrl", "uz_latn", "uz_cyrl"):
            if value.get(key):
                return str(value[key]).strip()

        for item in value.values():
            if isinstance(item, str) and item.strip():
                return item.strip()

    return str(value).strip()


async def fetch_fatvo(query):
    """
    Fatvo.uz ochiq API'sidan qidiradi.
    API tuzilishi o'zgarsa xato logga yoziladi.
    """
    global http_session

    variants = make_search_variants(query)
    found = {}
    errors = []

    for variant in variants:
        params = {
            "page": 1,
            "limit": 10,
            "search": variant,
            "sort": "created",
        }

        try:
            async with http_session.get(
                FATVO_API,
                params=params,
                timeout=aiohttp.ClientTimeout(total=18),
            ) as response:
                if response.status != 200:
                    errors.append(f"HTTP {response.status}")
                    logger.warning(
                        "Fatvo API HTTP xatosi: status=%s query=%s",
                        response.status,
                        variant,
                    )
                    continue

                payload = await response.json(content_type=None)

            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("data", [])

                if isinstance(items, dict):
                    items = items.get("data", items.get("items", []))
            else:
                items = []

            if not isinstance(items, list):
                logger.warning(
                    "Fatvo API kutilmagan javob turi: %s",
                    type(items).__name__,
                )
                continue

            for item in items:
                if isinstance(item, dict):
                    item_id = str(item.get("id") or "").strip()

                    if item_id:
                        found[item_id] = item

        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            errors.append("Tarmoq/API xatosi")
            logger.exception("Fatvo API so'rovida xato: %s", variant)

    if not found and errors:
        logger.warning("Fatvo qidiruvi natijasiz. Xatolar: %s", errors)

    return list(found.values())


async def search_fatvo_candidates(query, limit=5):
    try:
        items = await fetch_fatvo(query)
    except Exception:
        logger.exception("Fatvo qidiruvida kutilmagan xato")
        return []

    candidates = []
    seen = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        status = str(item.get("status") or "").lower()
        scope = str(item.get("scope") or "").lower()

        if status and status not in ("answered", "published"):
            continue

        if scope and scope not in ("public",):
            continue

        question = get_uz(item.get("question")).strip()
        title = get_uz(item.get("title")).strip()
        answer = get_uz(item.get("answer")).strip()
        item_id = str(item.get("id") or "").strip()

        if not item_id or not answer or not (question or title):
            continue

        if item_id in seen:
            continue

        seen.add(item_id)

        candidates.append({
            "id": item_id,
            "title": title or question,
            "question": question or title,
            "answer": answer,
            "source_name": "Fatvo.uz — Fatvo markazi",
            "source_url": f"https://fatvo.uz/uz/answers/{item_id}",
            "score": candidate_score(query, item),
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[:limit]


async def answer_from_fatvo(message: Message, query):
    if not message.from_user:
        return

    uid = message.from_user.id

    await message.answer("🔍 Fatvo.uz manbasidan qidiryapman...")

    results = await search_fatvo_candidates(query, limit=5)

    if results:
        fatvo_choices[uid] = results

        await message.answer(
            "📚 <b>FATVO.UZ — TOPILGAN MAVZULAR</b>\n\n"
            "Quyidagi mavzulardan savolingizga mosini tanlang. "
            "Tanlaganingizdan keyin manbadagi javob va havola ko‘rsatiladi.",
            reply_markup=fatvo_results_keyboard(results),
        )
        return

    fatvo_choices.pop(uid, None)

    await submit_question(message, query)


@dp.callback_query(F.data.startswith("fatvo:"))
async def choose_fatvo_result(callback: CallbackQuery):
    uid = callback.from_user.id
    results = fatvo_choices.get(uid, [])

    try:
        index = int(callback.data.split(":")[1])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Variant noto‘g‘ri.", show_alert=True)
        return

    if index < 0 or index >= len(results):
        await callback.answer(
            "Natijalar eskirgan. Qaytadan qidiring.",
            show_alert=True,
        )
        return

    result = results[index]
    await callback.answer()

    answer_text = result["answer"]

    # Telegram xabari uzunligi cheklangan. Uzun javobni qismlarga ajratamiz.
    header = (
        "📚 <b>FATVO.UZ MANBASIDAN JAVOB</b>\n\n"
        f"❓ <b>Mavzu:</b> {html_escape(result['question'])}\n\n"
        "✅ <b>Manbadagi javob:</b>\n"
    )

    footer = (
        f"\n\n📖 <b>Manba:</b> {html_escape(result['source_name'])}\n"
        f"🔗 {html_escape(result['source_url'])}\n\n"
        "Eslatma: javob manbada e'lon qilingan matn asosida berildi. "
        "Shaxsiy yoki murakkab holatlarda malakali Hanafiy olimga murojaat qiling."
    )

    full_text = header + html_escape(answer_text) + footer

    if len(full_text) <= 4000:
        await callback.message.answer(
            full_text,
            reply_markup=main_menu(uid in ADMIN_IDS),
            disable_web_page_preview=True,
        )
    else:
        await callback.message.answer(
            header + html_escape(answer_text[:3000]),
            disable_web_page_preview=True,
        )
        await callback.message.answer(
            html_escape(answer_text[3000:6000]) + footer,
            reply_markup=main_menu(uid in ADMIN_IDS),
            disable_web_page_preview=True,
        )


# =========================================================
# 6. ADMIN PANELI VA SAVOLNI TEKSHIRISH
# =========================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


async def send_pending_questions(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, user_id, question, created_at
            FROM questions
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 10
        """)
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("📭 Tekshirilmagan savollar yo‘q.")
        return

    for row in rows:
        await message.answer(
            "📥 <b>Tekshiruvdagi savol</b>\n\n"
            f"🆔 ID: <code>{row['id']}</code>\n"
            f"👤 Foydalanuvchi: <code>{row['user_id']}</code>\n"
            f"❓ {html_escape(row['question'])}",
            reply_markup=pending_keyboard(row["id"]),
        )


@dp.callback_query(F.data == "admin:pending")
async def admin_pending_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await callback.answer()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, user_id, question
            FROM questions
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 10
        """)
        rows = await cursor.fetchall()

    if not rows:
        await callback.message.answer("📭 Tekshirilmagan savollar yo‘q.")
        return

    for row in rows:
        await callback.message.answer(
            "📥 <b>Tekshiruvdagi savol</b>\n\n"
            f"🆔 ID: <code>{row['id']}</code>\n"
            f"👤 Foydalanuvchi: <code>{row['user_id']}</code>\n"
            f"❓ {html_escape(row['question'])}",
            reply_markup=pending_keyboard(row["id"]),
        )


@dp.callback_query(F.data.startswith("review:answer:"))
async def admin_answer_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        question_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol ID noto‘g‘ri.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM questions WHERE id = ? AND status = 'pending'",
            (question_id,),
        )
        row = await cursor.fetchone()

    if not row:
        await callback.answer("Savol topilmadi yoki oldin ko‘rilgan.", show_alert=True)
        return

    admin_states[callback.from_user.id] = {
        "mode": "answer_question",
        "question_id": question_id,
        "user_id": row["user_id"],
        "question": row["question"],
    }

    await callback.answer()
    await callback.message.answer(
        f"✍️ <b>Javob yozish</b>\n\n"
        f"❓ Savol: {html_escape(row['question'])}\n\n"
        "Endi javob matnini yuboring.\n"
        "Keyingi xabarda manba nomi va havolasini ham so‘rayman.\n"
        "Bekor qilish uchun /cancel yuboring."
    )


@dp.callback_query(F.data.startswith("review:reject:"))
async def admin_reject_question(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        question_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol ID noto‘g‘ri.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            UPDATE questions
            SET status = 'rejected', admin_id = ?
            WHERE id = ? AND status = 'pending'
        """, (callback.from_user.id, question_id))
        await db.commit()

        if cursor.rowcount:
            cursor = await db.execute(
                "SELECT user_id FROM questions WHERE id = ?",
                (question_id,),
            )
            row = await cursor.fetchone()
        else:
            row = None

    await callback.answer()

    if not row:
        await callback.message.answer("Savol topilmadi yoki oldin ko‘rilgan.")
        return

    try:
        await bot.send_message(
            row[0],
            "ℹ️ Savolingiz hozircha javoblar bazasiga kiritilmadi. "
            "Savolni aniqroq yozib, qayta yuborishingiz mumkin.",
        )
    except TelegramAPIError:
        logger.exception("Rad javobini foydalanuvchiga yuborib bo'lmadi")

    await callback.message.answer(f"❌ {question_id}-savol rad etildi.")


@dp.callback_query(F.data == "admin:stats")
async def admin_stats_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM questions")
        questions = (await cursor.fetchone())[0]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE status = 'pending'"
        )
        pending = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT COUNT(*) FROM verified_answers")
        answers = (await cursor.fetchone())[0]

    await callback.answer()
    await callback.message.answer(
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"❓ Jami savollar: {questions}\n"
        f"⏳ Tekshiruvda: {pending}\n"
        f"✅ Tasdiqlangan javoblar: {answers}"
    )


@dp.callback_query(F.data == "admin:home")
async def admin_home_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        "🏠 Asosiy menyu",
        reply_markup=main_menu(True),
    )


# =========================================================
# 7. BUYRUQLAR
# =========================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    await register_user(message.from_user)

    await message.answer(
        "🕌 <b>Hanafiy Fiqhi Ensiklopediyasi</b>\n\n"
        "Assalomu alaykum!\n"
        "Bu bot fiqhga oid savollarni manbalar asosida izlashga yordam beradi.\n\n"
        "🔎 Javob izlash — manbalardan qidirish.\n"
        "❓ Savol yuborish — javobi topilmagan savolni tekshiruvga yuborish.\n"
        "📚 Manbalar — foydalaniladigan saytlar.\n\n"
        "Eslatma: bot natijalari malakali olim maslahatining o‘rnini bosmaydi.",
        reply_markup=main_menu(is_admin(message.from_user.id)),
    )


@dp.message(Command("cancel"))
async def cancel_handler(message: Message):
    uid = message.from_user.id
    user_states.pop(uid, None)
    admin_states.pop(uid, None)

    await message.answer(
        "Amal bekor qilindi.",
        reply_markup=main_menu(is_admin(uid)),
    )


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu buyruq faqat adminlar uchun.")
        return

    await message.answer(
        "🛠 <b>Admin panel</b>",
        reply_markup=admin_menu(),
    )


# =========================================================
# 8. ADMIN JAVOBINI QABUL QILISH
# =========================================================

async def handle_admin_state(message: Message):
    uid = message.from_user.id
    state = admin_states.get(uid)

    if not state:
        return False

    text = (message.text or "").strip()

    if not text:
        await message.answer("Iltimos, matn yuboring.")
        return True

    if state["mode"] == "answer_question":
        state["answer"] = text
        state["mode"] = "answer_source_name"

        await message.answer(
            "📖 Javob olingan manbaning nomini yuboring.\n"
            "Masalan: Fatvo.uz yoki Muslim.uz.\n"
            "Agar manba hali aniqlanmagan bo‘lsa, 'Ko‘rsatilmagan' deb yozing."
        )
        return True

    if state["mode"] == "answer_source_name":
        state["source_name"] = text
        state["mode"] = "answer_source_url"

        await message.answer(
            "🔗 Endi manba havolasini yuboring.\n"
            "Agar havola bo‘lmasa, 'yo‘q' deb yozing."
        )
        return True

    if state["mode"] == "answer_source_url":
        source_url = "" if text.lower() in ("yo'q", "yo‘q", "yoq", "none") else text

        if source_url and not source_url.startswith(("https://", "http://")):
            await message.answer(
                "Havola http:// yoki https:// bilan boshlanishi kerak. "
                "Qayta yuboring yoki 'yo‘q' deb yozing."
            )
            return True

        question_id = state["question_id"]
        question = state["question"]
        answer = state["answer"]
        source_name = state["source_name"]
        user_id = state["user_id"]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO verified_answers(
                    question, answer, source_name, source_url, created_at, admin_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                question,
                answer,
                source_name,
                source_url,
                int(time.time()),
                uid,
            ))

            await db.execute("""
                UPDATE questions
                SET status = 'answered',
                    admin_id = ?,
                    admin_answer = ?,
                    source_name = ?,
                    source_url = ?
                WHERE id = ?
            """, (uid, answer, source_name, source_url, question_id))

            await db.commit()

        admin_states.pop(uid, None)

        await message.answer(
            "✅ Javob saqlandi va tasdiqlangan javoblar bazasiga qo‘shildi.",
            reply_markup=main_menu(True),
        )

        try:
            reply = (
                "📚 <b>Savolingizga admin javobi</b>\n\n"
                f"❓ <b>Savol:</b> {html_escape(question)}\n\n"
                f"✅ <b>Javob:</b>\n{html_escape(answer)}\n\n"
                f"📖 <b>Manba:</b> {html_escape(source_name)}"
            )

            if source_url:
                reply += f"\n🔗 {html_escape(source_url)}"

            await bot.send_message(
                user_id,
                reply,
                disable_web_page_preview=True,
            )
        except TelegramAPIError:
            logger.exception("Tasdiqlangan javob foydalanuvchiga yuborilmadi")

        return True

    return False


# =========================================================
# 9. ASOSIY MATN HANDLERI
# =========================================================

@dp.message(F.text)
async def text_handler(message: Message):
    uid = message.from_user.id
    text = message.text.strip()

    await register_user(message.from_user)

    if not text:
        return

    # Admin javob yozayotgan bo'lsa, avval admin holatini tekshiramiz.
    if is_admin(uid):
        handled = await handle_admin_state(message)

        if handled:
            return

    # Asosiy menyu.
    if text in ("🏠 Bosh menyu", "🔙 Menyu", "Menyu"):
        user_states.pop(uid, None)
        await message.answer(
            "🏠 Asosiy menyu",
            reply_markup=main_menu(is_admin(uid)),
        )
        return

    if text == "🛠 Admin panel":
        if not is_admin(uid):
            await message.answer("⛔ Ruxsat yo‘q.")
            return

        await message.answer(
            "🛠 <b>Admin panel</b>",
            reply_markup=admin_menu(),
        )
        return

    if text == "🔎 Javob izlash":
        user_states[uid] = "search"
        await message.answer(
            "🔎 Qaysi fiqh savoliga javob izlaymiz?\n"
            "Savolingizni yuboring. Masalan: Tahoratning farzlari nechta?"
        )
        return

    if text == "❓ Savol yuborish":
        user_states[uid] = "question"
        await message.answer(
            "❓ Savolingizni to‘liq yozib yuboring.\n"
            "Mos javob topilmasa, savol admin tekshiruviga yuboriladi."
        )
        return

    if text == "📚 Manbalar":
        await message.answer(
            "📚 <b>Foydalaniladigan manbalar</b>\n\n"
            f"• <a href=\"{FATVO_HOME}\">Fatvo.uz — Fatvo markazi</a>\n"
            f"• <a href=\"{MUSLIM_HOME}\">Muslim.uz — O‘zbekiston musulmonlari idorasi</a>\n\n"
            "Bot qidiruv natijalarini manba bilan ko‘rsatishga harakat qiladi. "
            "Muslim.uz havolasi manbalar ro‘yxati sifatida berilgan; "
            "bu kod uning barcha maqolalarini avtomatik qidirmaydi.",
            disable_web_page_preview=True,
        )
        return

    if text == "👤 Profilim":
        sent, answered = await get_profile(uid)

        await message.answer(
            "👤 <b>Profilingiz</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📨 Yuborilgan savollar: {sent}\n"
            f"✅ Javob berilgan savollar: {answered}",
            reply_markup=main_menu(is_admin(uid)),
        )
        return

    if text == "ℹ️ Yordam":
        await message.answer(
            "ℹ️ <b>Yordam</b>\n\n"
            "1. «Javob izlash»ni bosing.\n"
            "2. Savolingizni yozing.\n"
            "3. Natijalar chiqsa, mos mavzuni tugmadan tanlang.\n"
            "4. Javob topilmasa, savolingiz admin tekshiruviga yuboriladi.\n\n"
            "Bekor qilish: /cancel",
            reply_markup=main_menu(is_admin(uid)),
        )
        return

    if text == "📖 Fiqh haqida":
        await message.answer(
            "📖 <b>Fiqh haqida</b>\n\n"
            "Fiqh — Islom amaliy hukmlarini o‘rganuvchi ilm. "
            "Mazhabga oid savollarda ishonchli manbalarni tekshirish muhim.\n\n"
            "Ushbu botdagi avtomatik qidiruv natijasi xato yoki to‘liq bo‘lmasligi mumkin. "
            "Murakkab masalada malakali olimga murojaat qiling.",
            reply_markup=main_menu(is_admin(uid)),
        )
        return

    # Agar foydalanuvchi qidiruv yoki savol rejimida bo'lsa.
    mode = user_states.get(uid)

    if mode == "search":
        user_states.pop(uid, None)

        result = await search_answer(text)

        if result:
            await log_activity(uid, "verified_answer_found")

            await message.answer(
                "🔎 <b>ADMIN TASDIQLAGAN JAVOB</b>\n\n"
                f"❓ <b>Savol:</b> {html_escape(result['question'])}\n\n"
                f"✅ <b>Javob:</b>\n{html_escape(result['answer'])}\n\n"
                f"📖 <b>Manba:</b> {html_escape(result['source_name'] or 'Ko‘rsatilmagan')}\n"
                f"🔗 {html_escape(result['source_url'] or 'Havola ko‘rsatilmagan')}",
                reply_markup=main_menu(is_admin(uid)),
                disable_web_page_preview=True,
            )
            return

        await answer_from_fatvo(message, text)
        return

    if mode == "question":
        user_states.pop(uid, None)
        await answer_from_fatvo(message, text)
        return

    # Oddiy matn yuborilsa ham qidiruv sifatida qabul qilamiz.
    await answer_from_fatvo(message, text)


# =========================================================
# 10. XATOLARNI USHLASH VA BOTNI ISHGA TUSHIRISH
# =========================================================

async def main():
    global http_session

    await init_db()

    connector = aiohttp.TCPConnector(
        limit=20,
        enable_cleanup_closed=True,
    )

    http_session = aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "HanafiyFiqhiBot/1.0"},
    )

    try:
        # Bir xil botning eski polling jarayoni qolib ketmaganiga ishonch hosil qiling.
        await bot.delete_webhook(drop_pending_updates=False)

        logger.info("Bot ishga tushmoqda...")

        while True:
            try:
                await dp.start_polling(
                    bot,
                    allowed_updates=dp.resolve_used_update_types(),
                )
                break

            except TelegramNetworkError:
                logger.exception("Telegram tarmoq xatosi. 5 soniyada qayta uriniladi.")
                await asyncio.sleep(5)

            except TelegramAPIError:
                logger.exception("Telegram API xatosi. 5 soniyada qayta uriniladi.")
                await asyncio.sleep(5)

    finally:
        if http_session and not http_session.closed:
            await http_session.close()

        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
    except Exception:
        logger.exception("Bot ishga tushishida jiddiy xato")
        raise
