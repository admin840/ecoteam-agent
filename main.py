import os
import json
import asyncio
import logging
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
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

# ── Timezone: Egypt ──────────────────────────────────────────────────
EGYPT_TZ = ZoneInfo("Africa/Cairo")


def now_egypt() -> datetime:
    return datetime.now(EGYPT_TZ)


# ── Teams ────────────────────────────────────────────────────────────
TEAMS: dict[int, str] = {
    -4757552003: "Kuwaitmall",
    -1002683433256: "Meeven",
    -1003841167962: "Blinken",
    -4669249424: "Matajer",
    -1003637090384: "Bazar",
    -4860903521: "Minimarket",
    -1003714646416: "Khosomaat",
    -4704859302: "Trend",
    -1002691026546: "Aswaq",
    -1002658420756: "Flash",
    -1002546787010: "Deelat",
}

# ── Report requirements ──────────────────────────────────────────────
MORNING_REQUIRED = 3
AFTERNOON_REQUIRED = 3

# ── State tracking ───────────────────────────────────────────────────
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


def save_deduction(team: str, reason: str):
    data = load_deductions()
    data.append({
        "team": team, "date": now_egypt().strftime("%Y-%m-%d"),
        "reason": reason, "amount": "TBD",
    })
    DEDUCTIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Helpers ──────────────────────────────────────────────────────────
def is_owner(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == OWNER_CHAT_ID


def team_name(gid: int) -> str:
    return TEAMS.get(gid, str(gid))


def teams_keyboard(include_all: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if include_all:
        buttons.append([InlineKeyboardButton("📢 كل الفرق", callback_data="team_all")])
    for gid, name in TEAMS.items():
        buttons.append([InlineKeyboardButton(name, callback_data=f"team_{gid}")])
    return InlineKeyboardMarkup(buttons)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم", callback_data="confirm_yes"),
         InlineKeyboardButton("❌ لا", callback_data="confirm_no")]
    ])


async def send_to_group(context: ContextTypes.DEFAULT_TYPE, gid: int, text: str, parse_mode: str = None):
    try:
        await context.bot.send_message(chat_id=gid, text=text, parse_mode=parse_mode)
    except Exception as e:
        logger.error("Send to %s failed: %s", team_name(gid), e)


async def send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str):
    for gid in TEAMS:
        if gid not in paused_teams:
            await send_to_group(context, gid, text)


async def notify_owner(context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=text)
    except Exception as e:
        logger.error("Notify owner failed: %s", e)


# ══════════════════════════════════════════════════════════════════════
# Messages
# ══════════════════════════════════════════════════════════════════════
MORNING_MSG = (
    "صباح الخير! 🌅\n\n"
    "📋 مطلوب تقرير الصبح - 3 screenshots:\n"
    "1️⃣ شيت الطلبات اليومي (Google Sheets)\n"
    "2️⃣ البادجيت المصروف (Facebook/TikTok)\n"
    "3️⃣ داشبورد الإعلانات للفيسبوك والتيك توك (بعد آخر طلب طلع الصبح)\n\n"
    "شكراً لتعاونكم 🙏"
)

AFTERNOON_MSG = (
    "مساء الخير! 🌇\n\n"
    "📋 مطلوب تقرير الساعة 4 - 3 screenshots:\n"
    "1️⃣ الحساب الإعلاني (Facebook/TikTok)\n"
    "2️⃣ البادجيت المصروف لحد دلوقتي\n"
    "3️⃣ عدد الطلبات لحد الساعة 4 من الفيسبوك أو التيك توك\n\n"
    "شكراً 🙏"
)

