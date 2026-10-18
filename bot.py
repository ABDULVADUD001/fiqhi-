import asyncio
import logging
import os
import re
import time
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from html import escape

import aiohttp
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ==========================================================
# SOZLAMALAR
# ==========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "8758611320").split(",")
    if value.strip().isdigit()
}
DB_PATH = os.getenv("DB_PATH", "/data/fiqhi.db")

FATVO_API = "https://api.fatvo.uz/v1/questions/public"
FATVO_HOME = "https://fatvo.uz/uz"
MUSLIM_HOME = "https://muslim.uz/uz"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("fiqhi_bot")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi. Railway Variables bo'limiga BOT_TOKEN kiriting."
    )

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
http_session = None
user_states = {}
admin_states = {}
fatvo_choices = {}


# ==========================================================
# MENYULAR
# ==========================================================
def main_menu(admin=False):
    rows = [
        [KeyboardButton(text="🔎 Javob izlash"),
         KeyboardButton(text="❓ Savol yuborish")],
        [KeyboardButton(text="📚 Manbalar"),
         KeyboardButton(text="👤 Profilim")],
        [KeyboardButton(text="📖 Fiqh haqida"),
         KeyboardButton(text="ℹ️ Yordam")],
    ]
    if admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Savolingizni yozing...",
    )


def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📥 Tekshirilmagan savollar", callback_data="admin:pending"
        )],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats")],
    ])


def review_keyboard(question_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Javob berish", callback_data=f"review:answer:{question_id}"
        ),
        InlineKeyboardButton(
            text="❌ Rad etish", callback_data=f"review:reject:{question_id}"
        ),
    ]])


