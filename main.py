"""
EcoTeam Agent V2 - Telegram Bot
Manages 11 advertising teams. Team leaders send daily screenshots.
Bot tracks, analyzes, and reports using button-based classification.
"""
import os
import io
import json
import asyncio
import logging
import time as _time_mod
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)


async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """Edit message safely - ignore 'message not modified' errors."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            pass  # Same content, ignore
        else:
            raise

import analyzer

# ── Logging ──────────────────────────────────────────────────────────
_log_dir = os.environ.get("DATA_DIR", ".")
_file_handler = RotatingFileHandler(
    os.path.join(_log_dir, "bot.log"),
    maxBytes=5*1024*1024,  # 5MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(), _file_handler],
)
logger = logging.getLogger(__name__)

# ── Environment ──────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = 1126011968
EGYPT_TZ = ZoneInfo("Africa/Cairo")


def now_egypt() -> datetime:
    return datetime.now(EGYPT_TZ)


# ── Listening window: track when bot last sent a message in each group ─
_bot_last_msg_time: dict[int, datetime] = {}


def _record_bot_message(chat_id: int):
    """Record that the bot just sent a message in a group (not private chat)."""
    if chat_id != OWNER_CHAT_ID:
        _bot_last_msg_time[chat_id] = now_egypt()


def _is_bot_listening(chat_id: int, window_minutes: int = 3) -> bool:
    """Check if bot recently sent a message in this group (within window)."""
    last = _bot_last_msg_time.get(chat_id)
    if not last:
        return False
    return (now_egypt() - last).total_seconds() < window_minutes * 60


# ── Teams: group_chat_id → team_name ─────────────────────────────────
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

# Reverse lookup: team_name → group_id
TEAM_GIDS: dict[str, int] = {v: k for k, v in TEAMS.items()}

# Paused teams (group ids)
DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else ".")
_PAUSED_FILE = os.path.join(DATA_DIR, "paused_teams.json")


def _load_paused() -> set[int]:
    try:
        with open(_PAUSED_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_paused():
    try:
        with open(_PAUSED_FILE, "w") as f:
            json.dump(list(paused_teams), f)
    except Exception:
        pass


paused_teams: set[int] = _load_paused()

# ── Rate limiting ────────────────────────────────────────────────────
_rate_limit: dict[str, list[float]] = {}  # team -> list of timestamps


def _check_rate_limit(team_name: str, max_calls: int = 5, window_seconds: int = 300) -> bool:
    """Returns True if within rate limit, False if exceeded."""
    now = _time_mod.time()
    if team_name not in _rate_limit:
        _rate_limit[team_name] = []

    # Remove old entries
    _rate_limit[team_name] = [t for t in _rate_limit[team_name] if now - t < window_seconds]

    if len(_rate_limit[team_name]) >= max_calls:
        return False

    _rate_limit[team_name].append(now)
    return True


# ── Image type labels ────────────────────────────────────────────────
IMAGE_TYPE_LABELS = {
    "fb_pay": ("💳 دفع فيسبوك", "fb_payment", "Facebook"),
    "tt_pay": ("💳 دفع تيك توك", "tt_payment", "TikTok"),
    "fb_dash": ("📊 داشبورد فيسبوك", "fb_ads_dashboard", "Facebook"),
    "tt_dash": ("📊 داشبورد تيك توك", "tt_ads_dashboard", "TikTok"),
    "order_sheet": ("📋 شيت الطلبات", "order_sheet", ""),
    "creative": ("🎨 كريتيف/منتج", "creative_image", ""),
    "other": ("📎 صورة تانية", "other", ""),
}


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def get_team_name(chat_id: int) -> str | None:
    """Get team name from group chat id."""
    return TEAMS.get(chat_id)


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_CHAT_ID


def is_team_group(chat_id: int) -> bool:
    return chat_id in TEAMS


def _get_persistent_keyboard() -> ReplyKeyboardMarkup:
    """Return context-appropriate persistent keyboard."""
    hour = now_egypt().hour

    if hour < 12:  # Morning task time
        buttons = [[KeyboardButton("📋 تاسك الصبح"), KeyboardButton("🤖 مساعدة")]]
    elif hour < 16:  # Free time
        buttons = [[KeyboardButton("🤖 مساعدة")]]
    else:  # Afternoon task time
        buttons = [[KeyboardButton("📋 تاسك العصر"), KeyboardButton("🤖 مساعدة")]]

    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


async def send_long_message(context, chat_id: int, text: str, **kwargs):
    """Send a message, splitting if too long for Telegram's 4096 char limit."""
    if len(text) <= 4000:
        return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
    # Split on newlines
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 4000:
            parts.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        parts.append(current)
    last_msg = None
    for part in parts:
        last_msg = await context.bot.send_message(chat_id=chat_id, text=part, **kwargs)
    return last_msg