WEEKLY_MSG = (
    "📊 مطلوب التقرير الأسبوعي:\n\n"
    "📸 Screenshots المطلوبة:\n"
    "1️⃣ شيت الإعلانات الأسبوعي\n"
    "2️⃣ شيت الطلبات\n"
    "3️⃣ إجمالي عدد الطلبات\n"
    "4️⃣ عدد الطلبات تم التسليم\n"
    "5️⃣ عدد الطلبات الكانسل\n"
    "6️⃣ البادجيت المصروف\n"
    "7️⃣ سعر الطلب\n\n"
    "شكراً 🙏"
)

SALARY_REMINDER_MSG = "📢 تذكير: غداً موعد تسليم شيت الرواتب"
SALARY_REQUEST_MSG = (
    "📋 مطلوب اليوم:\n"
    "📸 Screenshot من تاب الرواتب\n\n"
    "شكراً 🙏"
)


# ══════════════════════════════════════════════════════════════════════
# Follow-up reminders
# ══════════════════════════════════════════════════════════════════════
def _get_missing(report_type: str) -> list[int]:
    photos = morning_photos if report_type == "morning" else afternoon_photos
    required = MORNING_REQUIRED if report_type == "morning" else AFTERNOON_REQUIRED
    return [gid for gid in TEAMS if gid not in paused_teams and photos.get(gid, 0) < required]


async def followup_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    report_type = data["report_type"]
    step = data["step"]
    missing = _get_missing(report_type)
    if not missing:
        return

    type_label = "تقرير الصبح" if report_type == "morning" else "تقرير الساعة 4"
    required = MORNING_REQUIRED if report_type == "morning" else AFTERNOON_REQUIRED

    if step <= 3:
        for gid in missing:
            photos = morning_photos if report_type == "morning" else afternoon_photos
            count = photos.get(gid, 0)
            await send_to_group(
                context, gid,
                f"⏰ تذكير ({step}): {type_label} غير مكتمل ({count}/{required})\n"
                f"الرجاء إرسال الـ screenshots الناقصة."
            )
    elif step == 4:
        for gid in missing:
            photos = morning_photos if report_type == "morning" else afternoon_photos
            count = photos.get(gid, 0)
            name = team_name(gid)
            await send_to_group(context, gid,
                f"🚨 تنبيه: لم يكتمل التقرير ({count}/{required}) - سيتم خصم من الراتب")
            await notify_owner(context,
                f"🚨 {name} لم يكمل {type_label} ({count}/{required})")
            save_deduction(name, f"عدم إكمال {type_label}")
            await send_to_group(context, gid, "📝 تم تسجيل خصم بسبب عدم إرسال التقرير")


def schedule_report_followups(context_or_jq, report_type: str):
    jq = context_or_jq if hasattr(context_or_jq, 'run_once') else context_or_jq.job_queue
    for job in jq.get_jobs_by_name(f"{report_type}_followup"):
        job.schedule_removal()
    for i, mins in enumerate([15, 30, 45, 120], 1):
        jq.run_once(
            followup_reminder, when=timedelta(minutes=mins),
            name=f"{report_type}_followup",
            data={"report_type": report_type, "step": i},
        )


# ══════════════════════════════════════════════════════════════════════
# AUTO scheduled jobs (Egypt timezone)
# ══════════════════════════════════════════════════════════════════════
async def auto_morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Auto-send morning reminder at 11:00 AM Egypt time."""
    morning_photos.clear()
    await send_to_all(context, MORNING_MSG)
    schedule_report_followups(context.job_queue, "morning")
    await notify_owner(context, "✅ تم إرسال تذكير الصبح التلقائي (11:00 AM)")
    logger.info("Auto morning reminder sent")


async def auto_afternoon_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Auto-send afternoon reminder at 4:00 PM Egypt time."""
    afternoon_photos.clear()
    await send_to_all(context, AFTERNOON_MSG)
    schedule_report_followups(context.job_queue, "afternoon")
    await notify_owner(context, "✅ تم إرسال تذكير العصر التلقائي (4:00 PM)")
    logger.info("Auto afternoon reminder sent")


