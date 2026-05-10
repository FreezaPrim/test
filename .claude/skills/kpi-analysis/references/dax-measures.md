# DAX — Power BI Measures (Advanced)

DAX has two contexts you must always reason about:

- **Row context** — one row at a time (calculated columns, iterators like `SUMX`).
- **Filter context** — the slicers/visuals/relationships filtering the model (measures evaluate here).

`CALCULATE` is the only function that *modifies* filter context. Everything advanced is a variation of it.

## 0. Naming & hygiene

- Measures live in a dedicated `_Measures` table (empty table, organized by display folder).
- Prefix variants: `Sales`, `Sales PY`, `Sales YoY %`, `Sales YoY Δ`.
- Format strings: currency `"$#,0;-$#,0;-"`; percent `"0.0%;-0.0%;-"`; thousands `"#,0,K"`.
- Always wrap division in `DIVIDE(num, den, [alt])`.
- Use `VAR` liberally — readability + avoids re-evaluation.

## 1. Base aggregations

```DAX
Sales            = SUM ( Sales[Amount] )
Orders           = DISTINCTCOUNT ( Sales[OrderID] )
Customers        = DISTINCTCOUNT ( Sales[CustomerKey] )
Avg Order Value  = DIVIDE ( [Sales], [Orders] )
Sales (Iter)     = SUMX ( Sales, Sales[Qty] * Sales[UnitPrice] )    -- when calc needs row context
```

## 2. `CALCULATE` patterns

```DAX
Sales EU        = CALCULATE ( [Sales], 'Region'[Continent] = "Europe" )
Sales No Filter = CALCULATE ( [Sales], REMOVEFILTERS ( 'Product' ) )
Sales All Time  = CALCULATE ( [Sales], ALL ( 'Date' ) )
Sales Same Cust = CALCULATE ( [Sales], ALLEXCEPT ( Sales, Sales[CustomerKey] ) )
Sales Top 10 P  = CALCULATE ( [Sales],
                    KEEPFILTERS ( TOPN ( 10, ALL ( 'Product'[Name] ), [Sales] ) ) )
```

Filter modifiers:

| Modifier            | Effect                                                            |
| ------------------- | ----------------------------------------------------------------- |
| `ALL(table/col)`    | remove filters from that column/table                             |
| `ALLEXCEPT(t, col)` | remove all filters except listed columns                          |
| `ALLSELECTED(...)`  | preserve outer (page/slicer) filters, remove visual-internal ones |
| `KEEPFILTERS(expr)` | layer expr on top of existing filters (intersection, not replace) |
| `REMOVEFILTERS`     | newer alias for `ALL` used inside `CALCULATE`                     |
| `USERELATIONSHIP`   | activate an inactive relationship (e.g. `OrderDate` vs `ShipDate`)|

## 3. Variables — the readable pattern

```DAX
YoY % =
VAR _curr = [Sales]
VAR _prev = CALCULATE ( [Sales], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )
VAR _yoy  = DIVIDE ( _curr - _prev, _prev )
RETURN
    IF ( ISBLANK ( _prev ), BLANK (), _yoy )
```

Variables are evaluated **once** in the context where they appear — not in any nested `CALCULATE`. This is the #1 source of bugs in advanced DAX.

## 4. Time intelligence (requires marked Date table)

```DAX
Sales YTD   = TOTALYTD     ( [Sales], 'Date'[Date] )
Sales QTD   = TOTALQTD     ( [Sales], 'Date'[Date] )
Sales MTD   = TOTALMTD     ( [Sales], 'Date'[Date] )
Sales PY    = CALCULATE ( [Sales], SAMEPERIODLASTYEAR ( 'Date'[Date] ) )
Sales PY YTD= CALCULATE ( [Sales], DATESYTD ( SAMEPERIODLASTYEAR ( 'Date'[Date] ) ) )
Sales LM    = CALCULATE ( [Sales], DATEADD ( 'Date'[Date], -1, MONTH ) )
Sales R12M  = CALCULATE ( [Sales], DATESINPERIOD ( 'Date'[Date], MAX('Date'[Date]), -12, MONTH ) )
Sales WTD   = CALCULATE ( [Sales], DATESBETWEEN ( 'Date'[Date],
                  MAX('Date'[Date]) - WEEKDAY(MAX('Date'[Date]),2) + 1,
                  MAX('Date'[Date]) ) )
```

