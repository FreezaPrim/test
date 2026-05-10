# Tableau — Calculated Fields, LODs, Table Calcs (Advanced)

Tableau has three calculation tiers — pick the right one or you'll get the wrong number.

| Tier               | Where it runs            | Granularity                                    | Use for                                          |
| ------------------ | ------------------------ | ---------------------------------------------- | ------------------------------------------------ |
| Row-level calc     | data source / row        | row                                            | new columns, classification, parsing             |
| LOD expression     | data source              | dimension set you specify (`FIXED`/`INCLUDE`)  | aggregations independent of view                 |
| Table calculation  | after query, in viz      | rows in the view (window)                      | running totals, %-of-total, rank                 |

Order of operations: **Extract filters → Data-source filters → Context filters → FIXED LODs → Dimension filters → INCLUDE/EXCLUDE LODs → Measure filters → Table calcs / Table calc filters**.

## 1. Row-level calcs

```tableau
// New field
[Profit] / [Sales]                                                    // ratio per row
IF [Quantity] > 10 THEN "Bulk" ELSE "Standard" END                    // bucket
DATEDIFF('day', [Order Date], [Ship Date])                            // ship time
DATEPART('weekday', [Order Date])                                     // 1..7
DATETRUNC('month', [Order Date])                                      // first of month
{IIF([Sales] > 0, [Profit]/[Sales], NULL)}                            // safe ratio

// Date math
DATEADD('month', -1, [Order Date])
DATEDIFF('month', [First Purchase], [Order Date])

// Strings
TRIM(SPLIT([Full Name], " ", 1))
REGEXP_EXTRACT([Email], '@(.+)$')
```

## 2. Aggregations

```tableau
SUM([Sales])                          // total
AVG([Sales])
MIN / MAX / MEDIAN
COUNT([Order ID])                     // non-null rows
COUNTD([Customer ID])                 // distinct
SUM(IF [Region]="EU" THEN [Sales] END)   // conditional sum (preferred over SUMIF idiom)
ATTR([Region])                        // returns value if 1 unique, else *
```

## 3. LOD expressions — the heart of Tableau analytics

```tableau
{ FIXED [Customer ID] : SUM([Sales]) }           // always at customer level — ignores view dims
{ INCLUDE [Product] : SUM([Sales]) }              // adds Product to the view's grain, then aggregates
{ EXCLUDE [Region] : SUM([Sales]) }               // removes Region from the view's grain
{ FIXED : SUM([Sales]) }                          // grand total, immune to all dim filters (except context)
{ FIXED [Region], [Year] : SUM([Sales]) }
```

| LOD       | When to use                                                                  |
| --------- | ---------------------------------------------------------------------------- |
| `FIXED`   | Customer-level metrics regardless of view (LTV, first purchase, total spend) |
| `INCLUDE` | "Average daily sales by month" — adds finer grain, then aggregates back up   |
| `EXCLUDE` | "% of region total" — removes a dimension already in the view                |

Critical rule: **`FIXED` ignores dimension filters** unless those filters are added to **Context** (right-click → Add to Context).

## 4. Time intelligence

```tableau
// YTD
IF DATETRUNC('year', [Order Date]) = DATETRUNC('year', TODAY())
   AND [Order Date] <= TODAY()
THEN [Sales] END

// Same period last year
{ FIXED [Customer ID] : SUM(IIF(YEAR([Order Date])=YEAR(TODAY())-1, [Sales], 0)) }

// Rolling 12 months
IF [Order Date] BETWEEN DATEADD('month', -12, TODAY()) AND TODAY()
THEN [Sales] END

// Working days between two dates (no NETWORKDAYS — build it):
DATEDIFF('day',[Start],[End])
- (DATEDIFF('week',[Start],[End])*2)
- (IF DATEPART('weekday',[Start])=1 THEN 1 ELSE 0 END)
- (IF DATEPART('weekday',[End])  =7 THEN 1 ELSE 0 END)
```

## 5. Period over period (with a relative date parameter)

```tableau
// Current vs Prior switch using a parameter [Date Range] = "MTD" | "YTD" | "Last 30 Days"
[Curr Sales] =
CASE [Date Range]
  WHEN "MTD" THEN IIF(DATETRUNC('month',[Order Date])=DATETRUNC('month',TODAY()), [Sales], NULL)
  WHEN "YTD" THEN IIF(YEAR([Order Date])=YEAR(TODAY()) AND [Order Date]<=TODAY(), [Sales], NULL)
END

[Prior Sales] = ... DATEADD('year',-1, ...) ...

[YoY %] = (SUM([Curr Sales]) - SUM([Prior Sales])) / SUM([Prior Sales])
```

## 6. Table calculations

Computed *after* aggregation, scoped by **Compute Using** (Pane / Table / specific dim).

```tableau
RUNNING_SUM(SUM([Sales]))                         // cumulative
WINDOW_SUM(SUM([Sales]))                          // total of all rows in window
WINDOW_AVG(SUM([Sales]), -2, 0)                   // 3-period rolling avg
RANK(SUM([Sales]))                                // ranking
INDEX()                                           // position in partition
FIRST() / LAST()                                  // -n .. 0 .. +n offsets
LOOKUP(SUM([Sales]), -1)                          // prior row
PERCENT_CHANGE(SUM([Sales]), -1)                  // QoQ %
TOTAL(SUM([Sales]))                               // partition total
```

`% of total` (column):

```tableau
SUM([Sales]) / TOTAL(SUM([Sales]))
```

YoY % via table calc (when Year is on Columns):

