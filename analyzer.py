"""
EcoTeam Agent V2 - AI-powered Performance Marketing Manager.
Manages 11 advertising teams running Facebook/TikTok message ads in Kuwait.

Core systems:
- Team sheet reading (individual Google Sheets per team)
- Master sheet aggregation
- Tracking sheet (logging received images, missing items)
- Screenshot number extraction (user selects type via buttons)
- Smart analysis with verification, anomaly detection, cross-team ranking
- Creative analysis (video + image) with scorecard
- Conversation memory per team
- Proactive monitoring & daily reports
"""
import os
import re
import csv
import json
import base64
import logging
import subprocess
import tempfile
import io as _io
import urllib.parse
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncio

import httpx
import anthropic

logger = logging.getLogger(__name__)
EGYPT_TZ = ZoneInfo("Africa/Cairo")


def _now_egypt():
    return datetime.now(EGYPT_TZ)


# ── Config ────────────────────────────────────────────────────────────
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
MASTER_SHEET_URL = os.environ.get("MASTER_SHEET_URL", "")
TRACKING_SHEET_URL = "https://script.google.com/macros/s/AKfycbxorxOudJDqD_55pElkEEtGM16VutZg-vxMSMDHaBCnIK17H5jAOcK0gfr5bKcqIl-p6Q/exec"
TRACKING_SHEET_ID = "1aKF2b3oRSkdvpybmmE_j1U34DVn6mpJSyjPp5btnBvg"

# Persistent storage: /data on Railway, fallback to cwd
DATA_DIR = Path("/data") if Path("/data").exists() else Path(".")

# SQLite database path
DB_PATH = os.path.join(os.environ.get("DATA_DIR", str(DATA_DIR)), "ecoteam.db")


# ── Team info (EXACT - do not change) ─────────────────────────────────
TEAM_INFO = {
    "Kuwaitmall":  {"leader": "سمر",    "sheet_name": "Fordeal",    "sheet_id": "1ckXTIE5P0POiOmeDSnGHPlJqHOF9a9LiGOLwu8XHMxo"},
    "Meeven":      {"leader": "غرام",   "sheet_name": "Meveen",     "sheet_id": "13SYsxvgLVDkVlZ1y1UnwngDVxn6wr91xwI_9eG3FZE4"},
    "Blinken":     {"leader": "اسراء",  "sheet_name": "Blinken",    "sheet_id": "1kd7ckJB46dDG99wn8XAyhxqnZuydC0OVcWHrjPeHHcw"},
    "Matajer":     {"leader": "شروق",   "sheet_name": "Matajer",    "sheet_id": "1Dr4JirGRML_R1APFt6yIky0QRQgLxnm3fMrLthzGmqU"},
    "Bazar":       {"leader": "اسلام",  "sheet_name": "Bazaar",     "sheet_id": "1HhLDRdP_CU0S335022XzkZIS5p2F_SMK4ogsoAWPxvI"},
    "Minimarket":  {"leader": "بسملة",  "sheet_name": "Minimarket", "sheet_id": "18ax0CSvFlID7Iy885szdGwm7fe2HCV07uZXSFjneYp4"},
    "Khosomaat":   {"leader": "حنين",   "sheet_name": "Khosomaat",  "sheet_id": "1kEo6lwJvlzE1EB24Qu763xVOMphAOytlqFVZ4vTBut8"},
    "Trend":       {"leader": "اسماء",  "sheet_name": "Click Cart", "sheet_id": "1C1TodG0bEXB_xgAyqtgFMaXilOdUD24vMbjx690Qipo"},
    "Aswaq":       {"leader": "محمود",  "sheet_name": "Aswaq",      "sheet_id": "1OiBgM6b_Y8bcrlhRsdC3o2lelZL3kz-aIeP7Pnnfnyo"},
    "Flash":       {"leader": "يحيي",   "sheet_name": "Flash",      "sheet_id": "1AcsEcnhgPnvrWJJXu7sEvWiHD38szgJhTrjtA-lmXjs"},
    "Deelat":      {"leader": "مريم",   "sheet_name": "Deelat",     "sheet_id": "19w3gqsL7vNh_XyBuBf-ZMhvEBFWiQGSV0yY2BBzOYxM"},
}

# Decision thresholds
CPO_GREEN = 150
CPO_YELLOW = 180
CPA_GREEN = 150
CPA_YELLOW = 180
CANCEL_RED = 30

# Image types the bot processes (user selects via button, no auto-classification)
IMAGE_TYPES = {
    "fb_ads_dashboard":  "داشبورد حملات Facebook Ads Manager",
    "tt_ads_dashboard":  "داشبورد حملات TikTok Ads",
    "fb_payment":        "صفحة دفع/billing من فيسبوك",
    "tt_payment":        "صفحة دفع/billing من تيك توك",
    "order_sheet":       "شيت الطلبات اليومي",
    "budget_sheet":      "شيت البادجيت أو أكواد فوري",
    "creative_image":    "إعلان (صورة/فيديو creative)",
    "other":             "صورة تانية",
}

REPORT_IMAGE_TYPES = {"fb_ads_dashboard", "tt_ads_dashboard", "order_sheet", "budget_sheet"}
PAYMENT_IMAGE_TYPES = {"fb_payment", "tt_payment"}

# Team sheet columns (0-13)
TEAM_SHEET_COLUMNS = [
    "Date", "Spend", "New Orders", "Yesterday New",
    "Delivered", "Cancel", "Hold", "CPO",
    "Daily Target", "Gap", "Lamp", "Del%", "Cancel%", "Hold%",
]


# ══════════════════════════════════════════════════════════════════════
# SQLITE DATABASE LAYER
# ══════════════════════════════════════════════════════════════════════

def _get_db():
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    """Create all tables if they don't exist."""
    try:
        conn = _get_db()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            team TEXT,
            leader TEXT,
            image_type TEXT,
            platform TEXT,
            account_num TEXT,
            amount TEXT,
            ai_notes TEXT,
            leader_comment TEXT,
            status TEXT,
            task_type TEXT,
            message_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            team TEXT,
            leader TEXT,
            bot_message TEXT,
            user_reply TEXT,
            context TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            team TEXT,
            leader TEXT,
            decision TEXT,
            reason TEXT,
            owner_note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS team_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT,
            platform TEXT,
            account_count INTEGER,
            updated_at TEXT,
            UNIQUE(team, platform)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS learnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            team TEXT,
            category TEXT,
            original TEXT,
            correction TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS daily_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            team TEXT,
            spend REAL,
            new_orders REAL,
            delivered REAL,
            cancel REAL,
            hold REAL,
            cpo REAL,
            cpa REAL,
            cancel_rate REAL,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(date, team)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS creative_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            team TEXT,
            creative_type TEXT,
            score TEXT,
            analysis TEXT,
            performance_impact TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS budget_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            team TEXT,
            platform TEXT,
            payment_method TEXT,
            amount REAL,
            currency TEXT DEFAULT 'EGP',
            source TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        conn.commit()
        conn.close()
        logger.info("SQLite DB initialized at %s", DB_PATH)
    except Exception as e:
        logger.error("Failed to initialize SQLite DB: %s", e)


# Initialize DB at module load time
_init_db()


# ── DB Migrations ────────────────────────────────────────────────────
DB_VERSION = 2  # Increment when schema changes


