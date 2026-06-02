# Roma

**Roma** is a fully local, offline AI analyst for customer-experience data. You
point it at your Excel files; it loads them into a local database, links them on
shared keys (like a customer ID), **learns from the data** to find what drives
satisfaction/churn, groups customers into segments, spots trends and anomalies,
and writes insight reports. Everything runs on your machine - **no Claude, no
API key, no internet needed at runtime.**

Optionally, if you have a local model running (via Ollama), Roma will also chat
with you in natural language. If you don't, it still works as a structured
analyst.

---

## Export to Excel (e&-branded)

Roma can turn any analysis into a professional, **e&-branded Excel workbook** -
multi-sheet, with a red title bar, styled headers, frozen panes, and alternating
row shading. Fully offline (built on openpyxl).

- `roma export detractors --when April` -> workbook with Summary + a sheet per
  breakdown (agent queue, short code, ...) + repeated detractors, all for April
- `roma export kpis` -> your CX KPIs
- `roma export drivers` -> ranked tNPS/CSAT drivers
- `roma export report` -> everything in one workbook
- In chat: "export the detractor report to excel for April", "save kpis to
  excel", or in Arabic "صدّر تقرير المنتقدين اكسل"

Files land in `roma_data\reports\`. (PowerPoint, Word, and PDF export can be
added the same way - ask when you want them.)

## Map / merge two files (VLOOKUP-style)

Roma can join two files on a shared key and pull columns from one onto the other
- the e& equivalent of a VLOOKUP, done step by step:

```bat
roma map
```

Roma then: opens a picker for the **base** file → lists its columns → you pick
the key (by number or name); opens the **lookup** file → you pick its matching
key → you pick which columns to **add** (numbers/names, or `all`). It merges
(left join), loads the result as a queryable table you can immediately analyze
("detractors by agent queue"), and saves an e&-branded Excel of the merged data.

You can also pass files directly: `roma map survey.xlsx micro.xlsx`. Unmatched
base rows keep their original data with the added columns left blank.

## The look

Roma opens with a cinematic, e&-red-on-black boot sequence - a gradient ROMA
wordmark, animated diagnostics, a calibrating progress bar, and a typewriter
greeting - then a brief "analyzing" pulse before each answer in chat. It's pure
terminal (no extra libraries) and turns itself off automatically when output is
piped to a file, so logs stay clean. Set `NO_COLOR=1` to disable effects.

## Trends, comparisons & automatic alerts

Roma doesn't just report one period - it tracks change and tells you what moved:

- **Compare periods:** `roma compare April March`, or in chat "detractors April
  vs March" / "compare Q1 vs Q2" - shows the count, rate, and the relative and
  percentage-point change.
- **Automatic alerts:** the moment you `roma add` new data, Roma compares the
  latest month to the previous one and flags notable shifts on its own (e.g.
  "Detractors increased 122% in April; biggest mover: Q_Billing up 26"). Run it
  any time with `roma watch`, or ask "what changed recently".
- **Charts in exports:** Excel reports now include bar charts on every detractor
  breakdown and a monthly detractor-rate **trend line** - all e&-red.

## Roma learns from you

Roma understands free phrasing (English/Arabic, typos, near-synonyms) without a
model - and when it still gets something wrong, **you teach it and it remembers
forever** (saved in `roma_data\learned\teachings.json`):

- **Correct an intent:** if Roma misreads a question, reply `I meant drivers`
  (or detractors, segments, kpis...). It re-answers and remembers your exact
  phrasing next time.
- **Teach a word:** `churn means detractor`, or from the command line
  `roma teach churn detractor`. From then on Roma treats "churn" as a detractor.
- **When unsure, Roma asks** instead of guessing - and learns from your answer.
- `roma teach` shows everything it has learned; `roma forget` clears it.

> Honest note: this is real learning of *your vocabulary and phrasing* - it makes
> Roma steadily understand you better. It is not a full language model; for
> understanding any phrasing with no teaching at all, a local model (Ollama)
> would be needed, which a locked-down laptop may not allow.

## Export to any format (e&-branded)

Roma turns any analysis into a professional, **e&-branded** file - fully offline:

- **Excel**: `roma export detractors --when April` (multi-sheet workbook)
- **PowerPoint**: `roma export report -f pptx --when April` (title + KPI + detractor + drivers slides)
- **Word**: `roma export report -f docx`
- **PDF**: `roma export report -f pdf`
- In chat: "export a powerpoint of the detractors for April", "save a word
  report", "export to pdf", or in Arabic "صدّر عرض بوربوينت".

Files land in `roma_data\reports\`.

## Analyst skills & assumptions

Roma now reasons like a senior analyst, not just a calculator:

- `roma stats` - descriptive statistics done properly: mean **and** median,
  spread, percentiles, distribution shape, outliers, and correlations - with a
  caution when the sample is small or the data is skewed.
- `roma assumptions` - shows the explicit rules Roma works by (metric
  definitions, how ids/dates are handled, "correlation isn't causation", telecom
  benchmarks, etc.). Your own definitions in the memory always override these.
- In chat: "show me descriptive statistics", "what are the correlations", "what
  assumptions do you use".

## Detractor analysis (with time windows)

Roma handles detractor questions in depth - count, breakdowns by any dimension,
and repeat detractors - and understands time windows like "April", "Q1", "last
month", "this year", or an explicit `2026-01-01 to 2026-03-31` range (English or
Arabic month names work too).

- `how many detractors in April` -> count + % for that month
- `detractors by agent queue` -> one breakdown table
- `detractors by short code in April` -> breakdown, time-filtered
- `detractors by agent queue and short code in April` -> a full report (count +
  every breakdown + repeats), all for April
- `repeated detractors` -> customers (by MSISDN) who were detractors more than once
- `roma detractors April` -> the one-shot full report from the command line

A detractor is a survey row scoring 0-6 on your tNPS column, per your own
definition.

## Ask in your own words

Roma now understands **free-form questions** - you don't have to use exact
commands. It has a built-in natural-language layer (no model or internet needed)
that figures out what you mean, in English or Arabic, and even with typos. For
example all of these work:

- "tell me what mainly makes my tnps go up or down" -> drivers
- "which call type has the most unhappy people" -> detractors by call type
- "are there customers calling again and again" -> repeat customers
- "show me the weird records" -> anomalies
- "ايه اللي بيأثر على ال tnps" -> drivers
- "اعرض الفئات" -> segments

If you install a local model (Ollama, below), Roma gets even better at unusual
phrasing and can also explain its findings in prose.

## Roma knows your world

Roma ships with a **memory** of Mohamed's CX work at e& - your KPI definitions
and formulas (NPS/tNPS, CSAT, FCR, Contact/Reach Rate, Win-back, Severity, QA
scoring), plus a researched CX analyst playbook with industry benchmarks. It
greets you by name, reasons like a senior CX analyst, and applies **your**
formulas to whatever sheet you load. Memory lives in `roma\roma\memory\` (and
you can drop extra `.md` notes into `roma_data\memory\`).

- `roma kpis` - computes your CX KPIs (tNPS with 9-10/0-6 buckets, detractor
  count, CSAT top-box, Reach Rate from `call0-1`, Contact Rate by joining
  detractors to the micro data).
- `roma onboard` - for any new sheet, Roma guesses what each column is, asks you
  about the ones it can't place, and proposes how files link - so the format
  never has to match a fixed template.
- `roma welcome` - the welcome screen.

## What it does

- **Works on a locked-down laptop:** no SQL Server (uses SQLite - just a file),
  no cloud login, no API key. Only `pip install` needs internet, once.
- **Point it at a folder:** `roma add "C:\some\folder"` loads every Excel file
  inside, or run `roma add` with nothing to open a folder picker.
- **Learns from your data (scikit-learn):**
  - *Drivers* - what most influences a metric like CSAT / churn / reopened.
  - *Segments* - natural customer groupings.
  - *Trends* - whether a metric is rising or falling over time.
  - *Anomalies* - unusual rows worth a look.
- **Remembers:** what it learns is saved to `roma_data\learned\knowledge.json`
  and refreshed whenever you add data or run `roma learn`.
- **Reports:** `roma report` writes a Markdown insights report from real numbers.
- **Reads many formats:** `.xlsx`, `.xls`, `.csv`, `.tsv`, `.json`, `.pdf`,
  `.txt`, `.md`. Tables become learnable data; PDFs/notes become searchable.

---

## Setup (Windows)

1. **Install Python 3.10+** from <https://www.python.org/downloads/windows/> and
   tick **"Add python.exe to PATH"** on the first screen.
2. **Run `setup_windows.bat`** (double-click). It creates a virtual environment
   and installs the libraries. No API key is required.

> **The easy way to run Roma:** double-click **`start_here.bat`**. It opens a
> terminal window that **stays open**, where you type `roma` commands. (Double-
> clicking other `.bat` files may flash and vanish - that's normal Windows
> behaviour for a command that finishes instantly, not a crash.)

---

## Using Roma

```bat
REM Load a whole folder of Excel files (or open a picker with bare 'roma add')
roma add "C:\path\to\your\data_folder"
roma add
roma add "C:\path\to\folder" --recursive

