"""
AI-powered Performance Marketing Manager.
Uses Claude Vision to analyze screenshots, verify budgets,
coach media buyers, and provide strategic recommendations.
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

# ── Performance Marketing Manager System Prompt ──────────────────────
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
1. بتحلل كل screenshot بعمق مش بس أرقام - بتفهم السياق
2. بتقارن مع البيانات في الشيت وتكتشف أي تلاعب أو خطأ
3. لو في مشكلة بتسأل أسئلة ذكية عشان تفهم السبب
4. بتدي نصايح عملية ومحددة مش كلام عام
5. بتطلب معلومات إضافية لو محتاج (شيت الإعلانات، الـ Creative، breakdown)
6. بتطور الـ Media Buyer وتعلمه يفكر صح

## قواعد المراجعة:
- كل المبالغ بالجنيه المصري (EGP)
- CPO أخضر ≤ 150 | أصفر ≤ 180 | أحمر > 180
- CPA أخضر ≤ 150 | أصفر ≤ 180 | أحمر > 180
- Cancel Rate أحمر ≥ 30%
- فرق أكتر من 10% في الـ Spend = مشكلة لازم تتراجع
- الدفع بطريقتين: فواتير (مديونية) أو أكواد فوري (رصيد مسبق)

## أسلوبك:
- مباشر وعملي - مش بتلف وتدور
- بتخاطب التيم ليدر بالاسم
- لو الأرقام تمام بتشجع وتمدح
- لو في مشكلة بتوضح إيه المشكلة وإيه الحل
- بتسأل أسئلة تخلي الـ Media Buyer يفكر ويتعلم
- ردك قصير ومختصر - مش مقال

## لغة الرد:
رد دايماً بالعربي (مصري). مختصر ومفيد."""


def get_leader(team_name: str) -> str:
    info = TEAM_INFO.get(team_name, {})
    return info.get("leader", "")


def get_sheet_name(team_name: str) -> str:
    info = TEAM_INFO.get(team_name, {})
    return info.get("sheet_name", team_name)


async def fetch_master_data() -> list[dict]:
    """Fetch all data from the Master Sheet via Web App."""
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
    """Get latest data for a team from Master Sheet."""
    sheet_name = get_sheet_name(team_name)
    for row in reversed(all_data):
        if row.get("المجموعة") == sheet_name:
            return row
    return None


def get_team_history(all_data: list[dict], team_name: str, days: int = 7) -> list[dict]:
    """Get last N days of data for a team."""
    sheet_name = get_sheet_name(team_name)
    rows = [r for r in all_data if r.get("المجموعة") == sheet_name]
    return rows[-days:] if len(rows) > days else rows


async def analyze_screenshot(image_bytes: bytes, team_name: str, report_type: str) -> dict:
    """Use Claude Vision to extract numbers from screenshot."""
    if not CLAUDE_API_KEY:
        return {"error": "Claude API key not configured"}

    leader = get_leader(team_name)

    prompt = f"""أنت بتراجع screenshot من فريق {team_name} (التيم ليدر: {leader}).
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

ملاحظات مهمة:
- لو الصورة فيها أكتر من حملة، اجمع الأرقام
- لو شايف حاجة غريبة أو مش منطقية اكتبها في notes
- لو الصورة مش واضحة أو مش screenshot إعلانات قول كده في notes"""

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
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
                    },
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
        return result

    except json.JSONDecodeError:
        return {"error": "parse_failed", "_raw": response_text}
    except Exception as e:
        logger.error("Claude API error: %s", e)
        return {"error": str(e)}


