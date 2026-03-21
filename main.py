import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, time
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Environment ──────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])

# ── Teams ────────────────────────────────────────────────────────────
TEAMS: dict[int, str] = {
    -4757552003: "Kuwaitmall",
    -2683433256: "Meeven",
    -4805699706: "Blinken",
    -4669249424: "Matajer",
    -3637090384: "Bazar",
    -4860903521: "Minimarket",
    -3714646416: "Khosomaat",
    -4704859302: "Trend",
    -2691026546: "Aswaq",
    -2658420756: "Flash",
}

# ── Report requirements ──────────────────────────────────────────────
MORNING_REQUIRED = 3   # sheets, budget, dashboard
AFTERNOON_REQUIRED = 2  # ad account, budget

# ── State tracking ───────────────────────────────────────────────────
# morning_photos:   {group_id: count}
# afternoon_photos: {group_id: count}
# paused_teams:     set of group_ids
morning_photos: dict[int, int] = {}
afternoon_photos: dict[int, int] = {}
paused_teams: set[int] = set()

# Conversation states
ALERT_PICK_TEAM, ALERT_TYPE_MSG, ALERT_CONFIRM = range(3)
BROADCAST_TYPE_MSG, BROADCAST_CONFIRM = range(10, 12)
TEAM_PICK, COMPARE_PICK, PAUSE_PICK = range(20, 23)

# ── Deductions file ──────────────────────────────────────────────────
DEDUCTIONS_FILE = Path("deductions.json")


def load_deductions() -> list[dict]:
    if DEDUCTIONS_FILE.exists():
        return json.loads(DEDUCTIONS_FILE.read_text(encoding="utf-8"))
    return []


def save_deduction(team_name: str, reason: str):
    data = load_deductions()
    data.append({
        "team": team_name,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "reason": reason,
        "amount": "TBD",
    })
    DEDUCTIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Helpers ──────────────────────────────────────────────────────────
def is_owner(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == OWNER_CHAT_ID


def team_name(gid: int) -> str:
    return TEAMS.get(gid, str(gid))


def teams_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for gid, name in TEAMS.items():
        buttons.append([InlineKeyboardButton(name, callback_data=f"team_{gid}")])
    return InlineKeyboardMarkup(buttons)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم", callback_data="confirm_yes"),
         InlineKeyboardButton("❌ لا", callback_data="confirm_no")]
    ])


async def send_to_group(context: ContextTypes.DEFAULT_TYPE, gid: int, text: str):
    try:
        await context.bot.send_message(chat_id=gid, text=text)
    except Exception as e:
        logger.error("Failed to send to %s (%s): %s", team_name(gid), gid, e)


async def send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str):
    for gid in TEAMS:
        if gid not in paused_teams:
            await send_to_group(context, gid, text)


async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=text)
    except Exception as e:
        logger.error("Failed to notify owner: %s", e)


# ── Morning report follow-ups ───────────────────────────────────────
MORNING_MSG = (
    "صباح الخير! 🌅\n\n"
    "📋 مطلوب تقرير الصبح - 3 screenshots:\n"
    "1️⃣ شيت الطلبات اليومي (Google Sheets)\n"
    "2️⃣ البادجيت المصروف (Facebook/TikTok)\n"
    "3️⃣ داشبورد الرسايل\n\n"
    "شكراً لتعاونكم 🙏"
)

AFTERNOON_MSG = (
    "مساء الخير! 🌇\n\n"
    "📋 مطلوب تقرير الساعة 4 - 2 screenshots:\n"
    "1️⃣ الحساب الإعلاني (Facebook/TikTok)\n"
    "2️⃣ البادجيت المصروف لحد دلوقتي\n\n"
    "شكراً 🙏"
)

WEEKLY_MSG = (
    "📊 مطلوب التقرير الأسبوعي:\n"
    "📸 Screenshot من تاب الإعلانات الأسبوعية\n\n"
    "شكراً 🙏"
)

SALARY_REMINDER_MSG = "📢 تذكير: غداً موعد تسليم شيت الرواتب"
SALARY_REQUEST_MSG = (
    "📋 مطلوب اليوم:\n"
    "📸 Screenshot من تاب الرواتب\n\n"
    "شكراً 🙏"
)


def _get_missing(report_type: str) -> list[int]:
    """Return group IDs that haven't completed their report."""
    photos = morning_photos if report_type == "morning" else afternoon_photos
    required = MORNING_REQUIRED if report_type == "morning" else AFTERNOON_REQUIRED
    return [gid for gid in TEAMS if gid not in paused_teams and photos.get(gid, 0) < required]