async def daily_noon_report(context: ContextTypes.DEFAULT_TYPE):
    """12:00 PM Egypt - Send daily summary to owner."""
    from analyzer import fetch_master_data, get_team_today_data, get_leader

    today = now_egypt().strftime("%d/%m/%Y")
    t = now_egypt().strftime("%I:%M %p")

    all_data = await fetch_master_data()

    lines = [
        "╔══════════════════════════════╗",
        "║     📊  الملخص اليومي        ║",
        f"║  📅 {today}   ⏰ {t}   ║",
        "╚══════════════════════════════╝",
        "",
    ]

    # ── Morning Report ──
    lines.append("┌─── 🌅 تقرير الصبح ───┐")
    lines.append("")
    m_done = 0
    for gid, name in TEAMS.items():
        leader = get_leader(name)
        count = morning_photos.get(gid, 0)
        paused = " ⏸" if gid in paused_teams else ""

        if count >= MORNING_REQUIRED:
            icon = "✅"
            m_done += 1
        elif count > 0:
            icon = f"🟡 {count}/{MORNING_REQUIRED}"
        else:
            icon = f"❌ 0/{MORNING_REQUIRED}"

        # Sheet data
        sheet_data = get_team_today_data(all_data, name)
        sheet_line = ""
        if sheet_data:
            spend = sheet_data.get("Spend اليوم", 0)
            orders = sheet_data.get("Orders اليوم", 0)
            cpo = sheet_data.get("CPO اليوم", "-")
            action = sheet_data.get("\U0001f6a6 اليوم", "")
            if spend or orders:
                sheet_line = f"\n     💰{spend:,} | 📦{orders} | CPO:{cpo} {action}"

        lines.append(f"  {icon} {name} ({leader}){paused}{sheet_line}")

    lines.append("")

    # ── Afternoon Report ──
    lines.append("┌─── 🌇 تقرير العصر ───┐")
    lines.append("")
    a_done = 0
    for gid, name in TEAMS.items():
        leader = get_leader(name)
        count = afternoon_photos.get(gid, 0)
        paused = " ⏸" if gid in paused_teams else ""

        if count >= AFTERNOON_REQUIRED:
            icon = "✅"
            a_done += 1
        elif count > 0:
            icon = f"🟡 {count}/{AFTERNOON_REQUIRED}"
        else:
            icon = f"❌ 0/{AFTERNOON_REQUIRED}"

        lines.append(f"  {icon} {name} ({leader}){paused}")

    lines.append("")
    lines.append("┌─── 📈 الإجمالي ───┐")
    lines.append(f"  الصبح: {m_done}/{len(TEAMS)}  |  العصر: {a_done}/{len(TEAMS)}")

    # Warnings
    missing_morning = [team_name(g) for g in TEAMS if morning_photos.get(g, 0) < MORNING_REQUIRED and g not in paused_teams]
    missing_afternoon = [team_name(g) for g in TEAMS if afternoon_photos.get(g, 0) < AFTERNOON_REQUIRED and g not in paused_teams]

    if missing_morning or missing_afternoon:
        lines.append("")
        lines.append("┌─── ⚠️ تحذيرات ───┐")
        if missing_morning:
            lines.append(f"  صبح ناقص: {', '.join(missing_morning)}")
        if missing_afternoon:
            lines.append(f"  عصر ناقص: {', '.join(missing_afternoon)}")

    await notify_owner(context, "\n".join(lines))


async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    """Midnight Egypt - reset daily tracking."""
    morning_photos.clear()
    afternoon_photos.clear()
    logger.info("Daily tracking reset (Egypt midnight)")