def _migrate_db():
    """Apply database migrations."""
    try:
        conn = _get_db()
        # Check current version
        try:
            version = conn.execute("SELECT version FROM db_meta").fetchone()
            current_version = version[0] if version else 0
        except Exception:
            conn.execute("CREATE TABLE IF NOT EXISTS db_meta (version INTEGER)")
            conn.execute("INSERT INTO db_meta VALUES (0)")
            conn.commit()
            current_version = 0

        if current_version < 1:
            # Migration 1: Add indexes for faster queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracking_team_date ON tracking(team, date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_perf_team_date ON daily_performance(team, date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_team ON conversations(team)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_learnings_team ON learnings(team)")
            current_version = 1

        if current_version < 2:
            # Migration 2: Add product_orders table for PDF data
            conn.execute("""CREATE TABLE IF NOT EXISTS product_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                team TEXT NOT NULL,
                product_name TEXT,
                quantity INTEGER DEFAULT 1,
                price REAL,
                area TEXT,
                source TEXT DEFAULT 'pdf_import',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_team_date ON product_orders(team, date)")
            current_version = 2

        # Update version
        conn.execute("UPDATE db_meta SET version = ?", (current_version,))
        conn.commit()
        conn.close()
        logger.info("DB at version %d", current_version)
    except Exception as e:
        logger.error("DB migration error: %s", e)


_migrate_db()


# ── DB wrapper functions ─────────────────────────────────────────────

def db_log_tracking(team, leader, image_type, platform="", account_num="",
                    amount="", ai_notes="", leader_comment="", status="pending",
                    task_type="morning", message_id=""):
    """Log an image/interaction to the local database."""
    try:
        now = _now_egypt()
        conn = _get_db()
        conn.execute(
            """INSERT INTO tracking (date, time, team, leader, image_type, platform,
               account_num, amount, ai_notes, leader_comment, status, task_type, message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now.strftime("%d/%m/%Y"), now.strftime("%H:%M"), team, leader,
             image_type, platform, account_num, amount, ai_notes,
             leader_comment, status, task_type, message_id),
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        logger.info("DB: logged tracking %s %s %s (id=%d)", team, image_type, task_type, row_id)
        return {"success": True, "id": row_id}
    except Exception as e:
        logger.error("DB tracking log error: %s", e)
        return {"success": False, "error": str(e)}


def db_get_tracking_today(team_name, task_type="morning"):
    """Get all tracking entries for a team today."""
    try:
        today = _now_egypt().strftime("%d/%m/%Y")
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM tracking WHERE team=? AND task_type=? AND date=? ORDER BY id",
            (team_name, task_type, today),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("DB tracking read error: %s", e)
        return []


def db_get_team_accounts(team_name):
    """Get known account counts for a team."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT platform, account_count FROM team_accounts WHERE team=?",
            (team_name,),
        ).fetchall()
        conn.close()
        return {r["platform"]: r["account_count"] for r in rows}
    except Exception as e:
        logger.error("DB account read error: %s", e)
        return {}


def db_save_team_accounts(team_name, platform, count):
    """Save/update account count for a team."""
    try:
        now = _now_egypt().isoformat()
        conn = _get_db()
        conn.execute(
            """INSERT INTO team_accounts (team, platform, account_count, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(team, platform) DO UPDATE SET account_count=?, updated_at=?""",
            (team_name, platform, count, now, count, now),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB save accounts error: %s", e)
        return False


def db_update_tracking_status(row_id, status, comment=""):
    """Update status of a tracking entry."""
    try:
        conn = _get_db()
        conn.execute(
            "UPDATE tracking SET status=?, leader_comment=? WHERE id=?",
            (status, comment, row_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB update tracking status error: %s", e)
        return False


def db_log_conversation(team, leader, bot_message, user_reply="", context=""):
    """Log a conversation exchange."""
    try:
        now = _now_egypt()
        conn = _get_db()
        conn.execute(
            """INSERT INTO conversations (date, team, leader, bot_message, user_reply, context)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now.strftime("%Y-%m-%d"), team, leader, bot_message[:500],
             user_reply[:300], context),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB conversation log error: %s", e)
        return False


def db_get_conversations(team_name, limit=10):
    """Get recent conversations for a team."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM conversations WHERE team=? ORDER BY id DESC LIMIT ?",
            (team_name, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        logger.error("DB conversation read error: %s", e)
        return []


def db_log_decision(team, leader, decision, reason="", owner_note=""):
    """Log an owner decision."""
    try:
        now = _now_egypt()
        conn = _get_db()
        conn.execute(
            """INSERT INTO decisions (date, team, leader, decision, reason, owner_note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now.strftime("%Y-%m-%d"), team, leader, decision, reason, owner_note),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB decision log error: %s", e)
        return False


def db_log_learning(team, category, original, correction):
    """Log a correction/learning."""
    try:
        now = _now_egypt()
        conn = _get_db()
        conn.execute(
            """INSERT INTO learnings (date, team, category, original, correction)
               VALUES (?, ?, ?, ?, ?)""",
            (now.strftime("%Y-%m-%d %H:%M"), team, category,
             original[:300], correction[:300]),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB learning log error: %s", e)
        return False


def db_get_learnings(team_name="", limit=20):
    """Get learnings, optionally filtered by team."""
    try:
        conn = _get_db()
        if team_name:
            rows = conn.execute(
                "SELECT * FROM learnings WHERE team=? ORDER BY id DESC LIMIT ?",
                (team_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM learnings ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        logger.error("DB learnings read error: %s", e)
        return []


def db_log_daily_performance(team, date, spend, new_orders, delivered,
                             cancel, hold, cpo, cpa, cancel_rate, source="sheet"):
    """Log daily performance numbers."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO daily_performance (date, team, spend, new_orders, delivered,
               cancel, hold, cpo, cpa, cancel_rate, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date, team) DO UPDATE SET
               spend=?, new_orders=?, delivered=?, cancel=?, hold=?,
               cpo=?, cpa=?, cancel_rate=?, source=?""",
            (date, team, spend, new_orders, delivered, cancel, hold, cpo, cpa,
             cancel_rate, source,
             spend, new_orders, delivered, cancel, hold, cpo, cpa, cancel_rate, source),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB daily performance log error: %s", e)
        return False


def db_get_daily_performance(team_name, days=30):
    """Get performance history for a team."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM daily_performance WHERE team=? ORDER BY date DESC LIMIT ?",
            (team_name, days),
        ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]
    except Exception as e:
        logger.error("DB performance read error: %s", e)
        return []


def db_log_creative(team, creative_type, score, analysis, performance_impact=""):
    """Log a creative analysis."""
    try:
        now = _now_egypt()
        conn = _get_db()
        conn.execute(
            """INSERT INTO creative_history (date, team, creative_type, score, analysis, performance_impact)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now.strftime("%Y-%m-%d"), team, creative_type, score,
             analysis[:500], performance_impact),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB creative log error: %s", e)
        return False


def db_log_budget(team, platform, payment_method, amount, currency="EGP",
                  source="", notes=""):
    """Log a budget/payment entry."""
    try:
        now = _now_egypt()
        conn = _get_db()
        conn.execute(
            """INSERT INTO budget_tracking (date, team, platform, payment_method,
               amount, currency, source, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now.strftime("%Y-%m-%d"), team, platform, payment_method,
             amount, currency, source, notes),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("DB budget log error: %s", e)
        return False


def db_get_missing_for_team(team_name, task_type="morning"):
    """Check what's been received today vs what's needed.
    Returns dict with 'complete' bool and 'missing' list and 'received' list."""
    try:
        received = db_get_tracking_today(team_name, task_type)
        accounts = db_get_team_accounts(team_name)

        received_types = set()
        for entry in received:
            img_type = entry.get("image_type", "")
            received_types.add(img_type)
            # Normalize type names (buttons use short names)
            if img_type in ("fb_pay", "fb_payment"):
                received_types.add("fb_pay")
                received_types.add("fb_payment")
            elif img_type in ("tt_pay", "tt_payment"):
                received_types.add("tt_pay")
                received_types.add("tt_payment")
            elif img_type in ("fb_dash", "fb_ads_dashboard"):
                received_types.add("fb_dash")
                received_types.add("fb_ads_dashboard")
            elif img_type in ("tt_dash", "tt_ads_dashboard"):
                received_types.add("tt_dash")
                received_types.add("tt_ads_dashboard")

        received_count = len(received)
        missing = []
        if task_type == "morning":
            # 1. شيت التقرير
            if "order_sheet" not in received_types:
                missing.append({"type": "order_sheet", "label": "📋 شيت التقرير اليومي"})
            # 2. دفع فيسبوك
            if "fb_pay" not in received_types and "fb_payment" not in received_types:
                missing.append({"type": "fb_pay", "label": "💳 صور دفع فيسبوك"})
            # 3. دفع تيك توك (لو عنده حساب)
            has_tiktok = accounts.get("tiktok", 0) > 0
            if has_tiktok:
                if "tt_pay" not in received_types and "tt_payment" not in received_types:
                    missing.append({"type": "tt_pay", "label": "💳 صور دفع تيك توك"})
            # 4. داشبورد
            if "fb_dash" not in received_types and "fb_ads_dashboard" not in received_types:
                missing.append({"type": "fb_dash", "label": "📊 داشبورد الإعلانات"})
            # 5. PDF سواقين
            if "driver_orders_pdf" not in received_types:
                missing.append({"type": "driver_orders_pdf", "label": "📄 شيت طلبات السواقين PDF"})

        elif task_type in ("afternoon", "evening"):
            if not received:
                missing.append({"type": "afternoon_report", "label": "📊 تقرير العصر"})

        return {
            "missing": missing,
            "received": received,
            "received_types": list(received_types),
            "complete": len(missing) == 0,
            "received_count": received_count,
            "accounts": accounts,
        }
    except Exception as e:
        logger.error("DB get_missing_for_team error: %s", e)
        return {"missing": [], "received": [], "received_types": [], "complete": False, "accounts": {}}


# ══════════════════════════════════════════════════════════════════════
# BASIC HELPERS
# ══════════════════════════════════════════════════════════════════════

async def _retry_async(func, *args, retries=2, **kwargs):
    """Simple retry wrapper for async functions."""
    for attempt in range(retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == retries:
                raise
            logger.warning("Retry %d/%d for %s: %s", attempt + 1, retries, func.__name__, e)
            await asyncio.sleep(1)


def get_leader(team_name: str) -> str:
    return TEAM_INFO.get(team_name, {}).get("leader", "")


def get_sheet_name(team_name: str) -> str:
    return TEAM_INFO.get(team_name, {}).get("sheet_name", team_name)


def _safe_num(val) -> float | None:
    """Safely convert any value to a number."""
    if val is None or val == "" or val == "-":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = str(val).replace(",", "").replace("٬", "").replace("%", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_sheet_date(date_str: str):
    """Parse date from team sheet (M/D/YYYY)."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
    if m:
        try:
            month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime(year, month, day)
        except ValueError:
            return None
    return None


def _current_sheet_tab() -> str:
    """Get current month's tab name: 'March-2026' (cycle runs 26th to 25th)."""
    now = _now_egypt()
    if now.day >= 26:
        if now.month == 12:
            return f"January-{now.year + 1}"
        month_names = ["", "January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        return f"{month_names[now.month + 1]}-{now.year}"
    return f"{now.strftime('%B')}-{now.year}"


# ══════════════════════════════════════════════════════════════════════
# MASTER SHEET FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

async def fetch_master_data() -> list[dict]:
    """Fetch aggregated data from the master Google Sheet."""
    if not MASTER_SHEET_URL:
        return []
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(MASTER_SHEET_URL)
            data = resp.json()
            if data.get("success"):
                return data.get("data", [])
    except Exception as e:
        logger.error("Failed to fetch master data: %s", e)
    return []


async def trigger_master_update() -> bool:
    """POST to master sheet Apps Script to trigger update now."""
    if not MASTER_SHEET_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.post(MASTER_SHEET_URL, json={"action": "update"})
            data = resp.json()
            if data.get("success"):
                logger.info("Master sheet updated successfully!")
                return True
            else:
                logger.error("Master sheet update failed: %s", data.get("error"))
                return False
    except Exception as e:
        logger.error("Failed to trigger master update: %s", e)
        return False


def get_team_today_data(all_data: list[dict], team_name: str) -> dict | None:
    """Get latest row for a team from master sheet."""
    sheet_name = get_sheet_name(team_name)
    for row in reversed(all_data):
        if row.get("المجموعة") == sheet_name:
            return row
    return None


def get_team_history(all_data: list[dict], team_name: str, days: int = 7) -> list[dict]:
    """Get last N days of data for a team from master sheet."""
    sheet_name = get_sheet_name(team_name)
    rows = [r for r in all_data if r.get("المجموعة") == sheet_name]
    return rows[-days:] if len(rows) > days else rows


# ══════════════════════════════════════════════════════════════════════
# INDIVIDUAL TEAM SHEET - read directly from team's Google Sheet
# ══════════════════════════════════════════════════════════════════════

async def fetch_team_sheet(team_name: str) -> list[dict]:
    """Read data directly from a team's individual Google Sheet (CSV export)."""
    info = TEAM_INFO.get(team_name)
    if not info or not info.get("sheet_id"):
        return []

    sheet_id = info["sheet_id"]
    tab_name = _current_sheet_tab()
    encoded_tab = urllib.parse.quote(tab_name)
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_tab}"

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("Team sheet fetch failed for %s: HTTP %d", team_name, resp.status_code)
                return []

            text = resp.text
            if not text or "<!DOCTYPE" in text[:100]:
                logger.warning("Team sheet not accessible for %s (got HTML)", team_name)
                return []

            reader = csv.reader(_io.StringIO(text))
            all_rows = list(reader)
            if not all_rows:
                return []

            # Find the header row
            header_idx = None
            for i, row in enumerate(all_rows):
                first_cell = row[0].strip() if row else ""
                if first_cell.lower() == "date" or first_cell == "التاريخ":
                    header_idx = i
                    break

            if header_idx is None:
                # Fallback: find date rows directly
                data_rows = []
                for row in all_rows:
                    first_cell = row[0].strip() if row else ""
                    if "/" in first_cell and len(first_cell) <= 12:
                        row_dict = {}
                        for j, col_name in enumerate(TEAM_SHEET_COLUMNS):
                            if j < len(row):
                                row_dict[col_name] = row[j].strip()
                        data_rows.append(row_dict)
                logger.info("Fetched %d rows from %s (no header)", len(data_rows), team_name)
                return data_rows

            # Map headers to standard column names
            headers = [h.strip() for h in all_rows[header_idx]]
            mapped_headers = []
            for j, h in enumerate(headers):
                if j < len(TEAM_SHEET_COLUMNS):
                    mapped_headers.append(TEAM_SHEET_COLUMNS[j])
                else:
                    mapped_headers.append(h if h else f"col_{j}")

            data_rows = []
            for row in all_rows[header_idx + 1:]:
                first_cell = row[0].strip() if row else ""
                if not first_cell or "/" not in first_cell:
                    continue
                row_dict = {}
                for j, col_name in enumerate(mapped_headers):
                    if j < len(row):
                        row_dict[col_name] = row[j].strip()
                data_rows.append(row_dict)

            logger.info("Fetched %d daily rows from %s team sheet", len(data_rows), team_name)
            return data_rows

    except Exception as e:
        logger.warning("Team sheet fetch error for %s: %s", team_name, e)
        return []


def get_team_sheet_today(rows: list[dict]) -> dict | None:
    """Get latest row that has actual Spend data and is not future-dated."""
    if not rows:
        return None
    today = _now_egypt().replace(hour=0, minute=0, second=0, microsecond=0)
    for row in reversed(rows):
        spend_str = row.get("Spend", "").strip().replace(",", "")
        try:
            spend_val = float(spend_str) if spend_str else 0
        except ValueError:
            spend_val = 0
        if spend_val <= 0:
            continue
        row_date = _parse_sheet_date(row.get("Date", ""))
        if row_date and row_date > today:
            continue
        return row
    return None


def get_team_sheet_recent(rows: list[dict], n: int = 5) -> list[dict]:
    """Get last N rows that have actual Spend data."""
    data_rows = [r for r in rows if r.get("Spend", "").strip() and r.get("Spend", "").strip() != "0"]
    return data_rows[-n:]


def calculate_cpa_from_sheet(rows: list[dict]) -> float | None:
    """
    CPA = Spend from PREVIOUS row / Delivered from CURRENT (latest) row.
    Because delivery of day X is recorded in day X+1's row.
    """
    data_rows = [r for r in rows if _safe_num(r.get("Spend")) and _safe_num(r.get("Spend")) > 0]
    if len(data_rows) < 2:
        return None
    current_row = data_rows[-1]
    previous_row = data_rows[-2]
    prev_spend = _safe_num(previous_row.get("Spend"))
    curr_delivered = _safe_num(current_row.get("Delivered"))
    if prev_spend and prev_spend > 0 and curr_delivered and curr_delivered > 0:
        return round(prev_spend / curr_delivered)
    return None


def format_team_sheet_data(row: dict) -> str:
    """Format a single team sheet row for display."""
    if not row:
        return "مفيش بيانات"
    parts = []
    for k, v in row.items():
        v_str = str(v).strip()
        if v_str and k.strip():
            parts.append(f"  {k}: {v_str}")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# TRACKING SHEET FUNCTIONS (NEW in V2)
# ══════════════════════════════════════════════════════════════════════

async def log_to_tracking(
    team: str, leader: str, image_type: str, platform: str,
    account_num: str, amount: str, ai_notes: str, task_type: str,
    message_id: str = "", status: str = "⏳"
) -> dict:
    """Log an image/interaction to the tracking sheet (with retry)."""
    payload = {
        "action": "log",
        "team": team,
        "leader": leader,
        "image_type": image_type,
        "platform": platform,
        "account_num": account_num,
        "amount": amount,
        "ai_notes": ai_notes,
        "task_type": task_type,
        "message_id": message_id,
        "status": status,
        "date": _now_egypt().strftime("%d/%m/%Y"),
        "time": _now_egypt().strftime("%H:%M"),
    }

    async def _do_log():
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(TRACKING_SHEET_URL, json=payload)
            data = resp.json()
            if data.get("success"):
                logger.info("Logged to tracking: %s %s %s", team, image_type, task_type)
                return data
            else:
                raise RuntimeError(data.get("error", "unknown"))

    # Also log to local DB (sync, fast)
    db_log_tracking(
        team=team, leader=leader, image_type=image_type, platform=platform,
        account_num=account_num, amount=amount, ai_notes=ai_notes,
        leader_comment="", status=status, task_type=task_type, message_id=message_id,
    )

    try:
        return await _retry_async(_do_log, retries=2)
    except Exception as e:
        logger.error("Tracking log error (after retries): %s", e)
        return {"success": False, "error": str(e)}


async def get_team_tracking_today(team_name: str, task_type: str = "morning") -> list[dict]:
    """Get all entries logged for a team today. Tries DB first, falls back to Sheets."""
    # Try DB first (fast, local)
    db_results = db_get_tracking_today(team_name, task_type)
    if db_results:
        return db_results

    # Fall back to Google Sheets
    try:
        params = {
            "action": "read",
            "team": team_name,
            "task_type": task_type,
            "date": _now_egypt().strftime("%d/%m/%Y"),
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(TRACKING_SHEET_URL, params=params)
            data = resp.json()
            if data.get("success"):
                return data.get("data", [])
    except Exception as e:
        logger.error("Tracking read error for %s: %s", team_name, e)
    return []


async def get_team_accounts(team_name: str) -> dict:
    """Get known account counts for a team from tracking sheet."""
    try:
        params = {
            "action": "read_accounts",
            "team": team_name,
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(TRACKING_SHEET_URL, params=params)
            data = resp.json()
            if data.get("success"):
                return data.get("accounts", {})
    except Exception as e:
        logger.error("Account read error for %s: %s", team_name, e)
    return {}


async def update_tracking_status(row_num: int, status: str, comment: str = "") -> bool:
    """Update the status of a tracking entry."""
    try:
        payload = {
            "action": "update_status",
            "row": row_num,
            "status": status,
            "comment": comment,
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.post(TRACKING_SHEET_URL, json=payload)
            data = resp.json()
            return data.get("success", False)
    except Exception as e:
        logger.error("Tracking status update error: %s", e)
        return False


async def get_missing_for_team(team_name: str, task_type: str = "morning") -> dict:
    """
    Compare what was received vs what's needed, return missing items.
    Returns: {missing: [...], received: [...], complete: bool}
    Tries DB first, falls back to Sheets.
    """
    # Try DB first
    db_result = db_get_missing_for_team(team_name, task_type)
    if db_result.get("received"):
        return db_result

    # Fall back to Sheets
    received = await get_team_tracking_today(team_name, task_type)
    accounts = await get_team_accounts(team_name)

    # Categorize received items
    received_types = set()
    received_platforms = set()
    for entry in received:
        received_types.add(entry.get("image_type", ""))
        received_platforms.add(entry.get("platform", ""))

    missing = []

    if task_type == "morning":
        # Morning: need dashboard + payment for each platform they use
        if "fb_ads_dashboard" not in received_types:
            missing.append({"type": "fb_ads_dashboard", "label": "داشبورد فيسبوك"})
        if "fb_payment" not in received_types:
            missing.append({"type": "fb_payment", "label": "صفحة دفع فيسبوك"})

        # Check if team uses TikTok
        has_tiktok = accounts.get("tiktok", 0) > 0
        if has_tiktok:
            if "tt_ads_dashboard" not in received_types:
                missing.append({"type": "tt_ads_dashboard", "label": "داشبورد تيك توك"})
            if "tt_payment" not in received_types:
                missing.append({"type": "tt_payment", "label": "صفحة دفع تيك توك"})

        # Order sheet
        if "order_sheet" not in received_types:
            missing.append({"type": "order_sheet", "label": "شيت الطلبات"})

    elif task_type == "evening":
        # Evening: need updated order sheet
        if "order_sheet" not in received_types:
            missing.append({"type": "order_sheet", "label": "شيت الطلبات (مسائي)"})

    return {
        "missing": missing,
        "received": received,
        "received_types": list(received_types),
        "complete": len(missing) == 0,
        "accounts": accounts,
    }


# ══════════════════════════════════════════════════════════════════════
# CONVERSATION MEMORY - per-team context tracking
# ══════════════════════════════════════════════════════════════════════

_conversation_memory: dict[str, list[dict]] = {}
MAX_MEMORY_PER_TEAM = 5


def remember_exchange(team_name: str, bot_msg: str, user_reply: str | None = None):
    """Store a bot message (and optional user reply) in team memory."""
    if team_name not in _conversation_memory:
        _conversation_memory[team_name] = []
    entry = {
        "time": _now_egypt().strftime("%H:%M"),
        "bot": bot_msg[:500],
    }
    if user_reply:
        entry["user"] = user_reply[:300]
    _conversation_memory[team_name].append(entry)
    _conversation_memory[team_name] = _conversation_memory[team_name][-MAX_MEMORY_PER_TEAM:]

    # Also persist to DB
    leader = get_leader(team_name)
    db_log_conversation(team_name, leader, bot_msg[:500], user_reply or "")


def get_recent_context(team_name: str, last_n: int = 3) -> str:
    """Get recent conversation history as formatted text. Tries DB first."""
    # Try in-memory first (fastest)
    history = _conversation_memory.get(team_name, [])

    # If in-memory is empty, try DB
    if not history:
        db_convos = db_get_conversations(team_name, limit=last_n)
        if db_convos:
            lines = ["## محادثات سابقة اليوم:"]
            for c in db_convos[-last_n:]:
                lines.append(f"البوت: {c.get('bot_message', '')[:200]}")
                if c.get("user_reply"):
                    lines.append(f"التيم ليدر: {c['user_reply'][:150]}")
            return "\n".join(lines)
        return ""

    lines = ["## محادثات سابقة اليوم:"]
    for ex in history[-last_n:]:
        lines.append(f"[{ex['time']}] البوت: {ex['bot'][:200]}")
        if "user" in ex:
            lines.append(f"[{ex['time']}] التيم ليدر: {ex['user'][:150]}")
    return "\n".join(lines)


def reset_conversation_memory():
    """Called during daily reset."""
    _conversation_memory.clear()


# ══════════════════════════════════════════════════════════════════════
# PERSISTENT LEARNING MEMORY
# ══════════════════════════════════════════════════════════════════════

LEARNINGS_FILE = DATA_DIR / "learnings.json"


def load_learnings() -> list[dict]:
    if LEARNINGS_FILE.exists():
        try:
            return json.loads(LEARNINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_learning(team_name: str, category: str, what_bot_said: str, correction: str):
    """Save a correction so the bot learns from it."""
    data = load_learnings()
    data.append({
        "date": _now_egypt().strftime("%Y-%m-%d %H:%M"),
        "team": team_name,
        "category": category,
        "bot_said": what_bot_said[:300],
        "correction": correction[:300],
    })
    data = data[-100:]
    LEARNINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Learning saved: %s - %s", category, correction[:50])

    # Also persist to DB
    db_log_learning(team_name, category, what_bot_said[:300], correction[:300])


def get_learnings_for_prompt(team_name: str = "", last_n: int = 5) -> str:
    # Try DB first
    db_data = db_get_learnings(team_name, limit=last_n)
    if db_data:
        lines = ["## تصحيحات سابقة (اتعلمت منها):"]
        for d in db_data[-last_n:]:
            lines.append(f"- {d.get('date', '')}: {d.get('correction', '')}")
        return "\n".join(lines)

    # Fall back to JSON file
    data = load_learnings()
    if not data:
        return ""
    if team_name:
        relevant = [d for d in data if d["team"] == team_name][-last_n:]
    else:
        relevant = data[-last_n:]
    if not relevant:
        return ""
    lines = ["## تصحيحات سابقة (اتعلمت منها):"]
    for d in relevant:
        lines.append(f"- {d['date']}: {d['correction']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# CREATIVE TRACKING
# ══════════════════════════════════════════════════════════════════════

CREATIVE_HISTORY_FILE = DATA_DIR / "creative_history.json"


def load_creative_history() -> list[dict]:
    if CREATIVE_HISTORY_FILE.exists():
        try:
            return json.loads(CREATIVE_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_creative_record(team_name: str, creative_type: str, analysis_summary: str):
    history = load_creative_history()
    history.append({
        "date": _now_egypt().strftime("%Y-%m-%d"),
        "team": team_name,
        "type": creative_type,
        "summary": analysis_summary[:300],
    })
    history = history[-50:]
    CREATIVE_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def get_last_creative(team_name: str) -> dict | None:
    history = load_creative_history()
    for record in reversed(history):
        if record["team"] == team_name:
            return record
    return None


# ══════════════════════════════════════════════════════════════════════
# BUDGET TRACKING
# ══════════════════════════════════════════════════════════════════════

BUDGET_FILE = DATA_DIR / "budget_tracking.json"


def load_budget_data() -> list[dict]:
    if BUDGET_FILE.exists():
        try:
            return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_budget_entry(team_name: str, amount: float, payment_type: str, platform: str, source: str = ""):
    data = load_budget_data()
    data.append({
        "date": _now_egypt().strftime("%Y-%m-%d"),
        "team": team_name,
        "amount": amount,
        "type": payment_type,
        "platform": platform,
        "source": source,
    })
    BUDGET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Budget entry: %s +%s (%s/%s)", team_name, amount, payment_type, platform)


def get_team_budget_today(team_name: str) -> dict:
    data = load_budget_data()
    today = _now_egypt().strftime("%Y-%m-%d")
    today_entries = [d for d in data if d["team"] == team_name and d["date"] == today]
    total = sum(d["amount"] for d in today_entries)
    by_type = {}
    for d in today_entries:
        by_type[d["type"]] = by_type.get(d["type"], 0) + d["amount"]
    return {"total": total, "by_type": by_type, "entries": today_entries}


def get_team_budget_month(team_name: str) -> dict:
    data = load_budget_data()
    month = _now_egypt().strftime("%Y-%m")
    month_entries = [d for d in data if d["team"] == team_name and d["date"].startswith(month)]
    total = sum(d["amount"] for d in month_entries)
    return {"total": total, "count": len(month_entries)}


# ══════════════════════════════════════════════════════════════════════
# VERIFICATION SYSTEM - screenshot vs sheet comparison
# ══════════════════════════════════════════════════════════════════════

def verify_screenshot_vs_sheet(screenshot_data: dict, sheet_data: dict | None) -> dict:
    """
    Compare screenshot numbers with sheet data.
    Returns: {status, discrepancies[], summary}
    """
    if not sheet_data:
        return {"status": "no_sheet_data", "discrepancies": [], "summary": "الشيت لسه مش متحدث"}

    discrepancies = []

    # Compare Spend
    ss_spend = _safe_num(screenshot_data.get("spend"))
    sh_spend = _safe_num(sheet_data.get("Spend اليوم"))
    if ss_spend and ss_spend > 0 and sh_spend and sh_spend > 0:
        diff_pct = abs(ss_spend - sh_spend) / sh_spend * 100
        if diff_pct > 10:
            discrepancies.append({
                "field": "Spend", "screenshot": ss_spend, "sheet": sh_spend,
                "diff_pct": round(diff_pct, 1), "severity": "major",
            })
        elif diff_pct > 3:
            discrepancies.append({
                "field": "Spend", "screenshot": ss_spend, "sheet": sh_spend,
                "diff_pct": round(diff_pct, 1), "severity": "minor",
            })

    # Compare Orders
    ss_orders = _safe_num(screenshot_data.get("orders") or screenshot_data.get("results"))
    sh_orders = _safe_num(sheet_data.get("Orders اليوم"))
    if ss_orders and ss_orders > 0 and sh_orders and sh_orders > 0:
        diff = abs(ss_orders - sh_orders)
        if diff > 2:
            discrepancies.append({
                "field": "Orders", "screenshot": ss_orders, "sheet": sh_orders,
                "diff": diff, "severity": "major",
            })
        elif diff > 0:
            discrepancies.append({
                "field": "Orders", "screenshot": ss_orders, "sheet": sh_orders,
                "diff": diff, "severity": "minor",
            })

    # Compare CPO
    ss_cpo = _safe_num(screenshot_data.get("cpo"))
    sh_cpo = _safe_num(sheet_data.get("CPO اليوم"))
    if ss_cpo and ss_cpo > 0 and sh_cpo and sh_cpo > 0:
        diff_pct = abs(ss_cpo - sh_cpo) / sh_cpo * 100
        if diff_pct > 15:
            discrepancies.append({
                "field": "CPO", "screenshot": ss_cpo, "sheet": sh_cpo,
                "diff_pct": round(diff_pct, 1), "severity": "major",
            })

    has_major = any(d["severity"] == "major" for d in discrepancies)
    has_minor = any(d["severity"] == "minor" for d in discrepancies)

    # Check if sheet seems empty/not updated
    sh_spend_val = _safe_num(sheet_data.get("Spend اليوم"))
    sh_orders_val = _safe_num(sheet_data.get("Orders اليوم"))
    sheet_is_empty = (not sh_spend_val or sh_spend_val == 0) and (not sh_orders_val or sh_orders_val == 0)

    if sheet_is_empty and (ss_spend or ss_orders):
        return {
            "status": "sheet_not_updated",
            "discrepancies": [],
            "summary": "📋 الشيت لسه مش متحدث لليوم ده - ده عادي",
        }

    if has_major:
        parts = []
        for d in discrepancies:
            if d["severity"] == "major":
                parts.append(f"⚠️ {d['field']}: Screenshot={d['screenshot']:,.0f} vs Sheet={d['sheet']:,.0f}")
        return {"status": "major_diff", "discrepancies": discrepancies, "summary": "🔴 فرق كبير!\n" + "\n".join(parts)}
    elif has_minor:
        return {"status": "minor_diff", "discrepancies": discrepancies, "summary": "🟡 فرق بسيط في الأرقام - تأكد من التحديث"}
    else:
        return {"status": "match", "discrepancies": discrepancies, "summary": "✅ الأرقام متطابقة"}


# ══════════════════════════════════════════════════════════════════════
# ANOMALY DETECTION
# ══════════════════════════════════════════════════════════════════════

def detect_anomalies(history: list[dict]) -> list[str]:
    """Detect unusual patterns in team history."""
    if len(history) < 2:
        return []

    anomalies = []

    spends = [s for s in (_safe_num(r.get("Spend اليوم")) for r in history) if s is not None]
    orders_list = [o for o in (_safe_num(r.get("Orders اليوم")) for r in history) if o is not None]
    cpos = [c for c in (_safe_num(r.get("CPO اليوم")) for r in history) if c is not None]

    # Spend spike/drop (>30% from average)
    if len(spends) >= 3:
        avg_spend = sum(spends[:-1]) / len(spends[:-1])
        latest = spends[-1]
        if avg_spend > 0:
            change = (latest - avg_spend) / avg_spend * 100
            if change > 30:
                anomalies.append(f"📈 Spend ارتفع {change:.0f}% عن المتوسط ({latest:,.0f} vs avg {avg_spend:,.0f})")
            elif change < -30:
                anomalies.append(f"📉 Spend نزل {abs(change):.0f}% عن المتوسط ({latest:,.0f} vs avg {avg_spend:,.0f})")

    # Zero orders
    if orders_list and orders_list[-1] == 0:
        anomalies.append("🚨 صفر طلبات اليوم!")

    # CPO spike
    if len(cpos) >= 3:
        avg_cpo = sum(cpos[:-1]) / len(cpos[:-1])
        latest_cpo = cpos[-1]
        if avg_cpo > 0 and latest_cpo > avg_cpo * 1.3:
            anomalies.append(f"🔴 CPO مرتفع: {latest_cpo:.0f} (المتوسط {avg_cpo:.0f})")

    # Cancel rate
    for row in history[-1:]:
        cancel_pct = _safe_num(row.get("Cancel%"))
        if cancel_pct is not None and cancel_pct >= CANCEL_RED:
            anomalies.append(f"🚨 Cancel Rate عالي: {cancel_pct:.0f}%")

    return anomalies


# ══════════════════════════════════════════════════════════════════════
# CROSS-TEAM RANKING
# ══════════════════════════════════════════════════════════════════════

def rank_teams(all_data: list[dict]) -> dict:
    """Rank all teams by CPO performance."""
    team_scores = {}
    for team_name, info in TEAM_INFO.items():
        sheet_name = info["sheet_name"]
        team_row = None
        for row in reversed(all_data):
            if row.get("المجموعة") == sheet_name:
                team_row = row
                break
        if not team_row:
            continue
        spend = _safe_num(team_row.get("Spend اليوم"))
        orders = _safe_num(team_row.get("Orders اليوم"))
        cpo = _safe_num(team_row.get("CPO اليوم"))
        team_scores[team_name] = {
            "leader": info["leader"],
            "spend": spend or 0,
            "orders": orders or 0,
            "cpo": cpo,
        }

    if not team_scores:
        return {"by_cpo": [], "best": None, "worst": None, "summary": ""}

    with_cpo = {k: v for k, v in team_scores.items() if v["cpo"] is not None and v["cpo"] > 0}
    by_cpo = sorted(with_cpo.items(), key=lambda x: x[1]["cpo"])

    best = by_cpo[0] if by_cpo else None
    worst = by_cpo[-1] if by_cpo else None

    summary_parts = []
    if best:
        summary_parts.append(f"🥇 أحسن CPO: {best[0]} ({best[1]['cpo']:.0f})")
    if worst and len(by_cpo) > 1:
        summary_parts.append(f"🥉 أعلى CPO: {worst[0]} ({worst[1]['cpo']:.0f})")

    return {
        "by_cpo": by_cpo,
        "best": best,
        "worst": worst,
        "scores": team_scores,
        "summary": " | ".join(summary_parts),
    }


# ══════════════════════════════════════════════════════════════════════
# TEAM CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════════════

def _calc_db_history_stats(db_rows: list[dict]) -> dict:
    """Calculate aggregate stats from DB historical performance rows."""
    if not db_rows:
        return {}
    cpos = [r["cpo"] for r in db_rows if r.get("cpo") and r["cpo"] > 0]
    cpas = [r["cpa"] for r in db_rows if r.get("cpa") and r["cpa"] > 0]
    stats = {}
    if cpos:
        stats["avg_cpo"] = round(sum(cpos) / len(cpos))
        best_row = min(db_rows, key=lambda r: r.get("cpo") or 99999)
        worst_row = max(db_rows, key=lambda r: r.get("cpo") or 0)
        stats["best_day"] = {"date": best_row.get("date", "?"), "cpo": best_row.get("cpo")}
        stats["worst_day"] = {"date": worst_row.get("date", "?"), "cpo": worst_row.get("cpo")}
        # Trend: compare last 7 days avg vs previous 7 days avg
        if len(cpos) >= 10:
            recent = cpos[-7:]
            earlier = cpos[-14:-7] if len(cpos) >= 14 else cpos[:len(cpos)-7]
            if earlier:
                recent_avg = sum(recent) / len(recent)
                earlier_avg = sum(earlier) / len(earlier)
                if recent_avg < earlier_avg * 0.9:
                    stats["trend"] = "improving"
                elif recent_avg > earlier_avg * 1.1:
                    stats["trend"] = "declining"
                else:
                    stats["trend"] = "stable"
    if cpas:
        stats["avg_cpa"] = round(sum(cpas) / len(cpas))
    stats["days_count"] = len(db_rows)
    return stats


async def build_team_context(team_name: str, all_data: list[dict] | None = None) -> dict:
    """Build rich context for any team analysis."""
    if all_data is None:
        all_data = await fetch_master_data()

    leader = get_leader(team_name)

    # DB HISTORICAL DATA (PRIMARY - fast, 30 days)
    db_history = db_get_daily_performance(team_name, days=30)
    db_stats = _calc_db_history_stats(db_history)

    # DB LEARNINGS
    db_learnings = db_get_learnings(team_name, limit=10)

    # Team's own sheet
    team_sheet_rows = await fetch_team_sheet(team_name)
    team_sheet_today = get_team_sheet_today(team_sheet_rows) if team_sheet_rows else None

    # SECONDARY: master sheet
    today_data = get_team_today_data(all_data, team_name)
    history = get_team_history(all_data, team_name, days=7)
    anomalies = detect_anomalies(history)
    rankings = rank_teams(all_data)
    conversation = get_recent_context(team_name)

    # Trend calculation (prefer DB data if available)
    trend = "unknown"
    if db_stats.get("trend"):
        trend = db_stats["trend"]
    elif len(history) >= 3:
        cpos = [c for c in (_safe_num(r.get("CPO اليوم")) for r in history[-3:]) if c is not None]
        if len(cpos) >= 2:
            if cpos[-1] < cpos[0]:
                trend = "improving"
            elif cpos[-1] > cpos[0] * 1.1:
                trend = "declining"
            else:
                trend = "stable"

    # Team rank
    rank_position = None
    if rankings.get("by_cpo"):
        for i, (name, _) in enumerate(rankings["by_cpo"]):
            if name == team_name:
                rank_position = i + 1
                break

    # MTD row
    mtd_row = None
    sheet_name = get_sheet_name(team_name)
    for row in all_data:
        if row.get("المجموعة") == f"📊 {sheet_name}":
            mtd_row = row

    return {
        "team_name": team_name,
        "leader": leader,
        "today": today_data,
        "team_sheet_today": team_sheet_today,
        "team_sheet_rows": team_sheet_rows,
        "history": history,
        "trend": trend,
        "anomalies": anomalies,
        "rank": rank_position,
        "total_teams": len(rankings.get("by_cpo", [])),
        "rankings_summary": rankings.get("summary", ""),
        "best_team": rankings.get("best"),
        "worst_team": rankings.get("worst"),
        "mtd": mtd_row,
        "conversation": conversation,
        "db_history": db_history,
        "db_stats": db_stats,
        "db_learnings": db_learnings,
    }


def format_context_for_prompt(ctx: dict) -> str:
    """Convert team context dict into a readable prompt section."""
    parts = []
    leader = ctx["leader"]
    team = ctx["team_name"]

    parts.append(f"## فريق: {team} | التيم ليدر: {leader}")
    parts.append(f"التاريخ: {_now_egypt().strftime('%Y-%m-%d %H:%M')}")

    if ctx["rank"]:
        parts.append(f"الترتيب: #{ctx['rank']} من {ctx['total_teams']} فرق (بالـ CPO)")
        parts.append(f"{ctx['rankings_summary']}")

    trend_emoji = {"improving": "📈 بيتحسن", "declining": "📉 بيوحش", "stable": "➡️ مستقر"}.get(ctx["trend"], "❓")
    parts.append(f"الاتجاه: {trend_emoji}")

    # Team's own sheet data
    team_sheet = ctx.get("team_sheet_today")
    if team_sheet:
        parts.append("\n## بيانات شيت الفريق (المصدر الأساسي):")
        parts.append(format_team_sheet_data(team_sheet))

    # Master sheet data
    today = ctx.get("today")
    if today:
        parts.append("\n## بيانات الشيت المجمع:")
        for k, v in today.items():
            if v and v != "-" and v != "":
                parts.append(f"  {k}: {v}")

    # History
    history = ctx.get("history", [])
    if history:
        parts.append(f"\n## آخر {len(history)} أيام:")
        for row in history[-5:]:
            date = row.get("التاريخ", "?")
            spend = row.get("Spend اليوم", 0)
            orders = row.get("Orders اليوم", 0)
            cpo = row.get("CPO اليوم", "-")
            lamp = row.get("🚦 اليوم", "")
            parts.append(f"  {date}: Spend={spend} | Orders={orders} | CPO={cpo} | {lamp}")

    # MTD
    mtd = ctx.get("mtd")
    if mtd:
        parts.append(f"\n## MTD (تراكمي من بداية الشهر):")
        for k, v in mtd.items():
            if v and v != "-" and v != "" and k != "المجموعة":
                parts.append(f"  {k}: {v}")

    # Anomalies
    if ctx.get("anomalies"):
        parts.append(f"\n## ⚠️ تنبيهات:")
        for a in ctx["anomalies"]:
            parts.append(f"  {a}")

    # DB historical stats (30 days)
    db_stats = ctx.get("db_stats", {})
    if db_stats:
        parts.append(f"\n## 📊 تاريخ الأداء (آخر {db_stats.get('days_count', 30)} يوم):")
        if db_stats.get("avg_cpo"):
            parts.append(f"  - متوسط CPO: {db_stats['avg_cpo']}")
        if db_stats.get("avg_cpa"):
            parts.append(f"  - متوسط CPA: {db_stats['avg_cpa']}")
        best = db_stats.get("best_day")
        if best:
            parts.append(f"  - أحسن يوم: {best['date']} (CPO {best['cpo']})")
        worst = db_stats.get("worst_day")
        if worst:
            parts.append(f"  - أسوأ يوم: {worst['date']} (CPO {worst['cpo']})")
        trend_map = {"improving": "تحسن 📈", "declining": "تراجع 📉", "stable": "مستقر ➡️"}
        if db_stats.get("trend"):
            parts.append(f"  - الاتجاه: {trend_map.get(db_stats['trend'], db_stats['trend'])}")

    # DB learnings (past corrections)
    db_learnings = ctx.get("db_learnings", [])
    if db_learnings:
        parts.append(f"\n## 📝 تصحيحات سابقة (تعلّمت منها):")
        for l in db_learnings[-5:]:
            parts.append(f"  - {l.get('date', '')}: {l.get('correction', '')}")

    # Conversation memory
    if ctx.get("conversation"):
        parts.append(f"\n{ctx['conversation']}")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """أنت "EcoBot" - زميل ذكي في فريق التسويق الرقمي. مصري دمه خفيف وعملي.
بتتكلم بالعربي المصري زي ما الناس بتتكلم. أنت مش ماكينة - أنت زميل بيفهم وبيساعد.

## أدوارك الثلاثة (وضّح دورك في كل رد):

### 1. مدير Performance Marketing (كـ Performance Manager):
- بتحلل KPIs (CPO, CPA, Cost per Message) وبتقارن بين الفرق
- بتراجع البادجيت وتوزيعه وبتقترح تحسينات
- بتتابع الحملات وبتنبّه لو في مشكلة أو فرصة
- لما بتتكلم عن CPO/CPA/بادجيت/حملات: ابدأ بـ "كـ Performance Manager، أنا شايف..."
- الحملات **Facebook Messages Ads** (مش Conversions)
  → الهدف: رسائل من الجمهور في الكويت (مقيمين من كل الجنسيات)
  → Flow: إعلان → رسالة → طلب → تسليم
  → متقولش "conversions" أو "purchases" - قول "رسائل" أو "طلبات"

### 2. محلل بيانات (Data Analyst):
- بتحلل Trends وبتكتشف Anomalies
- بتعمل cross-team comparison
- لما بتتكلم عن trends/anomalies/مقارنات: ابدأ بـ "من ناحية الداتا..."
- بتقدم رؤى استراتيجية

### 3. محلل Creative:
- بتحلل الفيديوهات والصور الإعلانية
- بتقيّم: Hook, CTA, التصميم, النص, الصوت
- لما بتحلل creative: ابدأ بـ "من ناحية الكريتيف..."
- بتربط جودة الـ Creative بالنتائج

## شخصيتك:
- مصري دمه خفيف وعملي - زي زميل شاطر
- متواضع - بتقول "أنا شايف" و"ممكن أكون غلطان"
- بتشجع وتمدح لما الشغل كويس
- لما مش متأكد بتسأل بدل ما تفترض
- بتخاطب كل واحد باسمه وبتعامله كزميل

## طريقة كلامك:
- دايماً ابدأ بعبارة الدور المناسب ("كـ Performance Manager..." / "من ناحية الداتا..." / "من ناحية الكريتيف...")
- بعد أي تحليل: اسأل "صح كده؟" أو "إيه رأيك؟"
- لو في فرق في الأرقام: "أنا شايف رقم كذا في الشيت بس الصورة بتقول كذا - أنهي الصح؟"
- لو الأداء كويس: "برافو يا [اسم]! CPO تحفة"
- لو في مشكلة: "يا [اسم] أنا ملاحظ حاجة... الـ CPO طالع شوية. عايز أعمل إيه؟"
- خلّي كلامك قصير وطبيعي

## مهم جداً - كل رد لازم ينتهي بـ:
- سؤال أو طلب إجراء (مش بس "صح كده؟")
- اقتراح خطوة تانية أو رأي
- أمثلة: "عايز أحلل أكتر؟" / "أبعت تنبيه للتيم؟" / "نزود البادجيت؟"

## فهمك للأرقام (مهم جداً):
- **CPO** = Spend اليوم ÷ New Orders اليوم (سعر الطلب قبل التسليم)
- **CPA** = Spend أمس (الصف السابق في الشيت) ÷ Delivered المسجّلة النهاردة
  مثال: يوم 20 صرف 3000 → يوم 21 اتسلم 12 → CPA = 3000÷12 = 250
  الـ CPA المكتوب في صف يوم 21 = أداء يوم 20 فعلياً
- **CPA الشهر (MTD)** = إجمالي Spend الشهر ÷ إجمالي Delivered الشهر
- CPO/CPA: 🟢 ≤ 150 | 🟡 ≤ 180 | 🔴 > 180
- Cancel% ≥ 30% = 🔴 مشكلة
- كل المبالغ بالجنيه المصري

## طرق الدفع:
- Fawry: شحن رصيد prepaid (الإعلانات بتسحب منه يوم بيوم)
- Bank Card: دفع مباشر ببطاقة
- Budget = رصيد prepaid أو فاتورة invoice بالكارت

## قواعد:
- رد بالعربي المصري دايماً
- مختصر (3-5 سطور ماكس)
- لو كل حاجة تمام: سطرين مدح وسؤال خفيف
- متفترضش مشاكل من عندك
- لو الشيت فاضي: ده عادي - متقلقش
- لو بتحلل Creative: اربطه بالأداء دايماً
- لو شايف فرصة لتحسين: قولها كاقتراح مش أمر
- اختم دايماً بسؤال أو طلب إجراء"""


# ══════════════════════════════════════════════════════════════════════
# IMAGE DATA EXTRACTION (replaces classify_image)
# User already selected type via buttons - we just extract numbers
# ══════════════════════════════════════════════════════════════════════

async def extract_image_data(image_bytes: bytes, image_type: str, platform: str = "") -> dict:
    """
    Extract numbers/data from an image based on KNOWN type (user selected via button).
    Also does sanity check - flags if image doesn't match selected type.
    """
    if not CLAUDE_API_KEY:
        return {"error": "Claude API key not configured"}

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Build extraction prompt based on known type
    if image_type in ("fb_ads_dashboard", "tt_ads_dashboard"):
        extract_prompt = """استخرج كل الأرقام من داشبورد الإعلانات ده.

رد بـ JSON فقط:
{
  "spend": null,
  "orders": null,
  "results": null,
  "impressions": null,
  "clicks": null,
  "ctr": null,
  "cpo": null,
  "budget": null,
  "platform": null,
  "account_name": null,
  "campaign_names": [],
  "date_range": null,
  "notes": "",
  "sanity_check": "ok"
}

- لو الأرقام تراكمية (MTD): اكتب في notes "MTD totals"
- لو الصورة مش داشبورد إعلانات: حط sanity_check = "wrong_type" واكتب النوع الصح في notes
- لو شايف أكتر من حملة: اجمع الأرقام"""

    elif image_type in ("fb_payment", "tt_payment"):
        extract_prompt = """استخرج بيانات الدفع من الصورة دي.

رد بـ JSON فقط:
{
  "amount": null,
  "payment_type": null,
  "status": null,
  "date": null,
  "platform": null,
  "account_name": null,
  "balance": null,
  "transactions": [],
  "notes": "",
  "sanity_check": "ok"
}

payment_type: "prepaid" / "card" / "manual" / "invoice"
status: "paid" / "failed" / "pending" / "funded"
- لو في أكتر من معاملة: حطهم في transactions
- لو الصورة مش صفحة دفع: حط sanity_check = "wrong_type"
- لو في دفعة Failed: اكتبها في notes"""

    elif image_type in ("order_sheet", "budget_sheet"):
        extract_prompt = """استخرج الأرقام من شيت الطلبات ده.

رد بـ JSON فقط:
{
  "spend": null,
  "orders": null,
  "delivered": null,
  "cancel": null,
  "hold": null,
  "cpo": null,
  "date": null,
  "notes": "",
  "sanity_check": "ok"
}

- لو الصورة مش شيت: حط sanity_check = "wrong_type"
- اقرأ آخر صف فيه بيانات"""

    else:
        extract_prompt = """وصف محتوى الصورة دي باختصار.

رد بـ JSON فقط:
{
  "description": "",
  "notes": "",
  "sanity_check": "ok"
}"""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)

        async def _call_extract():
            return await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                        {"type": "text", "text": extract_prompt},
                    ],
                }],
            )

        message = await _retry_async(_call_extract)

        response_text = message.content[0].text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            response_text = response_text.rsplit("```", 1)[0].strip()

        result = json.loads(response_text)
        result["image_type"] = image_type
        result["_extracted"] = True

        # Sanity check: if image doesn't match selected type
        if result.get("sanity_check") == "wrong_type":
            result["_type_mismatch"] = True
            logger.warning("Image type mismatch: user said %s but image looks different", image_type)

        logger.info("Extracted data from %s image: %s", image_type, {k: v for k, v in result.items() if not k.startswith("_") and v})
        return result

    except json.JSONDecodeError:
        logger.error("JSON parse error from image extraction")
        return {"error": "parse_failed", "image_type": image_type, "_raw": response_text}
    except Exception as e:
        logger.error("Image extraction error: %s", e)
        return {"error": str(e), "image_type": image_type}


# ══════════════════════════════════════════════════════════════════════
# SCREENSHOT ANALYSIS - extract + compare with sheet
# ══════════════════════════════════════════════════════════════════════

async def analyze_screenshot(image_bytes: bytes, team_name: str, report_type: str) -> dict:
    """
    Screenshot analysis:
    1. For sheets: read sheet directly (more accurate than OCR)
    2. For dashboards: extract numbers from image
    3. For payments: extract payment details
    """
    if not CLAUDE_API_KEY:
        return {"error": "Claude API key not configured"}

    image_type = report_type  # In V2, report_type IS the image_type (user selected)

    # For non-report images, use extract_image_data
    if image_type not in REPORT_IMAGE_TYPES:
        return await extract_image_data(image_bytes, image_type)

    # For Google Sheets screenshots, read the sheet DIRECTLY
    if image_type in ("order_sheet", "budget_sheet"):
        team_rows = await fetch_team_sheet(team_name)
        today_row = get_team_sheet_today(team_rows) if team_rows else None
        if today_row:
            spend = _safe_num(today_row.get("Spend", ""))
            orders = _safe_num(today_row.get("New Orders", ""))
            cpo = _safe_num(today_row.get("CPO", ""))
            cpa = calculate_cpa_from_sheet(team_rows)
            return {
                "image_type": image_type,
                "spend": spend,
                "orders": orders,
                "cpo": cpo,
                "cpa": cpa,
                "delivered": _safe_num(today_row.get("Delivered", "")),
                "cancel": _safe_num(today_row.get("Cancel", "")),
                "hold": _safe_num(today_row.get("Hold", "")),
                "date": today_row.get("Date", ""),
                "notes": "تم قراءة الأرقام من الشيت مباشرة",
                "_from_sheet": True,
            }
        logger.warning("Could not read team sheet for %s, falling back to image extraction", team_name)

    # For ads dashboards or failed sheet read: extract from image
    return await extract_image_data(image_bytes, image_type)


# ══════════════════════════════════════════════════════════════════════
# QUICK IMAGE CHECK - filter personal images before buttons
# ══════════════════════════════════════════════════════════════════════

async def quick_image_check(image_bytes: bytes) -> str:
    """Quick check if image is work-related or personal. Returns 'WORK' or 'PERSONAL'."""
    if not CLAUDE_API_KEY:
        return "WORK"  # default to work if no API key
    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
                    },
                    {
                        "type": "text",
                        "text": "Is this a work-related image (ads dashboard, payment, spreadsheet, creative, product photo, analytics) or a personal/casual image (selfie, food, child, meme, personal photo)? Reply with just: WORK or PERSONAL",
                    },
                ],
            }],
        )
        result = resp.content[0].text.strip().upper()
        if "PERSONAL" in result:
            return "PERSONAL"
        return "WORK"
    except Exception as e:
        logger.error("Quick image check error: %s", e)
        return "WORK"  # default to work on error


