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

# ── Feedback system ──────────────────────────────────────────────────
# Store last bot analysis per group so we know what to correct
_last_bot_analysis: dict[int, str] = {}  # gid -> last analysis text


def feedback_keyboard() -> InlineKeyboardMarkup:
    """Inline buttons for every bot analysis."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ صح كده", callback_data="fb_correct"),
            InlineKeyboardButton("❌ مش كده", callback_data="fb_wrong"),
            InlineKeyboardButton("💬 تعليق الأدمن", callback_data="fb_edit"),
        ]
    ])

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
            from analyzer import _safe_num as _sn
            spend = _sn(sheet_data.get("Spend اليوم", 0)) or 0
            orders = _sn(sheet_data.get("Orders اليوم", 0)) or 0
            cpo = sheet_data.get("CPO اليوم", "-")
            action = sheet_data.get("\U0001f6a6 اليوم", "")
            if spend or orders:
                sheet_line = f"\n     💰{spend:,.0f} | 📦{int(orders)} | CPO:{cpo} {action}"

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
    from analyzer import reset_conversation_memory
    reset_conversation_memory()
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
    today = now_egypt().strftime("%d/%m")
    t = now_egypt().strftime("%I:%M%p")

    m_done = 0
    a_done = 0
    done_list = []
    partial_list = []
    missing_list = []

    for gid, name in TEAMS.items():
        leader = get_leader(name)
        m = morning_photos.get(gid, 0)
        a = afternoon_photos.get(gid, 0)
        paused = " ⏸" if gid in paused_teams else ""

        if m >= MORNING_REQUIRED:
            m_done += 1
        if a >= AFTERNOON_REQUIRED:
            a_done += 1

        if m >= MORNING_REQUIRED and a >= AFTERNOON_REQUIRED:
            done_list.append(f"  ✅ {name}{paused}")
        elif m > 0 or a > 0:
            partial_list.append(f"  🟡 {name} - صبح {m}/{MORNING_REQUIRED} عصر {a}/{AFTERNOON_REQUIRED}{paused}")
        else:
            missing_list.append(f"  ❌ {name}{paused}")

    lines = [f"📊 حالة الفرق  {today} {t}\n"]

    if done_list:
        lines.append("مكتمل:")
        lines.extend(done_list)
        lines.append("")
    if partial_list:
        lines.append("ناقص:")
        lines.extend(partial_list)
        lines.append("")
    if missing_list:
        lines.append("لم يبدأ:")
        lines.extend(missing_list)
        lines.append("")

    lines.append(f"الصبح {m_done}/{len(TEAMS)} | العصر {a_done}/{len(TEAMS)}")

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

    from analyzer import _safe_num
    spend = _safe_num(today_data.get("Spend اليوم", 0)) or 0
    orders = _safe_num(today_data.get("Orders اليوم", 0)) or 0
    cpo = today_data.get("CPO اليوم", "-")
    action_today = today_data.get("\U0001f6a6 اليوم", "")
    spend_y = today_data.get("Spend أمس", "-")
    delivered = _safe_num(today_data.get("Delivered", 0)) or 0
    cancel = _safe_num(today_data.get("Cancel", 0)) or 0
    hold = _safe_num(today_data.get("Hold", 0)) or 0
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
def alert_teams_keyboard(selected: set) -> InlineKeyboardMarkup:
    """Build multi-select keyboard for /alert."""
    buttons = []
    buttons.append([InlineKeyboardButton("📢 كل الفرق", callback_data="alert_all")])
    for gid, name in TEAMS.items():
        check = "✅" if gid in selected else "⬜"
        buttons.append([InlineKeyboardButton(f"{check} {name}", callback_data=f"alert_toggle_{gid}")])
    if selected:
        buttons.append([InlineKeyboardButton(f"➡️ متابعة ({len(selected)} فريق)", callback_data="alert_done")])
    return InlineKeyboardMarkup(buttons)


async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return ConversationHandler.END
    context.user_data["alert_selected"] = set()
    await update.message.reply_text(
        "اختار الفرق (اضغط على أكتر من فريق):",
        reply_markup=alert_teams_keyboard(set()),
    )
    return ALERT_PICK_TEAM


async def alert_pick_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "alert_all":
        context.user_data["alert_selected"] = set(TEAMS.keys())
        names = "كل الفرق"
        await query.edit_message_text(f"📢 {names}\n\n✏️ اكتب الرسالة:")
        return ALERT_TYPE_MSG

    if data == "alert_done":
        selected = context.user_data.get("alert_selected", set())
        if not selected:
            await query.answer("اختار فريق واحد على الأقل")
            return ALERT_PICK_TEAM
        names = ", ".join(team_name(g) for g in selected)
        await query.edit_message_text(f"📤 الفرق: {names}\n\n✏️ اكتب الرسالة:")
        return ALERT_TYPE_MSG

    if data.startswith("alert_toggle_"):
        gid = int(data.replace("alert_toggle_", ""))
        selected = context.user_data.get("alert_selected", set())
        if gid in selected:
            selected.discard(gid)
        else:
            selected.add(gid)
        context.user_data["alert_selected"] = selected
        await query.edit_message_reply_markup(reply_markup=alert_teams_keyboard(selected))
        return ALERT_PICK_TEAM


async def alert_type_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alert_msg"] = update.message.text
    selected = context.user_data.get("alert_selected", set())
    names = ", ".join(team_name(g) for g in selected)
    await update.message.reply_text(
        f"📤 إرسال لـ {len(selected)} فريق:\n{names}\n\n{update.message.text}\n\nتأكيد؟",
        reply_markup=confirm_keyboard(),
    )
    return ALERT_CONFIRM


async def alert_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_yes":
        selected = context.user_data.get("alert_selected", set())
        msg = context.user_data["alert_msg"]
        sent = 0
        for gid in selected:
            await send_to_group(context, gid, msg)
            sent += 1
        await query.edit_message_text(f"✅ تم الإرسال لـ {sent} فريق")
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
# Photo handler: classify first, then act accordingly
# ══════════════════════════════════════════════════════════════════════
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return

    # Owner sends photo in group → bot stays silent (just observe)
    user_id = update.effective_user.id if update.effective_user else None
    if user_id == OWNER_CHAT_ID:
        return

    from analyzer import (
        analyze_screenshot, smart_analysis,
        generate_quick_summary, get_leader,
        handle_non_report_image,
        REPORT_IMAGE_TYPES,
    )

    name = team_name(gid)
    leader = get_leader(name)
    hour = now_egypt().hour

    # Download the image first
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())
    except Exception as e:
        logger.error("Photo download error (%s): %s", name, e)
        return

    # Step 1: Classify + extract (analyze_screenshot now classifies first)
    report_type = "morning" if hour < 16 else "afternoon"
    try:
        result = await analyze_screenshot(image_bytes, name, report_type)
    except Exception as e:
        logger.error("Photo analysis error (%s): %s", name, e)
        return

    img_type = result.get("image_type", "other")
    low_confidence = result.get("_low_confidence", False)

    # Step 1.5: If bot isn't sure about image type, ask!
    if low_confidence and img_type != "other":
        from analyzer import get_leader as _gl, IMAGE_TYPES
        _leader = _gl(name)
        type_label = IMAGE_TYPES.get(img_type, img_type)
        ask_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ صح كده", callback_data=f"imgtype_yes_{img_type}")],
            [InlineKeyboardButton("❌ لا، دي حاجة تانية", callback_data=f"imgtype_no_{img_type}")],
        ])
        await update.message.reply_text(
            f"🤔 يا {_leader}، أنا شايف إن الصورة دي: **{type_label}**\nصح كده؟",
            reply_markup=ask_keyboard,
            parse_mode="Markdown",
        )
        # Store image bytes for later processing after confirmation
        context.chat_data["pending_image"] = image_bytes
        context.chat_data["pending_result"] = result
        context.chat_data["pending_report_type"] = report_type
        return

    # Step 2a: If sheet read failed for order_sheet, handle specially
    if result.get("_sheet_read_failed"):
        # Try to analyze with smart_analysis using whatever data we have
        hour_now = now_egypt().hour
        if hour_now < 16:
            count = morning_photos.get(gid, 0) + 1
            morning_photos[gid] = count
        else:
            count = afternoon_photos.get(gid, 0) + 1
            afternoon_photos[gid] = count
        period = "الصبح" if hour_now < 16 else "العصر"
        required = MORNING_REQUIRED if hour_now < 16 else AFTERNOON_REQUIRED

        from analyzer import smart_analysis as _sa
        analysis = await _sa(name, result, report_type, image_bytes)
        if analysis:
            await update.message.reply_text(
                f"📸 تقرير {period}: ({count}/{required})\n\n{analysis}",
                reply_markup=feedback_keyboard(),
            )
            _last_bot_analysis[gid] = analysis
        else:
            await update.message.reply_text(f"📸 تقرير {period}: ({count}/{required})\n📋 تم استلام صورة الشيت")
        return

    # Step 2: Handle based on image type
    if img_type not in REPORT_IMAGE_TYPES:
        # NOT a report screenshot - don't count it, respond appropriately
        try:
            response = await handle_non_report_image(
                image_bytes, name, img_type, result.get("description", "")
            )
            if response:
                msg = await update.message.reply_text(
                    f"🤖 {response}",
                    reply_markup=feedback_keyboard(),
                )
                _last_bot_analysis[gid] = response
        except Exception as e:
            logger.error("Non-report image error (%s): %s", name, e)
        return  # Don't count, don't analyze further

    # Step 3: It's a report screenshot - count it
    if hour < 16:
        count = morning_photos.get(gid, 0) + 1
        morning_photos[gid] = count
        required = MORNING_REQUIRED
    else:
        count = afternoon_photos.get(gid, 0) + 1
        afternoon_photos[gid] = count
        required = AFTERNOON_REQUIRED

    period = "الصبح" if hour < 16 else "العصر"

    # Step 4: Quick summary + smart analysis (one combined message)
    if "error" not in result:
        summary = generate_quick_summary(result)

        analysis = await smart_analysis(name, result, report_type, image_bytes)
        if analysis:
            # Send count + analysis + feedback buttons
            header = f"📸 تقرير {period}: ({count}/{required})\n🤖 {summary}\n\n"
            await update.message.reply_text(
                header + analysis,
                reply_markup=feedback_keyboard(),
            )
            _last_bot_analysis[gid] = analysis
            if any(w in analysis for w in ["⚠️", "🔴", "🚨", "فرق", "مشكلة"]):
                await notify_owner(context, f"🔍 تنبيه - {name}:\n{analysis}")
        else:
            await update.message.reply_text(f"📸 تقرير {period}: ({count}/{required})\n🤖 {summary}")
    else:
        await update.message.reply_text(f"📸 تقرير {period}: ({count}/{required})")

    # Step 5: Completion message
    if count == required:
        await update.message.reply_text(f"✅ تقرير {period} مكتمل! شكراً {leader} 🎉")


# ══════════════════════════════════════════════════════════════════════
# Video handler: creative analysis
# ══════════════════════════════════════════════════════════════════════
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return
    # Owner sends video in group → bot stays silent
    user_id = update.effective_user.id if update.effective_user else None
    if user_id == OWNER_CHAT_ID:
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
            await update.message.reply_text(
                f"🤖 {analysis}",
                reply_markup=feedback_keyboard(),
            )
            _last_bot_analysis[gid] = analysis
        else:
            await update.message.reply_text("📸 ابعت screenshot من الإعلان عشان أقدر أحلله.")
    except Exception as e:
        logger.error("Video analysis error (%s): %s", name, e)
        await update.message.reply_text("⚠️ حصل مشكلة. جرب تاني.")


# ══════════════════════════════════════════════════════════════════════
# Text reply handler: interactive AI conversation
# ══════════════════════════════════════════════════════════════════════
async def handle_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI responds to replies to the bot. Owner is silent unless bot is invited."""
    gid = update.effective_chat.id
    if gid not in TEAMS:
        return
    if not update.message.text:
        return

    user_id = update.effective_user.id if update.effective_user else None
    text = update.message.text

    # ── Owner talking in group → bot watches silently ──
    if user_id == OWNER_CHAT_ID:
        from analyzer import remember_exchange
        name = team_name(gid)
        # Just observe and remember, don't reply
        remember_exchange(name, "", user_reply=f"[المالك]: {text[:200]}")
        return

    # ── "محتاج البوت" or mention → bot joins conversation ──
    bot_invited = any(w in text for w in ["محتاج البوت", "يا بوت", "EcoBot", "ecobot", "/bot"])

    # ── Normal reply to bot's message ──
    is_reply_to_bot = False
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        bot_user = await context.bot.get_me()
        is_reply_to_bot = update.message.reply_to_message.from_user.id == bot_user.id

    if not is_reply_to_bot and not bot_invited:
        return  # Not talking to the bot

    from analyzer import analyze_text_message, save_learning, get_leader
    name = team_name(gid)
    leader = get_leader(name)
    reply_to = ""
    if update.message.reply_to_message:
        reply_to = update.message.reply_to_message.text or ""

    # ── Check if this is an image type correction ──
    waiting_imgtype = context.chat_data.get("waiting_imgtype_correction", False)
    if waiting_imgtype:
        from analyzer import save_image_pattern, save_learning
        # User told us what the image really is
        save_image_pattern(text, text.strip())
        save_learning(name, "image_type", "نوع صورة غلط", f"التيم ليدر قال: {text}")
        context.chat_data["waiting_imgtype_correction"] = False
        await update.message.reply_text(
            f"✅ اتعلمت يا {leader}! المرة الجاية هعرفها لوحدي 💪"
        )
        return

    # ── Check if this is a correction after user clicked ❌ or ✏️ ──
    waiting = context.chat_data.get("waiting_correction", False)
    if waiting:
        wrong_analysis = context.chat_data.get("wrong_analysis", "")
        # Save the correction as a learning
        save_learning(name, "correction", wrong_analysis, text)
        context.chat_data["waiting_correction"] = False
        context.chat_data["wrong_analysis"] = ""
        await update.message.reply_text(
            f"✅ اتعلمت يا {leader}! شكراً جداً على التصحيح.\n"
            f"مش هكرر الغلطة دي تاني إن شاء الله 💪"
        )
        return

    try:
        response = await analyze_text_message(name, text, reply_to)
        if response:
            await update.message.reply_text(f"🤖 {response}")
    except Exception as e:
        logger.error("Text reply error (%s): %s", name, e)