def result_keyboard(results):
    rows = []
    for index, item in enumerate(results):
        title = re.sub(
            r"\s+", " ", item.get("title") or item.get("question") or "Mavzu"
        ).strip()
        if len(title) > 55:
            title = title[:52] + "..."
        rows.append([InlineKeyboardButton(
            text=f"📖 {title}", callback_data=f"fatvo:{index}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================================================
# MA'LUMOTLAR BAZASI / MIGRATSIYA
# ==========================================================
async def table_columns(db, table_name):
    cursor = await db.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in await cursor.fetchall()}


async def add_column_if_missing(db, table_name, column_name, definition):
    if column_name not in await table_columns(db, table_name):
        await db.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


async def init_db():
    folder = Path(DB_PATH).parent
    if str(folder) not in ("", "."):
        folder.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER UNIQUE,
                username TEXT DEFAULT '',
                full_name TEXT DEFAULT '',
                joined_at INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                question TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL DEFAULT 0,
                admin_id INTEGER,
                admin_answer TEXT,
                source_name TEXT,
                source_url TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS verified_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL DEFAULT '',
                answer TEXT NOT NULL DEFAULT '',
                source_name TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT 0,
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

        # Eski users jadvali: kerakli ustunlarni qo'shamiz.
        user_cols = await table_columns(db, "users")
        for name, definition in (
            ("user_id", "INTEGER"),
            ("username", "TEXT DEFAULT ''"),
            ("full_name", "TEXT DEFAULT ''"),
            ("joined_at", "INTEGER DEFAULT 0"),
        ):
            await add_column_if_missing(db, "users", name, definition)

        # Agar eski sxemada telegram_id bo'lsa, user_id ga ko'chiramiz.
        if "telegram_id" in user_cols:
            await db.execute("""
                UPDATE users SET user_id = telegram_id
                WHERE user_id IS NULL AND telegram_id IS NOT NULL
            """)

        # Eski users jadvalida id ustuni borligi Telegram ID degani emas.
        # Shu sababli uni avtomatik ravishda user_id ga ko'chirmaymiz.

        for name, definition in (
            ("user_id", "INTEGER"),
            ("question", "TEXT DEFAULT ''"),
            ("status", "TEXT DEFAULT 'pending'"),
            ("created_at", "INTEGER DEFAULT 0"),
            ("admin_id", "INTEGER"),
            ("admin_answer", "TEXT"),
            ("source_name", "TEXT"),
            ("source_url", "TEXT"),
        ):
            await add_column_if_missing(db, "questions", name, definition)

        for name, definition in (
            ("question", "TEXT DEFAULT ''"),
            ("answer", "TEXT DEFAULT ''"),
            ("source_name", "TEXT DEFAULT ''"),
            ("source_url", "TEXT DEFAULT ''"),
            ("created_at", "INTEGER DEFAULT 0"),
            ("admin_id", "INTEGER"),
        ):
            await add_column_if_missing(db, "verified_answers", name, definition)

        # UNIQUE indeksni yaratishdan oldin dublikatlarni bartaraf etamiz.
        # Faqat user_id ma'lum bo'lgan takroriy qatorlarni saqlab qolamiz.
        await db.execute("""
            DELETE FROM users
            WHERE user_id IS NOT NULL
              AND rowid NOT IN (
                SELECT MIN(rowid) FROM users
                WHERE user_id IS NOT NULL
                GROUP BY user_id
              )
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id
            ON users(user_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_questions_status
            ON questions(status)
        """)
        await db.commit()

    logger.info("Ma'lumotlar bazasi tayyor: %s", DB_PATH)


# ==========================================================
# FOYDALANUVCHILAR VA SAVOLLAR
# ==========================================================
async def register_user(user):
    if user is None:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM users WHERE user_id = ? LIMIT 1", (user.id,)
        )
        exists = await cursor.fetchone()
        if exists:
            await db.execute("""
                UPDATE users SET username = ?, full_name = ?
                WHERE user_id = ?
            """, (user.username or "", user.full_name or "", user.id))
        else:
            await db.execute("""
                INSERT INTO users(user_id, username, full_name, joined_at)
                VALUES (?, ?, ?, ?)
            """, (
                user.id, user.username or "", user.full_name or "", int(time.time())
            ))
        await db.commit()


async def log_activity(user_id, action):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_activity(user_id, action, created_at)
            VALUES (?, ?, ?)
        """, (user_id, action, int(time.time())))
        await db.commit()


async def submit_question(message, question):
    if not message.from_user:
        await message.answer("Foydalanuvchi ma'lumoti aniqlanmadi.")
        return None

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
        "Mos javob topilmadi. Savolingiz administrator tomonidan ko'rib chiqiladi.",
        reply_markup=main_menu(uid in ADMIN_IDS),
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    "📥 <b>Yangi savol</b>\n\n"
                    f"🆔 ID: <code>{question_id}</code>\n"
                    f"👤 Foydalanuvchi: <code>{uid}</code>\n"
                    f"❓ {escape(question)}"
                ),
                reply_markup=review_keyboard(question_id),
            )
        except TelegramAPIError:
            logger.exception("Adminga savol yuborilmadi: %s", admin_id)
    return question_id


async def get_profile(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE user_id = ?", (user_id,)
        )
        sent = (await cursor.fetchone())[0]
        cursor = await db.execute("""
            SELECT COUNT(*) FROM questions
            WHERE user_id = ? AND status = 'answered'
        """, (user_id,))
        answered = (await cursor.fetchone())[0]
    return sent, answered


# ==========================================================
# MATNNI NORMALIZATSIYA QILISH VA QIDIRUV
# ==========================================================
def normalize_text(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = text.replace("ʻ", "'").replace("’", "'").replace("ʼ", "'")
    text = text.replace("`", "'")
    text = re.sub(r"[^a-z0-9а-яёўқғҳ\s'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def words(text):
    return [
        part.strip("'-") for part in normalize_text(text).split()
        if len(part.strip("'-")) > 1
    ]


def similarity_score(first, second):
    first_norm, second_norm = normalize_text(first), normalize_text(second)
    first_words, second_words = set(words(first_norm)), set(words(second_norm))
    if not first_words or not second_words:
        return 0.0
    overlap = len(first_words & second_words) / len(first_words)
    sequence = SequenceMatcher(None, first_norm, second_norm).ratio()
    return 0.75 * overlap + 0.25 * sequence


async def search_verified_answer(query):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, question, answer, source_name, source_url
            FROM verified_answers
            WHERE question IS NOT NULL AND answer IS NOT NULL
            ORDER BY id DESC
        """)
        rows = await cursor.fetchall()

    best, best_score = None, 0.0
    for row in rows:
        score = similarity_score(query, row["question"])
        if score > best_score:
            best, best_score = dict(row), score
    return best if best and best_score >= 0.78 else None