async def check_weekly_and_salary(context: ContextTypes.DEFAULT_TYPE):
    """
    Monthly cycle: 26th to 25th
    Week 1 (day 26): weekly report
    Week 2 (day 2-3): weekly report
    Week 3 (day 9-10): weekly report
    Week 4 (day 16-17): NO weekly report
    Day 24: salary reminder
    Day 25: salary sheet request
    """
    today = now_egypt()
    day = today.day

    # Salary
    if day == 24:
        await send_to_all(context, SALARY_REMINDER_MSG)
        await notify_owner(context, "📢 تم إرسال تذكير الرواتب (يوم 24)")
        logger.info("Salary reminder sent (day 24)")
        return
    if day == 25:
        await send_to_all(context, SALARY_REQUEST_MSG)
        schedule_report_followups(context.job_queue, "salary")
        await notify_owner(context, "📋 تم إرسال طلب شيت الرواتب (يوم 25)")
        logger.info("Salary request sent (day 25)")
        return

    # Weekly reports: first 3 weeks of the cycle (26→25)
    # Week 1: day 26, Week 2: day 2-3, Week 3: day 9-10
    # Week 4 (day 16-17): skip - salary week
    weekly_days = [26, 2, 9]  # Only first 3 weeks
    if day in weekly_days:
        await send_to_all(context, WEEKLY_MSG)
        await notify_owner(context, f"📊 تم إرسال طلب التقرير الأسبوعي (يوم {day})")
        logger.info("Weekly report request sent (day %d)", day)


# ══════════════════════════════════════════════════════════════════════
# Commands
# ══════════════════════════════════════════════════════════════════════
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً! أنا EcoTeam Agent Bot 🤖\n"
        "مدير تسويق رقمي يعمل 24/7\n"
        "جاهز أساعدك في إدارة الفرق ومراجعة الأداء."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "📋 الأوامر المتاحة:\n\n"
        "⏰ تلقائي:\n"
        "• الساعة 11 صباحاً - تذكير الصبح\n"
        "• الساعة 4 عصراً - تذكير العصر\n"
        "• الساعة 12 ظهراً - ملخص يومي\n"
        "• يوم 26, 2, 9 - تقرير أسبوعي\n"
        "• يوم 24 - تذكير رواتب | يوم 25 - طلب شيت الرواتب\n\n"
        "📱 أوامر يدوية:\n"
        "/morning - تذكير الصبح\n"
        "/afternoon - تذكير العصر\n"
        "/status - حالة الفرق\n"
        "/compare - مقارنة بالأرقام الحقيقية\n"
        "/alert - تنبيه لفريق\n"
        "/broadcast - رسالة لكل الفرق\n"
        "/team - حالة فريق\n"
        "/pause - إيقاف تذكيرات\n"
        "/weekly - طلب التقرير الأسبوعي\n"
        "/report - ملخص يومي\n\n"
        "🤖 تلقائي:\n"
        "• تحليل screenshots بالذكاء الاصطناعي\n"
        "• مراجعة البادجيت ومقارنة الأرقام\n"
        "• تحليل فيديوهات وصور الإعلانات\n"
        "• رد ذكي على أسئلة التيم ليدر"
    )


async def morning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    morning_photos.clear()
    await send_to_all(context, MORNING_MSG)
    schedule_report_followups(context, "morning")
    await update.message.reply_text("✅ تم إرسال تذكير الصبح لكل الفرق.")


