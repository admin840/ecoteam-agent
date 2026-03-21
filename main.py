import os
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])

# Group IDs - add more groups here as needed
GROUP_IDS = [
    int(g.strip())
    for g in os.environ.get("GROUP_IDS", "-4757552003").split(",")
]

# Track which groups sent screenshots today: {group_id: True}
screenshots_today: dict[int, bool] = {}

# Messages
MORNING_MSG = (
    "صباح الخير! 🌅\n"
    "تذكير: الرجاء إرسال screenshots اليوم.\n"
    "شكراً لتعاونكم 🙏"
)

AFTERNOON_MSG = (
    "مساء الخير! 🌇\n"
    "تذكير: لو ما أرسلتوا screenshots اليوم، الرجاء إرسالها الحين.\n"
    "شكراً 🙏"
)

FOLLOWUP_30_MSG = (
    "⏰ تذكير ثاني:\n"
    "لسه ما وصلنا screenshots منكم.\n"
    "الرجاء إرسالها في أقرب وقت."
)


def is_owner(update: Update) -> bool:
    return update.effective_user.id == OWNER_CHAT_ID


def reset_daily_tracking():
    screenshots_today.clear()


async def send_to_all_groups(context: ContextTypes.DEFAULT_TYPE, text: str):
    for gid in GROUP_IDS:
        try:
            await context.bot.send_message(chat_id=gid, text=text)
        except Exception as e:
            logger.error("Failed to send to group %s: %s", gid, e)


async def check_30min(context: ContextTypes.DEFAULT_TYPE):
    """30 min after reminder: send follow-up to groups that haven't sent."""
    for gid in GROUP_IDS:
        if gid not in screenshots_today:
            try:
                await context.bot.send_message(chat_id=gid, text=FOLLOWUP_30_MSG)
            except Exception as e:
                logger.error("Failed to send 30min followup to %s: %s", gid, e)


async def check_1hr(context: ContextTypes.DEFAULT_TYPE):
    """1 hour: notify owner about missing groups."""
    missing = [gid for gid in GROUP_IDS if gid not in screenshots_today]
    if missing:
        text = (
            "⚠️ تنبيه: الجروبات التالية لم ترسل screenshots بعد ساعة:\n"
            + "\n".join(f"• Group: {gid}" for gid in missing)
        )
        try:
            await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=text)
        except Exception as e:
            logger.error("Failed to notify owner (1hr): %s", e)


async def check_1hr30(context: ContextTypes.DEFAULT_TYPE):
    """1.5 hours: second alert to owner with group names."""
    missing = [gid for gid in GROUP_IDS if gid not in screenshots_today]
    if missing:
        text = (
            "🚨 تنبيه أخير: الجروبات التالية لم ترسل screenshots بعد ساعة ونص:\n"
            + "\n".join(f"• Group: {gid}" for gid in missing)
        )
        try:
            await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=text)
        except Exception as e:
            logger.error("Failed to notify owner (1.5hr): %s", e)


def schedule_followups(context: ContextTypes.DEFAULT_TYPE):
    """Schedule the 30min, 1hr, 1.5hr follow-up checks."""
    now = datetime.now()
    context.job_queue.run_once(check_30min, when=timedelta(minutes=30), name="followup_30")
    context.job_queue.run_once(check_1hr, when=timedelta(hours=1), name="followup_60")
    context.job_queue.run_once(check_1hr30, when=timedelta(minutes=90), name="followup_90")
    logger.info("Follow-up checks scheduled at +30m, +1h, +1.5h from %s", now)


# --- Command Handlers (owner only for control commands) ---

async def morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    reset_daily_tracking()
    await send_to_all_groups(context, MORNING_MSG)
    schedule_followups(context)
    await update.message.reply_text("✅ تم إرسال تذكير الصبح لكل الجروبات.")


async def afternoon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await send_to_all_groups(context, AFTERNOON_MSG)
    schedule_followups(context)
    await update.message.reply_text("✅ تم إرسال تذكير العصر لكل الجروبات.")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if not context.args:
        await update.message.reply_text("استخدم: /broadcast [رسالة]")
        return
    msg = " ".join(context.args)
    await send_to_all_groups(context, msg)
    await update.message.reply_text(f"✅ تم إرسال الرسالة لكل الجروبات.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    sent = [gid for gid in GROUP_IDS if gid in screenshots_today]
    not_sent = [gid for gid in GROUP_IDS if gid not in screenshots_today]

    lines = [f"📊 حالة Screenshots - {today}\n"]
    if sent:
        lines.append("✅ أرسلوا:")
        for gid in sent:
            lines.append(f"  • Group: {gid}")
    if not_sent:
        lines.append("\n❌ لسه ما أرسلوا:")
        for gid in not_sent:
            lines.append(f"  • Group: {gid}")

    lines.append(f"\n📈 {len(sent)}/{len(GROUP_IDS)} جروبات أرسلت")
    await update.message.reply_text("\n".join(lines))


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً! أنا EcoTeam Agent Bot 🤖\n"
        "أنا جاهز أساعدك في إدارة الفرق."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "الأوامر المتاحة:\n"
        "/start - تشغيل البوت\n"
        "/help - عرض المساعدة\n"
        "/morning - تذكير الصبح\n"
        "/afternoon - تذكير العصر\n"
        "/broadcast [رسالة] - إرسال رسالة لكل الجروبات\n"
        "/status - حالة Screenshots اليوم"
    )


# --- Message Handler: detect screenshots ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track screenshots from groups."""
    if update.effective_chat.id in GROUP_IDS:
        # Check for photos (screenshots)
        if update.message and update.message.photo:
            screenshots_today[update.effective_chat.id] = True
            logger.info("Screenshot received from group %s", update.effective_chat.id)


async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle forwarded messages in private chat - reply with chat ID info."""
    if update.effective_chat.type != "private":
        return
    msg = update.message
    forward_chat = msg.forward_origin
    if forward_chat:
        # Try to get the original chat info
        if hasattr(forward_chat, "sender_chat") and forward_chat.sender_chat:
            fwd_info = f"{forward_chat.sender_chat.title} (ID: {forward_chat.sender_chat.id})"
        elif hasattr(forward_chat, "chat") and forward_chat.chat:
            fwd_info = f"{forward_chat.chat.title} (ID: {forward_chat.chat.id})"
        else:
            fwd_info = str(forward_chat)
        await msg.reply_text(
            f"Chat ID: {update.effective_chat.id}\n"
            f"Forward from: {fwd_info}"
        )
    else:
        await msg.reply_text(f"Chat ID: {update.effective_chat.id}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track photo messages as screenshots."""
    if update.effective_chat.id in GROUP_IDS:
        screenshots_today[update.effective_chat.id] = True
        logger.info("Screenshot received from group %s", update.effective_chat.id)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("morning", morning_cmd))
    app.add_handler(CommandHandler("afternoon", afternoon_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    # Handle forwarded messages in private chat (to get chat IDs)
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded))

    # Track photos as screenshots
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started! Owner: %s, Groups: %s", OWNER_CHAT_ID, GROUP_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