def make_search_variants(query):
    original = str(query or "").strip()
    normalized = normalize_text(original)
    variants = []

    def add(value):
        value = re.sub(r"\s+", " ", value).strip()
        if value and value not in variants:
            variants.append(value)

    add(original)
    add(normalized)
    add(normalized.replace("'", ""))
    add(normalized.replace("q", "k"))
    add(normalized.replace("x", "h"))
    for pattern, replacement in (
        (r"\btaxorat\b", "tahorat"),
        (r"\btahoratlar\b", "tahorat"),
        (r"\bfarzlari\b", "farz"),
        (r"\bfarzlar\b", "farz"),
        (r"\bruza\b", "ro'za"),
        (r"\broza\b", "ro'za"),
        (r"\bghusl\b", "g'usl"),
        (r"\bgusl\b", "g'usl"),
    ):
        add(re.sub(pattern, replacement, normalized))
    return variants[:5]


def get_uz(value):
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


def candidate_score(query, item):
    return similarity_score(
        query, f"{get_uz(item.get('title'))} {get_uz(item.get('question'))}"
    )


# ==========================================================
# FATVO.UZ API
# ==========================================================
async def fetch_fatvo(query):
    found = {}
    if http_session is None or http_session.closed:
        logger.error("HTTP sessiya tayyor emas.")
        return []

    for variant in make_search_variants(query):
        try:
            async with http_session.get(
                FATVO_API,
                params={"page": 1, "limit": 10, "search": variant, "sort": "created"},
                timeout=aiohttp.ClientTimeout(total=18),
            ) as response:
                if response.status != 200:
                    logger.warning("Fatvo API HTTP %s; query=%s", response.status, variant)
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
                continue
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    found[str(item["id"])] = item
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.exception("Fatvo API qidiruvida xato: %s", variant)
    return list(found.values())


async def search_fatvo_candidates(query, limit=5):
    items = await fetch_fatvo(query)
    results, seen = [], set()
    for item in items:
        status = str(item.get("status") or "").lower()
        scope = str(item.get("scope") or "").lower()
        if status and status not in ("answered", "published"):
            continue
        if scope and scope != "public":
            continue

        item_id = str(item.get("id") or "").strip()
        title = get_uz(item.get("title"))
        question = get_uz(item.get("question"))
        answer = get_uz(item.get("answer"))
        if not item_id or item_id in seen or not answer.strip():
            continue
        if not (title.strip() or question.strip()):
            continue
        seen.add(item_id)
        results.append({
            "id": item_id,
            "title": title or question,
            "question": question or title,
            "answer": answer,
            "source_name": "Fatvo.uz — Fatvo markazi",
            "source_url": f"https://fatvo.uz/uz/answers/{item_id}",
            "score": candidate_score(query, item),
        })
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


async def answer_from_fatvo(message, query):
    uid = message.from_user.id
    await message.answer("🔍 Fatvo.uz manbasidan qidiryapman...")
    try:
        results = await search_fatvo_candidates(query, limit=5)
    except Exception:
        logger.exception("Fatvo qidiruvida kutilmagan xato")
        results = []

    if results:
        fatvo_choices[uid] = results
        await message.answer(
            "📚 <b>FATVO.UZ — TOPILGAN MAVZULAR</b>\n\n"
            "Savolingizga mos mavzuni tanlang:",
            reply_markup=result_keyboard(results),
        )
    else:
        fatvo_choices.pop(uid, None)
        await submit_question(message, query)


@dp.callback_query(F.data.startswith("fatvo:"))
async def choose_fatvo_result(callback: CallbackQuery):
    uid = callback.from_user.id
    results = fatvo_choices.get(uid, [])
    try:
        index = int(callback.data.split(":")[1])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Variant noto'g'ri.", show_alert=True)
        return
    if index < 0 or index >= len(results):
        await callback.answer("Natijalar eskirgan. Qaytadan qidiring.", show_alert=True)
        return

    result = results[index]
    await callback.answer()
    header = (
        "📚 <b>FATVO.UZ MANBASIDAN JAVOB</b>\n\n"
        f"❓ <b>Mavzu:</b> {escape(result['question'])}\n\n"
        "✅ <b>Manbadagi javob:</b>\n\n"
    )
    footer = (
        f"\n\n📖 <b>Manba:</b> {escape(result['source_name'])}\n"
        f"🔗 {escape(result['source_url'])}\n\n"
        "Eslatma: javob manbadagi matn asosida yuborildi. "
        "Murakkab holatlarda malakali Hanafiy olim bilan maslahatlashing."
    )
    answer = result["answer"]
    full_text = header + escape(answer) + footer
    if len(full_text) <= 4000:
        await callback.message.answer(
            full_text,
            reply_markup=main_menu(uid in ADMIN_IDS),
            disable_web_page_preview=True,
        )
        return

    chunks = [answer[i:i + 3000] for i in range(0, len(answer), 3000)]
    await callback.message.answer(header + escape(chunks[0]))
    for chunk in chunks[1:-1]:
        await callback.message.answer(escape(chunk))
    await callback.message.answer(
        escape(chunks[-1]) + footer,
        reply_markup=main_menu(uid in ADMIN_IDS),
        disable_web_page_preview=True,
    )