# ══════════════════════════════════════════════════════════════════════
# SMART ANALYSIS - the core intelligence engine
# ══════════════════════════════════════════════════════════════════════

async def smart_analysis(
    team_name: str,
    screenshot_data: dict,
    report_type: str,
    image_bytes: bytes | None = None,
) -> str:
    """
    Full AI analysis with:
    - Team context (trend, rank, history, MTD)
    - Verification (screenshot vs sheet)
    - Anomaly detection
    - Conversation memory
    - Cross-team comparison
    """
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)

    # Build full context
    all_data = await fetch_master_data()
    ctx = await build_team_context(team_name, all_data)
    context_text = format_context_for_prompt(ctx)

    # Team sheet data (PRIMARY source)
    team_sheet = ctx.get("team_sheet_today")
    team_sheet_rows = ctx.get("team_sheet_rows", [])
    team_sheet_recent = get_team_sheet_recent(team_sheet_rows, 5)

    # Calculate CPA correctly from team sheet
    cpa_real = calculate_cpa_from_sheet(team_sheet_rows) if team_sheet_rows else None

    # Build team sheet section for prompt
    ts_text = ""
    if team_sheet:
        ts_text = f"\n## بيانات شيت الفريق (آخر يوم مسجّل):\n"
        ts_text += f"  التاريخ: {team_sheet.get('Date', '?')}\n"
        ts_text += f"  Spend: {team_sheet.get('Spend', '-')}\n"
        ts_text += f"  طلبات جديدة: {team_sheet.get('New Orders', '-')}\n"
        ts_text += f"  طلبات أمس: {team_sheet.get('Yesterday New', '-')}\n"
        ts_text += f"  Delivered: {team_sheet.get('Delivered', '-')}\n"
        ts_text += f"  Cancel: {team_sheet.get('Cancel', '-')}\n"
        ts_text += f"  Hold: {team_sheet.get('Hold', '-')}\n"
        ts_text += f"  CPO: {team_sheet.get('CPO', '-')}\n"
        if cpa_real:
            ts_text += f"  CPA الحقيقي (Spend أمس ÷ Delivered اليوم): {cpa_real}\n"
        ts_text += f"  🚦: {team_sheet.get('Lamp', '-')}\n"
        ts_text += f"  Del%: {team_sheet.get('Del%', '-')} | Cancel%: {team_sheet.get('Cancel%', '-')} | Hold%: {team_sheet.get('Hold%', '-')}\n"

    if team_sheet_recent and len(team_sheet_recent) > 1:
        ts_text += f"\n## آخر {len(team_sheet_recent)} أيام من شيت الفريق:\n"
        for r in team_sheet_recent:
            ts_text += f"  {r.get('Date','?')}: Spend={r.get('Spend','-')} | Orders={r.get('New Orders','-')} | CPO={r.get('CPO','-')} | {r.get('Lamp','')}\n"

    # Check if screenshot numbers look like MTD totals
    ss_spend = _safe_num(screenshot_data.get("spend"))
    ts_spend = _safe_num(team_sheet.get("Spend", "")) if team_sheet else None
    is_mtd = False
    if ss_spend and ts_spend and ts_spend > 0 and ss_spend > ts_spend * 5:
        is_mtd = True
        logger.info("Screenshot looks like MTD totals (ss=%s vs daily=%s)", ss_spend, ts_spend)

    # Get learnings (from DB + JSON fallback)
    learnings_text = get_learnings_for_prompt(team_name)
    # Also get DB learnings directly for richer context
    db_learnings = ctx.get("db_learnings", [])
    if db_learnings and not learnings_text:
        lines = ["## 📝 تصحيحات سابقة (تعلّمت منها):"]
        for l in db_learnings[-5:]:
            lines.append(f"- {l.get('date', '')}: {l.get('correction', '')}")
        learnings_text = "\n".join(lines)

    # DB history stats for prompts
    db_stats = ctx.get("db_stats", {})
    db_history_text = ""
    if db_stats:
        db_parts = [f"## 📊 تاريخ الأداء (آخر {db_stats.get('days_count', 30)} يوم):"]
        if db_stats.get("avg_cpo"):
            db_parts.append(f"  - متوسط CPO: {db_stats['avg_cpo']}")
        if db_stats.get("avg_cpa"):
            db_parts.append(f"  - متوسط CPA: {db_stats['avg_cpa']}")
        best = db_stats.get("best_day")
        if best:
            db_parts.append(f"  - أحسن يوم: {best['date']} (CPO {best['cpo']})")
        worst = db_stats.get("worst_day")
        if worst:
            db_parts.append(f"  - أسوأ يوم: {worst['date']} (CPO {worst['cpo']})")
        trend_map = {"improving": "تحسن 📈", "declining": "تراجع 📉", "stable": "مستقر ➡️"}
        if db_stats.get("trend"):
            db_parts.append(f"  - الاتجاه: {trend_map.get(db_stats['trend'], db_stats['trend'])}")
        db_history_text = "\n".join(db_parts)

    # Creative history
    creative_text = ""
    last_creative = get_last_creative(team_name)
    if last_creative:
        creative_text = f"\n## آخر Creative اتحلل:\n  تاريخ: {last_creative['date']} | نوع: {last_creative['type']}\n  ملخص: {last_creative['summary'][:150]}\n"

    # Budget tracking
    budget_text = ""
    budget_today = get_team_budget_today(team_name)
    if budget_today["total"] > 0:
        budget_text = f"\n## بادجيت النهاردة: {budget_today['total']:,.0f} جنيه\n"
        for btype, amount in budget_today["by_type"].items():
            budget_text += f"  {btype}: {amount:,.0f}\n"

    # Verify screenshot vs team sheet
    if is_mtd:
        verification = {"status": "mtd_totals", "discrepancies": [], "summary": "📊 الأرقام دي MTD (تراكمي الشهر) مش أرقام اليوم"}
    else:
        verify_source = None
        if team_sheet:
            verify_source = {
                "Spend اليوم": team_sheet.get("Spend", ""),
                "Orders اليوم": team_sheet.get("New Orders", ""),
                "CPO اليوم": team_sheet.get("CPO", ""),
            }
        elif ctx.get("today"):
            verify_source = ctx["today"]
        verification = verify_screenshot_vs_sheet(screenshot_data, verify_source)

    # Screenshot data section
    ss_parts = ["## بيانات الـ Screenshot:"]
    for k, v in screenshot_data.items():
        if k.startswith("_") or v is None:
            continue
        ss_parts.append(f"  {k}: {v}")
    ss_text = "\n".join(ss_parts)

    # Build the analysis prompt based on verification status
    if verification["status"] == "major_diff":
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}
{db_history_text}
{learnings_text}

