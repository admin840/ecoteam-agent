# EcoTeam Agent - Project Context
# انسخ الملف ده كامل في أول session جديدة

## المشروع
Telegram bot اسمه **Dailyreport** (@Dailyecoreport_bot) شغال على **Railway**.
بيدير 11 فريق إعلانات لمتاجر إلكترونية في الكويت. الفريق من مصر وكل الأرقام بالجنيه المصري.

## المجلد
`D:\ecoteam-agent` - مربوط بـ GitHub: `https://github.com/admin840/ecoteam-agent`

## الملفات
- `main.py` - البوت الرئيسي (Telegram handlers, scheduled jobs)
- `analyzer.py` - تحليل Screenshots بـ Claude Vision + مقارنة مع الشيت
- `requirements.txt` - python-telegram-bot, anthropic, httpx, faster-whisper
- `Dockerfile` - python:3.11-slim + ffmpeg
- `deductions.json` - سجل الخصومات

## Railway
- Project: adequate-serenity
- Service: worker
- Railway CLI مثبت ومربوط (railway logs, railway redeploy)
- Environment Variables:
  - BOT_TOKEN: 8651167735:AAHiZ54CjcLti098FH6IthKPanAmaGnC0dc
  - OWNER_CHAT_ID: 1126011968
  - CLAUDE_API_KEY: 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
  - MASTER_SHEET_URL: https://script.google.com/macros/s/AKfycbyDUua6dQmQTEhwew8jfZQivpXEElsO2ziYVY8S5v0j1bK1K75lh-5BNi3hUHy4yrmoVQ/exec

## الـ 11 فريق (Group IDs المصححة)
```python
TEAMS = {
    -4757552003: "Kuwaitmall",      # سمر - Fordeal
    -1002683433256: "Meeven",       # غرام - Meveen
    -1003841167962: "Blinken",      # اسراء - Blinken
    -4669249424: "Matajer",         # شروق - Matajer
    -1003637090384: "Bazar",        # اسلام/شيماء - Bazaar
    -4860903521: "Minimarket",      # بسملة - Minimarket
    -1003714646416: "Khosomaat",    # حنين - Khosomaat
    -4704859302: "Trend",           # اسماء - Click Cart
    -1002691026546: "Aswaq",        # محمود - Aswaq
    -1002658420756: "Flash",        # يحيي - Flash
    -1002546787010: "Deelat",       # مريم - Deelat
}
```

## Google Sheets
### Master Sheet المجمع
- ID: `1d6-wgT4HMshZMWtwT3S0slYXGY2Zg8yECXJLY0Bo4Kc`
- Web App URL (doGet): في MASTER_SHEET_URL
- بيرجع JSON فيه كل بيانات الفرق

### أعمدة الشيت
- التاريخ | المجموعة | Spend اليوم | Orders اليوم | CPO اليوم | 🚦 اليوم
- Spend أمس | Delivered | Cancel | Hold | Cancel% | CPA الحقيقي | 🚦 أمس

### أنواع الصفوف
- صفوف عادية: بيانات كل فريق يومياً
- 🔢 ALL: إجمالي كل الفرق لكل يوم
- 📈 MTD: تراكمي من بداية الشهر
- 📊 [Team]: MTD لكل فريق لوحده

### شيتات الفرق الفردية (11 شيت)
كل شيت فيه تابات:
- التقرير اليومي (March-2026) - أعمدة: Date, Spend, New Orders, Yesterday New, Delivered, Cancel, Hold, CPO, Daily Target, Gap, Lamp, Del%, Cancel%, Hold%
- بادجيت (أكواد فوري)
- بعضهم فيه: شيت إعلانات أسبوعي + شيت رواتب

## حدود القرار (من Google Apps Script)
- CPO: أخضر ≤ 150 | أصفر ≤ 180 | أحمر > 180
- CPA: أخضر ≤ 150 | أصفر ≤ 180 | أحمر > 180
- Cancel Rate: أحمر ≥ 30%
- الشهر من 26 لـ 25 الشهر التالي

## نظام الدفع
- **فواتير (مديونية)**: الفريق يشتغل والمنصة تحسب عليه، بيدفع بعد يوم أو يومين
- **أكواد فوري (رصيد مسبق)**: بيشحن رصيد ويصرف منه لحد ما يخلص
- كل الفرق بتستخدم الطريقتين
- صور إيصالات الدفع موجودة على جروبات Telegram