# ==========================================================
# ADMIN PANELI
# ==========================================================
async def show_pending(message):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, user_id, question FROM questions
            WHERE status = 'pending' ORDER BY id ASC LIMIT 10
        """)
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("📭 Tekshirilmagan savollar yo'q.")
        return
    for row in rows:
        user_label = (
            str(row["user_id"]) if row["user_id"] is not None
            else "ID topilmadi — eski savol"
        )
        await message.answer(
            "📥 <b>Tekshiruvdagi savol</b>\n\n"
            f"🆔 ID: <code>{row['id']}</code>\n"
            f"👤 Foydalanuvchi: <code>{escape(user_label)}</code>\n"
            f"❓ {escape(row['question'] or '')}",
            reply_markup=review_keyboard(row["id"]),
        )


@dp.callback_query(F.data == "admin:pending")
async def admin_pending(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    await callback.answer()
    await show_pending(callback.message)


@dp.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        users_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM questions")
        questions_count = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE status = 'pending'"
        )
        pending_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM verified_answers")
        answers_count = (await cursor.fetchone())[0]
    await callback.answer()
    await callback.message.answer(
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👥 Foydalanuvchilar: {users_count}\n"
        f"❓ Jami savollar: {questions_count}\n"
        f"⏳ Tekshiruvda: {pending_count}\n"
        f"✅ Tasdiqlangan javoblar: {answers_count}"
    )


@dp.callback_query(F.data.startswith("review:answer:"))
async def admin_start_answer(callback: CallbackQuery):
    uid = callback.from_user.id
    if uid not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    try:
        question_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol ID noto'g'ri.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, user_id, question FROM questions
            WHERE id = ? AND status = 'pending'
        """, (question_id,))
        row = await cursor.fetchone()

    if not row:
        await callback.answer("Savol topilmadi yoki oldin ko'rilgan.", show_alert=True)
        return

    # Eski bazadagi ayrim savollarda user_id bo'sh bo'lishi mumkin.
    if row["user_id"] is None:
        await callback.answer("Bu eski savolda foydalanuvchi ID si yo'q.", show_alert=True)
        await callback.message.answer(
            f"⚠️ {question_id}-savolning user_id qiymati bo'sh. "
            "Javobni yozish mumkin, lekin savol egasiga avtomatik yuborib bo'lmaydi. "
            "Yangi savollar to'g'ri saqlanadi."
        )
        return

    admin_states[uid] = {
        "mode": "answer",
        "question_id": row["id"],
        "user_id": row["user_id"],
        "question": row["question"] or "",
    }
    await callback.answer()
    await callback.message.answer(
        "✍️ <b>Javob yozish</b>\n\n"
        f"❓ {escape(row['question'] or '')}\n\n"
        "Manbaga asoslangan javob matnini yuboring.\nBekor qilish: /cancel"
    )