REM See what Roma has and how files link
roma list

REM Learn patterns from the data
roma learn

REM Compute your CX KPIs (NPS, CSAT, detractors, reach, contact rate)
roma kpis

REM Teach Roma about a new sheet's columns and links
roma onboard

REM Chat with it
roma chat

REM Ask one question
roma ask "what drives csat?"

REM Write an insights report (Markdown)
roma report

REM Start over
roma reset
```

### Things you can ask in chat (works even with no local model)
- `who are you` / `what can you do`
- `what drives tnps?`  (or csat, fulfillment, ...)
- `how many detractors` / `my kpis`
- `detractors by call_type`  (breakdown by any column)
- `repeat customers` / `repeated callers`
- `top owner_team` / `top short_code`  (most frequent values)
- `show me the segments`
- `trend of <metric> over time`
- `any anomalies?`
- `average <metric> by <column>`
- `what do you know?`

---

## Optional: local natural-language chat (Ollama)

By default Roma matches your question to an analysis by pattern, so a phrasing it
doesn't recognise gets a "here's what I can answer" reply. To make Roma
understand **any** phrasing in free-form language, give it a local model. This is
optional and fully offline once installed.

**Easy way:** double-click **`setup_chat_windows.bat`**. It checks for Ollama,
points you to the installer if needed, and downloads a small model for you.

**Manual way:**
1. Install Ollama from <https://ollama.com/download/windows> (installing it may
   need IT permission on a locked laptop).
2. Pull a model sized to your laptop's RAM:
   - `ollama pull llama3.2:3b` - ~2 GB, runs on **8 GB RAM**. Recommended start.
   - `ollama pull qwen2.5:7b` - ~4.7 GB, needs **~16 GB RAM**, noticeably smarter.
   - `ollama pull phi3:mini` - ~2.3 GB, very light.
3. Run `roma chat` - Roma auto-detects Ollama and switches on natural-language
   mode (the welcome screen shows the model name). To force a specific model, set
   `ROMA_OLLAMA_MODEL` in `.env`.

No GPU is required; these small models run on CPU. With a model present, reports
also gain a short written summary on top of the numbers.

---

## How it works

```
your Excel files ─▶ ingest ─▶ SQLite tables (+ documents for PDF/notes)
                                   │
                         shared-key link detection
                                   │
                 scikit-learn learns:  drivers, segments, trends, anomalies
                                   │            │
                       knowledge.json     roma report (Markdown)
                                   │
   your question ─▶ analyst answers in structured form
                    (+ local Ollama model for natural language, if available)
