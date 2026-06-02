# 📊 ملخص شامل — شغل Customer Experience (e&)

> **مرجع شامل** لكل الداشبوردات والريبورتات اللي اتعملت في مجال الـ Customer Experience لقطاع الـ Consumer في e&، مع الأسئلة اللي كل ريبورت بيجاوب عليها وطرق الحسبة التفصيلية لكل KPI.
>
> *آخر تحديث: مايو 2026*

---

## 📑 الفهرس

1. [نظرة عامة على شغل CX](#1-نظرة-عامة)
2. [المصطلحات الأساسية وطرق الحسبة](#2-المصطلحات-الأساسية-وطرق-الحسبة)
3. [الداشبوردات والريبورتات](#3-الداشبوردات-والريبورتات)
4. [طرق الحسبة التفصيلية (Formulas)](#4-طرق-الحسبة-التفصيلية)
5. [الـ Scripts والملفات](#5-الـ-scripts-والملفات)

---

## 1. نظرة عامة

شغل الـ CX اتقسم على عدة workstreams متوازية، كلها بتدور حول فهم تجربة العميل وتقليل الـ churn في قطاع الـ Consumer:

| Workstream | الهدف الأساسي |
|-----------|----------------|
| **Churn Analysis** | تحديد العملاء المعرضين للرحيل + قياس فاعلية الـ win-back |
| **CX Dashboard** | متابعة KPIs الأساسية (CSAT / tNPS / FCR) شهرياً |
| **Detractor Analytics** | تحليل الـ detractors وربطهم بالـ Micro data لقياس التواصل معهم |
| **tNPS 2025 Survey** | تحليل عميق للـ VOC وبناء pipeline كامل للتنبؤ والـ forecasting |
| **Escalated SR / Audit** | تدقيق الـ Service Requests المصعّدة وقياس صحة الإغلاق |
| **Reporting / Slides** | تحويل التحليلات لـ PowerPoint مصمم بـ branding e& |

---

## 2. المصطلحات الأساسية وطرق الحسبة

### **VOC** — Voice of Customer (صوت العميل)
الـ feedback الحر اللي العميل بيكتبه (الـ free-text العربي في عمود `VOC?`). أعلى مصدر بيانات نوعي لأنه بيمسك كلام العميل حرفياً بكل ما فيه من emotion وسياق.

### **NPS / tNPS** — (Transactional) Net Promoter Score
مقياس ولاء من −100 لـ +100.

```
NPS = % Promoters − % Detractors
```

| الفئة | الـ Score | الوصف |
|------|----------|-------|
| **Promoters** | 9–10 | عملاء مخلصون بيرشّحوا البراند |
| **Passives** | 7–8 | راضين بس مش متحمسين |
| **Detractors** | 0–6 | غير راضين وممكن يضروا السمعة |

> **tNPS** = ترانزاكشنال — بيتقاس مباشرة بعد تفاعل معيّن (زي مكالمة)، مش relationship-level.

### **CSAT** — Customer Satisfaction Score
بيسأل "قد إيه كنت راضي؟" على مقياس 1–5 أو 1–10. بيختلف عن NPS اللي بيسأل عن الاستعداد للترشيح.

### **CES** — Customer Effort Score (مجهود العميل)
قد إيه العميل اضطر يتعب عشان مشكلته تتحل. **الأقل أحسن.** بيتحسب من ذكر الـ VOC لتكرار المكالمات / زيارة الفرع / محاولات التطبيق.

### **FCR** — First Call Resolution
هل المشكلة اتحلت من أول تواصل؟ في الـ dataset بيتم تمثيله (proxy) بـ:

```
FCR = (is_repeat_caller == False) AND (Customer Compensated from CC? == "Yes within the call")
```

### **SLA** — Service Level Agreement
وقت الاستجابة/الحل المُلتزم به. بيُستخدم في توصيات الـ NBA (مثلاً "< 5 دقائق للـ P0 cases").

### **QA** — Quality Assurance
تقييم تفاعلات الـ agents. الطريقة التقليدية بتعمل sampling لـ ~5% يدوي — الـ Auto-Scoring engine بيسكور **100%** من المكالمات تلقائياً.

### **NBA** — Next Best Action
نظام توصية بيقول للـ agent إيه أفضل خطوة بناءً على سياق الـ case (المشكلة، الـ sentiment، التاريخ، الـ severity).

### **Churn** (فقدان عميل)
العميل بيسيب الشركة لمنافس. **Churn Signals** = مؤشرات لغوية زي "هحول لفودافون"، "هلغي الخط".

### **Escalation**
العميل بيهدد بالتصعيد (قانوني/إعلامي/رقابي) — جمل زي "جهاز قومي"، "هرفع قضيه". بتحتاج معالجة خاصة من supervisor.

### **Root Cause** (السبب الجذري)
السبب الأساسي للمشكلة. التصنيفات: Customer Related · CC Staff · System · Retail Staff · Product Design · Process Leakage · Vendor · Network.

---

## 3. الداشبوردات والريبورتات

### 🔻 3.1 — Churn Analysis Report

**الأسئلة اللي بيجاوب عليها:**
- كام عميل معرّض للـ churn؟
- قد إيه دقة الـ AI engine الجديد مقارنة بالقديم؟
- نسبة الـ win-back / الاحتفاظ بالعملاء كام؟
- إيه أكتر الأسباب اللي بتخلي العميل يفكّر يمشي؟

**أرقام رئيسية اتغطّت:**
- Potential churn cases: **67**
- AI accuracy: **45%** (الجديد) مقابل **37%** (القديم)
- Win-back rate: **56%**
- Retention split: **69% retained / 31% not retained**

**التصنيفات الرئيسية للأسباب:** Billing Issues · Network Coverage · Promotion Eligibility — مدعّمة بـ verbatim quotes عربي/إنجليزي.

**طريقة الحسبة:**
```
Win-back Rate = (عملاء رجعوا بعد محاولة الاسترجاع) / (إجمالي محاولات الاسترجاع)
Retention Rate = (عملاء فضلوا) / (إجمالي العملاء المعرضين للخطر)
```

**المخرجات:** سلايدات PowerPoint (doughnut + bar charts) بـ branding e& (أحمر `#E00800`، خلفيات داكنة، كروت بيضا).

---

### 🔻 3.2 — Customer Experience Dashboard (CSAT / tNPS / FCR)

**الأسئلة:**
- إزاي الـ KPIs الأساسية (CSAT / tNPS / FCR) ماشية شهر بشهر؟
- فين الـ variance مقارنة بالـ target / الفترة السابقة؟

**التصميم النهائي (v3):**
- **Header band:** mini line charts للـ **C-SAT** و **tNPS** و **FCR** (الـ trend الشهري بس لـ الـ 3 KPIs دول).
- **باقي الـ panels:** horizontal bar charts + data tables.
- أعمدة variance ملوّنة (أخضر/أحمر) لإظهار التحسّن/التدهور.

**طريقة الحسبة:**
```
Variance % = (Actual − Target) / Target
tNPS / CSAT / FCR = حسب التعريفات في القسم 2
```

---

### 🔻 3.3 — Detractor Micro Rates (Contact Rate & Reach Rate)

> أهم ريبورت بيربط الـ survey detractors بالـ Micro (call) data عشان نعرف هل تم التواصل معاهم فعلاً.

**الأسئلة:**
- من كل الـ detractors، كام واحد فيهم اتعمله contact (موجود في الـ Micro dataset)؟
- من اللي اتعمل ليهم contact، كام واحد اتم الوصول له فعلاً (call happened)؟

**طريقة الحسبة:**
```
Contact Rate = (detractors موجودين في الـ Micro) / (إجمالي الـ detractors)
Reach Rate   = (rows حيث call0-1 == 1) / (الأساس المناسب)
```

**التفاصيل التقنية:**
- استخراج الـ detractors من dataset الـ survey (**CN7736**).
- Left-join مع الـ Micro dataset.
- `call0-1` عمود binary = 1 معناه إن المكالمة حصلت.
- Breakdown لكل segment.

**المخرجات (sheets):** `detractor_micro_summary` + `detractor_micro_detail` + KPI block في الداشبورد.

---

### 🔻 3.4 — Digital Detractors Insights

**الأسئلة:**
- إيه أكتر أسباب عدم الرضا الرقمي؟ وإزاي اتغيّرت Q1-2026 vs Q4-2025؟
- قد إيه الـ friction في الرحلة الرقمية؟
- إيه نسبة الـ win-back conversion؟

**أرقام رئيسية:**
- Total detractors: **9,664**
- Win-back conversion rate: **53%**

**فئات عدم الرضا الأربعة (مع مقارنة ربع سنوية):**
1. Consumption (الاستهلاك)
2. Slow Speed (بطء السرعة)
3. Wrong Rate / Bad Line
4. Line Up & Down

**عناصر السلايد:** KPI strip · friction metrics · ranked bar charts · calling reasons breakdown · customer voice verbatims · جدولين findings ببيانات شهرية. (في نسخة dark mode أحمر، ونسخة light mode).

> ملحوظة: الـ KPI بتاع **"Rated by Mistake"** اتلوّن amber مش أحمر — عشان يبان إنه caveat مش performance metric.

---

### 🔻 3.5 — tNPS 2025 Survey Analytics (الـ Pipeline الكامل)

> أكبر workstream — pipeline بايثون كامل بيقرا ملفات survey متعددة، بيعمل join مع الـ Agent Queue Mapping، ويطلع داشبورد Excel واحد شامل.

**الأسئلة اللي بيجاوب عليها:**
- التوزيع الكامل للـ NPS وأسبابه؟
- مين الـ repeat callers وليه مشاكلهم متحلتش؟
- أداء كل agent (QA auto-score)؟
- توقّع متى هيتم اختراق الـ target (forecasting)؟

**التحليلات الرئيسية:**

| التحليل | المحتوى |
|--------|---------|
| **Sentiment Analysis** | توزيع المشاعر + sentiment per category/root cause |
| **Severity Scoring** | درجة خطورة لكل case من مؤشرات لغوية |
| **NBA** | توصية + script عربي + SLA لكل case (4 priorities: P0–P3) |
| **Repeat Caller Deep Dive** | 19 رقم = 43 case (~8% من الحجم) — رحلة كل واحد |
| **QA Auto-Scoring** | كل case score 0–100 من 5 dimensions |
| **Cohort Tracking** | نفس الـ MSISDN عبر الشهور (recovery rate) |
| **Forecasting** | Holt-Winters per-queue + prediction intervals + target breach date |
| **SLA Breach Heatmap** | queues × days اللي اخترقت الـ threshold |
| **FCR Impact** | مقارنة FCR=Yes vs No |

**تصنيفات الـ Lexicon (13 فئة):**
Bundle Consumption · Billing & Charging · Network Issues · Staff Problems · Promotion Failures · System/App Failure · 🚨 Churn Signals · Emotional Intensity · 🚨 Escalation Threat · Repeat/Chronic · Urgency · ⚠ Health/Safety · ✅ Positive/Resolved

**Severity Buckets:**
| Bucket | Range | التفسير |
|--------|-------|---------|
| None | 0 | مفيش مخاطر |
| Low | 1–3 | مشاكل بسيطة |
| Medium | 4–8 | مخاوف ملحوظة |
| High | 9–15 | مؤشرات خطر متعددة |
| Critical | 16+ | خطير — تدخّل فوري |

**NBA Priorities:**
| Priority | المُحفّز | SLA |
|----------|----------|-----|
| 🚨 P0 Critical | Churn / Escalation / Repeat | < 5–10 دقائق |
| 🟠 P1 High | Staff / Billing dispute / Emotional | < 10–15 دقيقة |
| 🟡 P2 Medium | مشكلة عادية + sentiment سلبي | < 10 دقائق |
| 🟢 P3 Standard | روتيني / sentiment محايد أو إيجابي | عادي |

---

### 🔻 3.6 — Escalated SR / Employee Audit System

> نظام تدقيق الـ Service Requests المصعّدة (بيانات Siebel) — بيقيس هل الإغلاق صحيح والتواصل تم بشكل سليم.

**الأسئلة:**
- نسبة الـ FCR/FVR وصحة الإغلاق (Correct vs Incorrect Closure)؟
- معدلات الـ SLA breach والـ compensation breakdown؟
- أداء الـ agents وصحة التصعيد (Escalation Validity)؟
- أنماط الحوادث المتكررة (Repeat Incidents)؟

**أعمدة الحسبة الرئيسية:**
- `Should Be Called` · `Number Of Trials` · `Correct / Incorrect Closure`
- `Reachability` · `Correct Reachability Should Be`
- `Closed Date − Open Date` (Duration) · `Duration Error`
- Vlookup Asset/Contact مع نتائج Micro

**حقول الـ input المهمة:** FCR/FVR · Reachability · Validity · SLA Pre-Violation · Number of Suspension · ARPU · Rate Plan · Compensation Value · Agent's Queue.

---

## 4. طرق الحسبة التفصيلية

### KPIs الأساسية
```
NPS / tNPS     = % Promoters − % Detractors
                 (Promoters 9–10 | Passives 7–8 | Detractors 0–6)

CSAT           = متوسط الرضا على مقياس 1–5 أو 1–10

FCR (proxy)    = (is_repeat_caller == False)
                 AND (Customer Compensated from CC? == "Yes within the call")

Contact Rate   = detractors في الـ Micro / إجمالي الـ detractors
Reach Rate     = rows (call0-1 == 1) / الأساس المناسب

Win-back Rate  = عملاء رجعوا / إجمالي محاولات الاسترجاع
Retention Rate = عملاء فضلوا / إجمالي المعرضين للخطر

Variance %     = (Actual − Target) / Target
```

### تحليلات إحصائية
```
Spike Day      = اليوم اللي حجم الـ cases فيه > (Q3 + 1.2 × IQR)

Incident       = نفس (Problem + Root Cause) يظهر 3+ مرات في اليوم
                 أو أعلى من الـ baseline بـ (Z-score ≥ 1.8σ)

Repeat Caller  = رقم تليفون يظهر أكتر من مرة في الـ dataset

Severity Score = مجموع أوزان المؤشرات اللغوية في الـ VOC
```

### QA Auto-Scoring (5 × 20 = 100)
```
Resolution (20) | Empathy (20) | Outcome (20) | Compliance (20) | Effort (20)

Grades: A (85+) · B (70–84) · C (55–69) · D (40–54) · F (<40)
```

### Forecasting
```
Method  = Holt-Winters Exponential Smoothing
Output  = forecast + lower CI + upper CI (per-queue + إجمالي)
Target  = تاريخ اختراق الـ NPS / detractor rate threshold
```

---

## 5. الـ Scripts والملفات

| الـ Script / الملف | الوظيفة |
|--------------------|---------|
| `pipeline_enhanced.py` | الـ pipeline الأساسي + detractor micro rates |
| `tnps_analyzer.py` | النسخة المطوّرة (cohort, forecasting, SLA heatmap, FCR impact) |
| `enhanced_voc.py` | Severity Score + Churn Risk Flag |
| `cx_4_analyses.py` | Sentiment Score + NBA rules + QA scoring |
| `comprehensive_excel.py` | LDA Topic Modeling |
| `consolidated_analysis.py` | Problem_Detail + RootCause_Detail |

**الـ output sheets الرئيسية:** Index · Sentiment Analysis · Sentiment by Category · Next Best Action · NBA Summary · Repeat Callers · Repeat Caller Detail · QA Per Case · QA Agent Scorecard · Master Enriched Data · SLA Breach Heatmap · FCR Impact · Channel Comparison · NPS Waterfall.

> كل الـ sheets فيها conditional formatting (heat maps) + freeze panes للجداول الكبيرة.

### الـ Branding (e&)
```
Red:    #E00800
Black:  #1A1A1A
Amber:  للـ caveats (زي "Rated by Mistake")
```

---

*مرجع داخلي — مبني على شغل CX الفعلي في e& قطاع الـ Consumer.*