async def followup_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Send follow-up reminder to groups that haven't completed their report."""
    data = context.job.data
    report_type = data["report_type"]
    step = data["step"]
    missing = _get_missing(report_type)

    if not missing:
        return

    type_label = "تقرير الصبح" if report_type == "morning" else "تقرير الساعة 4"

    if step <= 3:
        # Steps 1-3: reminders at +15, +30, +45 min
        for gid in missing:
            count = (morning_photos if report_type == "morning" else afternoon_photos).get(gid, 0)
            required = MORNING_REQUIRED if report_type == "morning" else AFTERNOON_REQUIRED
            await send_to_group(
                context, gid,
                f"⏰ تذكير ({step}): {type_label} غير مكتمل ({count}/{required})\n"
                f"الرجاء إرسال الـ screenshots الناقصة."
            )
    elif step == 4:
        # Step 4: penalty warning (1:00 PM for morning)
        for gid in missing:
            count = (morning_photos if report_type == "morning" else afternoon_photos).get(gid, 0)
            required = MORNING_REQUIRED if report_type == "morning" else AFTERNOON_REQUIRED
            name = team_name(gid)
            # Warning in group
            await send_to_group(
                context, gid,
                f"🚨 تنبيه: لم يكتمل التقرير ({count}/{required}) - سيتم خصم من الراتب"
            )
            # Notify owner
            await notify_owner(
                context,
                f"🚨 {name} لم يكمل {type_label} حتى الآن ({count}/{required})"
            )
            # Record deduction
            save_deduction(name, f"عدم إكمال {type_label}")
            # Notify group about deduction
            await send_to_group(
                context, gid,
                "📝 تم تسجيل خصم بسبب عدم إرسال التقرير"
            )


def schedule_report_followups(context: ContextTypes.DEFAULT_TYPE, report_type: str):
    """Schedule 4 follow-up checks at +15, +30, +45, +120 min."""
    jq = context.job_queue
    # Clear old jobs for this report type
    for job in jq.get_jobs_by_name(f"{report_type}_followup"):
        job.schedule_removal()

    intervals = [15, 30, 45, 120]  # minutes
    for i, mins in enumerate(intervals, 1):
        jq.run_once(
            followup_reminder,
            when=timedelta(minutes=mins),
            name=f"{report_type}_followup",
            data={"report_type": report_type, "step": i},
        )
    logger.info("Scheduled %s follow-ups at +15, +30, +45, +120 min", report_type)


