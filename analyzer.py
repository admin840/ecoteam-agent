"""
AI-powered screenshot analyzer and budget verifier.
Uses Claude Vision to extract numbers from screenshots
and compares them with Google Sheet data.
"""
import os
import json
import base64
import logging
from datetime import datetime
import httpx
import anthropic

logger = logging.getLogger(__name__)

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
MASTER_SHEET_URL = os.environ.get("MASTER_SHEET_URL", "")

# ── Team info with leaders and sheet mapping ─────────────────────────
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

# Decision thresholds (same as Google Apps Script)
CPO_GREEN = 150
CPO_YELLOW = 180
CPA_GREEN = 150
CPA_YELLOW = 180
CANCEL_RED = 30  # percent


def get_leader(team_name: str) -> str:
    """Get team leader name."""
    info = TEAM_INFO.get(team_name, {})
    return info.get("leader", "")


def get_sheet_name(team_name: str) -> str:
    """Get the sheet/group name used in Master Sheet."""
    info = TEAM_INFO.get(team_name, {})
    return info.get("sheet_name", team_name)


async def fetch_master_data() -> list[dict]:
    """Fetch all data from the Master Sheet via Web App."""
    if not MASTER_SHEET_URL:
        logger.warning("MASTER_SHEET_URL not set")
        return []
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(MASTER_SHEET_URL)
            data = resp.json()
            if data.get("success"):
                return data.get("data", [])
            logger.error("Master sheet error: %s", data.get("error"))
    except Exception as e:
        logger.error("Failed to fetch master data: %s", e)
    return []


def get_team_today_data(all_data: list[dict], team_name: str) -> dict | None:
    """Get today's data for a specific team from Master Sheet."""
    sheet_name = get_sheet_name(team_name)
    today_str = datetime.now().strftime("%d/%m/%Y")

    # Try today first, then yesterday (in case data not updated yet)
    for row in reversed(all_data):
        if row.get("المجموعة") == sheet_name:
            return row

    # If not found by exact date, get latest for this team
    for row in reversed(all_data):
        if row.get("المجموعة") == sheet_name:
            return row
    return None


async def analyze_screenshot(image_bytes: bytes, team_name: str, report_type: str) -> dict:
    """
    Use Claude Vision to analyze a screenshot and extract numbers.
    Returns dict with extracted data and analysis.
    """
    if not CLAUDE_API_KEY:
        return {"error": "Claude API key not configured"}

    leader = get_leader(team_name)
    sheet_name = get_sheet_name(team_name)

    # Build the prompt based on report type
    if report_type == "morning_sheet":
        prompt = f"""أنت محلل بيانات لفريق إعلانات اسمه {team_name} (التيم ليدر: {leader}).
هذه screenshot من شيت الطلبات اليومي (Google Sheets).

استخرج الأرقام التالية بالجنيه المصري:
- Spend (المصروف الإعلاني اليوم)
- Orders (عدد الطلبات الجديدة)
- Delivered (عدد الطلبات تم التسليم)
- Cancel (عدد الطلبات الكانسل)
- Hold (عدد الطلبات المعلقة)
- التاريخ (لو موجود)

رد بـ JSON فقط بالشكل ده:
{{"spend": 0, "orders": 0, "delivered": 0, "cancel": 0, "hold": 0, "date": "", "notes": ""}}

لو مش قادر تقرأ رقم حط null. لو لاحظت أي حاجة غريبة اكتبها في notes."""

    elif report_type == "morning_budget":
        prompt = f"""أنت محلل بيانات لفريق {team_name} (التيم ليدر: {leader}).
هذه screenshot من داشبورد الإعلانات (Facebook Ads أو TikTok Ads).

استخرج الأرقام التالية بالجنيه المصري:
- Amount Spent / المبلغ المنفق (الـ Spend)
- Results / النتائج (عدد الطلبات أو الـ conversions)
- Cost per Result / تكلفة النتيجة (CPO)
- Budget / الميزانية
- اسم الحساب أو الحملة (لو موجود)

رد بـ JSON فقط:
{{"spend": 0, "results": 0, "cpo": 0, "budget": 0, "account_name": "", "platform": "", "notes": ""}}

لو مش قادر تقرأ رقم حط null."""

    elif report_type == "morning_dashboard":
        prompt = f"""أنت محلل بيانات لفريق {team_name} (التيم ليدر: {leader}).
هذه screenshot من داشبورد الإعلانات للفيسبوك أو التيك توك (بعد آخر طلب طلع الصبح).

استخرج كل الأرقام المتاحة بالجنيه المصري:
- Amount Spent / المبلغ المنفق
- Results / النتائج
- Cost per Result
- Impressions / مرات الظهور
- Clicks / النقرات
- أي أرقام أخرى مهمة

رد بـ JSON فقط:
{{"spend": 0, "results": 0, "cpo": 0, "impressions": 0, "clicks": 0, "notes": ""}}"""

    elif report_type == "afternoon":
        prompt = f"""أنت محلل بيانات لفريق {team_name} (التيم ليدر: {leader}).
هذه screenshot من تقرير الساعة 4 - الحساب الإعلاني أو البادجيت المصروف.

استخرج الأرقام التالية بالجنيه المصري:
- Amount Spent / المبلغ المنفق لحد الساعة 4
- Results / عدد الطلبات لحد الساعة 4
- Cost per Result
- Budget المتبقي

رد بـ JSON فقط:
{{"spend": 0, "results": 0, "cpo": 0, "remaining_budget": 0, "notes": ""}}"""

    else:
        prompt = f"""أنت محلل بيانات لفريق {team_name}.
استخرج كل الأرقام والبيانات المهمة من هذه الصورة.
رد بـ JSON مع كل الأرقام اللي تقدر تستخرجها.
كل المبالغ بالجنيه المصري."""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        response_text = message.content[0].text
        # Try to parse JSON from response
        # Handle case where Claude wraps in ```json ... ```
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        result = json.loads(cleaned)
        result["raw_response"] = response_text
        return result

    except json.JSONDecodeError:
        logger.warning("Could not parse JSON from Claude response")
        return {"error": "Could not parse response", "raw_response": response_text}
    except Exception as e:
        logger.error("Claude API error: %s", e)
        return {"error": str(e)}


