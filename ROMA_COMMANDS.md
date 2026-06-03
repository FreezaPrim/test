# Roma — Full Command Reference

> Offline CX analytics tool for e& Consumer | tNPS / NPS / detractors / churn / win-back

---

## Installation

```bash
pip install -e .
```

---

## CLI Commands

### `roma add`
Load data files into Roma's SQLite database.

```bash
roma add survey.xlsx
roma add data/                        # whole folder
roma add *.csv --recursive            # recursive glob
roma add                              # opens native folder-picker
```

**Result:**
```
Ingesting 2 file(s) into Roma...
  + survey.xlsx  ->  table 'survey_data'  (4180 rows, 22 cols)
  + mapping.csv  ->  table 'mapping'      (312 rows, 5 cols)
Done. 2/2 file(s) loaded.
```

---

### `roma list`
Show all loaded tables, documents, and detectable join keys.

```bash
roma list
```

**Result:**
```
TABLES
  survey_data  (4180 rows)
      columns: MSISDN, Q1_ANSWER__TNPS, SHORT_CODE, AGENT_QUEUE, DATE, ...

POSSIBLE LINKS (shared columns Roma can join on)
  'MSISDN' shared by: mapping, survey_data
```

---

### `roma chat`
Start an interactive chat session (the main interface).

```bash
roma chat
```

**Result:** Opens the Roma prompt. Type any question in English or Arabic.

```
  you > my kpis
  roma> CX KPIs from your data:
    tNPS: +24   [41% promoters | 18% passives | 41% detractors]  n=4180
    Detractors: 1714  (41%)  scored 0-6
    ...

  you > print top detractors by shortcode
  roma>   Top by SHORT_CODE  ·  table: survey_data
        ┌────────────────┬────────────┐
        │ SHORT_CODE     │ Detractors │
        ├────────────────┼────────────┤
        │ SC_1042        │ 312        │
        │ SC_0871        │ 287        │
        └────────────────┴────────────┘
```

---

### `roma ask`
Ask a single question and print the answer (non-interactive).

```bash
roma ask "what drives tnps?"
roma ask "top detractors by queue in April"
```

**Result:**
```
What most influences 'Q1_ANSWER__TNPS' (RandomForest, R²=0.71, 4180 rows):
  - AGENT_QUEUE: importance 0.34 (more = higher TNPS)
  - SHORT_CODE:  importance 0.28
  - FCR_FLAG:    importance 0.19
```

---

### `roma kpis`
Compute all CX KPIs from loaded data.

```bash
roma kpis
```

**Result:**
```
CX KPIs from your data:
  - tNPS (Q1_ANSWER__TNPS):  +24   (41% promoters | 18% passives | 41% detractors  n=4180)
  - Promoters:               41%   (scored 9-10)
  - Passives:                18%   (scored 7-8)
  - Detractors:              41%   (1714 — scored 0-6)
  - CSAT Score:              3.8 / 5
  - Contact Rate:            68.2%
  - Reach Rate:              54.7%
```

---

### `roma detractors`
Full detractor report with breakdowns by every dimension.

```bash
roma detractors
roma detractors April
roma detractors Q1
```

**Result:**
```
Detractor report (April): 714 of 1820 respondents (39.2%), scored 0-6 on Q1_ANSWER__TNPS.

By SHORT_CODE:
   SC_1042: 312
   SC_0871: 287
   ...

Repeated detractors: 88 customers appeared more than once (by MSISDN).
```

---

### `roma tnps`
Generate the full 26-sheet TNPS Excel dashboard.

```bash
roma tnps
roma tnps April
roma tnps Q1 2024
```

**Result:**
```
Building TNPS dashboard (this may take a moment)...
Dashboard saved: roma_data/reports/TNPS_dashboard_20240401_143022.xlsx
```
Sheets include: Dashboard, KPIs, Detractors, Forecast, Top/Bottom, Agent Ranking,
Toxic Combos, Waterfall, Win-Back, Severity, Velocity Alerts, and more.

---

### `roma forecast`
Run Holt-Winters exponential smoothing forecast on detractor rate.

```bash
roma forecast
roma forecast --horizon 60
```

**Result:**
```
Forecast for next 30 days:
  2024-04-01: ~41.2% detractor rate
  2024-04-02: ~40.8% detractor rate
  ...
  Warning: Projected breach of target detractor rate: 2024-04-04
```

---

### `roma pptx`
Export a full 10-slide e&-branded PowerPoint presentation.

```bash
roma pptx
```

**Result:**
```
Building TNPS PowerPoint presentation (10 slides)...
Presentation saved: roma_data/reports/TNPS_deck_20240401_143022.pptx
```
Slides: Title | KPI Summary | Monthly Trend | Top SC | Top Queue |
NPS Waterfall | 30-Day Forecast | Toxic Combos | Agent Ranking | Win-Back

