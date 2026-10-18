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