## 🔴 فرق في الأرقام:
{verification['summary']}

## المطلوب:
{leader} كاتب في الشيت أرقام مختلفة عن اللي في الـ screenshot:
- وضّح بالظبط أنهي رقم مختلف
- اسأله: "أنهي الرقم الصح؟ الشيت ولا الـ screenshot؟"
- ابدأ بـ "أنا شايف إن..."
- اختم بـ "صح كده؟"
- لو في تصحيحات سابقة مشابهة، اتعلم منها ومتكررش نفس الغلط

خاطب {leader} بالاسم. مختصر (3-5 سطور). بالعربي المصري."""

    elif verification["status"] == "mtd_totals":
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}
{db_history_text}
{learnings_text}

## ملاحظة: الأرقام في الـ screenshot دي تراكمية (MTD) مش أرقام يوم واحد.

## المطلوب:
- وضّح إن الأرقام دي تراكمي الشهر مش أرقام اليوم
- احسب الـ CPO التراكمي وقيّمه
- قارن مع أرقام آخر يوم في الشيت
- ابدأ بـ "أنا شايف إن..." واختم بـ "صح كده؟"
- لو في تصحيحات سابقة مشابهة، اتعلم منها

خاطب {leader} بالاسم. مختصر (3-4 سطور). بالعربي المصري."""

    elif verification["status"] == "sheet_not_updated":
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}
{db_history_text}
{learnings_text}