# ══════════════════════════════════════════════════════════════════════
# PHOTO HANDLER - Step 1: Ask what type
# ══════════════════════════════════════════════════════════════════════

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When a photo arrives in a team group, download it and ask type."""
    msg = update.message
    if not msg or not msg.photo:
        return
    chat_id = msg.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return
    if chat_id in paused_teams:
        return

    # Owner silence: if photo is from owner, still process (owner might test)
    user_id = msg.from_user.id if msg.from_user else 0

    # Download the photo (largest size)
    photo_file = await msg.photo[-1].get_file()
    photo_bytes_io = io.BytesIO()
    await photo_file.download_to_memory(photo_bytes_io)
    photo_bytes = photo_bytes_io.getvalue()

    # Quick check: is this work or personal?
    leader = analyzer.get_leader(team_name)
    try:
        quick_check = await analyzer.quick_image_check(photo_bytes)
        if quick_check == "PERSONAL":
            await msg.reply_text(f"😄 يا {leader}، ده مش تقرير! لو محتاج حاجة أنا موجود 🙏")
            _record_bot_message(chat_id)
            return
    except Exception as e:
        logger.warning("Quick image check failed, proceeding with buttons: %s", e)

    # Store pending photo in chat_data keyed by message id
    msg_id = msg.message_id
    if "pending_photos" not in context.chat_data:
        context.chat_data["pending_photos"] = {}
    context.chat_data["pending_photos"][str(msg_id)] = {
        "file_id": msg.photo[-1].file_id,
        "image_bytes": photo_bytes,
        "user_id": user_id,
        "timestamp": now_egypt().isoformat(),
    }

    # Send type selection buttons
    # Callback data format: itype_{msg_id}_{type}
    # Must be ≤ 64 bytes
    keyboard = [
        [
            InlineKeyboardButton("💳 دفع فيسبوك", callback_data=f"it_{msg_id}_fb_pay"),
            InlineKeyboardButton("💳 دفع تيك توك", callback_data=f"it_{msg_id}_tt_pay"),
        ],
        [
            InlineKeyboardButton("📊 داشبورد فيسبوك", callback_data=f"it_{msg_id}_fb_dash"),
            InlineKeyboardButton("📊 داشبورد تيك توك", callback_data=f"it_{msg_id}_tt_dash"),
        ],
        [
            InlineKeyboardButton("📋 شيت الطلبات", callback_data=f"it_{msg_id}_order_sheet"),
            InlineKeyboardButton("🎨 كريتيف/منتج", callback_data=f"it_{msg_id}_creative"),
        ],
        [
            InlineKeyboardButton("📎 صورة تانية", callback_data=f"it_{msg_id}_other"),
        ],
    ]
    await msg.reply_text(
        "📷 الصورة دي إيه؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    _record_bot_message(chat_id)


# ══════════════════════════════════════════════════════════════════════
# CALLBACK: Image type selected
# ══════════════════════════════════════════════════════════════════════

async def callback_image_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User clicked an image type button."""
    query = update.callback_query
    await query.answer()

    data = query.data  # it_{msg_id}_{type}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    _, msg_id_str, img_type = parts[0], parts[1], parts[2]

    chat_id = query.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    # Retrieve pending photo
    pending = context.chat_data.get("pending_photos", {}).get(msg_id_str)
    if not pending:
        await safe_edit_message(query,"⚠️ الصورة دي قديمة، ابعتها تاني.")
        return

    type_info = IMAGE_TYPE_LABELS.get(img_type)
    if not type_info:
        await safe_edit_message(query,"⚠️ نوع مش معروف.")
        return

    label, analyzer_type, platform = type_info

    # For "other" type: just acknowledge
    if img_type == "other":
        await safe_edit_message(query,f"✅ تمام، سجلت الصورة كـ {label}")
        # Log to tracking
        leader = analyzer.get_leader(team_name)
        await analyzer.log_to_tracking(
            team=team_name, leader=leader, image_type="other",
            platform="", account_num="1", amount="",
            ai_notes="صورة تانية", task_type="morning",
            message_id=msg_id_str, status="✅",
        )
        # Clean up
        context.chat_data["pending_photos"].pop(msg_id_str, None)
        return

    # For order_sheet: no account count needed, go straight to processing
    if img_type == "order_sheet":
        await safe_edit_message(query,f"⏳ جاري تحليل {label}...")
        await _process_image(context, chat_id, team_name, msg_id_str,
                             analyzer_type, platform, account_num=1, total_accounts=1)
        return

    # For creative: analyze as creative image
    if img_type == "creative":
        await safe_edit_message(query,f"⏳ جاري تحليل الكريتيف...")
        image_bytes = pending.get("image_bytes")
        if not image_bytes:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ مش لاقي الصورة.")
            return
        creative_analysis = await analyzer.analyze_image_creative(image_bytes, team_name)
        if creative_analysis:
            # Log to tracking
            leader = analyzer.get_leader(team_name)
            await analyzer.log_to_tracking(
                team=team_name, leader=leader, image_type="creative_image",
                platform="", account_num="1", amount="",
                ai_notes="تحليل كريتيف", task_type="morning",
                message_id=msg_id_str, status="✅",
            )
            await send_long_message(context, chat_id, creative_analysis)
            _record_bot_message(chat_id)
            # Interactive buttons after creative analysis
            keyboard = [
                [
                    InlineKeyboardButton("ابعت للتيم ✅", callback_data=f"cr_{msg_id_str}_send"),
                    InlineKeyboardButton("عدّل وابعت ✏️", callback_data=f"cr_{msg_id_str}_edit"),
                    InlineKeyboardButton("سيبه 🤐", callback_data=f"cr_{msg_id_str}_skip"),
                ],
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text="من ناحية الكريتيف... عايز أبعت التقييم للتيم؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            _record_bot_message(chat_id)
            analyzer.remember_exchange(team_name, creative_analysis[:300])
        else:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ مش قادر أحلل الكريتيف.")
            _record_bot_message(chat_id)
        # Clean up
        context.chat_data["pending_photos"].pop(msg_id_str, None)
        return

    # For payment/dashboard types: check if we already know the account count
    # Store selection in pending photo
    pending["img_type"] = img_type
    pending["analyzer_type"] = analyzer_type
    pending["platform"] = platform
    pending["label"] = label

    # Auto-increment: if we already know total accounts for this type+platform
    expected_key = f"{analyzer_type}_{platform}"
    expected = context.chat_data.get("expected_accounts", {}).get(expected_key)
    if expected:
        total = expected["total"]
        received = expected["received"]
        next_account = received + 1
        if next_account <= total:
            pending["total_accounts"] = total
            pending["current_account"] = next_account
            await safe_edit_message(query,
                f"⏳ جاري تحليل {label} (حساب {next_account} من {total})..."
            )
            await _process_image(context, chat_id, team_name, msg_id_str,
                                 analyzer_type, platform, next_account, total)
            return

    keyboard = [
        [
            InlineKeyboardButton("1", callback_data=f"ac_{msg_id_str}_1"),
            InlineKeyboardButton("2", callback_data=f"ac_{msg_id_str}_2"),
            InlineKeyboardButton("3", callback_data=f"ac_{msg_id_str}_3"),
            InlineKeyboardButton("4", callback_data=f"ac_{msg_id_str}_4"),
            InlineKeyboardButton("5", callback_data=f"ac_{msg_id_str}_5"),
        ],
    ]
    await safe_edit_message(query,
        f"كام حساب عندك على {platform}؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ══════════════════════════════════════════════════════════════════════
# CALLBACK: Account count selected
# ══════════════════════════════════════════════════════════════════════

async def callback_account_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected account count."""
    query = update.callback_query
    await query.answer()

    data = query.data  # ac_{msg_id}_{count}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    msg_id_str = parts[1]
    count = int(parts[2])

    chat_id = query.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    pending = context.chat_data.get("pending_photos", {}).get(msg_id_str)
    if not pending:
        await safe_edit_message(query,"⚠️ الصورة دي قديمة، ابعتها تاني.")
        return

    analyzer_type = pending.get("analyzer_type", "other")
    platform = pending.get("platform", "")
    label = pending.get("label", "")

    # Store total accounts
    pending["total_accounts"] = count

    # Track which account number this is (first photo = account 1)
    current_account = pending.get("current_account", 1)

    await safe_edit_message(query,
        f"⏳ جاري تحليل {label} (حساب {current_account} من {count})..."
    )
    await _process_image(context, chat_id, team_name, msg_id_str,
                         analyzer_type, platform, current_account, count)


# ══════════════════════════════════════════════════════════════════════
# PROCESS IMAGE: Extract data, log, show confirmation
# ══════════════════════════════════════════════════════════════════════

async def _process_image(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    team_name: str,
    msg_id_str: str,
    analyzer_type: str,
    platform: str,
    account_num: int,
    total_accounts: int,
):
    """Extract data from image, log to tracking, show confirmation."""
    pending = context.chat_data.get("pending_photos", {}).get(msg_id_str)
    if not pending:
        return

    image_bytes = pending.get("image_bytes")
    if not image_bytes:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ مش لاقي الصورة.")
        return

    leader = analyzer.get_leader(team_name)

    # Rate limit check before Claude API call
    if not _check_rate_limit(team_name):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ يا {leader}، استنى شوية قبل ما تبعت تاني. البوت محتاج يلحق 😅",
        )
        return

    # Extract data using AI
    extracted = await analyzer.extract_image_data(image_bytes, analyzer_type, platform)

    # Check for type mismatch
    if extracted.get("_type_mismatch"):
        notes = extracted.get("notes", "")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ الصورة دي شكلها مش {analyzer.IMAGE_TYPES.get(analyzer_type, analyzer_type)}.\n{notes}\nلو أنا غلطان، كمل عادي.",
        )

    # Build summary
    summary = analyzer.generate_quick_summary(extracted)
    amount_str = ""
    if analyzer_type in analyzer.PAYMENT_IMAGE_TYPES:
        amt = extracted.get("amount")
        amount_str = str(amt) if amt else ""
    elif analyzer_type in analyzer.REPORT_IMAGE_TYPES:
        spend = extracted.get("spend")
        amount_str = str(spend) if spend else ""

    # Determine task_type based on time
    hour = now_egypt().hour
    task_type = "morning" if hour < 14 else "afternoon"

    # Log to tracking sheet
    ai_notes = json.dumps(
        {k: v for k, v in extracted.items() if not k.startswith("_") and v is not None},
        ensure_ascii=False,
    )[:500]

    log_result = await analyzer.log_to_tracking(
        team=team_name,
        leader=leader,
        image_type=analyzer_type,
        platform=platform,
        account_num=f"{account_num}/{total_accounts}",
        amount=amount_str,
        ai_notes=ai_notes,
        task_type=task_type,
        message_id=msg_id_str,
        status="⏳",
    )

    row_num = log_result.get("row", 0)

    # Store row num for confirmation callback
    pending["tracking_row"] = row_num
    pending["extracted"] = extracted
    pending["summary"] = summary

    # Build confirmation message + buttons
    type_label = analyzer.IMAGE_TYPES.get(analyzer_type, analyzer_type)
    conf_text = (
        f"تمام سجلت ✅\n"
        f"{type_label} - حساب {account_num} من {total_accounts}\n"
        f"{summary}\n"
        f"صح كده؟"
    )

    keyboard = [
        [
            InlineKeyboardButton("صح ✅", callback_data=f"cf_{msg_id_str}_ok"),
            InlineKeyboardButton("مش كده ❌", callback_data=f"cf_{msg_id_str}_wrong"),
            InlineKeyboardButton("تعليق 💬", callback_data=f"cf_{msg_id_str}_comment"),
        ],
    ]

    await context.bot.send_message(
        chat_id=chat_id,
        text=conf_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    _record_bot_message(chat_id)

    # ── Multi-account tracking ──
    # Count how many entries of this type+platform exist today for this team
    if total_accounts > 1:
        tracking_today = await analyzer.get_team_tracking_today(team_name, task_type)
        same_type_count = sum(
            1 for entry in tracking_today
            if entry.get("image_type") == analyzer_type
            and entry.get("platform") == platform
        )
        # Include the one we just logged
        same_type_count = max(same_type_count, account_num)

        if same_type_count < total_accounts:
            remaining = list(range(same_type_count + 1, total_accounts + 1))
            remaining_str = " و ".join(str(r) for r in remaining)
            platform_label = "فيسبوك" if "fb" in analyzer_type else "تيك توك" if "tt" in analyzer_type else platform
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"تمام! ✅ حساب {same_type_count} من {total_accounts} ({platform_label}) وصل. لسه مستني حساب {remaining_str}",
            )
            _record_bot_message(chat_id)
            # Store expected accounts info for auto-increment on next photo
            if "expected_accounts" not in context.chat_data:
                context.chat_data["expected_accounts"] = {}
            context.chat_data["expected_accounts"][f"{analyzer_type}_{platform}"] = {
                "total": total_accounts,
                "received": same_type_count,
                "img_type": pending.get("img_type", ""),
            }
        else:
            platform_label = "فيسبوك" if "fb" in analyzer_type else "تيك توك" if "tt" in analyzer_type else platform
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"ممتاز! 🎉 كل حسابات {platform_label} ({total_accounts}/{total_accounts}) وصلت!",
            )
            _record_bot_message(chat_id)
            # Clear expected accounts for this type
            if "expected_accounts" in context.chat_data:
                context.chat_data["expected_accounts"].pop(f"{analyzer_type}_{platform}", None)

    # ── Analysis after logging ──

    # For payment images, also do a smart payment analysis
    if analyzer_type in analyzer.PAYMENT_IMAGE_TYPES:
        payment_analysis = await analyzer.handle_payment_image(
            image_bytes, team_name, analyzer_type
        )
        if payment_analysis:
            await send_long_message(context, chat_id, payment_analysis)
            _record_bot_message(chat_id)
            # Interactive follow-up buttons
            keyboard = [
                [
                    InlineKeyboardButton("صح ✅", callback_data=f"ar_{msg_id_str}_ok"),
                    InlineKeyboardButton("مش كده ❌", callback_data=f"ar_{msg_id_str}_wrong"),
                    InlineKeyboardButton("تعليق 💬", callback_data=f"ar_{msg_id_str}_comment"),
                ],
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text="أنا شايف إن الأرقام كويسة... صح كده؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            _record_bot_message(chat_id)
            analyzer.remember_exchange(team_name, payment_analysis[:300])

    # For dashboard/report images, do smart analysis
    elif analyzer_type in analyzer.REPORT_IMAGE_TYPES:
        analysis = await analyzer.smart_analysis(
            team_name, extracted, analyzer_type, image_bytes
        )
        if analysis:
            await send_long_message(context, chat_id, analysis)
            _record_bot_message(chat_id)
            # Store last analysis for correction flow
            context.chat_data["last_analysis"] = analysis

            # Determine which interactive buttons to show based on analysis content
            has_problem = any(w in analysis for w in ["عالي", "مرتفع", "مشكلة", "ناقص", "وحش"])
            if has_problem:
                keyboard = [
                    [
                        InlineKeyboardButton("ابعت تنبيه للتيم", callback_data=f"ar_{msg_id_str}_alert"),
                        InlineKeyboardButton("استنى شوية", callback_data=f"ar_{msg_id_str}_wait"),
                        InlineKeyboardButton("حلل أكتر", callback_data=f"ar_{msg_id_str}_more"),
                    ],
                ]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="عايز أعمل إيه؟",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                _record_bot_message(chat_id)
            else:
                keyboard = [
                    [
                        InlineKeyboardButton("صح ✅", callback_data=f"ar_{msg_id_str}_ok"),
                        InlineKeyboardButton("مش كده ❌", callback_data=f"ar_{msg_id_str}_wrong"),
                        InlineKeyboardButton("تعليق 💬", callback_data=f"ar_{msg_id_str}_comment"),
                    ],
                ]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="صح كده؟",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                _record_bot_message(chat_id)


# ══════════════════════════════════════════════════════════════════════
# CALLBACK: Confirmation (ok / wrong / comment)
# ══════════════════════════════════════════════════════════════════════

async def callback_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed, rejected, or wants to comment on extraction."""
    query = update.callback_query
    await query.answer()

    data = query.data  # cf_{msg_id}_{action}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    msg_id_str = parts[1]
    action = parts[2]

    chat_id = query.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    pending = context.chat_data.get("pending_photos", {}).get(msg_id_str)
    row_num = pending.get("tracking_row", 0) if pending else 0

    if action == "ok":
        # Confirmed - update status
        if row_num:
            await analyzer.update_tracking_status(row_num, "✅")
        await safe_edit_message(query,query.message.text + "\n\n✅ تم التأكيد!")
        # Clean up
        if pending:
            context.chat_data["pending_photos"].pop(msg_id_str, None)

    elif action == "wrong":
        # User says extraction is wrong
        await safe_edit_message(query,
            query.message.text + "\n\n❌ إيه الغلط؟ ابعت الرقم الصح وهتعلم منك."
        )
        # Set waiting_correction state
        context.chat_data["waiting_correction"] = {
            "msg_id": msg_id_str,
            "row_num": row_num,
            "team_name": team_name,
        }

    elif action == "comment":
        # User wants to add a comment
        await safe_edit_message(query,
            query.message.text + "\n\n💬 اكتب التعليق..."
        )
        context.chat_data["waiting_comment"] = {
            "msg_id": msg_id_str,
            "row_num": row_num,
        }


# ══════════════════════════════════════════════════════════════════════
# CALLBACK: Owner deduction decision
# ══════════════════════════════════════════════════════════════════════

async def callback_owner_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner decides on a team: ok / deduct / recheck / msg."""
    query = update.callback_query
    await query.answer()

    data = query.data  # od_{team_gid}_{action}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    team_gid = int(parts[1])
    action = parts[2]
    team_name = TEAMS.get(team_gid, "")
    if not team_name:
        return
    leader = analyzer.get_leader(team_name)

    if action == "ok":
        await safe_edit_message(query,
            query.message.text + f"\n\n✅ تمام - {team_name} مفيش خصم."
        )

    elif action == "deduct":
        # Send deduction message to the team group
        deduct_msg = (
            f"⚠️ تنبيه يا {leader}:\n"
            f"في ناقص من تقرير الصبح مبعتش.\n"
            f"هيتم الخصم حسب القواعد."
        )
        try:
            await context.bot.send_message(chat_id=team_gid, text=deduct_msg)
            _record_bot_message(team_gid)
            await safe_edit_message(query,
                query.message.text + f"\n\n⚠️ تم إرسال إنذار خصم لـ {team_name}."
            )
        except Exception as e:
            logger.error("Failed to send deduction to %s: %s", team_name, e)
            await safe_edit_message(query,
                query.message.text + f"\n\n❌ فشل الإرسال: {e}"
            )

    elif action == "recheck":
        await safe_edit_message(query,
            query.message.text + "\n\n🔄 جاري المراجعة تاني..."
        )
        # Re-check and send updated report
        report = await analyzer.build_owner_team_report(team_name)
        updated_text = _format_owner_team_report(team_name, team_gid, report)
        keyboard = _build_owner_decision_keyboard(team_gid)
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"🔄 مراجعة محدثة:\n\n{updated_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif action == "msg":
        # Ask owner what message to send
        await safe_edit_message(query,
            query.message.text + "\n\n💬 اكتب الرسالة اللي عايز تبعتها..."
        )
        context.bot_data["owner_msg_target"] = team_gid


def _format_owner_team_report(team_name: str, team_gid: int, report: dict) -> str:
    """Format a team report for the owner."""
    leader = report["leader"]
    parts = [f"⚠️ {team_name} ({leader})"]

    # What they sent
    received_types = report.get("received_types", [])
    if received_types:
        type_labels = []
        for rt in received_types:
            label = analyzer.IMAGE_TYPES.get(rt, rt)
            type_labels.append(f"{label} ✅")
        parts.append(f"بعتت: {' + '.join(type_labels)}")

    # What's missing
    if not report["complete"]:
        missing_labels = [m["label"] for m in report["missing"]]
        parts.append(f"ناقص: {', '.join(missing_labels)}")
    else:
        parts.append("✅ بعتت كل المطلوب")

    # Sheet status
    sd = report.get("sheet_data", {})
    if report["sheet_status"] == "updated":
        spend = sd.get("spend", 0)
        orders = sd.get("orders", 0)
        cpo = sd.get("cpo")
        parts.append(
            f"الشيت: متحدث (Spend: {spend:,.0f} | Orders: {orders:.0f}"
            + (f" | CPO: {cpo:.0f}" if cpo else "")
            + ")"
        )
    else:
        parts.append("الشيت: مش متحدث")

    if report.get("recommendation"):
        parts.append(report["recommendation"])

    return "\n".join(parts)


def _build_owner_decision_keyboard(team_gid: int) -> list[list[InlineKeyboardButton]]:
    """Build inline keyboard for owner decision on a team."""
    return [
        [
            InlineKeyboardButton("✅ تمام", callback_data=f"od_{team_gid}_ok"),
            InlineKeyboardButton("⚠️ خصم", callback_data=f"od_{team_gid}_deduct"),
        ],
        [
            InlineKeyboardButton("🔄 راجع تاني", callback_data=f"od_{team_gid}_recheck"),
            InlineKeyboardButton("💬 رسالة", callback_data=f"od_{team_gid}_msg"),
        ],
    ]


# ══════════════════════════════════════════════════════════════════════
# TEXT HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages in team groups."""
    msg = update.message
    if not msg or not msg.text:
        return
    chat_id = msg.chat_id
    text = msg.text.strip()
    user_id = msg.from_user.id if msg.from_user else 0

    # === OWNER private chat: handle owner_msg_target ===
    if chat_id == OWNER_CHAT_ID or user_id == OWNER_CHAT_ID:
        target_gid = context.bot_data.get("owner_msg_target")
        if target_gid and chat_id == OWNER_CHAT_ID:
            # Owner is sending a message to a team
            team_name = TEAMS.get(target_gid, "")
            try:
                await context.bot.send_message(chat_id=target_gid, text=text)
                _record_bot_message(target_gid)
                await msg.reply_text(f"✅ تم إرسال الرسالة لـ {team_name}")
            except Exception as e:
                await msg.reply_text(f"❌ فشل الإرسال: {e}")
            context.bot_data.pop("owner_msg_target", None)
            return

    # === Group message handling ===
    team_name = get_team_name(chat_id)
    if not team_name:
        return
    if chat_id in paused_teams:
        return

    # Handle persistent keyboard buttons
    if text == "📋 تاسك الصبح":
        await _show_morning_checklist(update, context, team_name)
        return
    elif text == "📋 تاسك العصر":
        await _show_afternoon_checklist(update, context, team_name)
        return
    elif text == "🤖 مساعدة":
        await _show_help_menu(update, context, team_name)
        return

    # Owner silence mode in groups
    if is_owner(user_id) and not text.startswith("/"):
        # Owner sends text in group - bot stays silent
        # Unless they mention the bot or reply to bot
        if msg.reply_to_message and msg.reply_to_message.from_user:
            bot_user = await context.bot.get_me()
            if msg.reply_to_message.from_user.id != bot_user.id:
                return
        else:
            return

    # Check if waiting for correction
    correction_state = context.chat_data.get("waiting_correction")
    if correction_state:
        team = correction_state["team_name"]
        row_num = correction_state["row_num"]
        # Save the correction as a learning
        last_analysis = context.chat_data.get("last_analysis", "")
        analyzer.save_learning(team, "correction", last_analysis[:200], text)
        if row_num:
            await analyzer.update_tracking_status(row_num, "✅", comment=text)
        await msg.reply_text(f"✅ تمام اتعلمت! شكراً على التصحيح 🙏")
        _record_bot_message(chat_id)
        context.chat_data.pop("waiting_correction", None)
        return

    # Check if waiting for comment
    comment_state = context.chat_data.get("waiting_comment")
    if comment_state:
        row_num = comment_state["row_num"]
        if row_num:
            await analyzer.update_tracking_status(row_num, "✅", comment=text)
        await msg.reply_text("✅ سجلت التعليق 📝")
        _record_bot_message(chat_id)
        context.chat_data.pop("waiting_comment", None)
        return

    # Check if waiting for image type description
    if context.chat_data.get("waiting_imgtype"):
        context.chat_data.pop("waiting_imgtype", None)
        await msg.reply_text("✅ تمام، سجلت الوصف.")
        _record_bot_message(chat_id)
        return

    # If replying to bot's message → handle as conversation
    if msg.reply_to_message and msg.reply_to_message.from_user:
        bot_user = await context.bot.get_me()
        if msg.reply_to_message.from_user.id == bot_user.id:
            leader = analyzer.get_leader(team_name)
            if not _check_rate_limit(team_name):
                await msg.reply_text(f"⏳ يا {leader}، استنى شوية قبل ما تبعت تاني. البوت محتاج يلحق 😅")
                return
            reply_to_text = msg.reply_to_message.text or ""
            response = await analyzer.analyze_text_message(
                team_name, text, reply_to_text
            )
            if response:
                sent = await send_long_message(context, chat_id, response)
                _record_bot_message(chat_id)
                analyzer.db_log_conversation(team_name, leader, response, text)
            return

    # If bot recently sent a message in this group, treat any text as a response
    if _is_bot_listening(chat_id):
        leader = analyzer.get_leader(team_name)
        response = await analyzer.analyze_text_message(
            team_name, text, ""
        )
        if response:
            sent = await send_long_message(context, chat_id, response)
            _record_bot_message(chat_id)
            analyzer.db_log_conversation(team_name, leader, response, text)
        return

    # If team leader sends a question or mentions help keywords
    help_keywords = ["ممكن", "محتاج", "ساعدني", "عايز", "إيه", "ايه", "ليه", "كيف", "ازاي", "حلل", "اكتب", "شوف"]
    if any(kw in text for kw in help_keywords) or "?" in text or "؟" in text:
        leader = analyzer.get_leader(team_name)
        response = await analyzer.analyze_text_message(team_name, text, "")
        if response:
            sent = await send_long_message(context, chat_id, response)
            _record_bot_message(chat_id)
            analyzer.db_log_conversation(team_name, leader, response, text)
        return

    # Otherwise: ignore non-command text in groups


# ══════════════════════════════════════════════════════════════════════
# DOCUMENT / VIDEO HANDLERS
# ══════════════════════════════════════════════════════════════════════

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads (CSV, XLSX) from team leaders."""
    msg = update.message
    if not msg or not msg.document:
        return
    chat_id = msg.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    doc = msg.document
    fname = (doc.file_name or "").lower()

    # Accept CSV, Excel, and PDF files
    supported = (".csv", ".xlsx", ".xls", ".pdf")
    if not any(fname.endswith(ext) for ext in supported):
        return

    leader = analyzer.get_leader(team_name)
    await msg.reply_text(f"⏳ جاري تحليل الملف يا {leader}...")
    _record_bot_message(chat_id)

    try:
        file = await doc.get_file()
        file_bytes_io = io.BytesIO()
        await file.download_to_memory(file_bytes_io)
        file_bytes = file_bytes_io.getvalue()

        # === PDF: Driver orders sheet ===
        if fname.endswith(".pdf"):
            analysis = await analyzer.analyze_pdf_orders(file_bytes, team_name, fname)
            if analysis:
                await send_long_message(context, chat_id, analysis)
                _record_bot_message(chat_id)
            else:
                await msg.reply_text(f"✅ استلمت الـ PDF يا {leader}. مش قادر أحلله دلوقتي.")
                _record_bot_message(chat_id)
            return

        # === CSV / Excel ===
        file_content = ""
        if fname.endswith(".csv"):
            try:
                file_content = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    file_content = file_bytes.decode("utf-8-sig")
                except Exception:
                    file_content = file_bytes.decode("latin-1", errors="replace")
        elif fname.endswith((".xlsx", ".xls")):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
                rows_text = []
                ws = wb.active
                for row in ws.iter_rows(max_row=100, values_only=True):
                    row_str = "\t".join(str(c) if c is not None else "" for c in row)
                    rows_text.append(row_str)
                file_content = "\n".join(rows_text)
                wb.close()
            except ImportError:
                # openpyxl not available - send raw bytes info
                file_content = f"[Excel file: {len(file_bytes)} bytes, cannot parse without openpyxl]"
            except Exception as e:
                file_content = f"[Excel parse error: {e}]"

        if file_content and len(file_content) > 50:
            # Send to Claude for analysis
            analysis = await analyzer.analyze_document(
                file_content[:8000], team_name, fname
            )
            if analysis:
                await send_long_message(context, chat_id, analysis)
                _record_bot_message(chat_id)
                analyzer.remember_exchange(team_name, analysis[:300])
            else:
                await msg.reply_text(f"✅ استلمت الملف يا {leader}. مش قادر أحلله دلوقتي.")
                _record_bot_message(chat_id)
        else:
            await msg.reply_text(f"✅ استلمت الملف يا {leader}. الملف فاضي أو صغير.")

    except Exception as e:
        logger.error("Document download/analysis error: %s", e)
        await msg.reply_text("⚠️ مش قادر أفتح الملف.")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video uploads - creative analysis."""
    msg = update.message
    if not msg:
        return
    chat_id = msg.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    video = msg.video or msg.video_note
    if not video:
        return

    await msg.reply_text("⏳ جاري تحليل الفيديو... ده بياخد شوية.")

    try:
        file = await video.get_file()
        video_bytes_io = io.BytesIO()
        await file.download_to_memory(video_bytes_io)
        video_bytes = video_bytes_io.getvalue()

        # Get thumbnail if available
        thumb_bytes = None
        if msg.video and msg.video.thumbnail:
            thumb_file = await msg.video.thumbnail.get_file()
            thumb_io = io.BytesIO()
            await thumb_file.download_to_memory(thumb_io)
            thumb_bytes = thumb_io.getvalue()

        analysis = await analyzer.analyze_video_creative(
            video_bytes, team_name, thumb_bytes
        )
        if analysis:
            await send_long_message(context, chat_id, analysis)
            _record_bot_message(chat_id)
            # Interactive buttons after creative analysis
            msg_id = msg.message_id
            keyboard = [
                [
                    InlineKeyboardButton("ابعت للتيم ✅", callback_data=f"cr_{msg_id}_send"),
                    InlineKeyboardButton("عدّل وابعت ✏️", callback_data=f"cr_{msg_id}_edit"),
                    InlineKeyboardButton("سيبه 🤐", callback_data=f"cr_{msg_id}_skip"),
                ],
            ]
            await context.bot.send_message(
                chat_id=chat_id,
                text="من ناحية الكريتيف... عايز أبعت التقييم للتيم؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            _record_bot_message(chat_id)
        else:
            await msg.reply_text("⚠️ مش قادر أحلل الفيديو.")
    except Exception as e:
        logger.error("Video analysis error: %s", e)
        await msg.reply_text("⚠️ حصل مشكلة في تحليل الفيديو.")


# ══════════════════════════════════════════════════════════════════════
# INTERACTIVE MENU & CHECKLIST
# ══════════════════════════════════════════════════════════════════════

async def _show_morning_checklist(update, context, team_name):
    """Show live checklist of morning task items."""
    leader = analyzer.get_leader(team_name)
    chat_id = update.message.chat_id
    today = now_egypt().strftime("%d/%m/%Y")

    # Get what's been received today from DB
    entries = analyzer.db_get_tracking_today(team_name, "morning")

    # Determine what's received
    has_sheet = any(e.get("image_type") in ("order_sheet",) for e in entries)
    has_dashboard = any(e.get("image_type") in ("fb_dash", "tt_dash", "fb_ads_dashboard", "tt_ads_dashboard") for e in entries)
    has_pdf = any(e.get("image_type") in ("driver_orders_pdf",) for e in entries)

    # Count payment images by platform
    fb_payments = [e for e in entries if e.get("image_type") in ("fb_pay", "fb_payment")]
    tt_payments = [e for e in entries if e.get("image_type") in ("tt_pay", "tt_payment")]

    # Get expected account counts from DB
    accounts = analyzer.db_get_team_accounts(team_name)
    fb_expected = accounts.get("facebook", 1) if accounts else 1
    tt_expected = accounts.get("tiktok", 0) if accounts else 0

    # Build checklist
    sheet_icon = "✅" if has_sheet else "⬜"
    dash_icon = "✅" if has_dashboard else "⬜"
    pdf_icon = "✅" if has_pdf else "⬜"

    fb_icon = "✅" if len(fb_payments) >= fb_expected else f"⏳ ({len(fb_payments)}/{fb_expected})" if fb_payments else "⬜"

    lines = [
        f"📋 تاسك الصبح - {today}\n",
        f"1️⃣ شيت التقرير اليومي {sheet_icon}",
        f"2️⃣ صور الدفع فيسبوك {fb_icon}",
    ]

    if tt_expected > 0:
        tt_icon = "✅" if len(tt_payments) >= tt_expected else f"⏳ ({len(tt_payments)}/{tt_expected})" if tt_payments else "⬜"
        lines.append(f"3️⃣ صور الدفع تيك توك {tt_icon}")

    lines.extend([
        f"{'4' if tt_expected > 0 else '3'}️⃣ داشبورد الإعلانات {dash_icon}",
        f"{'5' if tt_expected > 0 else '4'}️⃣ شيت طلبات السواقين PDF {pdf_icon}",
    ])

    # Count completed
    total_items = 3 + (1 if tt_expected > 0 else 0) + 1  # sheet + fb + (tt) + dash + pdf
    completed = sum([has_sheet, len(fb_payments) >= fb_expected, has_dashboard, has_pdf])
    if tt_expected > 0:
        completed += 1 if len(tt_payments) >= tt_expected else 0

    if completed >= total_items:
        lines.append(f"\n🎉 ممتاز يا {leader}! كل حاجة وصلت!")
    else:
        remaining = total_items - completed
        lines.append(f"\nلسه {remaining} حاجات. ابعتيلي واحدة واحدة 🙏")

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=_get_persistent_keyboard(),
    )
    _record_bot_message(chat_id)


async def _show_afternoon_checklist(update, context, team_name):
    """Show afternoon task requirements."""
    leader = analyzer.get_leader(team_name)
    chat_id = update.message.chat_id

    # Check if afternoon data was sent
    entries = analyzer.db_get_tracking_today(team_name, "afternoon")

    status = "✅ وصل" if entries else "⬜ لسه"

    text = (
        f"📋 تاسك العصر - {now_egypt().strftime('%d/%m/%Y')}\n\n"
        f"المطلوب: {status}\n\n"
        f"1️⃣ أداء الإعلانات من الصبح لحد دلوقتي\n"
        f"2️⃣ عدد الرسائل مقابل عدد الطلبات (فيسبوك)\n"
        f"3️⃣ صرفتي كام على الفيسبوك؟\n"
        f"4️⃣ عدد الليدز/المبيعات على التيك توك\n"
        f"5️⃣ صرفتي كام على التيك توك؟\n"
        f"6️⃣ وضع التوصيل النهاردة\n"
        f"7️⃣ أي ملاحظات أو مشاكل؟\n\n"
        f"ابعتيلي screenshots أو اكتبي الأرقام 🙏"
    )

    await update.message.reply_text(text, reply_markup=_get_persistent_keyboard())
    _record_bot_message(chat_id)


async def _show_help_menu(update, context, team_name):
    """Show bot capabilities as inline buttons."""
    leader = analyzer.get_leader(team_name)
    chat_id = update.message.chat_id

    keyboard = [
        [
            InlineKeyboardButton("📊 حلل أرقامي", callback_data="help_analyze"),
            InlineKeyboardButton("📈 أداء الأسبوع", callback_data="help_weekly"),
        ],
        [
            InlineKeyboardButton("🎨 حلل كريتيف", callback_data="help_creative"),
            InlineKeyboardButton("✍️ اكتبلي إعلان", callback_data="help_adcopy"),
        ],
        [
            InlineKeyboardButton("📋 راجع شيت/ملف", callback_data="help_sheet"),
            InlineKeyboardButton("💡 اقتراحات تحسين", callback_data="help_suggest"),
        ],
        [
            InlineKeyboardButton("❓ سؤال تاني", callback_data="help_question"),
        ],
    ]

    await update.message.reply_text(
        f"🤖 أهلاً يا {leader}! إزاي أقدر أساعدك؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    _record_bot_message(chat_id)


async def callback_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle help menu button clicks - ACTUALLY DO THE WORK."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        await query.message.reply_text("⚠️ الجروب ده مش مسجل عندي.")
        return
    leader = analyzer.get_leader(team_name)
    action = query.data.replace("help_", "")
    logger.info("Help button: %s from %s (%s)", action, leader, team_name)

    # Step 1: Send "loading" message immediately (don't edit the button message)
    loading_msgs = {
        "analyze": f"📊 تمام يا {leader}! ابعتيلي screenshot أو اكتبيلي الأرقام وأنا هحللهم فوراً.",
        "creative": f"🎨 تمام يا {leader}! ابعتيلي صورة أو فيديو الإعلان وأنا هقيّمه.",
        "adcopy": f"✍️ تمام يا {leader}! قوليلي اسم المنتج ووصفه وأنا هكتبلك نسخ إعلانية.",
        "sheet": f"📋 تمام يا {leader}! ابعتيلي الملف (CSV أو Excel أو PDF) وأنا هحلله.",
        "question": f"😊 تمام يا {leader}! اسألي أي سؤال عن الإعلانات أو الأداء.",
    }

    # Step 2: For "weekly" and "suggest" - actually work NOW
    if action == "weekly":
        await query.message.reply_text(f"📈 خليني أشوف أداء {team_name} الأسبوع ده... ⏳")
        _record_bot_message(chat_id)
        try:
            analysis = await analyzer.analyze_text_message(
                team_name,
                f"حلل أداء فريق {team_name} الأسبوع ده بالتفصيل. قولي الأرقام والـ trends واقتراحاتك. استخدم البيانات التاريخية من الـ DB.",
                ""
            )
            if analysis:
                await send_long_message(context, chat_id, analysis)
            else:
                await context.bot.send_message(chat_id, f"يا {leader}، مش لاقي بيانات كافية للأسبوع ده. ابعتيلي الأرقام وأنا هحلل.")
        except Exception as e:
            logger.error("Weekly analysis error: %s", e)
            await context.bot.send_message(chat_id, f"يا {leader}، حصل مشكلة في التحليل. جرّبي تاني بعد شوية.")

    elif action == "suggest":
        await query.message.reply_text(f"💡 خليني أفكر في اقتراحات لـ {team_name}... ⏳")
        _record_bot_message(chat_id)
        try:
            analysis = await analyzer.analyze_text_message(
                team_name,
                f"بناءً على كل البيانات عندك عن فريق {team_name}، إيه اقتراحاتك لتحسين الأداء؟ CPO, CPA, cancel rate, budget allocation. كن محدد وعملي.",
                ""
            )
            if analysis:
                await send_long_message(context, chat_id, analysis)
            else:
                await context.bot.send_message(chat_id, f"يا {leader}، محتاج بيانات أكتر. ابعتيلي screenshot من الداشبورد.")
        except Exception as e:
            logger.error("Suggest analysis error: %s", e)
            await context.bot.send_message(chat_id, f"يا {leader}، حصل مشكلة. جرّبي تاني.")

    elif action == "adcopy":
        await query.message.reply_text(f"✍️ تمام يا {leader}! اكتبيلي:\n\n1️⃣ اسم المنتج\n2️⃣ وصف بسيط\n3️⃣ السعر\n4️⃣ العرض أو الخصم (لو في)\n\nوأنا هكتبلك 3 نسخ إعلانية مختلفة 🚀")
        _record_bot_message(chat_id)
        # Set waiting state for ad copy
        context.chat_data["waiting_adcopy"] = True

    else:
        # For analyze, creative, sheet, question - send prompt and wait
        msg = loading_msgs.get(action, f"يا {leader}، قوليلي محتاجة إيه وأنا هساعدك!")
        await query.message.reply_text(msg)
        _record_bot_message(chat_id)

    # Record in DB
    analyzer.db_log_conversation(team_name, f"help_{action}", "")


# ══════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    await update.message.reply_text(
        "أهلاً! أنا EcoBot 🤖\n"
        "مدير التسويق الرقمي بتاعكم.\n"
        "ابعتلي صور التقارير وأنا هحللها.\n\n"
        "الأوامر:\n"
        "/status - حالة كل الفرق\n"
        "/morning - تذكير الصبح\n"
        "/afternoon - أسئلة الـ 4\n"
        "/report - التقرير اليومي\n"
        "/team - تفاصيل فريق\n"
        "/alert - رسالة لفريق\n"
        "/broadcast - رسالة لكل الفرق\n"
        "/pause - إيقاف/تشغيل فريق"
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health check - owner only."""
    if not is_owner(update.message.from_user.id if update.message and update.message.from_user else 0):
        return

    checks = []

    # 1. Bot alive
    checks.append("✅ البوت شغال")

    # 2. DB check
    try:
        import sqlite3
        conn = sqlite3.connect(analyzer.DB_PATH)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        perf_count = conn.execute("SELECT COUNT(*) FROM daily_performance").fetchone()[0]
        tracking_count = conn.execute("SELECT COUNT(*) FROM tracking").fetchone()[0]
        conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        conn.close()
        checks.append(f"✅ DB: {len(tables)} tables | {perf_count} performance | {tracking_count} tracking | {conv_count} conversations")
    except Exception as e:
        checks.append(f"❌ DB: {e}")

    # 3. Claude API
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    checks.append(f"✅ Claude API: {'متصل' if api_key else '❌ مفيش مفتاح'}")

    # 4. Server uptime
    try:
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.read().split()[0])
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            checks.append(f"✅ السيرفر شغال من {hours} ساعة و {minutes} دقيقة")
    except Exception:
        checks.append("⚠️ مش قادر أقرأ uptime")

    # 5. Teams count
    checks.append(f"✅ {len(TEAMS)} فريق متصل")

    # 6. Paused teams
    if paused_teams:
        paused_names = [TEAMS.get(gid, "?") for gid in paused_teams]
        checks.append(f"⏸️ متوقفين: {', '.join(paused_names)}")

    await update.message.reply_text(
        "🏥 فحص النظام:\n\n" + "\n".join(checks)
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check all teams status from tracking sheet + team sheets."""
    if not is_owner(update.message.from_user.id if update.message and update.message.from_user else 0):
        return
    msg = update.message
    loading = await msg.reply_text("⏳ جاري المراجعة...")

    parts = [f"📊 حالة الفرق - {now_egypt().strftime('%Y-%m-%d %H:%M')}\n"]

    for team_name in analyzer.TEAM_INFO:
        try:
            report = await analyzer.build_owner_team_report(team_name)
            leader = report["leader"]
            line = f"{'✅' if report['complete'] else '⚠️'} {team_name} ({leader}): "

            if report["sheet_status"] == "updated":
                sd = report["sheet_data"]
                spend = sd.get("spend", 0)
                orders = sd.get("orders", 0)
                cpo = sd.get("cpo")
                line += f"Spend={spend:,.0f} Orders={orders:.0f}"
                if cpo:
                    line += f" CPO={cpo:.0f}"
            else:
                line += "الشيت مش متحدث"

            if not report["complete"]:
                missing = [m["label"] for m in report["missing"]]
                line += f" | ناقص: {', '.join(missing)}"

            parts.append(line)
        except Exception as e:
            parts.append(f"⚠️ {team_name}: خطأ ({e})")

    try:
        await loading.edit_text("\n".join(parts))
    except Exception:
        await send_long_message(context, msg.chat_id, "\n".join(parts))


async def cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force send morning reminder."""
    if not is_owner(update.message.from_user.id):
        await update.message.reply_text("⚠️ الأمر ده للمالك بس.")
        return
    await update.message.reply_text("⏳ جاري إرسال تذكير الصبح...")
    await send_morning_prereminder(context)
    await update.message.reply_text("✅ تم إرسال التذكير.")


async def cmd_afternoon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force send afternoon questions."""
    if not is_owner(update.message.from_user.id):
        await update.message.reply_text("⚠️ الأمر ده للمالك بس.")
        return
    await update.message.reply_text("⏳ جاري إرسال أسئلة المساء...")
    await send_afternoon_questions(context)
    await update.message.reply_text("✅ تم الإرسال.")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and show daily report."""
    if not is_owner(update.message.from_user.id if update.message and update.message.from_user else 0):
        return
    loading = await update.message.reply_text("⏳ جاري إعداد التقرير...")
    report = await analyzer.generate_smart_daily_report()
    if report:
        try:
            await loading.edit_text(report)
        except Exception:
            await send_long_message(context, update.message.chat_id, report)
    else:
        await loading.edit_text("⚠️ مفيش بيانات كافية للتقرير.")


async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show team details - let user pick a team."""
    if not is_owner(update.message.from_user.id if update.message and update.message.from_user else 0):
        return
    keyboard = []
    row = []
    for team_name in analyzer.TEAM_INFO:
        leader = analyzer.get_leader(team_name)
        row.append(InlineKeyboardButton(
            f"{team_name} ({leader})",
            callback_data=f"tm_{team_name[:8]}",
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text(
        "اختار الفريق:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_team_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed team info."""
    query = update.callback_query
    await query.answer()

    team_prefix = query.data.replace("tm_", "")
    team_name = None
    for tn in analyzer.TEAM_INFO:
        if tn.startswith(team_prefix) or tn[:8] == team_prefix:
            team_name = tn
            break
    if not team_name:
        await safe_edit_message(query,"⚠️ مش لاقي الفريق.")
        return

    await safe_edit_message(query,f"⏳ جاري تحميل بيانات {team_name}...")

    ctx = await analyzer.build_team_context(team_name)
    text = analyzer.format_context_for_prompt(ctx)

    # Truncate if too long
    if len(text) > 4000:
        text = text[:3900] + "\n\n... (مقطوع)"

    try:
        await safe_edit_message(query,text)
    except Exception:
        await send_long_message(context, query.message.chat_id, text)


async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send alert to selected teams. Usage: /alert [message]"""
    if not is_owner(update.message.from_user.id):
        await update.message.reply_text("⚠️ الأمر ده للمالك بس.")
        return

    text = update.message.text.replace("/alert", "").strip()
    if not text:
        await update.message.reply_text("استخدام: /alert [الرسالة]\nأو /broadcast [الرسالة] لكل الفرق")
        return

    # Send to all teams
    sent = 0
    for gid in TEAMS:
        try:
            await context.bot.send_message(chat_id=gid, text=text)
            sent += 1
        except Exception as e:
            logger.error("Alert send failed to %d: %s", gid, e)
    await update.message.reply_text(f"✅ تم الإرسال لـ {sent} فريق.")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast to all teams."""
    if not is_owner(update.message.from_user.id):
        await update.message.reply_text("⚠️ الأمر ده للمالك بس.")
        return

    text = update.message.text.replace("/broadcast", "").strip()
    if not text:
        await update.message.reply_text("استخدام: /broadcast [الرسالة]")
        return

    sent = 0
    for gid in TEAMS:
        try:
            await context.bot.send_message(chat_id=gid, text=text)
            sent += 1
        except Exception as e:
            logger.error("Broadcast failed to %d: %s", gid, e)
    await update.message.reply_text(f"✅ تم البث لـ {sent} فريق.")


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Compare team screenshots vs sheet."""
    if not is_owner(update.message.from_user.id if update.message and update.message.from_user else 0):
        return
    chat_id = update.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        await update.message.reply_text("⚠️ الأمر ده لازم يكون في جروب فريق.")
        return

    loading = await update.message.reply_text("⏳ جاري المقارنة...")

    tracking = await analyzer.get_missing_for_team(team_name)
    rows = await analyzer.fetch_team_sheet(team_name)
    today = analyzer.get_team_sheet_today(rows) if rows else None

    parts = [f"📊 مقارنة {team_name}:\n"]

    if today:
        parts.append("بيانات الشيت:")
        parts.append(analyzer.format_team_sheet_data(today))
    else:
        parts.append("الشيت مش متحدث النهاردة.")

    parts.append("")
    if tracking["complete"]:
        parts.append("✅ كل الصور المطلوبة اتبعتت.")
    else:
        parts.append("ناقص:")
        for m in tracking["missing"]:
            parts.append(f"  - {m['label']}")

    try:
        await loading.edit_text("\n".join(parts))
    except Exception:
        await send_long_message(context, chat_id, "\n".join(parts))


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle pause for a team."""
    if not is_owner(update.message.from_user.id):
        await update.message.reply_text("⚠️ الأمر ده للمالك بس.")
        return

    chat_id = update.message.chat_id
    # If in a team group, toggle that team
    if chat_id in TEAMS:
        if chat_id in paused_teams:
            paused_teams.discard(chat_id)
            _save_paused()
            await update.message.reply_text(f"▶️ تم تشغيل {TEAMS[chat_id]}")
        else:
            paused_teams.add(chat_id)
            _save_paused()
            await update.message.reply_text(f"⏸️ تم إيقاف {TEAMS[chat_id]}")
        return

    # If in private chat, show team selection
    keyboard = []
    row = []
    for gid, tn in TEAMS.items():
        status = "⏸️" if gid in paused_teams else "▶️"
        row.append(InlineKeyboardButton(
            f"{status} {tn}",
            callback_data=f"ps_{gid}",
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text(
        "اختار الفريق:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def callback_pause_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle pause from button."""
    query = update.callback_query
    await query.answer()

    gid = int(query.data.replace("ps_", ""))
    team_name = TEAMS.get(gid, "?")

    if gid in paused_teams:
        paused_teams.discard(gid)
        _save_paused()
        await safe_edit_message(query,f"▶️ تم تشغيل {team_name}")
    else:
        paused_teams.add(gid)
        _save_paused()
        await safe_edit_message(query,f"⏸️ تم إيقاف {team_name}")


# ══════════════════════════════════════════════════════════════════════
# SCHEDULED JOBS
# ══════════════════════════════════════════════════════════════════════

async def send_morning_prereminder(context: ContextTypes.DEFAULT_TYPE):
    """10:30 AM - Friendly morning reminder. Skip teams that already submitted."""
    for gid, team_name in TEAMS.items():
        if gid in paused_teams:
            continue
        leader = analyzer.get_leader(team_name)
        try:
            # CHECK DB FIRST - skip if team already submitted
            entries = analyzer.db_get_tracking_today(team_name, "morning")
            if entries and len(entries) >= 2:
                # Already sent 2+ items, skip reminder
                continue

            await context.bot.send_message(
                chat_id=gid,
                text=(
                    f"صباح الخير يا {leader}! ☀️\n"
                    f"فاكرين تقرير الصبح؟\n"
                    f"المطلوب: شيت + صور دفع + داشبورد + PDF سواقين\n"
                    f"الديدلاين الساعة 11:00 ⏰"
                ),
                reply_markup=_get_persistent_keyboard(),
            )
            _record_bot_message(gid)
        except Exception as e:
            logger.error("Pre-reminder failed for %s: %s", team_name, e)
        await asyncio.sleep(0.5)


async def send_smart_morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    """11:00 AM - Deadline reached. Only remind teams with MISSING items."""
    for gid, team_name in TEAMS.items():
        if gid in paused_teams:
            continue
        leader = analyzer.get_leader(team_name)
        try:
            # CHECK DB FIRST - what's missing?
            missing = await analyzer.get_missing_for_team(team_name, "morning")
            if missing["complete"]:
                # Team is done! Send thank you instead
                await context.bot.send_message(
                    chat_id=gid,
                    text=f"✅ شكراً يا {leader}! تقرير الصبح كامل. شغل ممتاز! 💪",
                )
                _record_bot_message(gid)
                continue

            missing_labels = [m["label"] for m in missing["missing"]]
            received = missing.get("received_count", 0)
            await context.bot.send_message(
                chat_id=gid,
                text=(
                    f"⏰ يا {leader}، الساعة 11.\n"
                    f"وصلني {received} حاجة ✅ بس لسه ناقص:\n"
                    + "\n".join(f"  ⬜ {ml}" for ml in missing_labels)
                    + "\n\nابعتيهم دلوقتي لو سمحتي 🙏"
                ),
                reply_markup=_get_persistent_keyboard(),
            )
            _record_bot_message(gid)
        except Exception as e:
            logger.error("Morning reminder failed for %s: %s", team_name, e)
        await asyncio.sleep(0.5)

    # Schedule follow-up reminders
    job_queue = context.job_queue
    job_queue.run_once(_reminder_followup_1, when=timedelta(minutes=15),
                       name="followup_1")
    job_queue.run_once(_reminder_followup_2, when=timedelta(minutes=30),
                       name="followup_2")
    job_queue.run_once(_reminder_followup_3, when=timedelta(minutes=45),
                       name="followup_3")


async def _reminder_followup_1(context: ContextTypes.DEFAULT_TYPE):
    """11:15 AM - First follow-up: specific missing items."""
    await _send_missing_reminder(context, "⏰ تذكير أول:")


async def _reminder_followup_2(context: ContextTypes.DEFAULT_TYPE):
    """11:30 AM - Second follow-up: stronger."""
    await _send_missing_reminder(context, "⚠️ تذكير تاني:")


async def _reminder_followup_3(context: ContextTypes.DEFAULT_TYPE):
    """11:45 AM - Last reminder."""
    await _send_missing_reminder(context, "🔴 تذكير أخير:")


async def _send_missing_reminder(context: ContextTypes.DEFAULT_TYPE, prefix: str):
    """Send reminders only to teams that still have missing items."""
    for gid, team_name in TEAMS.items():
        if gid in paused_teams:
            continue
        try:
            missing = await analyzer.get_missing_for_team(team_name, "morning")
            if missing["complete"]:
                continue
            leader = analyzer.get_leader(team_name)
            missing_labels = [m["label"] for m in missing["missing"]]
            await context.bot.send_message(
                chat_id=gid,
                text=(
                    f"{prefix}\n"
                    f"يا {leader}، لسه ناقص:\n"
                    + "\n".join(f"  - {ml}" for ml in missing_labels)
                ),
            )
            _record_bot_message(gid)
        except Exception as e:
            logger.error("Reminder failed for %s: %s", team_name, e)
        await asyncio.sleep(0.3)


async def final_morning_check(context: ContextTypes.DEFAULT_TYPE):
    """
    12:00 PM - Final check:
    1. Read tracking for each team
    2. Read each team's Google Sheet
    3. Trigger master sheet update
    4. Send individual team reports to OWNER
    5. Wait for owner decision on each team
    """
    # Trigger master sheet update
    await analyzer.trigger_master_update()

    # Build and send reports for each team to owner
    for team_name in analyzer.TEAM_INFO:
        gid = TEAM_GIDS.get(team_name)
        if not gid or gid in paused_teams:
            continue

        try:
            report = await analyzer.build_owner_team_report(team_name)

            # Only send to owner if there are missing items or issues
            if not report["complete"] or report["cpo_status"] == "red":
                text = _format_owner_team_report(team_name, gid, report)
                keyboard = _build_owner_decision_keyboard(gid)
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                # Team is complete - just notify briefly
                leader = report["leader"]
                sd = report.get("sheet_data", {})
                cpo = sd.get("cpo")
                cpo_str = f" CPO={cpo:.0f}" if cpo else ""
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"✅ {team_name} ({leader}): كل حاجة تمام.{cpo_str}",
                )
        except Exception as e:
            logger.error("Final check failed for %s: %s", team_name, e)
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"⚠️ {team_name}: خطأ في المراجعة - {e}",
            )
        await asyncio.sleep(0.5)


async def proactive_check(context: ContextTypes.DEFAULT_TYPE):
    """1:30 PM - Proactive check after master update."""
    alerts = await analyzer.proactive_sheet_check()
    if not alerts:
        return

    # Group alerts by severity
    critical = [a for a in alerts if a["severity"] == "critical"]
    warnings = [a for a in alerts if a["severity"] == "warning"]

    if critical or warnings:
        parts = ["🔍 نتائج المراجعة الاستباقية:\n"]
        for a in critical:
            parts.append(f"🚨 {a['msg']}")
        for a in warnings:
            parts.append(f"⚠️ {a['msg']}")

        try:
            await send_long_message(context, OWNER_CHAT_ID, "\n".join(parts))
        except Exception as e:
            logger.error("Proactive check send error: %s", e)


async def smart_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """2:00 PM - Smart daily report to owner."""
    report = await analyzer.generate_smart_daily_report()
    if report:
        try:
            await send_long_message(
                context, OWNER_CHAT_ID,
                f"📊 التقرير اليومي الذكي:\n\n{report}",
            )
        except Exception as e:
            logger.error("Daily report send error: %s", e)


async def send_afternoon_questions(context: ContextTypes.DEFAULT_TYPE):
    """4:00 PM - Send afternoon questions to each team + schedule follow-ups."""
    for gid, team_name in TEAMS.items():
        if gid in paused_teams:
            continue
        leader = analyzer.get_leader(team_name)
        try:
            await context.bot.send_message(
                chat_id=gid,
                text=(
                    f"مساء الخير يا {leader}! 🌤️\n"
                    f"وقت تقرير الساعة 4:\n\n"
                    f"1️⃣ أداء الإعلانات من الصبح لحد دلوقتي\n"
                    f"2️⃣ عدد الرسائل مقابل عدد الطلبات (فيسبوك)\n"
                    f"3️⃣ صرفتي كام على الفيسبوك؟\n"
                    f"4️⃣ عدد الليدز/المبيعات على التيك توك\n"
                    f"5️⃣ صرفتي كام على التيك توك؟\n"
                    f"6️⃣ وضع التوصيل النهاردة\n"
                    f"7️⃣ أي ملاحظات أو مشاكل؟\n\n"
                    f"ابعتيلي screenshots أو اكتبي الأرقام 🙏"
                ),
            )
            _record_bot_message(gid)
        except Exception as e:
            logger.error("Afternoon questions failed for %s: %s", team_name, e)
        await asyncio.sleep(0.5)

    # Schedule follow-up reminders every 15 minutes for 1.5 hours
    jq = context.job_queue
    for i, minutes in enumerate([15, 30, 45, 60, 75, 90], start=1):
        jq.run_once(
            _afternoon_followup,
            when=timedelta(minutes=minutes),
            name=f"afternoon_followup_{i}",
            data={"step": i, "total": 6},
        )
    # Final check at 5:30 PM (90 min after 4:00)
    jq.run_once(
        final_afternoon_check,
        when=timedelta(minutes=95),
        name="final_afternoon_check",
    )


async def _afternoon_followup(context: ContextTypes.DEFAULT_TYPE):
    """4:15-5:30 PM - Follow-up reminders for afternoon report."""
    job_data = context.job.data or {}
    step = job_data.get("step", 1)
    total = job_data.get("total", 6)

    prefixes = {
        1: "⏰ تذكير (1):",
        2: "⏰ تذكير (2):",
        3: "⚠️ تذكير (3):",
        4: "⚠️ تذكير (4):",
        5: "🔴 تذكير (5):",
        6: "🔴 تذكير أخير:",
    }
    prefix = prefixes.get(step, f"⏰ تذكير ({step}):")

    for gid, team_name in TEAMS.items():
        if gid in paused_teams:
            continue
        try:
            # Check tracking sheet - did they send afternoon data?
            entries = await analyzer.get_team_tracking_today(team_name, "afternoon")
            if entries:
                # Already sent something - skip reminder
                continue

            leader = analyzer.get_leader(team_name)
            await context.bot.send_message(
                chat_id=gid,
                text=(
                    f"{prefix}\n"
                    f"يا {leader}، لسه مستني تقرير الساعة 4 🙏\n"
                    f"ابعتيلي الأرقام أو screenshots"
                ),
            )
            _record_bot_message(gid)
        except Exception as e:
            logger.error("Afternoon followup %d failed for %s: %s", step, team_name, e)
        await asyncio.sleep(0.3)


async def final_afternoon_check(context: ContextTypes.DEFAULT_TYPE):
    """5:30 PM - Final afternoon check. Send reports to owner for decision."""
    for team_name in analyzer.TEAM_INFO:
        gid = TEAM_GIDS.get(team_name)
        if not gid or gid in paused_teams:
            continue

        try:
            # Check tracking sheet for afternoon entries
            entries = await analyzer.get_team_tracking_today(team_name, "afternoon")
            leader = analyzer.get_leader(team_name)

            if entries:
                # Team sent afternoon data - brief OK to owner
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=f"✅ {team_name} ({leader}): تقرير العصر وصل ({len(entries)} رد)",
                )
            else:
                # Team didn't send - report to owner with decision buttons
                keyboard = [
                    [
                        InlineKeyboardButton("✅ تمام", callback_data=f"od_{gid}_ok"),
                        InlineKeyboardButton("⚠️ خصم", callback_data=f"od_{gid}_deduct"),
                    ],
                    [
                        InlineKeyboardButton("🔄 راجع تاني", callback_data=f"od_{gid}_recheck"),
                        InlineKeyboardButton("💬 رسالة", callback_data=f"od_{gid}_msg"),
                    ],
                ]
                await context.bot.send_message(
                    chat_id=OWNER_CHAT_ID,
                    text=(
                        f"⚠️ {team_name} ({leader})\n"
                        f"تقرير العصر: لم يصل ❌\n"
                        f"مفيش أي رد من الساعة 4 لحد دلوقتي.\n\n"
                        f"إيه القرار؟"
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception as e:
            logger.error("Final afternoon check failed for %s: %s", team_name, e)
        await asyncio.sleep(0.5)


async def daily_reset(context: ContextTypes.DEFAULT_TYPE):
    """12:05 AM - Daily reset."""
    analyzer.reset_conversation_memory()
    logger.info("Daily reset completed at %s", now_egypt())


# ══════════════════════════════════════════════════════════════════════
# CALLBACK: Analysis reaction (ok / wrong / comment / alert / wait / more)
# ══════════════════════════════════════════════════════════════════════

async def callback_analysis_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user reaction to AI analysis."""
    query = update.callback_query
    await query.answer()

    data = query.data  # ar_{msg_id}_{action}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    msg_id_str = parts[1]
    action = parts[2]

    chat_id = query.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    last_analysis = context.chat_data.get("last_analysis", "")

    if action == "ok":
        await safe_edit_message(query,"تمام! ✅ شكراً على التأكيد.")
        analyzer.remember_exchange(team_name, "تأكيد التحليل", user_reply="صح")

    elif action == "wrong":
        await safe_edit_message(query,"❌ إيه الغلط؟ اكتبلي وأنا هتعلم.")
        context.chat_data["waiting_correction"] = {
            "msg_id": msg_id_str,
            "row_num": 0,
            "team_name": team_name,
        }
        analyzer.remember_exchange(team_name, "تصحيح مطلوب", user_reply="مش كده")

    elif action == "comment":
        await safe_edit_message(query,"💬 اكتب التعليق...")
        context.chat_data["waiting_comment"] = {
            "msg_id": msg_id_str,
            "row_num": 0,
        }

    elif action == "alert":
        # Send alert to the team
        leader = analyzer.get_leader(team_name)
        alert_msg = f"⚠️ تنبيه يا {leader}:\nالأرقام محتاجة مراجعة. راجع/ي التحليل وبلغيني."
        await context.bot.send_message(chat_id=chat_id, text=alert_msg)
        _record_bot_message(chat_id)
        await safe_edit_message(query,"✅ تم إرسال التنبيه للتيم.")
        analyzer.remember_exchange(team_name, "تنبيه اتبعت للتيم", user_reply="ابعت تنبيه")

    elif action == "wait":
        await safe_edit_message(query,"تمام، هستنى شوية وهراجع تاني. ⏳")
        analyzer.remember_exchange(team_name, "مستني", user_reply="استنى")

    elif action == "more":
        await safe_edit_message(query,"⏳ جاري تحليل أعمق...")
        # Do deeper analysis using last analysis as context
        if team_name:
            deep_analysis = await analyzer.analyze_text_message(
                team_name,
                "عايز تحليل أعمق للأرقام - إيه المشكلة وإيه الحل؟",
                last_analysis[:300],
            )
            if deep_analysis:
                await send_long_message(context, chat_id, deep_analysis)
                _record_bot_message(chat_id)
                analyzer.remember_exchange(team_name, deep_analysis[:300], user_reply="حلل أكتر")
            else:
                await context.bot.send_message(chat_id=chat_id, text="مش قادر أحلل أكتر دلوقتي.")
                _record_bot_message(chat_id)


# ══════════════════════════════════════════════════════════════════════
# CALLBACK: Creative reaction (send / edit / skip)
# ══════════════════════════════════════════════════════════════════════

async def callback_creative_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user reaction to creative analysis."""
    query = update.callback_query
    await query.answer()

    data = query.data  # cr_{msg_id}_{action}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    msg_id_str = parts[1]
    action = parts[2]

    chat_id = query.message.chat_id
    team_name = get_team_name(chat_id)
    if not team_name:
        return

    if action == "send":
        await safe_edit_message(query,"✅ تم! التقييم متبعت.")
        analyzer.remember_exchange(team_name, "تقييم كريتيف اتبعت", user_reply="ابعت")

    elif action == "edit":
        await safe_edit_message(query,"✏️ اكتب التعديل وهبعته...")
        context.chat_data["waiting_comment"] = {
            "msg_id": msg_id_str,
            "row_num": 0,
        }
        analyzer.remember_exchange(team_name, "تعديل تقييم كريتيف", user_reply="عدّل")

    elif action == "skip":
        await safe_edit_message(query,"تمام، مش هبعته. 🤐")
        analyzer.remember_exchange(team_name, "تقييم كريتيف اتلغى", user_reply="سيبه")


# ══════════════════════════════════════════════════════════════════════
# CALLBACK QUERY ROUTER
# ══════════════════════════════════════════════════════════════════════

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all callback queries based on prefix."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data

    if data.startswith("it_"):
        await callback_image_type(update, context)
    elif data.startswith("ac_"):
        await callback_account_count(update, context)
    elif data.startswith("cf_"):
        await callback_confirmation(update, context)
    elif data.startswith("od_"):
        await callback_owner_decision(update, context)
    elif data.startswith("tm_"):
        await callback_team_detail(update, context)
    elif data.startswith("ps_"):
        await callback_pause_toggle(update, context)
    elif data.startswith("ar_"):
        await callback_analysis_reaction(update, context)
    elif data.startswith("cr_"):
        await callback_creative_reaction(update, context)
    elif data.startswith("help_"):
        await callback_help(update, context)
    else:
        logger.warning("Unknown callback data: %s", data)
        await query.answer("⚠️")


# ══════════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and send critical errors to owner."""
    logger.error("Exception: %s", context.error, exc_info=context.error)

    # Only send to owner for serious errors (not network timeouts)
    error_str = str(context.error)
    if any(skip in error_str.lower() for skip in ["timeout", "timed out", "network", "connection", "conflict", "terminated by other"]):
        return

    try:
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"🚨 خطأ في النظام:\n{error_str[:500]}"
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    """Start the bot."""
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("morning", cmd_morning))
    app.add_handler(CommandHandler("afternoon", cmd_afternoon))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("health", cmd_health))

    # Callback queries (all go through router)
    app.add_handler(CallbackQueryHandler(callback_router))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Videos
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))

    # Documents
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Text messages (must be last)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text
    ))

    # Error handler
    app.add_error_handler(error_handler)

    # Schedule jobs (Egypt/Cairo timezone)
    jq = app.job_queue

    # 10:30 AM - Friendly pre-reminder
    jq.run_daily(
        send_morning_prereminder,
        time=time(hour=10, minute=30, tzinfo=EGYPT_TZ),
        name="morning_prereminder",
    )

    # 11:00 AM - Deadline + start tracking + schedule follow-ups
    jq.run_daily(
        send_smart_morning_reminder,
        time=time(hour=11, minute=0, tzinfo=EGYPT_TZ),
        name="morning_reminder",
    )

    # 12:00 PM - Final morning check + owner approval flow
    jq.run_daily(
        final_morning_check,
        time=time(hour=12, minute=0, tzinfo=EGYPT_TZ),
        name="final_morning_check",
    )

    # 1:30 PM - Proactive check
    jq.run_daily(
        proactive_check,
        time=time(hour=13, minute=30, tzinfo=EGYPT_TZ),
        name="proactive_check",
    )

    # 2:00 PM - Smart daily report
    jq.run_daily(
        smart_daily_report,
        time=time(hour=14, minute=0, tzinfo=EGYPT_TZ),
        name="smart_daily_report",
    )

    # 4:00 PM - Afternoon questions
    jq.run_daily(
        send_afternoon_questions,
        time=time(hour=16, minute=0, tzinfo=EGYPT_TZ),
        name="afternoon_questions",
    )

    # 12:05 AM - Daily reset
    jq.run_daily(
        daily_reset,
        time=time(hour=0, minute=5, tzinfo=EGYPT_TZ),
        name="daily_reset",
    )

    logger.info("EcoTeam Agent V2 starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