## Timezone
- **مصر (Africa/Cairo)** - كل الجدولة بتوقيت مصر

## الجدول الزمني اليومي
- 11:00 AM - تذكير الصبح تلقائي (3 screenshots)
- 12:00 PM - ملخص يومي للـ Owner
- 4:00 PM - تذكير العصر تلقائي (3 screenshots)
- 00:05 AM - Reset يومي

## تقرير الصبح (3 screenshots)
1. شيت الطلبات اليومي (Google Sheets)
2. البادجيت المصروف (Facebook/TikTok)
3. داشبورد الإعلانات (بعد آخر طلب طلع الصبح)

## تقرير العصر (3 screenshots)
1. الحساب الإعلاني (Facebook/TikTok)
2. البادجيت المصروف لحد دلوقتي
3. عدد الطلبات لحد الساعة 4

## التقارير الأسبوعية
- الشهر من 26 لـ 25
- أول 3 أسابيع فقط (يوم 26, 2, 9): تقرير أسبوعي
- الأسبوع الرابع: شيت الرواتب (يوم 24 تذكير، يوم 25 طلب)

## نظام التنبيهات
- +15 دقيقة: تذكير أول
- +30 دقيقة: تذكير تاني
- +45 دقيقة: تذكير تالت
- +120 دقيقة: خصم + تنبيه

## نظام الخصم
- لو الشيت اليومي فيه رقم ناقص = خصم يوم
- لو التقرير ما اتبعتش = خصم
- بيتسجل في deductions.json

---

## ⚠️ المطلوب في الـ Session الجديدة (REWRITE)

### المشكلة الحالية
البوت حالياً **أصم** - كل حاجة مفصولة:
- تحليل screenshots منفصل عن بيانات الشيت
- الـ Creative منفصل عن الأداء
- مفيش تعلم أو ربط بين الأحداث
- Performance Marketing Manager persona مش متفعلة بشكل حقيقي

### الفلسفة الأساسية
**الشيت اليومي = الحقيقة الوحيدة = عصب الداتا**
- لازم يكون صح 100%
- البوت يراجع كل رقم مع الـ screenshots
- لو في غلط → يطلب تصحيح → التيم ليدر يصحح ويبعت تاني → البوت يتأكد
- بعد التأكد 100% → نحلل ونقرر

### المطلوب بناؤه
1. **نظام سياق ذكي**: كل ما يحصل حدث (screenshot/فيديو/رسالة) البوت يجمع كل الداتا (trend 7 أيام + MTD + مقارنة فرق) ويديها لـ Claude يحلل بعمق

2. **ربط Creative بالأداء**: "آخر creative غيرتيه يوم 16 والـ CPO طلع 161 - ممكن يكون السبب؟"

3. **تقرير يومي ذكي**: مش أرقام جافة - تحليل حقيقي: "Meveen أحسن فريق الشهر CPO=108 | Aswaq محتاج مراجعة"

4. **محادثة تفاعلية**: البوت يسأل → التيم ليدر يرد → البوت يسأل تاني بناءً على الرد

5. **تحليل فيديو**: 8-10 فريمات + Whisper للصوت + Scorecard

6. **Flow المراجعة**: screenshot → قراءة أرقام → مقارنة مع شيت → لو فرق يطلب تصحيح → تأكيد بعد التعديل

7. **أوامر جديدة مقترحة**:
   - `/verify` - مراجعة شيت فريق مع screenshots
   - `/sheet` - طلب screenshot محدث بعد التعديل
   - `/missing` - مين الشيت ناقص أرقام
   - `/deductions` - سجل الخصومات

8. **تحسين شكل التقارير**: بسيط ومريح للعين

### ملاحظات مهمة من المالك
- "عايز البوت شغال 24 ساعة بيراقب ويتعلم من كل حاجة"
- "البوت يطلب شيت الإعلانات النشطة ويحللها"
- "البوت يطلب الـ Creative ويسأل ويحلل ويساعد الـ Media Buyer في التفكير"
- "مفيش حاجة هتعدي من غير مراجعة - عايز 24/7 مراقبة"
- "لو عايز خيار مدفوع أكتر استقراراً يلا بينا"
- "الأهم إنك تتحكم فيه من غير تدخل مني"
- "90% من الـ Creatives فيديوهات"
- "الـ Voiceover بيكون بلغات كتير (عربي، إنجليزي، هندي)"