## المطلوب:
الشيت لسه مش متحدث. حلل الأرقام اللي في الـ screenshot بس:
- لو فيها spend و orders: احسب الـ CPO وقيّمه
- ابدأ بـ "أنا شايف إن..." واختم بـ "صح كده؟"
- لو في تاريخ أداء أو تصحيحات سابقة، استخدمهم في التحليل

خاطب {leader} بالاسم. مختصر (2-3 سطور). بالعربي المصري."""

    else:
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}
{context_text}
{db_history_text}
{learnings_text}
{creative_text}
{budget_text}

## نتيجة المقارنة: {verification['summary']}

## المطلوب - حلل كـ Performance Marketing Manager + Data Analyst:
ابدأ بـ "أنا شايف إن..." وحلل:

### كمدير بيرفورمانس:
- قيّم الأداء: CPO/CPA كويس ولا محتاج تحسين؟
- لو CPO > 150: إيه الممكن يتعمل؟
- لو Cancel عالي: في مشكلة في جودة الطلبات
- قارن مع ترتيب الفريق

### كمحلل بيانات:
- Trend: الأداء بيتحسن ولا بيوحش؟
- لو في anomaly: نبّه عليه
- لو في creative history: اربط بين الـ creative والأداء

### قواعد:
- ابدأ بـ "أنا شايف إن..."
- اختم بـ "صح كده؟" أو سؤال بسيط
- خاطب {leader} بالاسم
- مختصر (4-6 سطور)
- بالعربي المصري"""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)

        messages_content = [{"type": "text", "text": analysis_prompt}]
        if image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode("utf-8")
            messages_content.insert(0, {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
            })

        async def _call_analysis():
            return await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": messages_content}],
            )

        message = await _retry_async(_call_analysis, retries=2)

        response = message.content[0].text.strip()
        remember_exchange(team_name, response)
        return response

    except Exception as e:
        logger.error("Smart analysis error (after retries): %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# PAYMENT IMAGE HANDLER
# ══════════════════════════════════════════════════════════════════════

async def handle_payment_image(image_bytes: bytes, team_name: str, image_type: str) -> str:
    """Smart response for payment/billing screenshots."""
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = f"""الصورة دي صفحة دفع/billing. اقرأها بعناية واستخرج كل التفاصيل:

## المطلوب تحديده:
1. **المنصة**: فيسبوك ولا تيك توك؟
2. **المبلغ**: كام بالظبط؟
3. **نوع الدفع**:
   - Prepaid balance = شحن رصيد من فوري
   - Credit/Debit Card = دفع ببطاقة
   - Manual payment = دفع يدوي
4. **الحالة**: Paid / Failed / Funded / Pending
5. **التاريخ**: تاريخ آخر معاملة

## شكل الرد (3-4 سطور):
ابدأ بـ "أنا شايف إن..." ووضّح:
- آخر دفعة: المبلغ + النوع + الحالة
- لو في Failed: ⚠️ نبّه
- اختم بـ "صح كده؟"

مهم: لو في دفعة Failed = مشكلة لازم تتنبه ليها
خاطب {leader} بالاسم. بالعربي المصري."""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)

        async def _call_payment():
            return await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )

        message = await _retry_async(_call_payment, retries=2)
        response = message.content[0].text.strip()
        remember_exchange(team_name, response)
        return response
    except Exception as e:
        logger.error("Payment image analysis error (after retries): %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# TEXT MESSAGE ANALYSIS
# ══════════════════════════════════════════════════════════════════════

async def analyze_text_message(team_name: str, text: str, reply_to_text: str = "") -> str:
    """Handle text replies from team leaders with conversation memory."""
    if not CLAUDE_API_KEY:
        return ""

    # Simple acknowledgements - don't waste API call
    simple_words = {"شكرا", "شكراً", "تمام", "اوك", "ok", "أوك", "حاضر", "ماشي",
                    "تم", "👍", "🙏", "ان شاء الله", "إن شاء الله", "هعمل كده",
                    "حسنا", "طيب", "اه", "أه", "اوكي"}
    cleaned = text.strip().replace("!", "").replace(".", "").replace("،", "")
    if cleaned in simple_words or len(cleaned) <= 4:
        leader = get_leader(team_name)
        remember_exchange(team_name, "👍", user_reply=text)
        return ""

    leader = get_leader(team_name)

    # Build context for substantive messages
    all_data = await fetch_master_data()
    ctx = await build_team_context(team_name, all_data)

    today = ctx.get("today")
    numbers_summary = ""
    if today:
        spend = today.get("Spend اليوم", 0)
        orders = today.get("Orders اليوم", 0)
        cpo = today.get("CPO اليوم", "-")
        numbers_summary = f"بيانات اليوم: Spend={spend} | Orders={orders} | CPO={cpo}"

    conv_history = ctx.get("conversation", "")

    prompt = f"""فريق: {team_name} | التيم ليدر: {leader}
{numbers_summary}

{conv_history}

الرسالة السابقة من البوت: "{reply_to_text[:300]}"

رد التيم ليدر {leader}: "{text}"

## أنت مدير Performance Marketing + محلل بيانات + محلل Creative:
- الحملات Facebook Messages Ads (مش conversions) - الجمهور مقيمين في الكويت
- لو {leader} بيسأل عن استراتيجية: جاوبه بخبرة عملية
- لو بيسأل عن بادجيت: اقتراح مبني على الأرقام
- لو بيسأل عن creative: انصحه بناءً على الأداء

## قواعد:
- ابدأ بـ "أنا شايف إن..." لو بتحلل
- لو الكلام عادي: رد بسطر واحد
- لو بيفسّر حاجة: اقبل تفسيره
- متكررش نفس الأسئلة
- رد مختصر (1-3 سطور). بالعربي المصري."""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, response, user_reply=text)
        return response
    except Exception as e:
        logger.error("Text analysis error: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# DOCUMENT (CSV/XLSX) ANALYSIS
# ══════════════════════════════════════════════════════════════════════

async def analyze_pdf_orders(pdf_bytes: bytes, team_name: str, filename: str) -> str:
    """Analyze a driver orders PDF - extract products, areas, quantities."""
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)
    today = _now_egypt().strftime("%Y-%m-%d")

    # Extract text from PDF
    pdf_text = ""
    try:
        import pdfplumber
        import io as _io2
        with pdfplumber.open(_io2.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pdf_text += page_text + "\n"
                # Also try tables
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if row:
                            pdf_text += " | ".join(str(c or "") for c in row) + "\n"
    except Exception as e:
        logger.error("PDF parse error: %s", e)
        return f"مش قادر أقرأ الـ PDF: {e}"

    logger.info("PDF text extracted: %d chars from %s", len(pdf_text), filename)

    if not pdf_text.strip() or len(pdf_text.strip()) < 20:
        # PDF might be scanned images - try sending as image to Claude
        logger.warning("PDF text empty or too short (%d chars) - may be image-based", len(pdf_text))
        try:
            encoded = base64.b64encode(pdf_bytes).decode("utf-8")
            client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
            message = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}},
                        {"type": "text", "text": f"حلل شيت الطلبات ده للسواقين. استخرج المنتجات والمناطق والكميات والأسعار. رد بالعربي المصري. خاطب {leader} بالاسم."}
                    ],
                }],
            )
            response = message.content[0].text.strip()
            if response:
                remember_exchange(team_name, f"[PDF analysis] {response[:200]}")
                return response
        except Exception as e:
            logger.error("PDF image analysis fallback error: %s", e)
        return f"الـ PDF مش قادر أقرأه يا {leader}. ممكن تبعتيه كصورة أوضح؟"

    # Save raw text to DB for future reference
    db_log_conversation(team_name, leader, f"[PDF orders] {filename}", pdf_text[:500])

    # Get historical context
    db_perf = db_get_daily_performance(team_name, days=7)
    history_text = ""
    if db_perf:
        history_text = "أداء آخر 7 أيام:\n"
        for row in db_perf[-7:]:
            history_text += f"  {row['date']}: Spend={row['spend']}, Orders={row['new_orders']}, CPO={row['cpo']}\n"

    # Get learnings
    learnings = db_get_learnings(team_name, limit=5)
    learnings_text = ""
    if learnings:
        learnings_text = "\nتصحيحات سابقة:\n" + "\n".join(
            f"  - {l['date']}: {l['correction']}" for l in learnings
        )

    prompt = f"""أنت مدير Performance Marketing + Data Analyst لفريق {team_name} (التيم ليدر: {leader}).

ده شيت الطلبات للسواقين النهاردة ({filename}):

{pdf_text[:8000]}

{history_text}
{learnings_text}

## المطلوب (حلل كـ Data Analyst):
1. كام طلب في الشيت النهاردة؟
2. إيه أكتر المنتجات اتطلبت؟ (Top 5)
3. إيه أكتر المناطق فيها طلبات؟
4. متوسط سعر الطلب كام؟
5. في منتجات ملفتة (كتير أو قليلة مقارنة بالعادي)؟
6. مقارنة عدد الطلبات مع أرقام شيت التقرير (لو متاحة)
7. توصية واحدة عملية بناءً على البيانات

## قواعد:
- ابدأ بـ "من ناحية الداتا..."
- بالعربي المصري
- خاطب {leader} بالاسم
- لو مش متأكد من حاجة اسأل
- اختم بسؤال عشان الأدمن يتفاعل
- مختصر (6-10 سطور)"""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        message = await _retry_async(
            client.messages.create,
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, f"[تحليل شيت سواقين] {response[:200]}")

        # Log to DB
        db_log_tracking(
            team=team_name, leader=leader, image_type="driver_orders_pdf",
            platform="", amount="", ai_notes=response[:500],
            task_type="morning", status="analyzed"
        )
        return response
    except Exception as e:
        logger.error("PDF Claude analysis error for %s: %s", team_name, e, exc_info=True)
        return f"حصل مشكلة في التحليل يا {leader}. جرّبي تبعتي الشيت تاني أو كصورة."


