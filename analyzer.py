"""
AI-powered Performance Marketing Manager.
Smart context system: builds full team intelligence before every analysis.
Verification: compares screenshots with sheet data, flags discrepancies.
Memory: tracks conversations per team for contextual follow-ups.
Cross-team: ranks teams, detects anomalies, provides strategic insights.
"""
import os
import json
import base64
import logging
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
import anthropic

logger = logging.getLogger(__name__)
EGYPT_TZ = ZoneInfo("Africa/Cairo")

def _now_egypt():
    return datetime.now(EGYPT_TZ)

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
MASTER_SHEET_URL = os.environ.get("MASTER_SHEET_URL", "")

# ── Team info ────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════
# Basic helpers (API unchanged for main.py compatibility)
# ══════════════════════════════════════════════════════════════════════

def get_leader(team_name: str) -> str:
    return TEAM_INFO.get(team_name, {}).get("leader", "")


def get_sheet_name(team_name: str) -> str:
    return TEAM_INFO.get(team_name, {}).get("sheet_name", team_name)


async def fetch_master_data() -> list[dict]:
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
    """
    Tell Apps Script to update the master sheet NOW.
    Call this when all teams have submitted their data.
    Returns True if update succeeded.
    """
    if not MASTER_SHEET_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.post(
                MASTER_SHEET_URL,
                json={"action": "update"},
            )
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
    sheet_name = get_sheet_name(team_name)
    for row in reversed(all_data):
        if row.get("المجموعة") == sheet_name:
            return row
    return None


def get_team_history(all_data: list[dict], team_name: str, days: int = 7) -> list[dict]:
    sheet_name = get_sheet_name(team_name)
    rows = [r for r in all_data if r.get("المجموعة") == sheet_name]
    return rows[-days:] if len(rows) > days else rows


# ══════════════════════════════════════════════════════════════════════
# PERSISTENT LEARNING MEMORY - bot learns from corrections
# ══════════════════════════════════════════════════════════════════════

LEARNINGS_FILE = Path("learnings.json")


def load_learnings() -> list[dict]:
    """Load all past corrections/learnings from file."""
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
        "category": category,  # "image_type", "numbers", "analysis", "other"
        "bot_said": what_bot_said[:300],
        "correction": correction[:300],
    })
    # Keep last 100 learnings
    data = data[-100:]
    LEARNINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Learning saved: %s - %s", category, correction[:50])


def get_learnings_for_prompt(team_name: str = "", last_n: int = 5) -> str:
    """Get relevant learnings to include in prompts."""
    data = load_learnings()
    if not data:
        return ""

    # Filter by team if specified, otherwise get recent
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
# IMAGE PATTERN MEMORY - bot learns image types from corrections
# ══════════════════════════════════════════════════════════════════════

IMAGE_PATTERNS_FILE = Path("image_patterns.json")