async def smart_analysis(
    team_name: str,
    screenshot_data: dict,
    report_type: str,
    image_bytes: bytes | None = None,
) -> str:
    """
    Full AI-powered analysis: compare with sheet, detect issues,
    give coaching advice, request additional info if needed.
    Returns the bot's message to send in the group.
    """
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)

    # Fetch sheet data for comparison
    all_data = await fetch_master_data()
    sheet_today = get_team_today_data(all_data, team_name)
    history = get_team_history(all_data, team_name, days=5)

    # Build context for Claude
    context_parts = [f"## فريق: {team_name} | التيم ليدر: {leader}"]
    context_parts.append(f"## نوع التقرير: {report_type}")
    context_parts.append(f"## التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # Screenshot data
    context_parts.append(f"\n## بيانات الـ Screenshot:")
    for k, v in screenshot_data.items():
        if k.startswith("_") or v is None:
            continue
        context_parts.append(f"- {k}: {v}")

    # Sheet data
    if sheet_today:
        context_parts.append(f"\n## بيانات الشيت (آخر تحديث):")
        for k, v in sheet_today.items():
            if v and v != "-" and v != "":
                context_parts.append(f"- {k}: {v}")

    # History
    if history:
        context_parts.append(f"\n## آخر {len(history)} أيام:")
        for row in history[-3:]:
            date = row.get("التاريخ", "?")
            spend = row.get("Spend اليوم", 0)
            orders = row.get("Orders اليوم", 0)
            cpo = row.get("CPO اليوم", "-")
            action = row.get("🚦 اليوم", "")
            context_parts.append(f"  {date}: Spend={spend} | Orders={orders} | CPO={cpo} | {action}")

    context_text = "\n".join(context_parts)

    # Build the analysis prompt
    analysis_prompt = f"""{context_text}

## المطلوب:
بناءً على البيانات دي، اعمل تحليل سريع وذكي:

1. **مراجعة الأرقام**: قارن أرقام الـ screenshot مع الشيت. لو في فرق أكتر من 10% في الـ Spend أو أكتر من 2 طلب - وضّح المشكلة واسأل {leader} عن السبب.

2. **تحليل الأداء**: شوف الـ CPO والـ trends. هل الأداء بيتحسن ولا بيوحش؟

3. **نصيحة عملية**: لو في مشكلة، قول إيه الحل. لو الأداء كويس، شجّع.

4. **طلب معلومات إضافية**: لو محتاج تشوف:
   - شيت الإعلانات النشطة (عشان تعرف أنهي حملة بتصرف أكتر)
   - الـ Creative اللي شغال (عشان تحلل لو في مشكلة في الإعلان)
   - Breakdown بالحملات (عشان تعرف أنهي حملة محتاجة تتوقف)
   اطلبه بشكل واضح.

5. **سؤال ذكي**: اسأل سؤال واحد يخلي الـ Media Buyer يفكر ويتعلم.

## قواعد الرد:
- خاطب {leader} بالاسم
- رد مختصر (4-8 سطور ماكس)
- لو كل حاجة تمام: سطرين مدح وخلاص
- لو في مشكلة: وضّح المشكلة + الحل + اسأل
- استخدم إيموجي بس متكترش
- لو الأرقام في الـ screenshot مختلفة عن الشيت: ده أولوية قصوى"""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

        messages_content = [{"type": "text", "text": analysis_prompt}]

        # Optionally include the image for deeper analysis
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

        return message.content[0].text.strip()

    except Exception as e:
        logger.error("Smart analysis error: %s", e)
        return ""


async def analyze_text_message(team_name: str, text: str, reply_to_text: str = "") -> str:
    """
    Handle text replies from team leaders.
    The bot understands context and responds intelligently.
    """
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)

    # Fetch recent data for context
    all_data = await fetch_master_data()
    sheet_today = get_team_today_data(all_data, team_name)

    context = f"فريق: {team_name} | التيم ليدر: {leader}\n"
    if sheet_today:
        spend = sheet_today.get("Spend اليوم", 0)
        orders = sheet_today.get("Orders اليوم", 0)
        cpo = sheet_today.get("CPO اليوم", "-")
        context += f"بيانات اليوم: Spend={spend} | Orders={orders} | CPO={cpo}\n"

    prompt = f"""{context}

الرسالة السابقة من البوت: "{reply_to_text}"

رد التيم ليدر {leader}: "{text}"

رد على {leader} كمدير تسويق ذكي:
- لو بيفسر سبب الفرق في الأرقام: قيّم تفسيره - هل منطقي ولا لا
- لو بيسأل سؤال: جاوبه بخبرة
- لو بيقول هيصلح حاجة: شجعه وتابع
- لو محتاج مساعدة: ساعده بنصيحة عملية
- لو التفسير مش مقنع: اسأل أكتر أو اطلب proof

رد مختصر (2-4 سطور). بالعربي المصري."""

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()

    except Exception as e:
        logger.error("Text analysis error: %s", e)
        return ""


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
# Video Creative Analysis
# ══════════════════════════════════════════════════════════════════════