async def analyze_document(file_content: str, team_name: str, filename: str) -> str:
    """Analyze uploaded CSV/Excel document with Claude."""
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)

    prompt = f"""أنت مدير Performance Marketing بتحلل ملف بيانات لفريق {team_name} (التيم ليدر: {leader}).
اسم الملف: {filename}

محتوى الملف:
{file_content[:6000]}

## المطلوب:
كـ Performance Manager + Data Analyst، حلل البيانات دي:
1. إيه نوع البيانات دي؟ (بيانات حملات / طلبات / بادجيت / غيره)
2. لو فيها أرقام spend/orders/CPO: قيّمها
3. لو فيها trends: وضّحها
4. أي ملاحظات أو anomalies
5. اقتراحات عملية (1-2)

ابدأ بـ "من ناحية الداتا..." واختم بسؤال.
خاطب {leader} بالاسم. بالعربي المصري. مختصر (5-8 سطور)."""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, f"[تحليل ملف] {response[:200]}")
        return response
    except Exception as e:
        logger.error("Document analysis error: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# QUICK SUMMARY
# ══════════════════════════════════════════════════════════════════════

def generate_quick_summary(screenshot_data: dict) -> str:
    """Generate a quick one-line summary of extracted data."""
    parts = []
    spend = _safe_num(screenshot_data.get("spend"))
    orders = _safe_num(screenshot_data.get("orders") or screenshot_data.get("results"))
    cpo = _safe_num(screenshot_data.get("cpo"))
    cpa = _safe_num(screenshot_data.get("cpa"))

    if spend and spend > 0:
        parts.append(f"Spend: {spend:,.0f}")
    if orders and orders > 0:
        parts.append(f"Orders: {int(orders)}")
    if cpo and cpo > 0:
        parts.append(f"CPO: {cpo:,.0f}")
    elif spend and orders and orders > 0:
        parts.append(f"CPO: {spend/orders:,.0f}")
    if cpa and cpa > 0:
        parts.append(f"CPA: {cpa:,.0f}")

    if screenshot_data.get("_from_sheet"):
        return "📋 " + (" | ".join(parts) if parts else "تم استلام الصورة")
    return "🤖 " + (" | ".join(parts) if parts else "تم استلام الصورة")


# ══════════════════════════════════════════════════════════════════════
# VIDEO CREATIVE ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def get_video_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return float(result.stdout.strip())
    except Exception:
        return 15.0


def extract_video_frames(video_bytes: bytes) -> list[bytes]:
    """Extract smart frames: 3 in first 3s (Hook), rest spread across content."""
    frames = []
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "input.mp4"
        video_path.write_bytes(video_bytes)

        duration = get_video_duration(str(video_path))
        if duration <= 0:
            duration = 15.0

        timestamps = [0.5, 1.5, 3.0]
        remaining = max(duration - 3, 1)
        content_frames = min(5, int(remaining / 2))
        for i in range(content_frames):
            t = 3.0 + (remaining / (content_frames + 1)) * (i + 1)
            timestamps.append(min(t, duration - 0.2))
        if duration > 4:
            timestamps.append(duration - 0.5)

        for i, ts in enumerate(timestamps):
            output_path = Path(tmpdir) / f"frame_{i}.jpg"
            cmd = [
                "ffmpeg", "-y", "-ss", f"{ts:.2f}",
                "-i", str(video_path),
                "-vframes", "1", "-q:v", "2",
                str(output_path),
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=15)
                if output_path.exists() and output_path.stat().st_size > 0:
                    frames.append(output_path.read_bytes())
            except Exception as e:
                logger.warning("Frame %d extract failed: %s", i, e)

    logger.info("Extracted %d frames from video (%.1fs)", len(frames), duration)
    return frames


def extract_audio_transcript(video_bytes: bytes) -> str:
    """Extract audio from video and transcribe using Whisper."""
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "input.mp4"
        audio_path = Path(tmpdir) / "audio.wav"
        video_path.write_bytes(video_bytes)

        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            str(audio_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if not audio_path.exists() or audio_path.stat().st_size < 1000:
                return ""
        except Exception as e:
            logger.warning("Audio extraction failed: %s", e)
            return ""

        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            segments, info = model.transcribe(str(audio_path), beam_size=3)
            text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
            transcript = " ".join(text_parts)
            logger.info("Transcript (%s, %.1fs): %s", info.language, info.duration, transcript[:100])
            return transcript
        except ImportError:
            logger.warning("faster-whisper not available")
            return ""
        except Exception as e:
            logger.warning("Transcription failed: %s", e)
            return ""


# Creative Scorecard prompt
CREATIVE_SCORECARD_PROMPT = """
## نظام التقييم (Scorecard):
قيّم كل عنصر من 1-10 وادي تعليق مختصر:

| العنصر | الدرجة | التعليق |
|--------|--------|---------|
| 🎣 Hook (أول 3 ثواني) | ?/10 | هل بيوقف الـ scroll؟ |
| 🎨 التصميم والجودة | ?/10 | احترافي؟ واضح؟ |
| ✍️ النص/الكوبي | ?/10 | مقنع؟ واضح؟ |
| 📢 CTA | ?/10 | واضح ومحفز؟ |
| 🎯 مناسب للـ Target | ?/10 | مناسب للجمهور؟ |
| 🔊 الصوت/Voiceover | ?/10 | واضح ومؤثر؟ |
| 📊 التقييم العام | ?/10 | |

### ثم اكتب:
- ✅ نقاط القوة (2-3)
- ❌ نقاط الضعف (2-3)
- 💡 اقتراحات التحسين (2-3 محددة وعملية)
- 🤔 سؤال للـ Media Buyer يخليه يفكر
"""


async def analyze_video_creative(
    video_bytes: bytes, team_name: str, thumbnail_bytes: bytes | None = None
) -> str:
    """Full video creative analysis with performance context."""
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)

    frames = extract_video_frames(video_bytes)
    if not frames and thumbnail_bytes:
        frames = [thumbnail_bytes]
    if not frames:
        return f"⚠️ مش قادر أحلل الفيديو. {leader}، ابعت screenshot من الإعلان."

    transcript = extract_audio_transcript(video_bytes)

    with tempfile.TemporaryDirectory() as tmpdir:
        vp = Path(tmpdir) / "v.mp4"
        vp.write_bytes(video_bytes)
        duration = get_video_duration(str(vp))

    # Get performance context
    all_data = await fetch_master_data()
    ctx = await build_team_context(team_name, all_data)

    perf_context = ""
    if ctx.get("today"):
        cpo = ctx["today"].get("CPO اليوم", "-")
        orders = ctx["today"].get("Orders اليوم", 0)
        perf_context = f"\n📊 أداء الفريق اليوم: CPO={cpo} | Orders={orders} | Trend: {ctx.get('trend', '?')}"
        perf_context += f"\nالترتيب: #{ctx.get('rank', '?')} من {ctx.get('total_teams', '?')} فرق"

    content = []
    frame_labels = []
    if len(frames) >= 3:
        frame_labels = ["Hook (0.5s)", "Hook (1.5s)", "Hook (3s)"]
        for i in range(3, len(frames) - 1):
            frame_labels.append(f"Content ({i})")
        if len(frames) > 3:
            frame_labels.append("CTA/End")

    for i, frame in enumerate(frames):
        label = frame_labels[i] if i < len(frame_labels) else f"Frame {i+1}"
        content.append({"type": "text", "text": f"📸 {label}:"})
        img_b64 = base64.b64encode(frame).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
        })

    prompt_parts = [
        f"أنت مدير Performance Marketing خبير بتراجع Creative (فيديو إعلاني) لفريق {team_name}.",
        f"التيم ليدر: {leader}",
        f"مدة الفيديو: {duration:.1f} ثانية",
        f"عدد الفريمات المستخرجة: {len(frames)}",
        perf_context,
    ]

    if transcript:
        prompt_parts.append(f"\n🔊 نص الـ Voiceover/الصوت:")
        prompt_parts.append(f"\"{transcript}\"")
        prompt_parts.append("حلل النص ده: هل مقنع؟ واضح؟ مناسب للجمهور؟")
    else:
        prompt_parts.append("\n🔇 الفيديو ده مفيهوش voiceover واضح.")
        prompt_parts.append("قيّم: هل الفيديو محتاج voiceover؟")

    prompt_parts.append(f"\n{CREATIVE_SCORECARD_PROMPT}")
    prompt_parts.append(f"""
## كمحلل Creative + مدير Performance:
- الحملات Facebook Messages Ads (الهدف رسائل مش conversions)
- الجمهور: مقيمين في الكويت من كل الجنسيات
- اربط جودة الـ Creative بالأداء
- ابدأ بـ "أنا شايف إن..."
- اختم بسؤال ذكي + "صح كده؟"
- Trend: {ctx.get('trend', '?')}

خاطب {leader} بالاسم. بالعربي المصري. مختصر وعملي.""")

    content.append({"type": "text", "text": "\n".join(prompt_parts)})

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, f"[تحليل فيديو] {response[:200]}")
        save_creative_record(team_name, "video", response[:200])
        return response
    except Exception as e:
        logger.error("Video analysis error: %s", e)
        return ""


