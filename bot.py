import os
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from db import (
    get_pool,
    init_db,
    save_person,
    get_name_by_face_token,
    is_tg_id_enrolled,
    get_all_people,
    get_person_by_id,
    delete_person_by_id,
    rename_person,
    update_person_face,
)
from face_api import (
    ensure_faceset_exists,
    enroll_face,
    remove_face,
    search_face,
    extract_best_frame_as_jpeg,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Admin(s) who can run /enroll, /enrollist, /remove, and who receive
# the video whenever someone checks in.
ADMIN_IDS = [5523761749]

CONTACT_USERNAME = "@Abdulloh_xkmv"
NOT_ENROLLED_MESSAGE = f"Uzur siz ro'yxatda emassiz, {CONTACT_USERNAME} bilan bog'laning."

DEFAULT_CONFIDENCE_THRESHOLD = 75.0

executor = ThreadPoolExecutor(max_workers=2)

# Tracks admin's in-progress enrollment: {admin_tg_id: {"tg_id": int, "name": str}}
pending_enroll = {}
# Tracks admin's in-progress rename: {admin_tg_id: person_id}
pending_rename = {}
# Tracks admin's in-progress "change photo": {admin_tg_id: person_id}
pending_photo_change = {}


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ---------- basic commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salom! Video xabar (circular video) yuboring, tekshirib beraman."
    )


async def enroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text(NOT_ENROLLED_MESSAGE)
        return

    if len(context.args) < 2:
        await update.message.reply_text("Foydalanish: /enroll <tg_id> <Ism>")
        return

    tg_id_str = context.args[0]
    name = " ".join(context.args[1:]).strip()

    if not tg_id_str.isdigit():
        await update.message.reply_text("tg_id faqat raqamlardan iborat bo'lishi kerak. Masalan: /enroll 123456789 Bobur")
        return

    pending_enroll[user_id] = {"tg_id": int(tg_id_str), "name": name}
    await update.message.reply_text(f"OK. Endi {name} ning aniq rasmini yuboring.")


# ---------- /enrollist and its inline buttons ----------

def _build_list_keyboard(people):
    buttons = [
        [InlineKeyboardButton(p["name"], callback_data=f"person:{p['id']}")]
        for p in people
    ]
    return InlineKeyboardMarkup(buttons)


async def enrollist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not is_admin(user_id):
        await update.message.reply_text(NOT_ENROLLED_MESSAGE)
        return

    pool = context.bot_data["pool"]
    people = await get_all_people(pool)

    if not people:
        await update.message.reply_text("Ro'yxat bo'sh.")
        return

    await update.message.reply_text(
        "📋 Ro'yxat:", reply_markup=_build_list_keyboard(people)
    )


async def handle_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.answer(NOT_ENROLLED_MESSAGE, show_alert=True)
        return

    await query.answer()
    pool = context.bot_data["pool"]
    data = query.data

    if data == "back_to_list":
        people = await get_all_people(pool)
        if not people:
            await query.edit_message_text("Ro'yxat bo'sh.")
            return
        await query.edit_message_text("📋 Ro'yxat:", reply_markup=_build_list_keyboard(people))
        return

    if data.startswith("person:"):
        person_id = int(data.split(":", 1)[1])
        person = await get_person_by_id(pool, person_id)
        if not person:
            await query.edit_message_text("Bu odam topilmadi (o'chirilgan bo'lishi mumkin).")
            return

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Ismini o'zgartirish", callback_data=f"rename:{person_id}")],
            [InlineKeyboardButton("🖼 Rasmini o'zgartirish", callback_data=f"changephoto:{person_id}")],
            [InlineKeyboardButton("🗑 O'chirish", callback_data=f"delete:{person_id}")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_list")],
        ])
        await query.edit_message_text(
            f"👤 {person['name']}\ntg_id: {person['tg_id']}",
            reply_markup=keyboard,
        )
        return

    if data.startswith("rename:"):
        person_id = int(data.split(":", 1)[1])
        pending_rename[user_id] = person_id
        await query.edit_message_text("Yangi ismni yozib yuboring:")
        return

    if data.startswith("changephoto:"):
        person_id = int(data.split(":", 1)[1])
        pending_photo_change[user_id] = person_id
        await query.edit_message_text("Yangi rasmni yuboring:")
        return

    if data.startswith("delete:"):
        person_id = int(data.split(":", 1)[1])
        person = await get_person_by_id(pool, person_id)
        if person:
            try:
                await remove_face(person["face_token"])
            except Exception as e:
                logger.error(f"Face++ removeface error (continuing anyway): {e}")
            await delete_person_by_id(pool, person_id)
            await query.edit_message_text(f"🗑️ {person['name']} o'chirildi.")
        else:
            await query.edit_message_text("Bu odam topilmadi.")
        return