async def afternoon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    afternoon_photos.clear()
    await send_to_all(context, AFTERNOON_MSG)
    schedule_report_followups(context, "afternoon")
    await update.message.reply_text("✅ تم إرسال تذكير العصر لكل الفرق.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    from analyzer import get_leader
    today = now_egypt().strftime("%d/%m/%Y")
    t = now_egypt().strftime("%I:%M %p")

    lines = [
        f"📊  حالة الفرق",
        f"📅  {today}  ⏰  {t}",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "     الفريق          صبح    عصر",
        "─────────────────────────────",
    ]

    m_done = 0
    a_done = 0
    for gid, name in TEAMS.items():
        leader = get_leader(name)
        m = morning_photos.get(gid, 0)
        a = afternoon_photos.get(gid, 0)

        m_icon = "✅" if m >= MORNING_REQUIRED else f"{'🟡' if m > 0 else '❌'}{m}/{MORNING_REQUIRED}"
        a_icon = "✅" if a >= AFTERNOON_REQUIRED else f"{'🟡' if a > 0 else '❌'}{a}/{AFTERNOON_REQUIRED}"
        paused = " ⏸" if gid in paused_teams else ""

        if m >= MORNING_REQUIRED:
            m_done += 1
        if a >= AFTERNOON_REQUIRED:
            a_done += 1

        lines.append(f"  {name:<12} {m_icon:<6}  {a_icon}{paused}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📈 الصبح: {m_done}/{len(TEAMS)}  |  العصر: {a_done}/{len(TEAMS)}")

    if m_done == len(TEAMS) and a_done == len(TEAMS):
        lines.append("\n🎉 كل الفرق خلصت التقارير!")
    elif m_done == 0 and a_done == 0:
        lines.append("\n⏳ لسه مفيش حد بعت")

    await update.message.reply_text("\n".join(lines))


async def weekly_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await send_to_all(context, WEEKLY_MSG)
    await update.message.reply_text("✅ تم إرسال طلب التقرير الأسبوعي.")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await daily_noon_report(context)
    await update.message.reply_text("✅ تم إرسال الملخص اليومي.")


# ══════════════════════════════════════════════════════════════════════
# /compare - Real numbers comparison from Master Sheet
# ══════════════════════════════════════════════════════════════════════
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

    await query.edit_message_text(f"⏳ جاري تحليل بيانات {name}...")

    from analyzer import fetch_master_data, get_team_today_data, get_team_history, get_leader

    leader = get_leader(name)
    all_data = await fetch_master_data()
    today_data = get_team_today_data(all_data, name)
    history = get_team_history(all_data, name, days=5)

    if not today_data:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ مش لاقي بيانات لفريق {name} في الشيت"
        )
        return ConversationHandler.END

    spend = today_data.get("Spend اليوم", 0)
    orders = today_data.get("Orders اليوم", 0)
    cpo = today_data.get("CPO اليوم", "-")
    action_today = today_data.get("\U0001f6a6 اليوم", "")
    spend_y = today_data.get("Spend أمس", "-")
    delivered = today_data.get("Delivered", 0)
    cancel = today_data.get("Cancel", 0)
    hold = today_data.get("Hold", 0)
    cancel_pct = today_data.get("Cancel%", "0%")
    cpa = today_data.get("CPA الحقيقي", "-")
    action_yest = today_data.get("\U0001f6a6 أمس", "")

    m = morning_photos.get(gid, 0)
    a = afternoon_photos.get(gid, 0)

    lines = [
        f"╔══════════════════════════════╗",
        f"║  📊 {name} ({leader})",
        f"║  📅 {today_data.get('التاريخ', '?')}",
        f"╚══════════════════════════════╝",
        "",
        "┌─── 📈 بيانات اليوم ───┐",
        f"│  💰 Spend:    {spend:>8,} جنيه",
        f"│  📦 Orders:   {orders:>8}",
        f"│  🏷️ CPO:      {cpo:>8}",
        f"│  🚦 القرار:   {action_today}",
        "└──────────────────────────┘",
        "",
        "┌─── 📉 بيانات أمس ───┐",
        f"│  💰 Spend أمس: {spend_y:>7}",
        f"│  ✅ Delivered:  {delivered:>7}",
        f"│  ❌ Cancel:     {cancel:>7} ({cancel_pct})",
        f"│  ⏳ Hold:       {hold:>7}",
        f"│  🏷️ CPA:       {cpa:>7}",
        f"│  🚦 القرار:    {action_yest}",
        "└──────────────────────────┘",
    ]

    # Trend
    if len(history) >= 2:
        lines.append("")
        lines.append("┌─── 📊 آخر 5 أيام ───┐")
        for row in history[-5:]:
            d = row.get("التاريخ", "?")
            s = row.get("Spend اليوم", 0)
            o = row.get("Orders اليوم", 0)
            c = row.get("CPO اليوم", "-")
            act = row.get("\U0001f6a6 اليوم", "")
            s_fmt = f"{s:,}" if isinstance(s, (int, float)) and s > 0 else "-"
            lines.append(f"│ {d} │ 💰{s_fmt:>7} │ 📦{o:>3} │ CPO:{c:>4} {act}")
        lines.append("└──────────────────────────┘")

    # Screenshots
    lines.append("")
    m_icon = "✅" if m >= MORNING_REQUIRED else f"{'🟡' if m > 0 else '❌'} {m}/{MORNING_REQUIRED}"
    a_icon = "✅" if a >= AFTERNOON_REQUIRED else f"{'🟡' if a > 0 else '❌'} {a}/{AFTERNOON_REQUIRED}"
    lines.append(f"📸 صبح: {m_icon}  |  عصر: {a_icon}")

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="\n".join(lines)
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════
# /alert, /broadcast, /team, /pause (same as before)
# ══════════════════════════════════════════════════════════════════════
async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("اختار الفريق أو كل الفرق:", reply_markup=teams_keyboard(include_all=True))
    return ALERT_PICK_TEAM


