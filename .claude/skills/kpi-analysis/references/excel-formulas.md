# Excel — Advanced Analytics Formulas

Modern Excel (Microsoft 365) uses dynamic arrays. Prefer the new functions (`FILTER`, `XLOOKUP`, `LET`, `LAMBDA`, `GROUPBY`, `PIVOTBY`) over legacy arrays when available.

## 1. Lookups & joins

```excel
=XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found], [match_mode], [search_mode])
=XLOOKUP(A2, Products[SKU], Products[Price], 0, 0)            'exact match, 0 if missing
=XLOOKUP(A2, dates, sales, , -1)                              'approx, next smaller (price-band lookup)
=XLOOKUP(1, (tbl[Region]=R)*(tbl[Year]=Y), tbl[Sales])        'multi-criteria
=INDEX(tbl[Sales], MATCH(1,(tbl[Region]=R)*(tbl[Year]=Y),0))  'legacy array equivalent
```

`XLOOKUP` replaces `VLOOKUP`/`HLOOKUP`/`INDEX-MATCH` in 99% of cases; supports left-lookup, default value, reverse search.

## 2. Aggregation with conditions

```excel
=SUMIFS(sum_range, crit_range1, crit1, crit_range2, crit2, ...)
=COUNTIFS(...)             'count with conditions
=AVERAGEIFS(...)
=MAXIFS(...) / MINIFS(...)

=SUMPRODUCT((tbl[Region]="EU")*(tbl[Year]=2025)*tbl[Sales])    'arbitrary boolean logic
=SUMPRODUCT(tbl[Qty]*tbl[Price])                               'weighted sum
```

Distinct count (no helper column):

```excel
=ROWS(UNIQUE(FILTER(tbl[Customer], tbl[Region]="EU")))
=SUMPRODUCT((tbl[Region]="EU")/COUNTIFS(tbl[Region],tbl[Region],tbl[Customer],tbl[Customer]))
```

## 3. Dynamic-array workhorses

```excel
=FILTER(array, include, [if_empty])
=FILTER(Sales, (Sales[Region]="EU")*(Sales[Amount]>1000), "no rows")

=SORT(array, [sort_index], [sort_order], [by_col])
=SORTBY(array, by_array1, sort_order1, ...)

=UNIQUE(array, [by_col], [exactly_once])
=UNIQUE(FILTER(tbl[Customer], tbl[Year]=2025))

=SEQUENCE(rows, [cols], [start], [step])    'build a series
=TAKE / DROP / CHOOSEROWS / CHOOSECOLS      'array slicing
=TOCOL / TOROW                              'flatten
=VSTACK / HSTACK                            'concatenate ranges
```

## 4. `LET` and `LAMBDA` (named calc + reusable functions)

```excel
=LET(
    s,  SUM(Sales[Amount]),
    py, SUMIFS(Sales[Amount], Sales[Year], YEAR(TODAY())-1),
    yoy, (s-py)/py,
    yoy)

'reusable Lambda — define once in Name Manager:
PctChange = LAMBDA(curr, prev, IF(prev=0, NA(), (curr-prev)/prev))
=PctChange(C5, C4)
```

## 5. Time intelligence in Excel

Build a **Calendar table** (one row per date, with Year, Quarter, Month, MonthNum, FYWeek, IsWeekday). Then:

```excel
'YTD
=SUMIFS(Sales[Amount], Sales[Date], ">="&DATE(YEAR(TODAY()),1,1), Sales[Date], "<="&TODAY())

'Same-period last year
=SUMIFS(Sales[Amount], Sales[Date], ">="&EDATE(start,-12), Sales[Date], "<="&EDATE(end,-12))

'Rolling 12-month
=SUMIFS(Sales[Amount], Sales[Date], ">="&EDATE(TODAY(),-12), Sales[Date], "<="&TODAY())

'Working days between
=NETWORKDAYS.INTL(start, end, 1, holidays)        '1 = Sat/Sun weekend
```

## 6. Statistics & forecasting

```excel
=AVERAGE / MEDIAN / MODE.SNGL / STDEV.S / VAR.S / PERCENTILE.INC(arr, k)
=QUARTILE.INC(arr, q)
=CORREL(x, y)                                'Pearson r
=SLOPE(y,x) / INTERCEPT(y,x) / RSQ(y,x)      'linear regression
=LINEST(y, x, TRUE, TRUE)                    'multi-coef regression with stats (array)
=TREND(known_y, known_x, new_x)              'linear projection
=GROWTH(known_y, known_x, new_x)             'exponential projection
=FORECAST.ETS(target_date, values, dates, [seasonality], [completion], [aggregation])
=FORECAST.ETS.CONFINT(...)                   'confidence interval
=FORECAST.ETS.SEASONALITY(...)               'auto-detect period
```

## 7. Ranking, top-N, ABC / Pareto