def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
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
    Returns list of JPEG image bytes.
    """
    frames = []
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "input.mp4"
        video_path.write_bytes(video_bytes)

        duration = get_video_duration(str(video_path))
        if duration <= 0:
            duration = 15.0

        # Smart timestamp distribution:
        # Hook: 0.5s, 1.5s, 3s (first 3 seconds - critical!)
        # Content: evenly spaced from 3s to end
        timestamps = [0.5, 1.5, 3.0]

        remaining = max(duration - 3, 1)
        content_frames = min(5, int(remaining / 2))
        for i in range(content_frames):
            t = 3.0 + (remaining / (content_frames + 1)) * (i + 1)
            timestamps.append(min(t, duration - 0.2))

        # Last frame (CTA usually at the end)
        if duration > 4:
            timestamps.append(duration - 0.5)

        # Extract each frame
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
    """
    Extract audio from video and transcribe using Whisper.
    Returns the transcript text or empty string.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "input.mp4"
        audio_path = Path(tmpdir) / "audio.wav"
        video_path.write_bytes(video_bytes)

        # Extract audio with ffmpeg (16kHz mono WAV for Whisper)
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

        # Transcribe with faster-whisper
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("tiny", device="cpu", compute_type="int8")
            # Auto-detect language (supports Arabic, English, Hindi, etc.)
            segments, info = model.transcribe(
                str(audio_path),
                beam_size=3,
            )
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
    """
    Full video creative analysis:
    1. Extract 8-10 smart frames
    2. Extract and transcribe audio (voiceover)
    3. Send everything to Claude with scorecard
    """
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)

    # Step 1: Extract frames
    frames = extract_video_frames(video_bytes)
    if not frames and thumbnail_bytes:
        frames = [thumbnail_bytes]
    if not frames:
        return f"⚠️ مش قادر أحلل الفيديو. {leader}، ابعت screenshot من الإعلان."

    # Step 2: Extract and transcribe audio
    transcript = extract_audio_transcript(video_bytes)

    # Step 3: Get video duration
    with tempfile.TemporaryDirectory() as tmpdir:
        vp = Path(tmpdir) / "v.mp4"
        vp.write_bytes(video_bytes)
        duration = get_video_duration(str(vp))

    # Step 4: Build Claude message
    content = []

    # Add frame labels
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

    # Build analysis prompt
    prompt_parts = [
        f"أنت مدير Performance Marketing خبير بتراجع Creative (فيديو إعلاني) لفريق {team_name}.",
        f"التيم ليدر: {leader}",
        f"مدة الفيديو: {duration:.1f} ثانية",
        f"عدد الفريمات المستخرجة: {len(frames)}",
    ]

    if transcript:
        prompt_parts.append(f"\n🔊 نص الـ Voiceover/الصوت (ممكن يكون عربي، إنجليزي، هندي، أو لغة تانية):")
        prompt_parts.append(f"\"{transcript}\"")
        prompt_parts.append("حلل النص ده: هل مقنع؟ واضح؟ بيوصل الرسالة؟ مناسب للجمهور المستهدف؟")
    else:
        prompt_parts.append("\n🔇 الفيديو ده مفيهوش voiceover واضح (موسيقى بس أو صامت).")
        prompt_parts.append("قيّم: هل الفيديو محتاج voiceover عشان يكون أقوى؟")

    prompt_parts.append(f"\n{CREATIVE_SCORECARD_PROMPT}")
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
        return message.content[0].text.strip()

    except Exception as e:
        logger.error("Video analysis error: %s", e)
        return ""


async def analyze_image_creative(image_bytes: bytes, team_name: str) -> str:
    """Analyze an image creative with full scorecard."""
    if not CLAUDE_API_KEY:
        return ""

    leader = get_leader(team_name)
    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = f"""أنت مدير Performance Marketing خبير بتراجع Creative (إعلان صورة) لفريق {team_name} (التيم ليدر: {leader}).

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
        return message.content[0].text.strip()

    except Exception as e:
        logger.error("Image creative analysis error: %s", e)
        return ""