# ---------- text messages (rename flow + access gate) ----------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    pool = context.bot_data["pool"]

    if user_id in pending_rename:
        person_id = pending_rename.pop(user_id)
        await rename_person(pool, person_id, text)
        await update.message.reply_text(f"✅ Ism yangilandi: {text}")
        return

    if is_admin(user_id):
        return  # admin free-texting, nothing to do

    if not await is_tg_id_enrolled(pool, user_id):
        await update.message.reply_text(NOT_ENROLLED_MESSAGE)


# ---------- photo handling: new enrollment OR photo-change flow ----------

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    pool = context.bot_data["pool"]

    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = bytes(await photo_file.download_as_bytearray())

    if user_id in pending_photo_change:
        person_id = pending_photo_change.pop(user_id)
        try:
            person = await get_person_by_id(pool, person_id)
            new_face_token = await enroll_face(photo_bytes, person["name"] if person else "unknown")
        except Exception as e:
            logger.error(f"Face++ enroll error (change photo): {e}")
            await update.message.reply_text("⚠️ Face API bilan muammo. Qayta urinib ko'ring.")
            return
        if new_face_token is None:
            await update.message.reply_text("Rasmda yuz topilmadi. Yana bir marta yuboring.")
            return
        await update_person_face(pool, person_id, new_face_token)
        await update.message.reply_text("✅ Rasm yangilandi.")
        return

    if user_id in pending_enroll:
        info = pending_enroll.pop(user_id)
        name = info["name"]
        tg_id = info["tg_id"]

        try:
            face_token = await enroll_face(photo_bytes, name)
        except Exception as e:
            logger.error(f"Face++ enroll error: {e}")
            await update.message.reply_text("⚠️ Face API bilan muammo. Qayta urinib ko'ring.")
            return

        if face_token is None:
            await update.message.reply_text(
                "Rasmda yuz topilmadi. /enroll ni qayta yuboring va aniqroq rasm tanlang."
            )
            return

        await save_person(pool, name, face_token, tg_id)
        await update.message.reply_text(f"✅ {name} (tg_id: {tg_id}) ro'yxatga qo'shildi!")
        return

    # random photo, not part of any flow — ignore


# ---------- the actual check-in flow ----------

async def handle_video_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_tg_id = update.message.from_user.id
    pool = context.bot_data["pool"]

    if not await is_tg_id_enrolled(pool, sender_tg_id):
        await update.message.reply_text(NOT_ENROLLED_MESSAGE)
        return

    status_msg = await update.message.reply_text("🔍 Tekshirilmoqda...")

    video_note = update.message.video_note
    file = await video_note.get_file()
    path = f"/tmp/{video_note.file_unique_id}.mp4"
    await file.download_to_drive(path)

    loop = asyncio.get_running_loop()
    frame_jpeg = await loop.run_in_executor(executor, extract_best_frame_as_jpeg, path)

    try:
        os.remove(path)
    except OSError:
        pass

    if frame_jpeg is None:
        await status_msg.edit_text("❌ Videoni o'qib bo'lmadi. Qaytadan yuboring.")
        return

    try:
        candidates, thresholds = await search_face(frame_jpeg)
    except Exception as e:
        logger.error(f"Face++ search error: {e}")
        await status_msg.edit_text("⚠️ Face API bilan muammo. Qayta urinib ko'ring.")
        return

    if not candidates:
        await status_msg.edit_text("❌ Yuz aniqlanmadi. Yorug'lik/burchakni o'zgartirib qayta urinib ko'ring.")
        return

    threshold = thresholds.get("1e-5", DEFAULT_CONFIDENCE_THRESHOLD)
    logger.info(f"Face++ search candidates: {candidates}, threshold={threshold}")

    name = None
    for face_token, confidence in candidates:
        if confidence < threshold:
            break
        matched_name = await get_name_by_face_token(pool, face_token)
        if matched_name:
            name = matched_name
            break

    if not name:
        await status_msg.edit_text("❌ Yuz tanilmadi.")
        return

    await status_msg.edit_text(f"✅ Qabul qilindi, Xush kelibsiz {name}!")

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=f"✅ {name} keldi.")
            await context.bot.forward_message(
                chat_id=admin_id,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id,
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


# ---------- startup ----------

async def post_init(application):
    await ensure_faceset_exists()
    logger.info("Face++ FaceSet ready.")

    pool = await get_pool()
    await init_db(pool)
    application.bot_data["pool"] = pool
    logger.info("Database ready.")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("enroll", enroll))
    app.add_handler(CommandHandler("enrollist", enrollist))
    app.add_handler(CallbackQueryHandler(handle_list_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video_note))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    port = int(os.environ.get("PORT", "10000"))
    external_url = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
    webhook_path = BOT_TOKEN
    webhook_url = f"{external_url}/{webhook_path}"

    logger.info(f"Bot starting (webhook mode) on port {port} -> {webhook_url}")
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=webhook_path,
        webhook_url=webhook_url,
    )


if __name__ == "__main__":
    main()