```

## Notes & limits

- The "self-learning" is statistical/ML learning from your **tabular** data: the
  more (and cleaner) data you add, the better the driver/segment/trend results.
  Reports and notes you add are stored and searchable and used as chat context.
- A driver model's quality score (r2 or accuracy) depends on your data; the
  ranked importances are usually the most useful output.
- Roma reads text from PDFs, not scanned images.
- Renaming a file's extension does not convert it - export real `.xlsx`/`.csv`.

## If pip is blocked ("No matching distribution found for pandas")

This means the work laptop's `pip` couldn't reach the internet package server
(PyPI) - almost always a company proxy or firewall, not a Roma problem. Roma
needs `pandas`, `openpyxl`, and `scikit-learn` to run, so this must be solved.

**First, check the network.** In a terminal in this folder run `ping pypi.org`.
If you get replies, it's a proxy issue - try these in order:

1. **Re-run `setup_windows.bat`.** The updated script trusts the PyPI hosts,
   waits longer, retries, and will ask you to paste your company proxy URL if
   needed (format `http://user:pass@proxy.company.com:8080`).
2. **Ask IT for the internal package index** (many companies host one). Then in
   the activated venv run, replacing the URL with theirs:
   `pip install -r requirements.txt --index-url https://your-company-index/simple`
3. **Offline install** (works with no internet on the work laptop at all):
   - On a machine that *does* have internet (home PC, or this laptop on a phone
     hotspot) with the **same Python version**, run `download_wheels.bat` - it
     fills a `wheels` folder.
   - Copy the whole Roma folder (with `wheels`) to the work laptop.
   - Run `install_offline.bat` there.

The setup now **verifies** the libraries actually installed and only says
"complete" if they did - so you won't get a false success again.

- **A window flashes and closes** - use `start_here.bat`, or run commands in an
  open Command Prompt so output stays visible.
- **"No tabular data loaded"** - run `roma add` on your Excel files first.
- **"Couldn't find a likely target column"** - tell Roma which metric to model,
  e.g. `roma ask "what drives <your_column>?"`.