```tableau
(ZN(SUM([Sales])) - LOOKUP(ZN(SUM([Sales])),-1)) / ABS(LOOKUP(ZN(SUM([Sales])),-1))
```

## 7. Ranking & top-N

```tableau
[Rank]       = RANK(SUM([Sales]))                // table calc — respects view filters
[Top N]      = INDEX() <= [N parameter]          // filter on this — keep top N
```

For an LOD-based, view-independent rank (rare):

```tableau
{ FIXED : COUNTD( IIF({FIXED [Customer]:SUM([Sales])} > {FIXED [Customer ID]: SUM([Sales])},
                     [Customer ID], NULL) ) } + 1
```

## 8. ABC / Pareto

```tableau
[Cumul Sales]         = RUNNING_SUM(SUM([Sales]))
[Cumul %]             = RUNNING_SUM(SUM([Sales])) / TOTAL(SUM([Sales]))
[ABC Class] =
IF [Cumul %] <= 0.80 THEN "A"
ELSEIF [Cumul %] <= 0.95 THEN "B"
ELSE "C" END
```

Sort the dim descending by `SUM([Sales])` then add the table calc.

## 9. Cohort analysis

```tableau
// Calc 1: customer's first purchase month — FIXED is essential
[Cohort Month]   = { FIXED [Customer ID] : MIN(DATETRUNC('month', [Order Date])) }

// Calc 2: months since acquisition
[Cohort Age]     = DATEDIFF('month', [Cohort Month], DATETRUNC('month', [Order Date]))

// Calc 3: cohort size (denominator)
[Cohort Size]    = { FIXED [Cohort Month] : COUNTD([Customer ID]) }

// Active customers in (cohort, age) cell
[Active]         = COUNTD([Customer ID])

// Retention
[Retention %]    = COUNTD([Customer ID]) / SUM({FIXED [Cohort Month] : COUNTD([Customer ID])})
```

Build heatmap: rows = `Cohort Month`, columns = `Cohort Age`, color = `Retention %`.

## 10. RFM segmentation

```tableau
[Recency Days]    = { FIXED [Customer ID] : DATEDIFF('day', MAX([Order Date]), TODAY()) }
[Frequency]       = { FIXED [Customer ID] : COUNTD([Order ID]) }
[Monetary]        = { FIXED [Customer ID] : SUM([Sales]) }

[R Score] =
IF [Recency Days] <= 30  THEN 5
ELSEIF [Recency Days] <= 60  THEN 4
ELSEIF [Recency Days] <= 90  THEN 3
ELSEIF [Recency Days] <= 180 THEN 2 ELSE 1 END

[Segment] =
IF  [R Score]>=4 AND [F Score]>=4 AND [M Score]>=4 THEN "Champion"
ELSEIF [R Score]>=4 AND [F Score]<=2                 THEN "New"
ELSEIF [R Score]<=2 AND [F Score]>=4                 THEN "At Risk"
ELSEIF [R Score]<=2 AND [F Score]<=2                 THEN "Lost"
ELSE "Loyal" END
```

## 11. Variance & budget vs. actual

Blend `Actual` and `Budget` data sources, or join. Then:

```tableau
[Variance]    = SUM([Actual]) - SUM([Budget])
[Variance %]  = ([Variance]) / SUM([Budget])

[Status]      = IF [Variance %] >=  0.02 THEN "Above target"
              ELSEIF [Variance %] <= -0.02 THEN "Below target"
              ELSE "On target" END
```

Bullet chart: actual = bar, budget = reference line, target zone = distribution band.

## 12. Parameters & dynamic measures

```tableau
[Selected Metric]   // string parameter: "Sales" | "Profit" | "Quantity"

[Active Measure] =
CASE [Selected Metric]
  WHEN "Sales"    THEN SUM([Sales])
  WHEN "Profit"   THEN SUM([Profit])
  WHEN "Quantity" THEN SUM([Quantity])
END
```

Use parameter actions (Tableau 2019.2+) so visuals can drive the parameter value.

## 13. Sets, groups, and bins

- **Set** — dynamic membership; great for top-N comparisons (in/out coloring).
- **Group** — static rollup of dim values.
- **Bin** — fixed-width bucketing of a measure (histogram, distribution).
- **Set Actions** — click a mark to add/remove from a set; powers asymmetric drill-downs.

## 14. Performance

1. Use **extracts** for big sources; tune with hidden unused fields and aggregation.
2. `FIXED` LODs on huge data are heavy — use `Context filters` to scope them.
3. Avoid string `CONTAINS` on millions of rows; precompute flags in Prep.
4. Push joins to the database; avoid blends except for cross-source.
5. Limit marks (use top-N filters); table calcs over millions of rows are slow.
6. Replace `IF...THEN...ELSEIF` chains with `CASE` when matching one field equality.
7. Materialize calcs in the extract (Tableau Prep) when reused on dashboards.

## 15. Common bugs

| Symptom                                              | Cause                                                                  |
| ---------------------------------------------------- | ---------------------------------------------------------------------- |
| `FIXED` total ignores my filter                      | Filter not in **Context** — `FIXED` runs before dim filters            |
| Table calc breaks when sort changes                  | "Compute Using" is set to wrong dim — pin to specific field            |
| Cohort retention >100%                               | Same customer counted in multiple months — verify with `COUNTD`        |
| Blended fields show `*`                              | `ATTR` returned multiple values — change blend keys or use cross-DB join|
| Year-over-year shows huge spike                      | Comparing partial current period to full prior period — add date guard |
| Date axis missing months                             | "Show Missing Values" off, or extract filter dropping months           |