# ── Scheduled daily jobs ─────────────────────────────────────────────
async def daily_noon_report(context: ContextTypes.DEFAULT_TYPE):
    """12:00 PM - Send daily summary to owner."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📊 ملخص يومي - {today}\n"]

    lines.append("═══ تقرير الصبح ═══")
    for gid, name in TEAMS.items():
        count = morning_photos.get(gid, 0)
        status = "✅" if count >= MORNING_REQUIRED else f"❌ ({count}/{MORNING_REQUIRED})"
        paused = " ⏸" if gid in paused_teams else ""
        lines.append(f"  {name}: {status}{paused}")

    lines.append("\n═══ تقرير الساعة 4 ═══")
    for gid, name in TEAMS.items():
        count = afternoon_photos.get(gid, 0)
        status = "✅" if count >= AFTERNOON_REQUIRED else f"❌ ({count}/{AFTERNOON_REQUIRED})"
        paused = " ⏸" if gid in paused_teams else ""
        lines.append(f"  {name}: {status}{paused}")

    m_done = sum(1 for gid in TEAMS if morning_photos.get(gid, 0) >= MORNING_REQUIRED)
    a_done = sum(1 for gid in TEAMS if afternoon_photos.get(gid, 0) >= AFTERNOON_REQUIRED)
    lines.append(f"\n📈 الصبح: {m_done}/{len(TEAMS)} | العصر: {a_done}/{len(TEAMS)}")

    # Warnings
    warnings = []
    for gid, name in TEAMS.items():
        if morning_photos.get(gid, 0) < MORNING_REQUIRED and gid not in paused_teams:
            warnings.append(f"⚠️ {name}: تقرير الصبح ناقص")
        if afternoon_photos.get(gid, 0) < AFTERNOON_REQUIRED and gid not in paused_teams:
            warnings.append(f"⚠️ {name}: تقرير العصر ناقص")
    if warnings:
        lines.append("\n🚨 تحذيرات:")
        lines.extend(f"  {w}" for w in warnings)

    await notify_owner(context, "\n".join(lines))


async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    """Reset tracking at midnight."""
    morning_photos.clear()
    afternoon_photos.clear()
    logger.info("Daily tracking reset")


async def check_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    """Check if today is a weekly report day (26, 2, 9, 16, 23)."""
    day = datetime.now().day
    if day in (26, 2, 9, 16, 23):
        await send_to_all(context, WEEKLY_MSG)
        logger.info("Weekly report reminder sent (day %d)", day)


async def check_salary(context: ContextTypes.DEFAULT_TYPE):
    """Check salary-related dates (24 = reminder, 25 = request)."""
    day = datetime.now().day
    if day == 24:
        await send_to_all(context, SALARY_REMINDER_MSG)
    elif day == 25:
        await send_to_all(context, SALARY_REQUEST_MSG)


# ── Command: /start ──────────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً! أنا EcoTeam Agent Bot 🤖\n"
        "أنا جاهز أساعدك في إدارة الفرق."
    )


# ── Command: /help ───────────────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "📋 الأوامر المتاحة:\n\n"
        "/morning - تذكير الصبح (3 screenshots)\n"
        "/afternoon - تذكير العصر (2 screenshots)\n"
        "/status - حالة الفرق النهارده\n"
        "/alert - إرسال تنبيه لفريق معين\n"
        "/broadcast - إرسال رسالة لكل الفرق\n"
        "/team - عرض حالة فريق\n"
        "/compare - مقارنة تقرير الصبح والعصر\n"
        "/pause - إيقاف تذكيرات لفريق\n"
        "/weekly - طلب التقرير الأسبوعي\n"
        "/report - ملخص يومي كامل\n"
        "/help - عرض المساعدة"
    )


# ── Command: /morning ────────────────────────────────────────────────
async def morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    morning_photos.clear()
    await send_to_all(context, MORNING_MSG)
    schedule_report_followups(context, "morning")
    await update.message.reply_text("✅ تم إرسال تذكير الصبح لكل الفرق.")


# ── Command: /afternoon ──────────────────────────────────────────────
async def afternoon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    afternoon_photos.clear()
    await send_to_all(context, AFTERNOON_MSG)
    schedule_report_followups(context, "afternoon")
    await update.message.reply_text("✅ تم إرسال تذكير العصر لكل الفرق.")


# ── Command: /status ─────────────────────────────────────────────────
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📊 حالة الفرق - {today}\n"]

    for gid, name in TEAMS.items():
        m = morning_photos.get(gid, 0)
        a = afternoon_photos.get(gid, 0)
        m_icon = "✅" if m >= MORNING_REQUIRED else f"⏳ {m}/{MORNING_REQUIRED}"
        a_icon = "✅" if a >= AFTERNOON_REQUIRED else f"⏳ {a}/{AFTERNOON_REQUIRED}"
        paused = " ⏸" if gid in paused_teams else ""
        lines.append(f"{name}: صبح {m_icon} | عصر {a_icon}{paused}")

    m_done = sum(1 for g in TEAMS if morning_photos.get(g, 0) >= MORNING_REQUIRED)
    a_done = sum(1 for g in TEAMS if afternoon_photos.get(g, 0) >= AFTERNOON_REQUIRED)
    lines.append(f"\n📈 الصبح: {m_done}/{len(TEAMS)} | العصر: {a_done}/{len(TEAMS)}")
    await update.message.reply_text("\n".join(lines))


# ── Command: /weekly ─────────────────────────────────────────────────
async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await send_to_all(context, WEEKLY_MSG)
    await update.message.reply_text("✅ تم إرسال طلب التقرير الأسبوعي لكل الفرق.")


# ── Command: /report ─────────────────────────────────────────────────
async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await daily_noon_report(context)
    await update.message.reply_text("✅ تم إرسال الملخص اليومي.")


# ══ Conversation: /alert ═════════════════════════════════════════════
async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("اختار الفريق:", reply_markup=teams_keyboard())
    return ALERT_PICK_TEAM


async def alert_pick_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = int(query.data.replace("team_", ""))
    context.user_data["alert_gid"] = gid
    await query.edit_message_text(f"فريق: {team_name(gid)}\n\n✏️ اكتب الرسالة:")
    return ALERT_TYPE_MSG


async def alert_type_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alert_msg"] = update.message.text
    gid = context.user_data["alert_gid"]
    await update.message.reply_text(
        f"📤 إرسال لـ {team_name(gid)}:\n\n{update.message.text}\n\nتأكيد؟",
        reply_markup=confirm_keyboard(),
    )
    return ALERT_CONFIRM


async def alert_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_yes":
        gid = context.user_data["alert_gid"]
        msg = context.user_data["alert_msg"]
        await send_to_group(context, gid, msg)
        await query.edit_message_text(f"✅ تم الإرسال لـ {team_name(gid)}")
    else:
        await query.edit_message_text("❌ تم الإلغاء")
    context.user_data.clear()
    return ConversationHandler.END


# ══ Conversation: /broadcast ═════════════════════════════════════════
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("✏️ اكتب الرسالة اللي عايز تبعتها لكل الفرق:")
    return BROADCAST_TYPE_MSG


async def broadcast_type_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["broadcast_msg"] = update.message.text
    await update.message.reply_text(
        f"📤 إرسال لـ {len(TEAMS)} جروبات:\n\n{update.message.text}\n\nتأكيد؟",
        reply_markup=confirm_keyboard(),
    )
    return BROADCAST_CONFIRM


async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_yes":
        msg = context.user_data["broadcast_msg"]
        await send_to_all(context, msg)
        await query.edit_message_text(f"✅ تم الإرسال لكل الفرق ({len(TEAMS)} جروبات)")
    else:
        await query.edit_message_text("❌ تم الإلغاء")
    context.user_data.clear()
    return ConversationHandler.END


# ══ Conversation: /team ══════════════════════════════════════════════
async def team_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("اختار الفريق:", reply_markup=teams_keyboard())
    return TEAM_PICK


async def team_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = int(query.data.replace("team_", ""))
    name = team_name(gid)
    m = morning_photos.get(gid, 0)
    a = afternoon_photos.get(gid, 0)
    m_status = "✅ مكتمل" if m >= MORNING_REQUIRED else f"⏳ {m}/{MORNING_REQUIRED}"
    a_status = "✅ مكتمل" if a >= AFTERNOON_REQUIRED else f"⏳ {a}/{AFTERNOON_REQUIRED}"
    paused = "\n⏸ التذكيرات متوقفة" if gid in paused_teams else ""

    await query.edit_message_text(
        f"📋 حالة فريق {name}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"تقرير الصبح: {m_status}\n"
        f"تقرير العصر: {a_status}{paused}"
    )
    return ConversationHandler.END


# ══ Conversation: /compare ═══════════════════════════════════════════
async def compare_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("اختار الفريق للمقارنة:", reply_markup=teams_keyboard())
    return COMPARE_PICK


async def compare_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = int(query.data.replace("team_", ""))
    name = team_name(gid)
    m = morning_photos.get(gid, 0)
    a = afternoon_photos.get(gid, 0)

    lines = [
        f"📊 مقارنة فريق {name}",
        "━━━━━━━━━━━━━━━",
        f"تقرير الصبح: {m}/{MORNING_REQUIRED} screenshots",
        f"تقرير العصر: {a}/{AFTERNOON_REQUIRED} screenshots",
        "",
    ]

    if m >= MORNING_REQUIRED and a >= AFTERNOON_REQUIRED:
        lines.append("✅ التقارير مكتملة - ماشي تمام!")
    elif m >= MORNING_REQUIRED and a < AFTERNOON_REQUIRED:
        lines.append("⚠️ تقرير الصبح مكتمل لكن تقرير العصر ناقص")
    elif m < MORNING_REQUIRED and a >= AFTERNOON_REQUIRED:
        lines.append("⚠️ تقرير العصر مكتمل لكن تقرير الصبح ناقص")
    else:
        lines.append("🚨 التقريرين ناقصين - في مشكلة!")

    await query.edit_message_text("\n".join(lines))
    return ConversationHandler.END


# ══ Conversation: /pause ═════════════════════════════════════════════
async def pause_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("اختار الفريق لإيقاف/تشغيل التذكيرات:", reply_markup=teams_keyboard())
    return PAUSE_PICK


async def pause_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = int(query.data.replace("team_", ""))
    name = team_name(gid)
    if gid in paused_teams:
        paused_teams.discard(gid)
        await query.edit_message_text(f"▶️ تم تشغيل التذكيرات لفريق {name}")
    else:
        paused_teams.add(gid)
        await query.edit_message_text(f"⏸ تم إيقاف التذكيرات لفريق {name}")
    return ConversationHandler.END


# ── Photo handler: track screenshots ─────────────────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track photos from team groups as report screenshots."""
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return

    name = team_name(gid)
    now = datetime.now()
    hour = now.hour

    # Determine which report this photo belongs to
    if hour < 16:
        # Morning report (before 4 PM)
        count = morning_photos.get(gid, 0) + 1
        morning_photos[gid] = count
        if count <= MORNING_REQUIRED:
            await update.message.reply_text(f"📸 تقرير الصبح: ({count}/{MORNING_REQUIRED})")
            if count == MORNING_REQUIRED:
                await update.message.reply_text(f"✅ تقرير الصبح مكتمل! شكراً {name} 🎉")
                logger.info("Morning report complete for %s", name)
    else:
        # Afternoon report (4 PM onwards)
        count = afternoon_photos.get(gid, 0) + 1
        afternoon_photos[gid] = count
        if count <= AFTERNOON_REQUIRED:
            await update.message.reply_text(f"📸 تقرير العصر: ({count}/{AFTERNOON_REQUIRED})")
            if count == AFTERNOON_REQUIRED:
                await update.message.reply_text(f"✅ تقرير العصر مكتمل! شكراً {name} 🎉")
                logger.info("Afternoon report complete for %s", name)