---

### `roma export`
Export to Excel, PowerPoint, Word, or PDF.

```bash
roma export                           # full Excel report
roma export kpis --format excel
roma export detractors --format excel --when April
roma export report --format pptx
roma export report --format docx
roma export report --format pdf
```

**Result:**
```
Saved: roma_data/reports/Roma_Report_20240401.xlsx
```

---

### `roma map`
VLOOKUP-style join: merge a mapping/lookup file onto a loaded table.

```bash
roma map base.xlsx mapping.csv
roma map                              # opens file pickers interactively
```

**Result:**
```
Matching 'SHORT_CODE' <-> 'SC_CODE', adding: Topic, Owner_Team ...
Done. Matched 3842 of 4180 base rows.
  Loaded as table 'mapped' (ask Roma about it in chat).
  Excel saved: roma_data/reports/mapped_20240401.xlsx
```

---

### `roma compare`
Compare detractor counts between two time periods.

```bash
roma compare April March
roma compare Q1 Q2
roma compare "last month" "this month"
```

**Result:**
```
Detractors April vs March:
  March: 698 (38.4%)
  April: 714 (39.2%)
  Change: up 16 (+2.3% relative, +0.8 pts)
```

---

### `roma skill list`
List all available analytics workflow skills.

```bash
roma skill list
```

**Result:**
```
Available skills:
  morning_review            (5 steps) — Daily CX health check
  deep_dive                 (6 steps) — Full detractor deep-dive

  Run a skill:  roma skill run <name>
  Location:     roma_data/skills/
```

---

### `roma skill run`
Run a named analytics workflow (executes each step automatically).

```bash
roma skill run morning_review
roma skill run deep_dive
roma skill run morning_review --save   # also saves output as Word doc
```

**Result:**
```
  +-- Skill: morning_review -------------------------------------------+
  |  Daily CX health check                                             |
  |  5 steps                                                           |
  +---------------------------------------------------------------------+

  -- Step 1/5: my kpis
     tNPS: +24  |  41% promoters  |  41% detractors  (n=4180)

  -- Step 2/5: forecast next 30 days
     !! Breach projected: 2024-04-04  (target 40%)

  -- Step 3/5: show severity
     Critical: 412  |  High: 631  |  Medium: 671

  -- Step 4/5: velocity alerts
     SC_1042 week 14:  +8.3pp spike  (threshold: 5pp)

  -- Step 5/5: print top detractors by shortcode
     SC_1042: 312  |  SC_0871: 287  |  SC_2210: 241 ...

  Skill complete.  5 steps done.
```

---

### `roma stats`
Descriptive statistics and correlations across all loaded tables.

```bash
roma stats
```

**Result:**
```
Statistics for 'survey_data' (4180 rows, 22 columns):
  Q1_ANSWER__TNPS:  mean=6.8  median=7.0  std=2.9  min=0  max=10
  ...

Top correlations:
  - Q1_ANSWER__TNPS & FCR_FLAG: r=0.61 (strong positive)
  - Q1_ANSWER__TNPS & WAIT_TIME: r=-0.44 (moderate negative)
```

---

### `roma watch`
Auto-detect notable month-over-month changes (velocity alerts).

```bash
roma watch
```

**Result:**
```
Velocity alerts (week-over-week spikes):
  - SC_1042 week 14: detractor rate jumped +8.3pp (threshold: 5pp)
  - QUEUE_VIP week 13: detractor rate jumped +6.1pp
```

---

### `roma learn`
Train Roma's ML models on the loaded data.

```bash
roma learn
```

**Result:**
```
Roma is learning from your data...
  learned what drives 'Q1_ANSWER__TNPS' - top factors: AGENT_QUEUE, SHORT_CODE, FCR_FLAG
  found 4 customer segments
Done. Ask about it with 'roma chat', or get the full picture with 'roma report'.
```

---

### `roma report`
Generate a full auto-insights narrative report (Word/text).

```bash
roma report
```

**Result:**
```
Roma is learning from your data and writing a report...
Report written to: roma_data/reports/Roma_Insights_20240401.docx
```

---

### `roma onboard`
Interactive setup: tell Roma which columns are which (NPS, MSISDN, date, etc.).

```bash
roma onboard
```

**Result:** Interactive prompts to label your columns — stored permanently in `knowledge.json`.

---

### `roma teach`
Teach Roma custom vocabulary, or view what it has learned.

```bash
roma teach churn detractor           # "churn" = detractor
roma teach                           # view all learned words
```

**Result:**
```
Learned: 'churn' = 'detractor'.
```

