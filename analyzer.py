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
import httpx
import anthropic

logger = logging.getLogger(__name__)

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
        return {"status": "no_sheet_data", "discrepancies": [], "summary": "مفيش بيانات في الشيت للمقارنة"}

    discrepancies = []

    # Compare Spend
    ss_spend = _safe_num(screenshot_data.get("spend"))
    sh_spend = _safe_num(sheet_data.get("Spend اليوم"))
    if ss_spend is not None and sh_spend is not None and sh_spend > 0:
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

    # Compare Orders
    ss_orders = _safe_num(screenshot_data.get("orders") or screenshot_data.get("results"))
    sh_orders = _safe_num(sheet_data.get("Orders اليوم"))
    if ss_orders is not None and sh_orders is not None:
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

    # Compare CPO
    ss_cpo = _safe_num(screenshot_data.get("cpo"))
    sh_cpo = _safe_num(sheet_data.get("CPO اليوم"))
    if ss_cpo is not None and sh_cpo is not None and sh_cpo > 0:
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
    This is the core intelligence - gathers everything Claude needs.
    """
    if all_data is None:
        all_data = await fetch_master_data()

    leader = get_leader(team_name)
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
    parts.append(f"التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Rank
    if ctx["rank"]:
        parts.append(f"الترتيب: #{ctx['rank']} من {ctx['total_teams']} فرق (بالـ CPO)")
        parts.append(f"{ctx['rankings_summary']}")

    # Trend
    trend_emoji = {"improving": "📈 بيتحسن", "declining": "📉 بيوحش", "stable": "➡️ مستقر"}.get(ctx["trend"], "❓")
    parts.append(f"الاتجاه: {trend_emoji}")

    # Today data
    today = ctx.get("today")
    if today:
        parts.append("\n## بيانات الشيت اليوم:")
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

SYSTEM_PROMPT = """أنت مدير تسويق رقمي (Performance Marketing Manager) خبير، اسمك "EcoBot".
شغال مع فريق إعلانات بيشتغل على Facebook Ads و TikTok Ads لمتاجر إلكترونية في الكويت.

## خبراتك:
- 10+ سنين في Performance Marketing و Paid Media
- خبير في Facebook Ads Manager و TikTok Ads
- تحليل البيانات والـ KPIs (CPO, CPA, ROAS, CTR, CVR)
- إدارة البادجيت وتوزيعه على الحملات
- تحسين الـ Creatives والـ Ad Copy
- استراتيجيات الـ Scaling والـ Testing

## طريقة شغلك:
1. بتحلل كل screenshot بعمق مش بس أرقام - بتفهم السياق والـ trend
2. بتقارن مع البيانات في الشيت وتكتشف أي فرق أو خطأ
3. لو الأرقام مش متطابقة بتطلب تصحيح فوراً - ده أولوية قصوى
4. بتحلل الأداء في سياق الشهر كله مش بس اليوم
5. بتقارن الفريق مع باقي الفرق عشان تعرف هو فين
6. لو في مشكلة بتسأل أسئلة ذكية وبتطلب proof
7. بتدي نصايح عملية ومحددة مش كلام عام
8. بتطلب معلومات إضافية لو محتاج (شيت الإعلانات، الـ Creative، breakdown)
9. بتربط بين الـ Creative والأداء - لو الأداء وحش بتسأل عن آخر تغيير في الإعلانات

## حدود القرار:
- CPO: 🟢 ≤ 150 | 🟡 ≤ 180 | 🔴 > 180
- CPA: 🟢 ≤ 150 | 🟡 ≤ 180 | 🔴 > 180
- Cancel Rate: 🔴 ≥ 30%
- فرق أكتر من 10% في الـ Spend = لازم يتراجع
- كل المبالغ بالجنيه المصري (EGP)
- الدفع بطريقتين: فواتير (مديونية) أو أكواد فوري (رصيد مسبق)