def load_image_patterns() -> dict:
    """Load learned image patterns: {description_keyword: correct_type}"""
    if IMAGE_PATTERNS_FILE.exists():
        try:
            return json.loads(IMAGE_PATTERNS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_image_pattern(description: str, correct_type: str):
    """Save a learned image pattern so bot recognizes it next time."""
    patterns = load_image_patterns()
    # Extract keywords from description
    keywords = [w.strip().lower() for w in description.split() if len(w.strip()) > 2]
    for kw in keywords:
        patterns[kw] = correct_type
    IMAGE_PATTERNS_FILE.write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Image pattern learned: %s -> %s", description[:50], correct_type)


def check_learned_patterns(description: str) -> str | None:
    """Check if we've learned what this image type is from past corrections."""
    patterns = load_image_patterns()
    if not patterns:
        return None
    desc_lower = description.lower()
    matches = {}
    for keyword, img_type in patterns.items():
        if keyword in desc_lower:
            matches[img_type] = matches.get(img_type, 0) + 1
    if matches:
        # Return the type with most keyword matches
        return max(matches, key=matches.get)
    return None


# ══════════════════════════════════════════════════════════════════════
# INDIVIDUAL TEAM SHEET - read directly from team's own Google Sheet
# ══════════════════════════════════════════════════════════════════════

import csv
import io as _io

def _current_sheet_tab() -> str:
    """Get current month's tab name: 'March-2026' (cycle runs 26th to 25th)."""
    now = _now_egypt()
    # Month cycle: 26th to 25th. If we're before 26th, use current month name.
    # If 26th or later, use next month name.
    if now.day >= 26:
        # Next month's cycle has started
        if now.month == 12:
            return f"January-{now.year + 1}"
        month_names = ["", "January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        return f"{month_names[now.month + 1]}-{now.year}"
    return f"{now.strftime('%B')}-{now.year}"


# Standard column names for team sheets (same across all teams)
TEAM_SHEET_COLUMNS = [
    "Date", "Spend", "New Orders", "Yesterday New",
    "Delivered", "Cancel", "Hold", "CPO",
    "Daily Target", "Gap", "Lamp", "Del%", "Cancel%", "Hold%",
]


async def fetch_team_sheet(team_name: str) -> list[dict]:
    """
    Read data directly from a team's individual Google Sheet.
    The sheet has a summary section at top, then daily data below.
    We find the "Date" header row and parse from there.
    """
    info = TEAM_INFO.get(team_name)
    if not info or not info.get("sheet_id"):
        return []

    sheet_id = info["sheet_id"]
    tab_name = _current_sheet_tab()

    import urllib.parse
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

            # Parse CSV - raw rows first
            reader = csv.reader(_io.StringIO(text))
            all_rows = list(reader)

            if not all_rows:
                return []

            # Find the header row (the one that starts with "Date" or contains date-like header)
            header_idx = None
            for i, row in enumerate(all_rows):
                first_cell = row[0].strip() if row else ""
                if first_cell.lower() == "date" or first_cell == "التاريخ":
                    header_idx = i
                    break

            if header_idx is None:
                # Fallback: use our known column names and find first date row
                data_rows = []
                for row in all_rows:
                    first_cell = row[0].strip() if row else ""
                    if "/" in first_cell and len(first_cell) <= 12:
                        # Map to our standard columns
                        row_dict = {}
                        for j, col_name in enumerate(TEAM_SHEET_COLUMNS):
                            if j < len(row):
                                row_dict[col_name] = row[j].strip()
                        data_rows.append(row_dict)
                logger.info("Fetched %d rows from %s (no header, used standard columns)", len(data_rows), team_name)
                return data_rows

            # Use the real header row
            headers = [h.strip() for h in all_rows[header_idx]]
            # Map headers to standard names (first 14 columns)
            mapped_headers = []
            for j, h in enumerate(headers):
                if j < len(TEAM_SHEET_COLUMNS):
                    mapped_headers.append(TEAM_SHEET_COLUMNS[j])
                else:
                    mapped_headers.append(h if h else f"col_{j}")

            # Parse data rows after header
            data_rows = []
            for row in all_rows[header_idx + 1:]:
                first_cell = row[0].strip() if row else ""
                if not first_cell or "/" not in first_cell:
                    continue  # Skip non-data rows

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
    """Get latest row that has actual Spend data (not just a date)."""
    if not rows:
        return None
    for row in reversed(rows):
        spend_str = row.get("Spend", "").strip().replace(",", "")
        # Must have a non-zero spend value
        try:
            spend_val = float(spend_str) if spend_str else 0
        except ValueError:
            spend_val = 0
        if spend_val > 0:
            return row
    return None


def calculate_cpa_from_sheet(rows: list[dict]) -> float | None:
    """
    Calculate CPA correctly:
    CPA = Spend from PREVIOUS row ÷ Delivered from CURRENT (latest) row.
    Because delivery of day X is recorded in day X+1's row.
    """
    data_rows = [r for r in rows if _safe_num(r.get("Spend")) and _safe_num(r.get("Spend")) > 0]
    if len(data_rows) < 2:
        return None

    current_row = data_rows[-1]  # Latest row
    previous_row = data_rows[-2]  # Previous row

    prev_spend = _safe_num(previous_row.get("Spend"))
    curr_delivered = _safe_num(current_row.get("Delivered"))

    if prev_spend and prev_spend > 0 and curr_delivered and curr_delivered > 0:
        return round(prev_spend / curr_delivered)
    return None


def get_team_sheet_recent(rows: list[dict], n: int = 5) -> list[dict]:
    """Get last N rows that have actual Spend data."""
    data_rows = [r for r in rows if r.get("Spend", "").strip() and r.get("Spend", "").strip() != "0"]
    return data_rows[-n:]


def format_team_sheet_data(row: dict) -> str:
    """Format team sheet row - pass all non-empty values."""
    if not row:
        return "مفيش بيانات"
    parts = []
    for k, v in row.items():
        v_str = str(v).strip()
        if v_str and v_str != "" and k.strip():
            parts.append(f"  {k}: {v_str}")
    return "\n".join(parts)


def format_team_sheet_table(rows: list[dict]) -> str:
    """Format multiple team sheet rows into a readable table."""
    if not rows:
        return ""
    parts = []
    for row in rows:
        vals = [f"{str(v).strip()}" for v in row.values() if str(v).strip()]
        parts.append(" | ".join(vals))
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# 1. CONVERSATION MEMORY - per-team context tracking
# ══════════════════════════════════════════════════════════════════════

_conversation_memory: dict[str, list[dict]] = {}
MAX_MEMORY_PER_TEAM = 5


def remember_exchange(team_name: str, bot_msg: str, user_reply: str | None = None):
    """Store a bot message (and optional user reply) in team memory."""
    if team_name not in _conversation_memory:
        _conversation_memory[team_name] = []

    entry = {
        "time": datetime.now().strftime("%H:%M"),
        "bot": bot_msg[:500],  # truncate to save memory
    }
    if user_reply:
        entry["user"] = user_reply[:300]

    _conversation_memory[team_name].append(entry)
    # Keep only last N exchanges
    _conversation_memory[team_name] = _conversation_memory[team_name][-MAX_MEMORY_PER_TEAM:]


def get_recent_context(team_name: str, last_n: int = 3) -> str:
    """Get recent conversation history as formatted text for prompts."""
    history = _conversation_memory.get(team_name, [])
    if not history:
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
# 2. SMART HELPERS - numbers parsing
# ══════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════
# 3. VERIFICATION SYSTEM - screenshot vs sheet comparison
# ══════════════════════════════════════════════════════════════════════

def verify_screenshot_vs_sheet(screenshot_data: dict, sheet_data: dict | None) -> dict:
    """
    Compare screenshot numbers with sheet data.
    Returns: {status, discrepancies[], summary}
    status: 'match' | 'minor_diff' | 'major_diff' | 'no_sheet_data'
    """
    if not sheet_data:
        return {"status": "no_sheet_data", "discrepancies": [], "summary": "الشيت لسه مش متحدث"}

    discrepancies = []

    # Compare Spend - only when BOTH have real numbers
    ss_spend = _safe_num(screenshot_data.get("spend"))
    sh_spend = _safe_num(sheet_data.get("Spend اليوم"))
    if ss_spend is not None and ss_spend > 0 and sh_spend is not None and sh_spend > 0:
        # Both have real values - compare them
        diff_pct = abs(ss_spend - sh_spend) / sh_spend * 100
        if diff_pct > 10:
            discrepancies.append({
                "field": "Spend",
                "screenshot": ss_spend,
                "sheet": sh_spend,
                "diff_pct": round(diff_pct, 1),
                "severity": "major",
            })
        elif diff_pct > 3:
            discrepancies.append({
                "field": "Spend",
                "screenshot": ss_spend,
                "sheet": sh_spend,
                "diff_pct": round(diff_pct, 1),
                "severity": "minor",
            })
    # If sheet is 0 but screenshot has data = sheet not updated yet (NOT a problem)

    # Compare Orders - only when BOTH have real numbers
    ss_orders = _safe_num(screenshot_data.get("orders") or screenshot_data.get("results"))
    sh_orders = _safe_num(sheet_data.get("Orders اليوم"))
    if ss_orders is not None and ss_orders > 0 and sh_orders is not None and sh_orders > 0:
        # Both have real values - compare them
        diff = abs(ss_orders - sh_orders)
        if diff > 2:
            discrepancies.append({
                "field": "Orders",
                "screenshot": ss_orders,
                "sheet": sh_orders,
                "diff": diff,
                "severity": "major",
            })
        elif diff > 0:
            discrepancies.append({
                "field": "Orders",
                "screenshot": ss_orders,
                "sheet": sh_orders,
                "diff": diff,
                "severity": "minor",
            })
    # If sheet is 0 but screenshot has orders = sheet not updated yet (NOT a problem)

    # Compare CPO - only when BOTH have real numbers
    ss_cpo = _safe_num(screenshot_data.get("cpo"))
    sh_cpo = _safe_num(sheet_data.get("CPO اليوم"))
    if ss_cpo is not None and ss_cpo > 0 and sh_cpo is not None and sh_cpo > 0:
        diff_pct = abs(ss_cpo - sh_cpo) / sh_cpo * 100
        if diff_pct > 15:
            discrepancies.append({
                "field": "CPO",
                "screenshot": ss_cpo,
                "sheet": sh_cpo,
                "diff_pct": round(diff_pct, 1),
                "severity": "major",
            })

    # Determine overall status
    has_major = any(d["severity"] == "major" for d in discrepancies)
    has_minor = any(d["severity"] == "minor" for d in discrepancies)

    # Check if sheet seems empty/not updated
    sh_spend_val = _safe_num(sheet_data.get("Spend اليوم"))
    sh_orders_val = _safe_num(sheet_data.get("Orders اليوم"))
    sheet_is_empty = (not sh_spend_val or sh_spend_val == 0) and (not sh_orders_val or sh_orders_val == 0)

    if sheet_is_empty and (ss_spend or ss_orders):
        # Sheet not updated yet - this is normal, NOT a problem
        return {
            "status": "sheet_not_updated",
            "discrepancies": [],
            "summary": "📋 الشيت لسه مش متحدث لليوم ده - ده عادي",
        }

    if has_major:
        status = "major_diff"
        parts = []
        for d in discrepancies:
            if d["severity"] == "major":
                parts.append(f"⚠️ {d['field']}: Screenshot={d['screenshot']:,.0f} vs Sheet={d['sheet']:,.0f}")
        summary = "🔴 فرق كبير!\n" + "\n".join(parts)
    elif has_minor:
        status = "minor_diff"
        summary = "🟡 فرق بسيط في الأرقام - تأكد من التحديث"
    else:
        status = "match"
        summary = "✅ الأرقام متطابقة"

    return {"status": status, "discrepancies": discrepancies, "summary": summary}


# ══════════════════════════════════════════════════════════════════════
# 4. ANOMALY DETECTION
# ══════════════════════════════════════════════════════════════════════

def detect_anomalies(history: list[dict]) -> list[str]:
    """Detect unusual patterns in team history."""
    if len(history) < 2:
        return []

    anomalies = []

    # Collect numeric values
    spends = [_safe_num(r.get("Spend اليوم")) for r in history]
    spends = [s for s in spends if s is not None]

    orders_list = [_safe_num(r.get("Orders اليوم")) for r in history]
    orders_list = [o for o in orders_list if o is not None]

    cpos = [_safe_num(r.get("CPO اليوم")) for r in history]
    cpos = [c for c in cpos if c is not None]

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
# 5. CROSS-TEAM INTELLIGENCE - ranking & comparison
# ══════════════════════════════════════════════════════════════════════

def rank_teams(all_data: list[dict]) -> dict:
    """
    Rank all teams by performance.
    Returns: {by_cpo: [...], by_spend: [...], best, worst, summary}
    """
    team_scores = {}

    for team_name, info in TEAM_INFO.items():
        sheet_name = info["sheet_name"]
        # Find latest row for this team
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

    # Sort by CPO (lower is better), exclude teams with no CPO
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
# 6. TEAM CONTEXT BUILDER - the brain
# ══════════════════════════════════════════════════════════════════════

async def build_team_context(team_name: str, all_data: list[dict] | None = None) -> dict:
    """
    Build rich context for any team analysis.
    Uses team's OWN sheet as primary source, falls back to master sheet.
    """
    if all_data is None:
        all_data = await fetch_master_data()

    leader = get_leader(team_name)

    # PRIMARY: Try to read team's own sheet first
    team_sheet_rows = await fetch_team_sheet(team_name)
    team_sheet_today = get_team_sheet_today(team_sheet_rows) if team_sheet_rows else None

    # FALLBACK: Master sheet data
    today_data = get_team_today_data(all_data, team_name)
    history = get_team_history(all_data, team_name, days=7)
    anomalies = detect_anomalies(history)
    rankings = rank_teams(all_data)
    conversation = get_recent_context(team_name)

    # Calculate trend
    trend = "unknown"
    if len(history) >= 3:
        cpos = [_safe_num(r.get("CPO اليوم")) for r in history[-3:]]
        cpos = [c for c in cpos if c is not None]
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

    # MTD data
    mtd_row = None
    sheet_name = get_sheet_name(team_name)
    for row in all_data:
        if row.get("المجموعة") == f"📊 {sheet_name}":
            mtd_row = row

    return {
        "team_name": team_name,
        "leader": leader,
        "today": today_data,
        "team_sheet_today": team_sheet_today,  # from team's own sheet (primary)
        "team_sheet_rows": team_sheet_rows,     # all rows from team sheet
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
    }


def format_context_for_prompt(ctx: dict) -> str:
    """Convert team context dict into a readable prompt section."""
    parts = []
    leader = ctx["leader"]
    team = ctx["team_name"]

    parts.append(f"## فريق: {team} | التيم ليدر: {leader}")
    parts.append(f"التاريخ: {_now_egypt().strftime('%Y-%m-%d %H:%M')}")

    # Rank
    if ctx["rank"]:
        parts.append(f"الترتيب: #{ctx['rank']} من {ctx['total_teams']} فرق (بالـ CPO)")
        parts.append(f"{ctx['rankings_summary']}")

    # Trend
    trend_emoji = {"improving": "📈 بيتحسن", "declining": "📉 بيوحش", "stable": "➡️ مستقر"}.get(ctx["trend"], "❓")
    parts.append(f"الاتجاه: {trend_emoji}")

    # Team's own sheet data (PRIMARY source)
    team_sheet = ctx.get("team_sheet_today")
    if team_sheet:
        parts.append("\n## بيانات شيت الفريق (المصدر الأساسي):")
        parts.append(format_team_sheet_data(team_sheet))

    # Master sheet data (SECONDARY/fallback)
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

    # Conversation memory
    if ctx.get("conversation"):
        parts.append(f"\n{ctx['conversation']}")

    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT - Performance Marketing Manager
# ══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """أنت "EcoBot" - مدير تسويق رقمي مصري + محلل بيانات + محلل كريتف.
لسه بتتعلم كل يوم ومعترف بده - بتسأل عشان تفهم مش عشان تحكم.

## أدوارك الثلاثة:
### 1. مدير Performance Marketing:
- بتحلل KPIs (CPO, CPA, Cost per Message) وبتقارن بين الفرق
- بتراجع البادجيت وتوزيعه وبتقترح تحسينات
- بتتابع الحملات وبتنبّه لو في مشكلة أو فرصة
- بتفهم إن الحملات دي **Facebook Messages Ads** (مش Conversions)
  → الهدف: رسائل من الجمهور في الكويت (مقيمين من كل الجنسيات)
  → Flow: إعلان → رسالة → طلب → تسليم
  → متقولش "conversions" أو "purchases" - قول "رسائل" أو "طلبات"

### 2. محلل Creative:
- بتحلل الفيديوهات والصور الإعلانية
- بتقيّم: Hook, CTA, التصميم, النص, الصوت
- بتربط جودة الـ Creative بالنتائج: "الـ CPO طلع عشان الـ Hook ضعيف"
- بتسأل أسئلة ذكية: "جربتوا فيديو بـ voiceover عربي بدل إنجليزي؟"

### 3. محلل بيانات (Data Analyst):
- بتحلل Trends: "الـ CPO بيتحسن آخر 3 أيام"
- بتكتشف Anomalies: "ليه الـ Spend نزل 40% فجأة؟"
- بتعمل تقارير ذكية مش أرقام جافة
- بتقدم رؤى استراتيجية: "لو زودتوا البادجيت 20% هتجيبوا 30 طلب زيادة بنفس الـ CPO"

## شخصيتك:
- مصري دمه خفيف وعملي - بتتكلم زي ما الناس بتتكلم مش زي الآلات
- متواضع - بتقول "أنا شايف" و"ممكن أكون غلطان" مش "الأرقام بتقول"
- بتشجع وتمدح لما الشغل كويس - مش بس بتنتقد
- لما مش متأكد بتسأل بدل ما تفترض
- بتخاطب كل واحد باسمه وبتعامله كزميل مش كموظف

## طريقة كلامك:
- دايماً ابدأ بـ "أنا شايف إن..." أو "من اللي قدامي..." مش حقائق مطلقة
- لو في فرق في الأرقام: "أنا شايف رقم كذا في الشيت بس الصورة بتقول كذا - أنهي الصح؟"
- لو الأداء كويس: "برافو يا [اسم]! CPO تحفة 💪"
- لو في مشكلة: "يا [اسم] أنا ملاحظ حاجة... الـ CPO طالع شوية. إيه رأيك؟"
- لو مش فاهم حاجة: "ممكن توضحلي الصورة دي؟ أنا مش متأكد فهمتها صح"
- خلّي كلامك قصير وطبيعي - زي ما بتكلم زميلك على واتساب

## فهمك للأرقام (مهم جداً):
- **CPO** = Spend اليوم ÷ New Orders اليوم (سعر الطلب قبل التسليم)
- **CPA** = Spend أمس (الصف السابق) ÷ Delivered المسجّلة النهاردة (السعر الحقيقي - الأهم)
  مثال: يوم 20 صرف 3000 جاب 25 طلب → CPO=120. يوم 21 اتسلم 12 بس → CPA=250
  الـ CPA المكتوب في صف يوم 21 = أداء يوم 20 فعلياً
- **CPA الشهر** = إجمالي Spend ÷ إجمالي Delivered (الصورة الكبيرة)
- CPO/CPA: 🟢 ≤ 150 | 🟡 ≤ 180 | 🔴 > 180
- Cancel% ≥ 30% = 🔴 مشكلة
- كل المبالغ بالجنيه المصري

## قواعد:
- رد بالعربي المصري دايماً
- مختصر (3-5 سطور ماكس)
- لو كل حاجة تمام: سطرين مدح وسؤال خفيف
- متفترضش مشاكل من عندك
- لو الشيت فاضي: ده عادي ممكن لسه محدش حدّثه - متقلقش
- لو بتحلل Creative: اربطه بالأداء دايماً
- لو شايف فرصة لتحسين: قولها كاقتراح مش أمر"""


# ══════════════════════════════════════════════════════════════════════
# IMAGE CLASSIFICATION - understand what was sent before acting
# ══════════════════════════════════════════════════════════════════════

# Valid image types the bot understands
IMAGE_TYPES = {
    "fb_ads_dashboard":  "داشبورد حملات Facebook Ads Manager (campaigns, ad sets, spend, results)",
    "tt_ads_dashboard":  "داشبورد حملات TikTok Ads (campaigns, spend, conversions)",
    "fb_payment":        "صفحة دفع/billing/payment من فيسبوك (فواتير، prepaid، payment activity)",
    "tt_payment":        "صفحة دفع/billing/payment من تيك توك (فواتير، prepaid، payment activity)",
    "order_sheet":       "شيت الطلبات اليومي (Google Sheets) فيه طلبات وأرقام",
    "budget_sheet":      "شيت البادجيت أو أكواد فوري",
    "creative_image":    "إعلان (صورة/فيديو creative) مصممة للنشر",
    "other":             "صورة تانية مش مرتبطة بالتقارير",
}

# Which types count as report screenshots
REPORT_IMAGE_TYPES = {"fb_ads_dashboard", "tt_ads_dashboard", "order_sheet", "budget_sheet"}
# Which types are payment/billing (acknowledge only)
PAYMENT_IMAGE_TYPES = {"fb_payment", "tt_payment"}


async def classify_image(image_bytes: bytes) -> dict:
    """
    Step 1: Classify the image BEFORE doing anything else.
    Returns: {type, confidence, description, platform}
    """
    if not CLAUDE_API_KEY:
        return {"type": "other", "confidence": 0, "description": ""}

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """أنت خبير في التسويق الرقمي. شوف الصورة دي وحدد نوعها بدقة.

رد بـ JSON فقط:
{
  "type": "...",
  "confidence": 0.0,
  "platform": "facebook|tiktok|google_sheets|other",
  "description": "وصف قصير لمحتوى الصورة بالعربي"
}

## الأنواع - اختار واحد بس:

### داشبوردات الإعلانات (فيها حملات وأرقام أداء):
- "fb_ads_dashboard" = صفحة Facebook Ads Manager فيها قائمة حملات campaigns أو ad sets مع أرقام أداء (spend, results, impressions, reach, CPC, CPM). بتبان فيها جدول الحملات وأسماءها وحالتها (active/paused).
- "tt_ads_dashboard" = صفحة TikTok Ads Manager فيها حملات أو ad groups مع أرقام (cost, conversions, impressions). شكل TikTok مختلف عن فيسبوك.

### صفحات الدفع/الفواتير (مش فيها حملات - فيها فلوس ودفع):
- "fb_payment" = صفحة billing أو payment settings أو payment activity من فيسبوك. بتبان فيها: invoices, payment method, prepaid balance, transactions, أو "Paid" status. مفيهاش حملات.
- "tt_payment" = صفحة billing أو payment من تيك توك. بتبان فيها: balance, top up, transactions, payment history. مفيهاش حملات.

### شيتات (Google Sheets):
- "order_sheet" = شيت Google Sheets فيه جدول طلبات يومية (تاريخ، طلبات، delivered، cancel، hold). بيبان عليه شكل Google Sheets.
- "budget_sheet" = شيت بادجيت أو أكواد فوري أو رصيد. بيبان عليه شكل Google Sheets.

### غيره:
- "creative_image" = صورة إعلان أو creative مصممة (صورة منتج، عرض، بانر). مش screenshot من منصة.
- "other" = أي حاجة تانية مش من اللي فوق.

## الفرق المهم:
- صفحة الدفع (payment/billing) ≠ داشبورد الحملات (campaigns)
- صفحة الدفع بتبين فواتير ومبالغ مدفوعة - مفيهاش أسماء حملات أو نتائج إعلانية
- داشبورد الحملات بتبين campaigns وأرقام أداء (spend, results, impressions)
- لو الصورة فيها كلمة "Payment" أو "Billing" أو "Invoice" أو "Prepaid" = payment
- لو الصورة فيها كلمة "Campaigns" أو "Ad Sets" أو "Results" أو "Impressions" = dashboard"""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        response_text = message.content[0].text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            response_text = response_text.rsplit("```", 1)[0].strip()

        result = json.loads(response_text)
        confidence = result.get("confidence", 0)
        # Normalize: Claude might return 0-100 instead of 0-1
        if isinstance(confidence, (int, float)) and confidence > 1:
            confidence = confidence / 100
        result["confidence"] = confidence
        description = result.get("description", "")

        # Check if learned patterns override the classification
        learned = check_learned_patterns(description)
        if learned and confidence < 0.8:
            logger.info("Learned pattern override: %s -> %s", result.get("type"), learned)
            result["type"] = learned
            result["confidence"] = 0.85
            result["_learned"] = True

        # Mark low confidence for the bot to ask
        result["_low_confidence"] = confidence < 0.7

        logger.info("Image classified as: %s (%.0f%%)", result.get("type"), confidence * 100)
        return result

    except Exception as e:
        logger.error("Image classification error: %s", e)
        return {"type": "other", "confidence": 0, "description": str(e), "_low_confidence": True}


# ══════════════════════════════════════════════════════════════════════
# SCREENSHOT ANALYSIS - classify first, then extract
# ══════════════════════════════════════════════════════════════════════

async def analyze_screenshot(image_bytes: bytes, team_name: str, report_type: str) -> dict:
    """
    Smart screenshot analysis:
    1. Classify the image type
    2. Extract relevant data based on type
    3. Return structured result with image_type field
    """
    if not CLAUDE_API_KEY:
        return {"error": "Claude API key not configured"}

    # Step 1: Classify the image
    classification = await classify_image(image_bytes)
    img_type = classification.get("type", "other")
    img_desc = classification.get("description", "")

    # For non-report images, return early with classification info
    if img_type not in REPORT_IMAGE_TYPES:
        return {
            "image_type": img_type,
            "description": img_desc,
            "platform": classification.get("platform", ""),
            "notes": img_desc,
            "_classified": True,
        }

    # Step 2: For Google Sheets screenshots, read the sheet DIRECTLY instead of from image
    if img_type in ("order_sheet", "budget_sheet"):
        team_rows = await fetch_team_sheet(team_name)
        today_row = get_team_sheet_today(team_rows) if team_rows else None
        if today_row:
            spend = _safe_num(today_row.get("Spend", ""))
            orders = _safe_num(today_row.get("New Orders", ""))
            cpo = _safe_num(today_row.get("CPO", ""))
            # Calculate CPA correctly from sheet rows
            cpa = calculate_cpa_from_sheet(team_rows)
            return {
                "image_type": img_type,
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
        # If can't read sheet directly, return basic result - DON'T extract MTD totals from image
        logger.warning("Could not read team sheet for %s, returning basic classification", team_name)
        return {
            "image_type": img_type,
            "description": img_desc,
            "notes": "مش قادر أقرأ الشيت مباشرة - هبص على الصورة",
            "_sheet_read_failed": True,
        }

    # Step 3: Extract numbers from image (for ads dashboards)
    leader = get_leader(team_name)

    prompt = f"""أنت بتراجع screenshot من فريق {team_name} (التيم ليدر: {leader}).
نوع الصورة: {img_type} ({img_desc})
نوع التقرير: {report_type}

استخرج كل الأرقام اللي تقدر تشوفها في الصورة دي.
كل المبالغ بالجنيه المصري.

رد بـ JSON فقط بالشكل ده (حط null لأي رقم مش موجود):
{{
  "spend": null,
  "orders": null,
  "results": null,
  "delivered": null,
  "cancel": null,
  "hold": null,
  "cpo": null,
  "cpa": null,
  "budget": null,
  "impressions": null,
  "clicks": null,
  "ctr": null,
  "platform": null,
  "account_name": null,
  "campaign_names": [],
  "date": null,
  "notes": ""
}}

ملاحظات:
- لو الصورة فيها أكتر من حملة، اجمع الأرقام
- لو شايف حاجة غريبة اكتبها في notes"""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        response_text = message.content[0].text
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0].strip()

        result = json.loads(cleaned)
        result["_raw"] = response_text
        result["image_type"] = img_type
        result["_low_confidence"] = classification.get("_low_confidence", False)
        return result

    except json.JSONDecodeError:
        return {"error": "parse_failed", "image_type": img_type, "_raw": response_text}
    except Exception as e:
        logger.error("Claude API error: %s", e)
        return {"error": str(e), "image_type": img_type}


# ══════════════════════════════════════════════════════════════════════
# SMART RESPONSE FOR NON-REPORT IMAGES
# ══════════════════════════════════════════════════════════════════════

async def handle_non_report_image(
    image_bytes: bytes, team_name: str, image_type: str, description: str
) -> str:
    """
    Smart response for payment receipts, creatives, and other non-report images.
    Instead of trying to extract ads numbers, respond appropriately.
    """
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Payment instructions - detailed reading
    payment_instruction = f"""الصورة دي صفحة دفع/billing. اقرأها بعناية واستخرج كل التفاصيل:

## المطلوب تحديده:
1. **المنصة**: فيسبوك ولا تيك توك؟ (شوف شكل الصفحة والـ logo)
2. **المبلغ**: كام بالظبط؟ اقرأ كل الأرقام الظاهرة
3. **نوع الدفع**:
   - Prepaid balance = شحن رصيد من فوري (الإعلانات بتسحب منه يوم بيوم)
   - Credit/Debit Card = دفع ببطاقة
   - Manual payment = دفع يدوي
4. **الحالة**: Paid (تم) / Failed (فشل) / Funded (اتمول) / Pending (معلق)
5. **التاريخ**: تاريخ آخر معاملة

## شكل الرد (3-4 سطور):
"يا {leader}، أنا شايف دي صفحة [فيسبوك/تيك توك]:
- آخر دفعة: [المبلغ] جنيه [شحن رصيد/بطاقة] بتاريخ [التاريخ] - [الحالة]
- [لو في Failed: ⚠️ في دفعة فاشلة لازم تتحل]
- [لو في معاملات تانية مهمة اذكرها]"

مهم: لو في دفعة Failed = مشكلة لازم تتنبه ليها
خاطب {leader} بالاسم. بالعربي المصري."""

    type_instructions = {
        "fb_payment": payment_instruction,
        "tt_payment": payment_instruction,

        "creative_image": "__USE_FULL_CREATIVE_ANALYSIS__",

        "other": f"""رد بسطر واحد: "تم استلام الصورة ✅"
متحللش ومتسألش.
خاطب {leader} بالاسم.""",
    }

    prompt = type_instructions.get(image_type, type_instructions["other"])

    # Creative images get full scorecard analysis
    if prompt == "__USE_FULL_CREATIVE_ANALYSIS__":
        return await analyze_image_creative(image_bytes, team_name)

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, response)
        return response

    except Exception as e:
        logger.error("Non-report image analysis error: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# SMART ANALYSIS - the core intelligence
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

    # ── Team sheet data (PRIMARY source for comparison) ──
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

    # Check if screenshot numbers look like MTD totals (much bigger than daily)
    ss_spend = _safe_num(screenshot_data.get("spend"))
    ts_spend = _safe_num(team_sheet.get("Spend", "")) if team_sheet else None
    is_mtd = False
    if ss_spend and ts_spend and ts_spend > 0 and ss_spend > ts_spend * 5:
        # Screenshot spend is 5x+ bigger than daily = probably MTD totals
        is_mtd = True
        logger.info("Screenshot looks like MTD totals (ss=%s vs daily=%s)", ss_spend, ts_spend)

    # Get learnings for this team
    learnings_text = get_learnings_for_prompt(team_name)

    # Verify screenshot vs team sheet (skip if MTD)
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

    # ── Build the analysis prompt ──
    if verification["status"] == "major_diff":
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}

## 🔴 فرق في الأرقام:
{verification['summary']}

## المطلوب:
{leader} كاتب في الشيت أرقام مختلفة عن اللي في الـ screenshot:
- وضّح بالظبط أنهي رقم مختلف (الشيت بيقول X والـ screenshot بيقول Y)
- اسأله: "أنهي الرقم الصح؟ الشيت ولا الـ screenshot؟"
- لو الفرق كبير اطلب يبعت screenshot تاني أو يعدّل الشيت

قواعد: خاطب {leader} بالاسم. مختصر (3-5 سطور). بالعربي المصري. متزعلوش."""

    elif verification["status"] == "mtd_totals":
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}

## ملاحظة: الأرقام في الـ screenshot دي تراكمية (MTD) مش أرقام يوم واحد.
الـ screenshot بيقول: Spend={screenshot_data.get('spend','-')} | Orders={screenshot_data.get('orders') or screenshot_data.get('results','-')}
شيت الفريق آخر يوم: Spend={team_sheet.get('Spend','-') if team_sheet else '?'} | Orders={team_sheet.get('New Orders','-') if team_sheet else '?'}

## المطلوب:
- وضّح إن الأرقام دي تراكمي الشهر مش أرقام اليوم
- احسب الـ CPO التراكمي وقيّمه
- قارن مع أرقام آخر يوم في الشيت
- لو الأداء كويس: امدح. لو محتاج يتحسن: نصيحة واحدة

خاطب {leader} بالاسم. مختصر (3-4 سطور). بالعربي المصري."""

    elif verification["status"] == "sheet_not_updated":
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}

## المطلوب:
الشيت لسه مش متحدث لليوم ده. حلل الأرقام اللي في الـ screenshot بس:
- لو فيها spend و orders: احسب الـ CPO وقيّمه
- قول ملاحظة مفيدة واحدة بس
- لو الأرقام كويسة: امدح باختصار

قواعد: خاطب {leader} بالاسم. مختصر (2-3 سطور). بالعربي المصري."""

    else:
        analysis_prompt = f"""## فريق: {team_name} | التيم ليدر: {leader}

{ss_text}
{ts_text}
{context_text}
{learnings_text}

## نتيجة المقارنة: {verification['summary']}

## المطلوب - حلل كـ Performance Marketing Manager + Data Analyst:
ابدأ بـ "أنا شايف إن..." وحلل بناءً على الصورة + شيت الفريق + التريند:

### كمدير بيرفورمانس:
- قيّم الأداء: CPO/CPA كويس ولا محتاج تحسين؟
- لو الـ CPO > 150: إيه الممكن يتعمل؟ (تغيير creative؟ تعديل targeting؟ تقليل بادجيت؟)
- لو الـ Cancel عالي: يبقى في مشكلة في جودة الطلبات أو الـ audience
- قارن مع ترتيب الفريق وسط باقي الفرق لو البيانات متاحة

### كمحلل بيانات:
- شوف الـ Trend: الأداء بيتحسن ولا بيوحش ولا مستقر؟
- لو في anomaly (ارتفاع/انخفاض مفاجئ): نبّه عليه
- لو عندك بيانات كافية: ادي insight واحد مفيد

### قواعد:
- ابدأ بـ "أنا شايف" مش حقائق مطلقة
- خاطب {leader} بالاسم
- مختصر (4-6 سطور)
- حلل بس اللي قدامك - متفترضش مشاكل
- لو حد صحّحلك قبل كده (في التصحيحات السابقة) - خد بالك منها
- لو كل حاجة تمام: امدح + نصيحة خفيفة واحدة
- بالعربي المصري"""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

        messages_content = [{"type": "text", "text": analysis_prompt}]

        if image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode("utf-8")
            messages_content.insert(0, {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
            })

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": messages_content}],
        )

        response = message.content[0].text.strip()

        # Auto-remember this exchange
        remember_exchange(team_name, response)

        return response

    except Exception as e:
        logger.error("Smart analysis error: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════
# TEXT MESSAGE ANALYSIS - interactive conversation with memory
# ══════════════════════════════════════════════════════════════════════

async def analyze_text_message(team_name: str, text: str, reply_to_text: str = "") -> str:
    """
    Handle text replies from team leaders with full conversation memory.
    Smart enough to know when to just acknowledge and when to engage.
    """
    if not CLAUDE_API_KEY:
        return ""

    # Simple acknowledgements - just respond briefly, no analysis needed
    simple_words = {"شكرا", "شكراً", "تمام", "اوك", "ok", "أوك", "حاضر", "ماشي",
                    "تم", "👍", "🙏", "ان شاء الله", "إن شاء الله", "هعمل كده",
                    "حسنا", "طيب", "اه", "أه", "اوكي"}
    cleaned = text.strip().replace("!", "").replace(".", "").replace("،", "")
    if cleaned in simple_words or len(cleaned) <= 4:
        # Don't waste an API call - just acknowledge
        leader = get_leader(team_name)
        remember_exchange(team_name, f"👍", user_reply=text)
        return ""  # Return empty = don't reply to simple acknowledgements

    leader = get_leader(team_name)

    # For substantive messages, build context
    all_data = await fetch_master_data()
    ctx = await build_team_context(team_name, all_data)

    # Today's numbers summary
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
- لو {leader} بيسأل عن استراتيجية أو تحسين: جاوبه بخبرة عملية
- لو بيسأل عن بادجيت: ادي اقتراح مبني على الأرقام
- لو بيسأل عن creative: انصحه بناءً على الأداء
- لو بيسأل عن targeting: اقترح audiences مناسبة

## قواعد:
- لو {leader} بيفسّر حاجة: اقبل تفسيره إلا لو فعلاً غير منطقي
- متفترضش مشاكل مش موجودة
- لو الكلام عادي ومش محتاج تحليل: رد بسطر واحد بس
- لو بيسأل سؤال تقني: جاوبه بخبرة عملية حقيقية
- لو بيشتكي: اسمعه واتفهمه وادي حل عملي
- لو قال هيعمل حاجة: شجعه
- متكررش نفس الأسئلة

رد مختصر (1-3 سطور). بالعربي المصري. متبالغش."""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
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
    """
    Extract smart frames from video:
    - 3 frames in first 3 seconds (Hook analysis)
    - 5 frames spread across the rest (Content + CTA)
    """
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
                logger.info("No audio track or too short")
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
            logger.info("Transcript (%s, %.1fs): %s",
                        info.language, info.duration, transcript[:100])
            return transcript
        except ImportError:
            logger.warning("faster-whisper not available")
            return ""
        except Exception as e:
            logger.warning("Transcription failed: %s", e)
            return ""


# ── Creative Evaluation Scorecard ────────────────────────────────────
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

    # Get performance context to link creative with results
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
        prompt_parts.append("حلل النص ده: هل مقنع؟ واضح؟ بيوصل الرسالة؟ مناسب للجمهور المستهدف؟")
    else:
        prompt_parts.append("\n🔇 الفيديو ده مفيهوش voiceover واضح.")
        prompt_parts.append("قيّم: هل الفيديو محتاج voiceover عشان يكون أقوى؟")

    prompt_parts.append(f"\n{CREATIVE_SCORECARD_PROMPT}")
    prompt_parts.append(f"""
## كمحلل Creative + مدير Performance:
- الحملات دي Facebook Messages Ads (الهدف رسائل مش conversions)
- الجمهور: مقيمين في الكويت من كل الجنسيات
- اربط جودة الـ Creative بالأداء:
  * لو الـ CPO عالي: هل الـ Hook ضعيف؟ الـ CTA مش واضح؟
  * لو الـ CPO كويس: إيه اللي مميز في الإعلان ده؟ نكرره!
- اسأل سؤال ذكي يخلي الـ Media Buyer يفكر
- لو الـ Voiceover بلغة معينة: مناسبة للجمهور؟
- Trend حالي: {ctx.get('trend', '?')}

خاطب {leader} بالاسم. بالعربي المصري. مختصر وعملي.""")

    content.append({"type": "text", "text": "\n".join(prompt_parts)})

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        response = message.content[0].text.strip()
        remember_exchange(team_name, f"[تحليل فيديو] {response[:200]}")
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

    # Get performance context
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

لو الأداء وحش، اربط بين جودة الإعلان والنتائج.
خاطب {leader} بالاسم. بالعربي المصري. مختصر."""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
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
        return response

    except Exception as e:
        logger.error("Image creative analysis error: %s", e)
        return ""
