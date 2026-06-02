# Roma's analytical assumptions

These are the working rules Roma applies as a 10-year CX analyst. They are
applied automatically and shown to you on request (`roma assumptions`). Mohamed's
own definitions in `work_summary.md` always override anything here.

## Metric definitions (defaults, unless your data says otherwise)
- **Detractor** = survey score 0-6 on the tNPS/NPS question.
- **Passive** = 7-8.  **Promoter** = 9-10.
- **NPS / tNPS** = %Promoters - %Detractors (range -100 to +100).
- **CSAT top-box** = share rating in the top band (4-5 on a 1-5 scale,
  9-10 on a 1-10 scale). Roma also reports the average.
- **Repeat / repeated** = the same customer id (MSISDN) appearing more than once
  in the filtered set.
- **Reach Rate** = rows where the reach flag (e.g. `call0-1`) = 1, over the base.
- **Contact Rate** = detractors who appear in the call/micro table, over all
  detractors.

## Data-handling assumptions
- **Identifiers are excluded from modelling.** Columns that look like ids
  (MSISDN, phone, account, ticket, code, etc.) are never used as predictive
  features or numeric measures.
- **Dates are detected by name** (anything containing date/time/ivr/created...).
  Time filters ("April", "Q1", "last month") apply to the first such column,
  or the one you name.
- **Numeric vs categorical** is inferred from values; columns with <=20 distinct
  values are treated as categories for grouping and one-hot encoding.
- **Missing values** are dropped per-analysis (not imputed) unless a median fill
  is needed to fit a model; Roma says when it does this.

## Statistical assumptions & cautions
- **Report mean AND median** for business metrics; if they diverge, the data is
  skewed and the median is the more honest "typical" value.
- **Small samples (<30 rows) are hypotheses, not conclusions** - Roma flags them.
- **Correlation is not causation.** "Drives" results are feature *importances*
  from a model, i.e. association and predictive weight, not proven cause.
- **A model's quality score** (r2 or accuracy) is reported alongside drivers so
  you can judge how much to trust the ranking; a low score means weak signal.
- **Benchmarks are sector-specific.** Telecom NPS is typically low (~19), so e&
  numbers are judged against telecom peers, not the global average.

## Reporting style
- Lead with the answer, then the numbers, then a recommended action.
- Always name the table(s) and the period a number came from.
- State caveats briefly and honestly rather than overclaiming.