## أسلوبك:
- مباشر وعملي - مش بتلف وتدور
- بتخاطب التيم ليدر بالاسم
- لو الأرقام تمام بتشجع وتمدح
- لو في مشكلة بتوضح إيه المشكلة وإيه الحل
- بتسأل أسئلة تخلي الـ Media Buyer يفكر ويتعلم
- ردك قصير ومختصر - مش مقال
- رد دايماً بالعربي (مصري)"""


# ══════════════════════════════════════════════════════════════════════
# IMAGE CLASSIFICATION - understand what was sent before acting
# ══════════════════════════════════════════════════════════════════════

# Valid image types the bot understands
IMAGE_TYPES = {
    "ads_dashboard":    "screenshot من Ads Manager أو TikTok Ads (حملات، أرقام، spend)",
    "order_sheet":      "شيت الطلبات اليومي (Google Sheets) فيه طلبات وأرقام",
    "budget_sheet":     "شيت البادجيت أو أكواد فوري",
    "payment_receipt":  "إيصال دفع أو payment activity أو فاتورة",
    "creative_image":   "إعلان (صورة creative) - مش screenshot أرقام",
    "other":            "صورة تانية مش مرتبطة بالتقارير",
}


async def classify_image(image_bytes: bytes) -> dict:
    """
    Step 1: Classify the image BEFORE doing anything else.
    Returns: {type, confidence, description}
    """
    if not CLAUDE_API_KEY:
        return {"type": "other", "confidence": 0, "description": ""}

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """شوف الصورة دي وقولي نوعها. رد بـ JSON فقط:
{
  "type": "...",
  "confidence": 0.0,
  "description": "وصف قصير جداً لمحتوى الصورة"
}

الأنواع المتاحة:
- "ads_dashboard" = screenshot من Facebook Ads Manager أو TikTok Ads فيه حملات وأرقام spend/results/impressions
- "order_sheet" = شيت طلبات (Google Sheets) فيه أرقام طلبات يومية
- "budget_sheet" = شيت بادجيت أو أكواد فوري أو رصيد
- "payment_receipt" = إيصال دفع أو payment activity أو فاتورة أو transaction
- "creative_image" = إعلان أو creative (صورة منتج/عرض مصممة للإعلان)
- "other" = أي حاجة تانية (صورة شخصية، chat، حاجة مش مرتبطة)

confidence: رقم من 0.0 لـ 1.0 بيوضح أد إيه أنت متأكد"""

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
        logger.info("Image classified as: %s (%.0f%%)", result.get("type"), result.get("confidence", 0) * 100)
        return result

    except Exception as e:
        logger.error("Image classification error: %s", e)
        return {"type": "other", "confidence": 0, "description": str(e)}


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
    if img_type in ("payment_receipt", "other", "creative_image"):
        return {
            "image_type": img_type,
            "description": img_desc,
            "notes": img_desc,
            "_classified": True,
        }

    # Step 2: Extract numbers for report-type images
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

    type_instructions = {
        "payment_receipt": f"""الصورة دي إيصال دفع أو payment activity.

رد بسطر واحد بس: "تم ✅ إيصال دفع [المبلغ لو واضح]"
متحللش. متسألش أسئلة. متربطش بالـ spend أو الشيت.
ده إيصال دفع عادي مش screenshot إعلانات.

خاطب {leader} بالاسم. سطر واحد فقط.""",

        "creative_image": f"""الصورة دي creative أو إعلان مصمم.
وصف: {description}

حلل الـ Creative بسرعة:
- التصميم كويس؟
- الرسالة واضحة؟
- الـ CTA موجود؟
- نصيحة سريعة واحدة

خاطب {leader} بالاسم. 3-4 سطور ماكس.""",

        "other": f"""الصورة دي مش screenshot تقرير ومش إعلان.
وصف: {description}

لو الصورة مش مرتبطة بالشغل: قول "تم استلام الصورة" بس.
لو فيها حاجة مرتبطة بالشغل: علّق عليها باختصار.