Custom (non-Gregorian) calendars: build columns `YearOffset`, `MonthOffset` and shift via offset arithmetic — DAX time-intelligence functions assume standard calendars.

## 5. Period over period

```DAX
Sales Δ YoY     = [Sales] - [Sales PY]
Sales YoY %     = DIVIDE ( [Sales] - [Sales PY], [Sales PY] )
Sales MoM %     = VAR _p = CALCULATE ( [Sales], DATEADD ( 'Date'[Date], -1, MONTH ) )
                  RETURN DIVIDE ( [Sales] - _p, _p )
```

## 6. Rolling averages & trend

```DAX
Sales R3M Avg =
AVERAGEX (
    DATESINPERIOD ( 'Date'[Date], MAX ( 'Date'[Date] ), -3, MONTH ),
    [Sales]
)

Sales 7D Avg =
CALCULATE (
    AVERAGEX ( VALUES ( 'Date'[Date] ), [Sales] ),
    DATESINPERIOD ( 'Date'[Date], MAX ( 'Date'[Date] ), -7, DAY )
)
```

## 7. Ranking & top-N

```DAX
Product Rank   = RANKX ( ALL ( 'Product'[Name] ), [Sales],, DESC, DENSE )
Region Rank    = IF ( HASONEVALUE ( 'Region'[Name] ),
                      RANKX ( ALL ( 'Region'[Name] ), [Sales] ) )

Top 5 Sales    = CALCULATE ( [Sales],
                    KEEPFILTERS ( TOPN ( 5, ALL ( 'Product'[Name] ), [Sales] ) ) )

Other Sales    = [Sales] - [Top 5 Sales]
```

## 8. Cumulative / running totals

```DAX
Sales Cumulative =
CALCULATE (
    [Sales],
    'Date'[Date] <= MAX ( 'Date'[Date] ),
    ALL ( 'Date' )
)

Sales YTD Cumul = TOTALYTD ( [Sales], 'Date'[Date] )
```

## 9. ABC / Pareto

```DAX
Product Sales Cumul =
VAR _curr = [Sales]
VAR _tbl =
    ADDCOLUMNS (
        ALL ( 'Product'[Name] ),
        "@s", [Sales]
    )
VAR _bigger = FILTER ( _tbl, [@s] >= _curr )
RETURN
    SUMX ( _bigger, [@s] )

Product Cumul %  = DIVIDE ( [Product Sales Cumul], CALCULATE ( [Sales], ALL ( 'Product' ) ) )

ABC Class =
SWITCH ( TRUE (),
    [Product Cumul %] <= 0.80, "A",
    [Product Cumul %] <= 0.95, "B",
    "C"
)
```

## 10. Cohort & retention

Add a calculated column `FirstOrderDate` per customer:

```DAX
Customer[FirstOrderDate] =
CALCULATE ( MIN ( Sales[OrderDate] ), ALLEXCEPT ( Customer, Customer[CustomerKey] ) )
```

Cohort active customers (any month after acquisition):

```DAX
Active Customers Cohort =
CALCULATE (
    DISTINCTCOUNT ( Sales[CustomerKey] ),
    USERELATIONSHIP ( Sales[OrderDate], 'Date'[Date] )
)

Retention % =
VAR _cohort = CALCULATE ( DISTINCTCOUNT ( Customer[CustomerKey] ),
                  ALLEXCEPT ( Customer, Customer[CohortMonth] ) )
RETURN DIVIDE ( [Active Customers Cohort], _cohort )
```

## 11. RFM segmentation

```DAX
Recency      = DATEDIFF ( MAX ( Sales[OrderDate] ), TODAY (), DAY )
Frequency    = DISTINCTCOUNT ( Sales[OrderID] )
Monetary     = [Sales]

R Score = SWITCH ( TRUE (),
            [Recency] <= 30,  5,
            [Recency] <= 60,  4,
            [Recency] <= 90,  3,
            [Recency] <= 180, 2, 1 )
F Score = ...   M Score = ...

RFM Code = [R Score]*100 + [F Score]*10 + [M Score]

RFM Segment =
SWITCH ( TRUE (),
    [R Score]>=4 && [F Score]>=4 && [M Score]>=4, "Champion",
    [R Score]>=4 && [F Score]<=2,                  "New",
    [R Score]<=2 && [F Score]>=4,                  "At Risk",
    [R Score]<=2 && [F Score]<=2,                  "Lost",
    "Loyal" )
```