async def compare_and_verify(team_name: str, screenshot_data: dict, report_type: str) -> str:
    """
    Compare screenshot data with Master Sheet data.
    Returns a smart analysis message in Arabic.
    """
    leader = get_leader(team_name)
    all_data = await fetch_master_data()
    sheet_data = get_team_today_data(all_data, team_name)

    if not sheet_data:
        return ""  # No sheet data to compare

    lines = []
    warnings = []
    has_problem = False

    screenshot_spend = screenshot_data.get("spend")
    sheet_spend = sheet_data.get("Spend اليوم", 0)

    screenshot_orders = screenshot_data.get("orders") or screenshot_data.get("results")
    sheet_orders = sheet_data.get("Orders اليوم", 0)

    # ── Compare Spend ──
    if screenshot_spend is not None and sheet_spend:
        diff = abs(screenshot_spend - sheet_spend)
        pct = (diff / sheet_spend * 100) if sheet_spend > 0 else 0

        if pct > 10 and diff > 100:  # More than 10% difference and > 100 EGP
            has_problem = True
            warnings.append(
                f"⚠️ الـ Spend في الداشبورد: {screenshot_spend:,.0f} جنيه\n"
                f"   لكن في الشيت مسجل: {sheet_spend:,.0f} جنيه\n"
                f"   فرق: {diff:,.0f} جنيه ({pct:.0f}%)"
            )
        elif pct > 5:
            warnings.append(
                f"🟡 فرق بسيط في الـ Spend: داشبورد {screenshot_spend:,.0f} / شيت {sheet_spend:,.0f}"
            )

    # ── Compare Orders ──
    if screenshot_orders is not None and sheet_orders:
        diff = abs(screenshot_orders - sheet_orders)
        if diff > 2:  # More than 2 orders difference
            has_problem = True
            warnings.append(
                f"⚠️ عدد الطلبات في الداشبورد: {screenshot_orders}\n"
                f"   لكن في الشيت مسجل: {sheet_orders}\n"
                f"   فرق: {diff} طلب"
            )

    # ── Check CPO ──
    if screenshot_spend and screenshot_orders and screenshot_orders > 0:
        real_cpo = screenshot_spend / screenshot_orders
        sheet_cpo = sheet_data.get("CPO اليوم", 0)

        if real_cpo > CPO_YELLOW:
            warnings.append(
                f"🔴 CPO عالي: {real_cpo:.0f} جنيه/طلب (الحد: {CPO_YELLOW})\n"
                f"   لازم تراجع الإعلان!"
            )
        elif real_cpo > CPO_GREEN:
            warnings.append(
                f"🟡 CPO محتاج مراقبة: {real_cpo:.0f} جنيه/طلب"
            )

        # Compare with sheet CPO
        if sheet_cpo and isinstance(sheet_cpo, (int, float)) and sheet_cpo > 0:
            cpo_diff = abs(real_cpo - sheet_cpo)
            if cpo_diff > 20:
                has_problem = True
                warnings.append(
                    f"⚠️ CPO في الداشبورد: {real_cpo:.0f} لكن في الشيت: {sheet_cpo}\n"
                    f"   ده معناه الأرقام مش متطابقة!"
                )

    # ── Check for illogical numbers ──
    if screenshot_spend is not None and screenshot_spend > 50000:
        warnings.append(
            f"🔍 الـ Spend عالي جداً: {screenshot_spend:,.0f} جنيه - تأكد إن ده الرقم الصح"
        )

    if screenshot_orders is not None and screenshot_orders == 0 and screenshot_spend and screenshot_spend > 500:
        warnings.append(
            f"🚨 مصروف {screenshot_spend:,.0f} جنيه وصفر طلبات! في مشكلة كبيرة"
        )

    # ── Build response ──
    if not warnings:
        return ""  # All good, no message needed

    if has_problem:
        lines.append(f"🔍 مراجعة تقرير {team_name} ({leader}):\n")
        lines.extend(warnings)
        lines.append(f"\n💬 {leader}، إيه تفسيرك؟")
    else:
        lines.append(f"📊 ملاحظات على تقرير {team_name}:\n")
        lines.extend(warnings)

    return "\n".join(lines)


async def generate_smart_summary(team_name: str, screenshot_data: dict) -> str:
    """Generate a smart one-line summary of the screenshot data."""
    leader = get_leader(team_name)
    spend = screenshot_data.get("spend")
    orders = screenshot_data.get("orders") or screenshot_data.get("results")
    cpo = screenshot_data.get("cpo")
    notes = screenshot_data.get("notes", "")

    parts = []
    if spend is not None:
        parts.append(f"Spend: {spend:,.0f}")
    if orders is not None:
        parts.append(f"Orders: {orders}")
    if cpo is not None:
        parts.append(f"CPO: {cpo:,.0f}")
    elif spend and orders and orders > 0:
        parts.append(f"CPO: {spend/orders:,.0f}")

    summary = " | ".join(parts) if parts else "تم استلام الصورة"

    if notes:
        summary += f"\n📝 {notes}"

    return summary