خاطب {leader} بالاسم. سطر واحد بس.""",
    }

    prompt = type_instructions.get(image_type, type_instructions["other"])

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

    # Verify screenshot vs sheet
    verification = verify_screenshot_vs_sheet(screenshot_data, ctx["today"])
    verification_text = verification["summary"]

    # Screenshot data section
    ss_parts = ["## بيانات الـ Screenshot:"]
    for k, v in screenshot_data.items():
        if k.startswith("_") or v is None:
            continue
        ss_parts.append(f"  {k}: {v}")
    ss_text = "\n".join(ss_parts)

    # Build the analysis prompt based on verification status
    if verification["status"] == "major_diff":
        # Priority: fix discrepancies first
        analysis_prompt = f"""{context_text}

{ss_text}

## 🔴 نتيجة المقارنة:
{verification_text}

تفاصيل الفروقات:
{json.dumps(verification['discrepancies'], ensure_ascii=False, indent=2)}

## المطلوب (أولوية قصوى - الأرقام مش متطابقة):
1. وضّح لـ {leader} إن في فرق بين أرقام الـ screenshot والشيت
2. حدد بالظبط أنهي رقم مختلف وقد إيه الفرق
3. اطلب منه يراجع ويصلح الشيت أو يفسّر سبب الفرق
4. لو الفرق في الـ Spend: اسأل هل في campaign اتوقفت أو budget اتغير
5. لو الفرق في الـ Orders: اسأل هل في طلبات ناقصة أو مكررة

قواعد: خاطب {leader} بالاسم. مختصر (4-6 سطور). بالعربي المصري."""

    else:
        # Normal analysis with full intelligence
        analysis_prompt = f"""{context_text}

{ss_text}

## نتيجة المقارنة: {verification_text}

## المطلوب:
حلل الـ screenshot ده بناءً على اللي شايفه في الصورة + بيانات الشيت:

1. اقرأ الأرقام من الصورة وقارنها مع الشيت لو متاح
2. لو الأرقام كويسة: امدح باختصار
3. لو في حاجة محتاجة تتحسن: قول نصيحة عملية واحدة
4. لو محتاج معلومات إضافية (breakdown، creative): اطلبها

## قواعد مهمة جداً:
- خاطب {leader} بالاسم
- رد مختصر (3-5 سطور ماكس)
- متفترضش مشاكل مش واضحة من الصورة - حلل بس اللي قدامك
- لو الشيت فاضي أو مفيش بيانات: متقولش "في مشكلة" - ممكن لسه محدش حدّث الشيت
- لو كل حاجة عادية: سطرين بس وخلاص
- متسألش أكتر من سؤال واحد
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

## قواعد مهمة جداً:
- لو {leader} بيفسّر حاجة أو بيرد على سؤالك: اقبل تفسيره إلا لو فعلاً غير منطقي
- متفترضش مشاكل مش موجودة - لو مقالش في مشكلة متخترعش مشاكل
- لو الكلام عادي ومش محتاج تحليل: رد بسطر واحد بس
- لو بيسأل سؤال: جاوبه بخبرة عملية
- لو بيشتكي أو زعلان: اسمعه واتفهمه
- لو قال هيعمل حاجة: شجعه ومتطلبش أكتر
- متكررش نفس الأسئلة اللي سألتها قبل كده

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
    spend = screenshot_data.get("spend")
    orders = screenshot_data.get("orders") or screenshot_data.get("results")
    cpo = screenshot_data.get("cpo")

    if spend is not None:
        parts.append(f"Spend: {spend:,.0f}")
    if orders is not None:
        parts.append(f"Orders: {orders}")
    if cpo is not None:
        parts.append(f"CPO: {cpo:,.0f}")
    elif spend and orders and orders > 0:
        parts.append(f"CPO: {spend/orders:,.0f}")

    return " | ".join(parts) if parts else "تم استلام الصورة"


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
    prompt_parts.append(f"\nلو الأداء وحش ({ctx.get('trend', '')}), اربط بين جودة الـ Creative والنتائج.")
    prompt_parts.append(f"\nخاطب {leader} بالاسم. بالعربي المصري. مختصر وعملي.")

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