async def alert_pick_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    picked = query.data.replace("team_", "")
    if picked == "all":
        context.user_data["alert_gid"] = "all"
        await query.edit_message_text("📢 كل الفرق\n\n✏️ اكتب الرسالة:")
    else:
        gid = int(picked)
        context.user_data["alert_gid"] = gid
        await query.edit_message_text(f"فريق: {team_name(gid)}\n\n✏️ اكتب الرسالة:")
    return ALERT_TYPE_MSG


async def alert_type_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alert_msg"] = update.message.text
    target = context.user_data["alert_gid"]
    label = f"كل الفرق ({len(TEAMS)})" if target == "all" else team_name(target)
    await update.message.reply_text(
        f"📤 إرسال لـ {label}:\n\n{update.message.text}\n\nتأكيد؟",
        reply_markup=confirm_keyboard(),
    )
    return ALERT_CONFIRM


async def alert_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_yes":
        target = context.user_data["alert_gid"]
        msg = context.user_data["alert_msg"]
        if target == "all":
            await send_to_all(context, msg)
            await query.edit_message_text(f"✅ تم الإرسال لكل الفرق")
        else:
            await send_to_group(context, target, msg)
            await query.edit_message_text(f"✅ تم الإرسال لـ {team_name(target)}")
    else:
        await query.edit_message_text("❌ تم الإلغاء")
    context.user_data.clear()
    return ConversationHandler.END


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("✏️ اكتب الرسالة:")
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
        await send_to_all(context, context.user_data["broadcast_msg"])
        await query.edit_message_text(f"✅ تم الإرسال")
    else:
        await query.edit_message_text("❌ تم الإلغاء")
    context.user_data.clear()
    return ConversationHandler.END


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
    from analyzer import get_leader
    leader = get_leader(name)
    m = morning_photos.get(gid, 0)
    a = afternoon_photos.get(gid, 0)
    m_status = "✅" if m >= MORNING_REQUIRED else f"⏳ {m}/{MORNING_REQUIRED}"
    a_status = "✅" if a >= AFTERNOON_REQUIRED else f"⏳ {a}/{AFTERNOON_REQUIRED}"
    paused = "\n⏸ التذكيرات متوقفة" if gid in paused_teams else ""
    await query.edit_message_text(
        f"📋 {name} ({leader})\n━━━━━━━━━━━━━\n"
        f"تقرير الصبح: {m_status}\nتقرير العصر: {a_status}{paused}"
    )
    return ConversationHandler.END


async def pause_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text("اختار الفريق:", reply_markup=teams_keyboard())
    return PAUSE_PICK