# ══════════════════════════════════════════════════════════════════════
# Feedback handler: ✅ صح / ❌ غلط / ✏️ هعدل
# ══════════════════════════════════════════════════════════════════════
async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button clicks for feedback on bot analysis."""
    query = update.callback_query
    await query.answer()

    data = query.data
    gid = update.effective_chat.id
    name = team_name(gid) if gid in TEAMS else "?"

    from analyzer import get_leader, remember_exchange, save_learning
    leader = get_leader(name)

    if data == "fb_correct":
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=gid,
            text=f"✅ تمام يا {leader}! شكراً إنك أكدتلي 💪",
        )
        remember_exchange(name, f"[✅] {leader} أكد")

    elif data == "fb_wrong":
        await query.edit_message_reply_markup(reply_markup=None)
        last = _last_bot_analysis.get(gid, "")
        await context.bot.send_message(
            chat_id=gid,
            text=(
                f"😅 أنا آسف يا {leader}! لسه بتعلم والله.\n"
                f"قولي إيه اللي قولته غلط وإيه الصح؟\n"
                f"رد على الرسالة دي عشان أتعلم وأبقى أحسن المرة الجاية 🙏"
            ),
        )
        # Mark that we're waiting for correction from this group
        context.chat_data["waiting_correction"] = True
        context.chat_data["wrong_analysis"] = last[:300]
        remember_exchange(name, f"[❌ غلط] مستني تصحيح من {leader}")

    elif data == "fb_edit":
        await query.edit_message_reply_markup(reply_markup=None)
        last = _last_bot_analysis.get(gid, "")
        await context.bot.send_message(
            chat_id=gid,
            text=(
                f"🙏 شكراً يا {leader}!\n"
                f"قولي إيه المعلومة اللي محتاجة تتعدل وأنا هتعلمها.\n"
                f"رد على الرسالة دي بالتعديل ✏️"
            ),
        )
        context.chat_data["waiting_correction"] = True
        context.chat_data["wrong_analysis"] = last[:300]
        remember_exchange(name, f"[✏️ تعديل] مستني تصحيح من {leader}")


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


# ══════════════════════════════════════════════════════════════════════
# Image type confirmation handler
# ══════════════════════════════════════════════════════════════════════
async def handle_imgtype_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle image type yes/no confirmation buttons."""
    query = update.callback_query
    await query.answer()

    data = query.data
    gid = update.effective_chat.id
    name = team_name(gid) if gid in TEAMS else "?"

    from analyzer import get_leader, save_image_pattern, remember_exchange

    leader = get_leader(name)

    if data.startswith("imgtype_yes_"):
        # User confirmed image type is correct
        img_type = data.replace("imgtype_yes_", "")
        await query.edit_message_reply_markup(reply_markup=None)

        # Save pattern for future
        pending_result = context.chat_data.get("pending_result", {})
        desc = pending_result.get("description", "")
        if desc:
            save_image_pattern(desc, img_type)

        await context.bot.send_message(
            chat_id=gid,
            text=f"✅ شكراً يا {leader}! اتعلمت 💪",
        )

        # Now process the image normally
        image_bytes = context.chat_data.get("pending_image")
        if image_bytes:
            pending_result["_low_confidence"] = False
            from analyzer import (
                handle_non_report_image, smart_analysis,
                generate_quick_summary, REPORT_IMAGE_TYPES,
            )

            if img_type not in REPORT_IMAGE_TYPES:
                response = await handle_non_report_image(
                    image_bytes, name, img_type, desc
                )
                if response:
                    await context.bot.send_message(
                        chat_id=gid, text=f"🤖 {response}",
                        reply_markup=feedback_keyboard(),
                    )
                    _last_bot_analysis[gid] = response
            else:
                # Count toward report requirement
                hour_now = now_egypt().hour
                if hour_now < 16:
                    count = morning_photos.get(gid, 0) + 1
                    morning_photos[gid] = count
                    required = MORNING_REQUIRED
                else:
                    count = afternoon_photos.get(gid, 0) + 1
                    afternoon_photos[gid] = count
                    required = AFTERNOON_REQUIRED
                period = "الصبح" if hour_now < 16 else "العصر"

                result = pending_result
                if "error" not in result:
                    summary = generate_quick_summary(result)
                    report_type = context.chat_data.get("pending_report_type", "morning")
                    analysis = await smart_analysis(name, result, report_type, image_bytes)
                    if analysis:
                        leader = get_leader(name)
                        header = f"📸 تقرير {period}: ({count}/{required})\n{summary}\n\n"
                        await context.bot.send_message(
                            chat_id=gid,
                            text=header + analysis,
                            reply_markup=feedback_keyboard(),
                        )
                        _last_bot_analysis[gid] = analysis
                    if count == required:
                        leader = get_leader(name)
                        await context.bot.send_message(
                            chat_id=gid, text=f"✅ تقرير {period} مكتمل! شكراً {leader} 🎉"
                        )

        # Clean up
        context.chat_data.pop("pending_image", None)
        context.chat_data.pop("pending_result", None)
        context.chat_data.pop("pending_report_type", None)

    elif data.startswith("imgtype_no_"):
        # User says classification is wrong
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=gid,
            text=(
                f"😅 معلش يا {leader}! قولي الصورة دي إيه بالظبط؟\n"
                f"رد على الرسالة دي وقولي (مثلاً: فاتورة فيسبوك، شيت الطلبات، داشبورد تيك توك...)"
            ),
        )
        context.chat_data["waiting_imgtype_correction"] = True
        remember_exchange(name, f"[❌ نوع صورة غلط] مستني التصحيح من {leader}")


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
            ALERT_PICK_TEAM: [CallbackQueryHandler(alert_pick_team, pattern=r"^alert_")],
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

    # Feedback & image type buttons handler
    app.add_handler(CallbackQueryHandler(handle_feedback, pattern="^fb_"))
    app.add_handler(CallbackQueryHandler(handle_imgtype_callback, pattern="^imgtype_"))

    # Media & text handlers
    app.add_handler(MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, handle_forwarded))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.ANIMATION, handle_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_text))

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