```excel
=RANK.EQ(value, ref, [order])                'with ties = same rank
=RANK.AVG(value, ref, [order])
=LARGE(arr, k) / SMALL(arr, k)

'Top 5 customers by revenue (dynamic array):
=SORTBY(UNIQUE(Sales[Customer]),
        SUMIFS(Sales[Amount], Sales[Customer], UNIQUE(Sales[Customer])), -1)

'ABC classification (A=top 80% of revenue, B=next 15%, C=last 5%):
=LET(
    cust, UNIQUE(Sales[Customer]),
    rev,  SUMIFS(Sales[Amount], Sales[Customer], cust),
    sorted, SORTBY(HSTACK(cust, rev), rev, -1),
    cum,    SCAN(0, INDEX(sorted,,2), LAMBDA(a,b,a+b)),
    total,  SUM(rev),
    class,  IF(cum/total<=0.8,"A",IF(cum/total<=0.95,"B","C")),
    HSTACK(sorted, class))
```

## 8. Cohort & retention (pivot-friendly helper columns)

```excel
'Acquisition month per customer:
=EOMONTH(MINIFS(Sales[Date], Sales[Customer], [@Customer]), 0)

'Months since first purchase (cohort age):
=DATEDIF([@FirstPurchase], [@OrderDate], "m")
```

Then pivot: rows = AcquisitionMonth, columns = CohortAge, values = `DISTINCTCOUNT(Customer)`.

## 9. Variance & budget vs. actual

```excel
=Actual - Budget                              'absolute variance
=IF(Budget=0, NA(), (Actual-Budget)/Budget)   '% variance
=IF(Actual>=Budget, "On Track", "Off Track")  'status
```

For favorable/unfavorable variance on cost vs. revenue lines, sign-flip cost variances:

```excel
=IF(LineType="Revenue", Actual-Budget, Budget-Actual)
```

## 10. RFM (Recency, Frequency, Monetary)

```excel
'per customer:
R = (TODAY() - MAXIFS(Sales[Date], Sales[Customer], cust))     'days since last
F = COUNTIFS(Sales[Customer], cust)                            'order count
M = SUMIFS(Sales[Amount], Sales[Customer], cust)               'spend

'score 1-5 by quintile:
=MATCH(R, PERCENTILE.INC(R_range, {0.2;0.4;0.6;0.8;1}), 1)     'reverse for R
```

RFM segment label = concatenation `R&F&M` (e.g. `555` = champion, `111` = lost).

## 11. Power Query (M) essentials

Power Query is Excel's ETL layer (also identical in Power BI). Prefer it over formulas for transformations.

```m
let
    Source        = Excel.Workbook(File.Contents("C:\sales.xlsx"), null, true),
    Data          = Source{[Item="Sales",Kind="Table"]}[Data],
    Typed         = Table.TransformColumnTypes(Data, {
                        {"Date", type date},
                        {"Amount", Currency.Type},
                        {"Region", type text}}),
    Filtered      = Table.SelectRows(Typed, each [Amount] > 0),
    WithYear      = Table.AddColumn(Filtered, "Year", each Date.Year([Date]), Int64.Type),
    GroupedRegion = Table.Group(WithYear, {"Region","Year"},
                        {{"Sales", each List.Sum([Amount]), Currency.Type},
                         {"Orders", each Table.RowCount(_), Int64.Type}})
in
    GroupedRegion
```

Common patterns:

- **Unpivot wide tables**: `Table.UnpivotOtherColumns(t, {"Date"}, "Metric", "Value")`
- **Merge (join)**: `Table.NestedJoin(left, {"key"}, right, {"key"}, "joined", JoinKind.LeftOuter)`
- **Append**: `Table.Combine({t1, t2})`
- **Conditional column**: `if [Region]="EU" then "Europe" else "Other"`
- **Replace nulls**: `Table.ReplaceValue(t, null, 0, Replacer.ReplaceValue, {"Amount"})`

## 12. PivotTable / `GROUPBY` / `PIVOTBY`

Excel 2024+ adds `GROUPBY` and `PIVOTBY` — full pivot logic in a formula:

```excel
=GROUPBY(Sales[Region], Sales[Amount], SUM, 3, 1, -2)
'                          ↑agg   ↑hdr ↑sort  ↑total

=PIVOTBY(Sales[Region], Sales[Year], Sales[Amount], SUM)
```

For older Excel, build PivotTables and use **GETPIVOTDATA** to reference cells safely:

```excel
=GETPIVOTDATA("Amount", $A$3, "Region", "EU", "Year", 2025)
```

## 13. Conditional formatting for KPI cards

- **3-color scale** on variance %
- **Icon set** (arrow up/flat/down) on YoY change
- **Data bars** on rank list
- Use **formula-based** rules for thresholds: `=B2>=$F$1` highlights cells beating target.

## 14. Common mistakes

| Pitfall                                         | Fix                                                            |
| ----------------------------------------------- | -------------------------------------------------------------- |
| `VLOOKUP` breaks when columns inserted          | Use `XLOOKUP` or `INDEX/MATCH` with named ranges               |
| Volatile `INDIRECT`/`OFFSET` slows file         | Replace with structured table refs or `INDEX`                  |
| `#DIV/0!` in dashboards                         | `IFERROR(x/y, 0)` or guard `IF(y=0, NA(), x/y)`                |
| Dates stored as text                            | `DATEVALUE` or convert via Power Query                         |
| Hard-coded ranges break on new data             | Use Excel **Tables** (`Ctrl+T`) — refs auto-expand             |
| Distinct count via helper column on huge data   | Move to Power Pivot / Power Query, use `DISTINCTCOUNT`         |