## 12. Variance & budget vs. actual

```DAX
Budget          = SUM ( Budget[Amount] )
Variance        = [Sales] - [Budget]
Variance %      = DIVIDE ( [Variance], [Budget] )

Variance Sign   =
SWITCH ( TRUE (),
    [Variance] > 0,  "▲",
    [Variance] < 0,  "▼",
    "▬" )

Status =
SWITCH ( TRUE (),
    [Variance %] >=  0.02, "Above target",
    [Variance %] <= -0.02, "Below target",
    "On target" )
```

For mixed cost/revenue lines: store `LineSign` (+1 revenue, -1 cost) on the budget table and compute `[Variance] * SELECTEDVALUE(Account[Sign])`.

## 13. What-if parameters

```DAX
Discount % = SELECTEDVALUE ( 'Discount'[Discount %], 0 )

Adjusted Sales = [Sales] * ( 1 - [Discount %] )
Margin Impact  = [Adjusted Sales] - [Sales]
```

Bind parameter table to a slicer. Use `SELECTEDVALUE` to read it.

## 14. Disconnected / parameter tables

```DAX
Selected Metric = SELECTEDVALUE ( 'Metric Switch'[Metric], "Sales" )

Active KPI =
SWITCH ( [Selected Metric],
    "Sales",   [Sales],
    "Orders",  [Orders],
    "Margin",  [Gross Margin],
    [Sales] )
```

Use this with a slicer to make any visual a multi-metric chart.

## 15. Detection — `HASONEVALUE`, `SELECTEDVALUE`, `ISFILTERED`

```DAX
Region Header =
IF ( HASONEVALUE ( 'Region'[Name] ),
     SELECTEDVALUE ( 'Region'[Name] ),
     "All regions" )

Show Total = NOT ISFILTERED ( 'Product'[Name] )
```

## 16. Calculation Groups (Tabular Editor)

Replace dozens of `Sales PY`, `Margin PY`, `Cost PY` with one Time Intelligence group:

```DAX
-- Calc Item: 'YoY %'
VAR _c = SELECTEDMEASURE()
VAR _p = CALCULATE ( SELECTEDMEASURE(), SAMEPERIODLASTYEAR('Date'[Date]) )
RETURN DIVIDE ( _c - _p, _p )
```

Apply the calc group to any base measure via a slicer.

## 17. Performance rules

1. Measures > calculated columns when possible (calc columns inflate model size).
2. Avoid bidirectional relationships — use `CROSSFILTER` inside `CALCULATE` if needed.
3. `COUNTROWS(VALUES(col))` ≥ `DISTINCTCOUNT(col)` performance, identical result.
4. Star schema beats snowflake for the engine.
5. Disable auto date/time; use a single explicit Date table.
6. Replace nested `IF` with `SWITCH ( TRUE (), … )`.
7. `DIVIDE` is faster + safer than `IF(y=0,…,x/y)`.
8. Use Performance Analyzer + DAX Studio (Server Timings) to find slow queries.

## 18. Common bugs

| Symptom                                                   | Cause                                                                |
| --------------------------------------------------------- | -------------------------------------------------------------------- |
| YoY blank in current month                                | No PY data — wrap in `IF(NOT ISBLANK([Sales PY]), …)`                |
| Total row shows wrong number                              | Non-additive measure summed at total — recompute via `SUMX(VALUES…)` |
| Time intelligence returns BLANK                           | Date table not marked, or contiguous dates missing                   |
| Filter from one table doesn't reach another               | Wrong relationship direction or inactive relationship                |
| Slicer changes don't affect a measure                     | `ALL` / `REMOVEFILTERS` is dropping the slicer's filter              |
| `RANKX` ties not handled                                  | Use `DENSE` ties param; tie-break with secondary measure             |