async def pause_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = int(query.data.replace("team_", ""))
    name = team_name(gid)
    if gid in paused_teams:
        paused_teams.discard(gid)
        await query.edit_message_text(f"▶️ تم تشغيل التذكيرات لـ {name}")
    else:
        paused_teams.add(gid)
        await query.edit_message_text(f"⏸ تم إيقاف التذكيرات لـ {name}")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════
# Photo handler: AI analysis
# ══════════════════════════════════════════════════════════════════════
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return

    from analyzer import (
        analyze_screenshot, smart_analysis,
        generate_quick_summary, get_leader,
    )

    name = team_name(gid)
    leader = get_leader(name)
    hour = now_egypt().hour

    # Track count and determine report type
    if hour < 16:
        count = morning_photos.get(gid, 0) + 1
        morning_photos[gid] = count
        required = MORNING_REQUIRED
        report_types = {1: "morning_sheet", 2: "morning_budget", 3: "morning_dashboard"}
        report_type = report_types.get(count, "morning_sheet")
        if count <= required:
            await update.message.reply_text(f"📸 تقرير الصبح: ({count}/{required})")
    else:
        count = afternoon_photos.get(gid, 0) + 1
        afternoon_photos[gid] = count
        required = AFTERNOON_REQUIRED
        report_type = "afternoon"
        if count <= required:
            await update.message.reply_text(f"📸 تقرير العصر: ({count}/{required})")

    # AI Analysis
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

        result = await analyze_screenshot(bytes(image_bytes), name, report_type)
        if "error" not in result:
            summary = generate_quick_summary(result)
            await update.message.reply_text(f"🤖 {summary}")

            analysis = await smart_analysis(name, result, report_type, bytes(image_bytes))
            if analysis:
                await update.message.reply_text(analysis)
                if any(w in analysis for w in ["⚠️", "🔴", "🚨", "فرق", "مشكلة"]):
                    await notify_owner(context, f"🔍 تنبيه - {name}:\n{analysis}")
    except Exception as e:
        logger.error("Photo analysis error (%s): %s", name, e)

    # Completion
    if hour < 16 and count == required:
        await update.message.reply_text(f"✅ تقرير الصبح مكتمل! شكراً {leader} 🎉")
    elif hour >= 16 and count == required:
        await update.message.reply_text(f"✅ تقرير العصر مكتمل! شكراً {leader} 🎉")


# ══════════════════════════════════════════════════════════════════════
# Video handler: creative analysis
# ══════════════════════════════════════════════════════════════════════
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return
    from analyzer import analyze_video_creative, get_leader
    name = team_name(gid)
    leader = get_leader(name)
    await update.message.reply_text(f"🎬 جاري تحليل الفيديو... استنى ثواني يا {leader}")
    try:
        video = update.message.video or update.message.animation
        if not video:
            return
        file = await context.bot.get_file(video.file_id)
        video_bytes = await file.download_as_bytearray()
        thumb_bytes = None
        if video.thumbnail:
            tf = await context.bot.get_file(video.thumbnail.file_id)
            thumb_bytes = bytes(await tf.download_as_bytearray())
        analysis = await analyze_video_creative(bytes(video_bytes), name, thumb_bytes)
        if analysis:
            await update.message.reply_text(f"🤖 {analysis}")
        else:
            await update.message.reply_text("📸 ابعت screenshot من الإعلان عشان أقدر أحلله.")
    except Exception as e:
        logger.error("Video analysis error (%s): %s", name, e)
        await update.message.reply_text("⚠️ حصل مشكلة. جرب تاني.")