---

### `roma forget`
Clear everything Roma has learned from your corrections.

```bash
roma forget
roma forget --yes                    # skip confirmation
```

---

### `roma assumptions`
Show the rules and formulas Roma uses for its calculations.

```bash
roma assumptions
```

---

### `roma reset`
Delete all loaded data from Roma's database.

```bash
roma reset
roma reset --yes                     # skip confirmation
```

---

### `roma welcome`
Show the Roma welcome / capabilities screen.

```bash
roma welcome
```

---

## Chat Questions (inside `roma chat`)

These are typed at the `you >` prompt:

| Question | What Roma does |
|---|---|
| `my kpis` | Full KPI breakdown: tNPS, promoters, passives, detractors, CSAT |
| `print top detractors by shortcode` | Bordered Unicode table ranked by detractor count |
| `print top detractors by queue in April` | Same, filtered to April only |
| `same for March` | Repeats last breakdown with time filter changed to March |
| `show severity` | Critical / High / Medium detractor classification |
| `forecast next 30 days` | Holt-Winters forecast with breach warning |
| `win-back recovery` | Month-over-month detractor recovery & churn rate |
| `velocity alerts` | Week-over-week detractor spikes by queue/SC |
| `toxic combos` | Worst Short Code + Queue combinations |
| `agent ranking` | Agent detractor rate + percentile band |
| `peer benchmark` | Agent vs. queue average comparison |
| `nps waterfall` | Monthly promoter gains vs. detractor losses |
| `channel comparison` | Detractor rate by channel |
| `fcr impact` | First-call resolution effect on tNPS |
| `hour pattern` | Detractor rate by hour of day |
| `day of week` | Detractor rate by weekday |
| `cohort analysis` | Detractor recovery rate by first-detractor cohort month |
| `what drives tnps?` | ML-ranked key drivers (Random Forest) |
| `show segments` | K-means customer clustering |
| `any anomalies?` | Isolation Forest outlier detection |
| `repeat callers` | Customers who appeared more than once |
| `detractors by queue full report` | Complete multi-dimension breakdown |
| `April vs March` | Period comparison (count + % change) |
| `join mapping` | Interactive file-picker join in chat |
| `export tnps presentation` | Save 10-slide PPTX |
| `export tnps dashboard` | Save 26-sheet Excel |
| `save chat` | Export this session as a Word doc |
| `churn means detractor` | Teach Roma a word |
| `what do you know?` | Summary of all loaded data |
| **Multi-step** e.g. `show kpis then forecast then severity` | ReAct loop runs all steps and combines results |

---

## Built-in Skill Files

Located in `roma_data/skills/` — add your own `.txt` files there.

### `morning_review.txt`
```
# Daily CX health check
my kpis
forecast next 30 days
show severity
velocity alerts
print top detractors by shortcode
```

### `deep_dive.txt`
```
# Full detractor deep-dive
my kpis
print top detractors by queue
toxic combos
agent ranking
win-back recovery
export tnps presentation
```

**Create a custom skill:**
```bash
# Create roma_data/skills/my_skill.txt
echo "my kpis" > roma_data/skills/weekly.txt
echo "velocity alerts" >> roma_data/skills/weekly.txt
echo "agent ranking" >> roma_data/skills/weekly.txt

roma skill run weekly
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ROMA_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `ROMA_OLLAMA_MODEL` | *(auto)* | Preferred Ollama model name |
| `ROMA_LLM_HOST` | *(none)* | Custom OpenAI-compat LLM server (highest priority) |
| `ROMA_MAX_JOIN_TABLES` | `6` | Max tables used in feature matrix for ML |

Set in `.env` file next to the project or as shell env vars.

---

## LLM Support (optional — all offline)

Roma works fully without any LLM. If you have one running locally it will
auto-detect and use it to phrase answers naturally.

| Engine | Default port | How to start |
|---|---|---|
| **Ollama** *(recommended)* | 11434 | `ollama serve` + `ollama pull llama3` |
| LM Studio | 1234 | Start LM Studio → Local Server |
| llama.cpp server | 8080 | `./server -m model.gguf` |
| vLLM / SGLang | 8000 | `python -m vllm.entrypoints.openai.api_server ...` |
| Custom | any | `ROMA_LLM_HOST=http://host:port roma chat` |

---

## Data Folder Structure

```
roma_data/
├── roma.db              SQLite database (all loaded data)
├── reports/             Exported Excel, PPTX, Word, PDF files
├── learned/
│   └── knowledge.json   Learned column mappings + custom vocabulary
└── skills/
    ├── morning_review.txt
    ├── deep_dive.txt
    └── your_skill.txt   Add your own here
```