# ── Forwarded message handler ────────────────────────────────────────
async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    msg = update.message
    await msg.reply_text(
        f"Your Chat ID: {update.effective_chat.id}\n"
        f"Message Chat ID: {msg.chat.id}\n"
        f"Forward Origin: {msg.forward_origin}"
    )


# ── Fallback for conversations ───────────────────────────────────────
async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء")
    context.user_data.clear()
    return ConversationHandler.END


# ══ Main ═════════════════════════════════════════════════════════════
async def post_init(application):
    """Delete any existing webhook to avoid conflicts."""
    await application.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted, polling mode ready.")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # ── Conversation: /alert ──
    alert_conv = ConversationHandler(
        entry_points=[CommandHandler("alert", alert_start)],
        states={
            ALERT_PICK_TEAM: [CallbackQueryHandler(alert_pick_team, pattern=r"^team_")],
            ALERT_TYPE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_type_msg)],
            ALERT_CONFIRM: [CallbackQueryHandler(alert_confirm, pattern=r"^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    # ── Conversation: /broadcast ──
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_TYPE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_type_msg)],
            BROADCAST_CONFIRM: [CallbackQueryHandler(broadcast_confirm, pattern=r"^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    # ── Conversation: /team ──
    team_conv = ConversationHandler(
        entry_points=[CommandHandler("team", team_start)],
        states={TEAM_PICK: [CallbackQueryHandler(team_pick, pattern=r"^team_")]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    # ── Conversation: /compare ──
    compare_conv = ConversationHandler(
        entry_points=[CommandHandler("compare", compare_start)],
        states={COMPARE_PICK: [CallbackQueryHandler(compare_pick, pattern=r"^team_")]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    # ── Conversation: /pause ──
    pause_conv = ConversationHandler(
        entry_points=[CommandHandler("pause", pause_start)],
        states={PAUSE_PICK: [CallbackQueryHandler(pause_pick, pattern=r"^team_")]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    # Register handlers
    app.add_handler(alert_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(team_conv)
    app.add_handler(compare_conv)
    app.add_handler(pause_conv)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("morning", morning_cmd))
    app.add_handler(CommandHandler("afternoon", afternoon_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("weekly", weekly_cmd))
    app.add_handler(CommandHandler("report", report_cmd))

    # Forwarded messages (private chat)
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded))

    # Photos from groups
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # ── Scheduled jobs ──
    jq = app.job_queue
    # Daily summary at 12:00 PM
    jq.run_daily(daily_noon_report, time=time(hour=12, minute=0))
    # Daily reset at midnight
    jq.run_daily(daily_reset, time=time(hour=0, minute=0))
    # Weekly report check at 10:00 AM
    jq.run_daily(check_weekly_report, time=time(hour=10, minute=0))
    # Salary check at 10:00 AM
    jq.run_daily(check_salary, time=time(hour=10, minute=0))

    logger.info("Bot started! Owner: %s, Teams: %s", OWNER_CHAT_ID, list(TEAMS.values()))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