# ══════════════════════════════════════════════════════════════════════
# Text reply handler: interactive AI conversation
# ══════════════════════════════════════════════════════════════════════
async def handle_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI responds intelligently to any reply to the bot in groups."""
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return
    if not update.message.reply_to_message:
        return
    if not update.message.reply_to_message.from_user:
        return
    bot_user = await context.bot.get_me()
    if update.message.reply_to_message.from_user.id != bot_user.id:
        return

    from analyzer import analyze_text_message
    name = team_name(gid)
    text = update.message.text
    reply_to = update.message.reply_to_message.text or ""

    try:
        response = await analyze_text_message(name, text, reply_to)
        if response:
            await update.message.reply_text(f"🤖 {response}")
    except Exception as e:
        logger.error("Text reply error (%s): %s", name, e)


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


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء")
    context.user_data.clear()
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════
async def post_init(application):
    await application.bot.delete_webhook(drop_pending_updates=True)
    from telegram import BotCommand
    commands = [
        BotCommand("morning", "تذكير الصبح - 3 screenshots"),
        BotCommand("afternoon", "تذكير العصر - 3 screenshots"),
        BotCommand("status", "حالة الفرق النهارده"),
        BotCommand("compare", "مقارنة بالأرقام الحقيقية"),
        BotCommand("alert", "إرسال تنبيه لفريق"),
        BotCommand("broadcast", "إرسال رسالة لكل الفرق"),
        BotCommand("team", "عرض حالة فريق"),
        BotCommand("pause", "إيقاف تذكيرات لفريق"),
        BotCommand("weekly", "طلب التقرير الأسبوعي"),
        BotCommand("report", "ملخص يومي كامل"),
        BotCommand("help", "قائمة الأوامر"),
        BotCommand("start", "تشغيل البوت"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot ready! Timezone: Egypt (Africa/Cairo)")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Conversations
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("alert", alert_start)],
        states={
            ALERT_PICK_TEAM: [CallbackQueryHandler(alert_pick_team, pattern=r"^team_")],
            ALERT_TYPE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_type_msg)],
            ALERT_CONFIRM: [CallbackQueryHandler(alert_confirm, pattern=r"^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_TYPE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_type_msg)],
            BROADCAST_CONFIRM: [CallbackQueryHandler(broadcast_confirm, pattern=r"^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("team", team_start)],
        states={TEAM_PICK: [CallbackQueryHandler(team_pick, pattern=r"^team_")]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("compare", compare_start)],
        states={COMPARE_PICK: [CallbackQueryHandler(compare_pick, pattern=r"^team_")]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("pause", pause_start)],
        states={PAUSE_PICK: [CallbackQueryHandler(pause_pick, pattern=r"^team_")]},
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    ))

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("morning", morning_cmd))
    app.add_handler(CommandHandler("afternoon", afternoon_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("weekly", weekly_cmd))
    app.add_handler(CommandHandler("report", report_cmd))

    # Media & text handlers
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.ANIMATION, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_group_text))

    # ══ Scheduled jobs (Egypt timezone) ══
    jq = app.job_queue
    egypt_tz = EGYPT_TZ

    # Auto morning reminder: 11:00 AM Egypt
    jq.run_daily(auto_morning_reminder, time=time(hour=11, minute=0, tzinfo=egypt_tz))
    # Auto afternoon reminder: 4:00 PM Egypt
    jq.run_daily(auto_afternoon_reminder, time=time(hour=16, minute=0, tzinfo=egypt_tz))
    # Daily summary: 12:00 PM Egypt
    jq.run_daily(daily_noon_report, time=time(hour=12, minute=0, tzinfo=egypt_tz))
    # Daily reset: midnight Egypt
    jq.run_daily(daily_reset, time=time(hour=0, minute=5, tzinfo=egypt_tz))
    # Weekly + salary check: 10:00 AM Egypt
    jq.run_daily(check_weekly_and_salary, time=time(hour=10, minute=0, tzinfo=egypt_tz))

    logger.info(
        "Bot started! Owner: %s, Teams: %s, TZ: Egypt",
        OWNER_CHAT_ID, list(TEAMS.values())
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