async def analyze_image_creative(image_bytes: bytes, team_name: str) -> str:
    """Analyze an image creative with scorecard + performance context."""
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    all_data = await fetch_master_data()
    ctx = await build_team_context(team_name, all_data)

    perf_context = ""
    if ctx.get("today"):
        cpo = ctx["today"].get("CPO اليوم", "-")
        perf_context = f"\n📊 أداء الفريق: CPO={cpo} | Trend: {ctx.get('trend', '?')} | Rank: #{ctx.get('rank', '?')}"

    prompt = f"""أنت مدير Performance Marketing خبير بتراجع Creative (إعلان صورة) لفريق {team_name} (التيم ليدر: {leader}).
{perf_context}

حلل الإعلان ده بنظام الـ Scorecard:

| العنصر | الدرجة | التعليق |
|--------|--------|---------|
| 🎨 التصميم | ?/10 | |
| ✍️ النص/الكوبي | ?/10 | |
| 📢 CTA | ?/10 | |
| 🎯 مناسب للـ Target | ?/10 | |
| 📊 التقييم العام | ?/10 | |

ثم:
- ✅ نقاط القوة
- ❌ نقاط الضعف
- 💡 اقتراحات التحسين (2-3)
- 🤔 سؤال للـ Media Buyer

ابدأ بـ "أنا شايف إن..." واختم بـ "صح كده؟"
لو الأداء وحش، اربط بين جودة الإعلان والنتائج.
خاطب {leader} بالاسم. بالعربي المصري. مختصر."""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, f"[تحليل صورة] {response[:200]}")
        save_creative_record(team_name, "image", response[:200])
        return response
    except Exception as e:
        logger.error("Image creative analysis error: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# OWNER REPORT FOR A TEAM (NEW in V2)
# ══════════════════════════════════════════════════════════════════════

async def build_owner_team_report(team_name: str) -> dict:
    """
    Build a report for the owner about one team's status.
    Checks: tracking sheet + team sheet + compares.
    Returns: {received, missing, sheet_status, recommendation, summary}
    """
    leader = get_leader(team_name)

    # What did they send today?
    tracking = await get_missing_for_team(team_name, "morning")

    # Team sheet data
    team_rows = await fetch_team_sheet(team_name)
    today_row = get_team_sheet_today(team_rows) if team_rows else None
    cpa = calculate_cpa_from_sheet(team_rows) if team_rows else None
    recent = get_team_sheet_recent(team_rows, 5) if team_rows else []

    # Sheet status
    sheet_status = "not_updated"
    sheet_data = {}
    if today_row:
        sheet_status = "updated"
        sheet_data = {
            "spend": _safe_num(today_row.get("Spend", "")),
            "orders": _safe_num(today_row.get("New Orders", "")),
            "cpo": _safe_num(today_row.get("CPO", "")),
            "delivered": _safe_num(today_row.get("Delivered", "")),
            "cancel": _safe_num(today_row.get("Cancel", "")),
            "cpa": cpa,
            "date": today_row.get("Date", ""),
        }

    # CPO evaluation
    cpo = sheet_data.get("cpo")
    cpo_status = "unknown"
    if cpo:
        if cpo <= CPO_GREEN:
            cpo_status = "green"
        elif cpo <= CPO_YELLOW:
            cpo_status = "yellow"
        else:
            cpo_status = "red"

    # Recommendation
    recommendation = ""
    if cpo_status == "red":
        recommendation = f"⚠️ CPO عالي ({cpo:.0f}) - محتاج مراجعة creative أو targeting"
    elif cpo_status == "yellow":
        recommendation = f"🟡 CPO مقبول ({cpo:.0f}) - تابع وممكن يتحسن"
    elif cpo_status == "green" and cpo:
        recommendation = f"✅ أداء ممتاز (CPO={cpo:.0f}) - ممكن نزود البادجيت"

    # Build summary text
    summary_parts = [f"📊 {team_name} ({leader}):"]
    if sheet_status == "updated":
        summary_parts.append(f"  Spend: {sheet_data.get('spend', 0):,.0f} | Orders: {sheet_data.get('orders', 0):.0f} | CPO: {cpo if cpo else '?'}")
        if cpa:
            summary_parts.append(f"  CPA: {cpa}")
    else:
        summary_parts.append("  الشيت لسه مش متحدث")

    if not tracking["complete"]:
        missing_labels = [m["label"] for m in tracking["missing"]]
        summary_parts.append(f"  ناقص: {', '.join(missing_labels)}")
    else:
        summary_parts.append("  ✅ بعت كل المطلوب")

    if recommendation:
        summary_parts.append(f"  {recommendation}")

    return {
        "team_name": team_name,
        "leader": leader,
        "received": tracking["received"],
        "received_types": tracking["received_types"],
        "missing": tracking["missing"],
        "complete": tracking["complete"],
        "sheet_status": sheet_status,
        "sheet_data": sheet_data,
        "cpo_status": cpo_status,
        "recommendation": recommendation,
        "summary": "\n".join(summary_parts),
    }


# ══════════════════════════════════════════════════════════════════════
# PROACTIVE MONITORING
# ══════════════════════════════════════════════════════════════════════

async def proactive_sheet_check() -> list[dict]:
    """Check ALL team sheets proactively. Returns list of alerts."""
    alerts = []

    for team_name, info in TEAM_INFO.items():
        try:
            rows = await fetch_team_sheet(team_name)
            if not rows:
                alerts.append({
                    "team": team_name, "leader": info["leader"],
                    "type": "no_data", "severity": "warning",
                    "msg": f"مش قادر أقرأ شيت {team_name}"
                })
                continue

            today_row = get_team_sheet_today(rows)
            recent = get_team_sheet_recent(rows, 5)

            if not today_row:
                alerts.append({
                    "team": team_name, "leader": info["leader"],
                    "type": "not_updated", "severity": "info",
                    "msg": f"{info['leader']} لسه محدثش/محدثتش الشيت النهاردة"
                })
                continue

            spend = _safe_num(today_row.get("Spend", ""))
            orders = _safe_num(today_row.get("New Orders", ""))
            cpo = _safe_num(today_row.get("CPO", ""))
            cpa = calculate_cpa_from_sheet(rows)

            # Zero spend alert
            if spend == 0 and len(recent) > 1:
                prev_spend = _safe_num(recent[-2].get("Spend", "")) if len(recent) >= 2 else 0
                if prev_spend and prev_spend > 0:
                    alerts.append({
                        "team": team_name, "leader": info["leader"],
                        "type": "zero_spend", "severity": "critical",
                        "msg": f"⚠️ {team_name} صرف صفر النهاردة! أمس كان {prev_spend:,.0f}"
                    })

            # CPO spike
            if cpo and cpo > 200:
                alerts.append({
                    "team": team_name, "leader": info["leader"],
                    "type": "high_cpo", "severity": "warning",
                    "msg": f"🔴 {team_name} CPO = {cpo:.0f} (عالي جداً)"
                })

            # CPA spike
            if cpa and cpa > 200:
                alerts.append({
                    "team": team_name, "leader": info["leader"],
                    "type": "high_cpa", "severity": "warning",
                    "msg": f"🔴 {team_name} CPA = {cpa:.0f} (تكلفة التسليم عالية)"
                })

            # 3-day declining trend
            if len(recent) >= 3:
                cpos = [_safe_num(r.get("CPO", "")) for r in recent[-3:]]
                cpos = [c for c in cpos if c and c > 0]
                if len(cpos) == 3 and cpos[0] < cpos[1] < cpos[2]:
                    alerts.append({
                        "team": team_name, "leader": info["leader"],
                        "type": "declining_trend", "severity": "warning",
                        "msg": f"📉 {team_name} CPO بيعلى 3 أيام ورا بعض: {cpos[0]:.0f} → {cpos[1]:.0f} → {cpos[2]:.0f}"
                    })

        except Exception as e:
            logger.error("Proactive check error for %s: %s", team_name, e)

    return alerts


# ══════════════════════════════════════════════════════════════════════
# SMART DAILY REPORT (for owner)
# ══════════════════════════════════════════════════════════════════════

async def generate_smart_daily_report() -> str:
    """Generate a comprehensive daily report with AI analysis."""
    if not CLAUDE_API_KEY:
        return ""

    all_teams_data = []
    for team_name, info in TEAM_INFO.items():
        rows = await fetch_team_sheet(team_name)
        today = get_team_sheet_today(rows) if rows else None
        cpa = calculate_cpa_from_sheet(rows) if rows else None
        recent = get_team_sheet_recent(rows, 5) if rows else []

        if today:
            all_teams_data.append({
                "team": team_name,
                "leader": info["leader"],
                "spend": _safe_num(today.get("Spend", "")),
                "orders": _safe_num(today.get("New Orders", "")),
                "cpo": _safe_num(today.get("CPO", "")),
                "delivered": _safe_num(today.get("Delivered", "")),
                "cancel": _safe_num(today.get("Cancel", "")),
                "cpa": cpa,
                "date": today.get("Date", ""),
                "days_data": len(recent),
            })

    if not all_teams_data:
        return "مفيش بيانات كافية للتقرير"

    data_text = "## بيانات كل الفرق النهاردة:\n"
    total_spend = 0
    total_orders = 0
    total_delivered = 0

    for t in sorted(all_teams_data, key=lambda x: x.get("cpo") or 999):
        data_text += f"- {t['team']} ({t['leader']}): Spend={t['spend']:,.0f} | Orders={t['orders']:.0f} | CPO={t['cpo']:.0f if t['cpo'] else '?'} | CPA={t['cpa'] if t['cpa'] else '?'}\n"
        total_spend += t.get("spend") or 0
        total_orders += t.get("orders") or 0
        total_delivered += t.get("delivered") or 0

    data_text += f"\n## الإجمالي:\nSpend: {total_spend:,.0f} | Orders: {total_orders:.0f} | Delivered: {total_delivered:.0f}\n"
    if total_orders > 0:
        data_text += f"CPO إجمالي: {total_spend/total_orders:.0f}\n"
    if total_delivered > 0:
        data_text += f"CPA إجمالي: {total_spend/total_delivered:.0f}\n"

    prompt = f"""{data_text}

## المطلوب - تقرير يومي ذكي للمالك:
اكتب تقرير مختصر وعملي (10-15 سطر) يشمل:

1. **ملخص عام**: إجمالي الصرف والطلبات - الأداء كويس ولا محتاج تحسين؟
2. **أحسن 3 فرق**: مين الأحسن ولية؟
3. **أسوأ 3 فرق**: مين محتاج مساعدة ولية؟
4. **توصيات استراتيجية**: (2-3 نصائح عملية)
   - فريق يستاهل زيادة بادجيت؟
   - فريق محتاج يوقف ويراجع؟
   - فريق محتاج يغير creative؟
5. **سؤال للإدارة**: سؤال واحد مهم للمتابعة

الحملات كلها Facebook Messages Ads. الجمهور مقيمين في الكويت.
CPO/CPA: أخضر ≤ 150 | أصفر ≤ 180 | أحمر > 180

ابدأ بـ "أنا شايف إن..." واختم بـ "صح كده؟"
بالعربي المصري. مختصر وعملي."""

    try:
        client = anthropic.AsyncAnthropic(api_key=CLAUDE_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        logger.error("Smart report error: %s", e)
        return ""