@dp.callback_query(F.data.startswith("review:reject:"))
async def admin_reject(callback: CallbackQuery):
    uid = callback.from_user.id
    if uid not in ADMIN_IDS:
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    try:
        question_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Savol ID noto'g'ri.", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            UPDATE questions SET status = 'rejected', admin_id = ?
            WHERE id = ? AND status = 'pending'
        """, (uid, question_id))
        changed = cursor.rowcount
        cursor = await db.execute(
            "SELECT user_id FROM questions WHERE id = ?", (question_id,)
        )
        row = await cursor.fetchone()
        await db.commit()

    await callback.answer()
    if not changed or not row:
        await callback.message.answer("Savol topilmadi yoki oldin ko'rilgan.")
        return
    if row[0] is not None:
        try:
            await bot.send_message(
                chat_id=int(row[0]),
                text="ℹ️ Savolingiz hozircha tasdiqlanmadi. "
                     "Savolni aniqroq qilib qayta yuborishingiz mumkin.",
            )
        except (TelegramAPIError, TypeError, ValueError):
            logger.exception("Foydalanuvchiga xabar yuborilmadi")
    await callback.message.answer(f"❌ {question_id}-savol rad etildi.")


async def handle_admin_state(message):
    if not message.from_user:
        return False
    uid = message.from_user.id
    state = admin_states.get(uid)
    if not state:
        return False

    text = (message.text or "").strip()
    if not text:
        await message.answer("Matn yuboring.")
        return True

    if state["mode"] == "answer":
        state["answer"] = text
        state["mode"] = "source_name"
        await message.answer(
            "📖 Javobning manba nomini yuboring.\n"
            "Masalan: Fatvo.uz yoki Muslim.uz.\n"
            "Aniq manba bo'lmasa, 'Ko'rsatilmagan' deb yozing."
        )
        return True

    if state["mode"] == "source_name":
        state["source_name"] = text
        state["mode"] = "source_url"
        await message.answer("🔗 Manba havolasini yuboring yoki 'yo'q' deb yozing.")
        return True

    if state["mode"] == "source_url":
        if text.lower() in ("yo'q", "yo‘q", "yoq", "yo'q."):
            source_url = ""
        elif text.startswith(("https://", "http://")):
            source_url = text
        else:
            await message.answer(
                "Havola http:// yoki https:// bilan boshlansin. Yoki 'yo'q' deb yozing."
            )
            return True

        question_id = state["question_id"]
        user_id = state.get("user_id")
        question = state["question"]
        answer = state["answer"]
        source_name = state["source_name"]

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO verified_answers(
                    question, answer, source_name, source_url, created_at, admin_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (question, answer, source_name, source_url, int(time.time()), uid))
            await db.execute("""
                UPDATE questions SET status = 'answered', admin_id = ?,
                    admin_answer = ?, source_name = ?, source_url = ?
                WHERE id = ?
            """, (uid, answer, source_name, source_url, question_id))
            await db.commit()

        admin_states.pop(uid, None)
        await message.answer(
            "✅ Javob saqlandi va tasdiqlangan javoblar bazasiga qo'shildi.",
            reply_markup=main_menu(True),
        )

        # ID yo'q bo'lsa, hech qachon bot.send_message(None, ...) chaqirilmaydi.
        if user_id is None:
            logger.error(
                "Javob bazaga saqlandi, lekin user_id None: question_id=%s",
                question_id,
            )
            await message.answer(
                "⚠️ Javob bazaga saqlandi, ammo bu eski savolda foydalanuvchi ID si "
                "bo'lmagani uchun savol egasiga yuborilmadi."
            )
            return True

        reply = (
            "📚 <b>Savolingizga admin javobi</b>\n\n"
            f"❓ <b>Savol:</b> {escape(question)}\n\n"
            f"✅ <b>Javob:</b>\n{escape(answer)}\n\n"
            f"📖 <b>Manba:</b> {escape(source_name)}"
        )
        if source_url:
            reply += f"\n🔗 {escape(source_url)}"

        try:
            await bot.send_message(
                chat_id=int(user_id),
                text=reply,
                disable_web_page_preview=True,
            )
        except (TelegramAPIError, TypeError, ValueError):
            logger.exception("Javob foydalanuvchiga yuborilmadi: user_id=%s", user_id)
            await message.answer(
                "⚠️ Javob bazaga saqlandi, lekin Telegram orqali foydalanuvchiga "
                "yuborilmadi. U botni avval ochmagan yoki ID noto'g'ri bo'lishi mumkin."
            )
        return True
    return False


# ==========================================================
# BOT BUYRUQLARI
# ==========================================================
@dp.message(CommandStart())
async def start_handler(message: Message):
    await register_user(message.from_user)
    await message.answer(
        "🕌 <b>Hanafiy Fiqhi Ensiklopediyasi</b>\n\n"
        "Assalomu alaykum!\n"
        "Fiqh savollariga javobni manbalardan qidirishingiz mumkin.\n\n"
        "🔎 <b>Javob izlash</b> — manbadan qidirish.\n"
        "❓ <b>Savol yuborish</b> — savolni tekshiruvga yuborish.\n"
        "📚 <b>Manbalar</b> — foydalaniladigan saytlar.\n\n"
        "Eslatma: avtomatik natijalar xato yoki to'liq bo'lmasligi mumkin. "
        "Murakkab masalada malakali olimga murojaat qiling.",
        reply_markup=main_menu(message.from_user.id in ADMIN_IDS),
    )


@dp.message(Command("cancel"))
async def cancel_handler(message: Message):
    uid = message.from_user.id
    user_states.pop(uid, None)
    admin_states.pop(uid, None)
    await message.answer("Amal bekor qilindi.", reply_markup=main_menu(uid in ADMIN_IDS))


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Bu buyruq faqat adminlar uchun.")
        return
    await message.answer("🛠 <b>Admin panel</b>", reply_markup=admin_menu())


# ==========================================================
# ASOSIY MATN HANDLERI
# ==========================================================
@dp.message(F.text)
async def text_handler(message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    text = message.text.strip()
    if not text:
        return

    await register_user(message.from_user)
    if uid in ADMIN_IDS and await handle_admin_state(message):
        return

    if text == "🔎 Javob izlash":
        user_states[uid] = "search"
        await message.answer("🔎 Fiqh savolingizni yozing.\nMasalan: Tahoratning farzlari nechta?")
        return
    if text == "❓ Savol yuborish":
        user_states[uid] = "question"
        await message.answer("❓ Savolingizni to'liq yozib yuboring.")
        return
    if text == "🛠 Admin panel":
        if uid not in ADMIN_IDS:
            await message.answer("⛔ Ruxsat yo'q.")
            return
        await message.answer("🛠 <b>Admin panel</b>", reply_markup=admin_menu())
        return
    if text == "📚 Manbalar":
        await message.answer(
            "📚 <b>Manbalar</b>\n\n"
            f'• <a href="{FATVO_HOME}">Fatvo.uz — Fatvo markazi</a>\n'
            f'• <a href="{MUSLIM_HOME}">Muslim.uz — O‘zbekiston musulmonlari idorasi</a>\n\n'
            "Avtomatik qidiruv hozir Fatvo.uz API'sidan foydalanadi. "
            "Muslim.uz alohida avtomatik qidirilmaydi.",
            disable_web_page_preview=True,
        )
        return
    if text == "👤 Profilim":
        sent, answered = await get_profile(uid)
        await message.answer(
            "👤 <b>Profil</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📨 Yuborilgan savollar: {sent}\n"
            f"✅ Javob berilgan savollar: {answered}",
            reply_markup=main_menu(uid in ADMIN_IDS),
        )
        return
    if text == "📖 Fiqh haqida":
        await message.answer(
            "📖 <b>Fiqh haqida</b>\n\n"
            "Fiqh Islomdagi amaliy hukmlarni o'rganuvchi ilm. "
            "Hanafiy mazhabiga oid savollarda ishonchli manbalarni tekshirish "
            "va zarur bo'lsa malakali olim bilan maslahatlashish muhim.",
            reply_markup=main_menu(uid in ADMIN_IDS),
        )
        return
    if text == "ℹ️ Yordam":
        await message.answer(
            "ℹ️ <b>Yordam</b>\n\n"
            "1. «Javob izlash»ni bosing.\n"
            "2. Savolingizni yozing.\n"
            "3. Natijalar chiqsa, mos mavzuni tanlang.\n"
            "4. Javob topilmasa, savolingiz admin tekshiruviga yuboriladi.\n\n"
            "Bekor qilish: /cancel",
            reply_markup=main_menu(uid in ADMIN_IDS),
        )
        return

    mode = user_states.pop(uid, None)
    if mode == "search":
        verified = await search_verified_answer(text)
        if verified:
            reply = (
                "🔎 <b>ADMIN TASDIQLAGAN JAVOB</b>\n\n"
                f"❓ <b>Savol:</b> {escape(verified['question'])}\n\n"
                f"✅ <b>Javob:</b>\n{escape(verified['answer'])}\n\n"
                f"📖 <b>Manba:</b> {escape(verified['source_name'] or 'Ko‘rsatilmagan')}"
            )
            if verified["source_url"]:
                reply += f"\n🔗 {escape(verified['source_url'])}"
            await message.answer(
                reply,
                reply_markup=main_menu(uid in ADMIN_IDS),
                disable_web_page_preview=True,
            )
            return
        await answer_from_fatvo(message, text)
        return
    if mode == "question":
        await submit_question(message, text)
        return
    await answer_from_fatvo(message, text)


# ==========================================================
# ISHGA TUSHIRISH
# ==========================================================
async def main():
    global http_session
    await init_db()
    http_session = aiohttp.ClientSession(
        headers={"User-Agent": "HanafiyFiqhiBot/1.0"},
        timeout=aiohttp.ClientTimeout(total=20),
    )
    try:
        logger.info("Bot ishga tushmoqda...")
        # Bir token uchun faqat bitta polling jarayoni ishlashi kerak.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
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

