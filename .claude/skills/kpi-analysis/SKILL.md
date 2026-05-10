---
name: kpi-analysis
description: Universal KPI analysis skill covering Excel, Power BI (DAX), and Tableau. Use when the user needs to design KPIs, build measures/calculations, model data for analytics, design dashboards (Maven Analytics style), or migrate logic between Excel, Power BI, and Tableau. Triggers on requests involving KPIs, metrics, dashboards, DAX, M/Power Query, Tableau LOD, calculated fields, or business analytics.
---

# KPI Analysis — Universal Skill (Excel · Power BI · Tableau)

This skill is a working reference for advanced business analytics. It covers the full workflow:

1. **Define KPIs** that match the business question (catalog by domain)
2. **Model the data** correctly (star schema, grain, relationships)
3. **Write the formulas** in the target tool (Excel / DAX / Tableau)
4. **Design the dashboard** following Maven Analytics layout principles

## When to use this skill

Invoke when the user asks for any of:

- "build a KPI / metric / measure" in Excel, Power BI, or Tableau
- "DAX for ...", "calculated field for ...", "LOD for ..."
- "dashboard for sales / finance / HR / marketing / supply chain / customer"
- "convert this Excel formula to DAX" (or any cross-tool translation)
- data model design, star schema, fact/dim tables
- time intelligence (YoY, MoM, YTD, rolling avg, period-over-period)
- ranking, top-N, ABC analysis, Pareto, cohort, retention, RFM
- forecasting, variance, budget vs. actual

## Routing — load only what you need

| If the user is working on…              | Read this reference first                          |
| --------------------------------------- | -------------------------------------------------- |
| Excel formulas / Power Query / pivots   | `references/excel-formulas.md`                     |
| Power BI / DAX / data model             | `references/dax-measures.md`                       |
| Tableau calcs / LODs / table calcs      | `references/tableau-calcs.md`                      |
| Choosing the right KPI for the domain   | `references/kpi-catalog.md`                        |
| Dashboard layout, color, typography     | `references/design-patterns.md`                    |
| Star schema, grain, role-playing dims   | `references/data-modeling.md`                      |
| Cross-tool formula translation          | All three tool references + `kpi-catalog.md`       |

Read references progressively — do not preload everything. For multi-tool questions (e.g. "same KPI in all three tools") read each tool's reference and synthesize.

## Core analytical playbook

Always run through these checks before writing a formula:

1. **What question does the KPI answer?** A KPI without a decision attached is a vanity metric.
2. **What is the grain of the fact table?** (one row per order? per order-line? per day-store-SKU?)
3. **What is the time dimension?** Mark a proper Date/Calendar table — never use the fact's date column for time intelligence.
4. **Additive, semi-additive, or non-additive?**
   - Additive: revenue, units, cost (sum across all dims)
   - Semi-additive: inventory, headcount, AR balance (sum across non-time dims, use last/avg over time)
   - Non-additive: ratios, %, distinct counts (recompute at every level — never sum the parts)
5. **Filter context vs. row context** (DAX) — or **scope of aggregation** (Tableau LOD) — get this wrong and the number is wrong.
6. **Sanity-check** against a known total before publishing.

## Default deliverables

When asked to "build a KPI" or "build a dashboard", produce:

1. KPI definition: name, formula in plain English, numerator/denominator, grain, owner, target/threshold
2. Implementation: formula in the target tool (with comments)
3. Visual: chart type recommendation + why (see `design-patterns.md`)
4. Validation: how to sanity-check the result

## Cross-tool equivalence cheat sheet

| Concept              | Excel                          | DAX (Power BI)                          | Tableau                                  |
| -------------------- | ------------------------------ | --------------------------------------- | ---------------------------------------- |
| Sum with filter      | `SUMIFS`                       | `CALCULATE(SUM(...), filter)`           | `SUM(IF cond THEN x END)` or LOD         |
| Distinct count       | `COUNTA(UNIQUE(...))`          | `DISTINCTCOUNT(col)`                    | `COUNTD(col)`                            |
| YoY %                | `(curr-prev)/prev`             | `DIVIDE([Sales]-[Sales PY],[Sales PY])` | `(SUM([S])-LOOKUP(SUM([S]),-1))/LOOKUP…` |
| Running total        | `SUM($A$2:A2)`                 | `CALCULATE(SUM(...), DATESYTD(...))`    | `RUNNING_SUM(SUM([Sales]))`              |
| Rank                 | `RANK.EQ` / `RANK`             | `RANKX(ALL(table), [measure])`          | `RANK(SUM([Sales]))`                     |
| Top N filter         | `LARGE` + `FILTER`             | `TOPN(5, table, [measure])`             | `INDEX() <= 5` filter / Set              |
| Fixed (ignore filter)| `SUMIFS` w/ absolute refs      | `CALCULATE(..., ALL(dim))`              | `{FIXED dim : SUM([x])}`                 |
| Row-level fixed agg  | helper column                  | `CALCULATE` w/ `EARLIER` or variables   | `{INCLUDE dim : SUM([x])}`               |

## Style

- Always show the formula **and** the data-model assumption it depends on.
- Prefer measures over calculated columns (DAX) and LODs over table calcs (Tableau) when the result must respect filters.
- Wrap divisions in `DIVIDE(num, den)` (DAX) or `IIF(den=0, NULL, num/den)` (Tableau) — never produce `#DIV/0!`.
- Never invent a KPI name; use the standard one from `kpi-catalog.md` if it exists.
