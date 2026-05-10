# Data Modeling for Analytics

The model determines what's *possible*, *correct*, and *fast*. Get it right before writing a single measure.

## 1. Star schema — the default

```
        ┌────────────┐
        │  DimDate   │
        └────┬───────┘
             │
┌────────────┴─────────────┐
│         FactSales        │
│  (one row per orderline) │
└──┬────────────┬──────────┘
   │            │
┌──┴──────┐  ┌──┴────────┐
│DimCust. │  │DimProduct │
└─────────┘  └───────────┘
```

- **Fact** tables hold measurements (events, transactions). Numeric, additive when possible. Long & narrow.
- **Dim** tables hold attributes (who, what, where, when, why). Short & wide.
- Relationships: Dim → Fact, **single direction**, **one-to-many**.
- Avoid snowflakes (Dim → Dim). Flatten dim hierarchies into the dim itself.

This is the optimal shape for **VertiPaq** (Power BI) and **Tableau extracts** alike.

## 2. Grain — name it explicitly

Every fact table has exactly one grain. Examples:

| Fact                 | Grain                                  | Typical measures              |
| -------------------- | -------------------------------------- | ----------------------------- |
| FactSales            | one row per order line                 | Qty, Net Sales, Cost          |
| FactInvoiceHeader    | one row per invoice                    | Invoice Total, Tax            |
| FactInventorySnap    | one row per SKU·Store·Day              | OnHand, Reserved              |
| FactBudget           | one row per Account·Cost Center·Month  | Budget Amount                 |
| FactWeb              | one row per session event              | Pageviews, Duration           |

Mixing grains in one fact table is the #1 cause of wrong totals. Build separate fact tables and connect via **conformed dimensions**.

## 3. The Date dimension (always required)

```sql
DimDate
├── DateKey         (yyyymmdd int)
├── Date            (date)
├── Year, Quarter, MonthNum, MonthName
├── YearMonth       ("2025-03")
├── WeekOfYear      (ISO)
├── DayOfWeek, DayName
├── IsWeekday, IsHoliday
├── FiscalYear, FiscalQuarter, FiscalPeriod
├── YearOffset      (current=0, prior=-1, …)
└── MonthOffset     (current month=0, prior=-1, …)
```

Rules:
- **Contiguous** dates from earliest fact to latest + 1 year buffer (forecasts).
- **One row per day**, no gaps.
- Mark as date table (Power BI: "Mark as date table"; Tableau: filter dim has Date type).
- Offsets enable easy "current vs. prior" without time-intelligence functions for non-Gregorian calendars.

## 4. Slowly Changing Dimensions (SCD)

| Type   | Behavior                                                                | Use                                       |
| ------ | ----------------------------------------------------------------------- | ----------------------------------------- |
| SCD 0  | Never change                                                            | birth date, original ID                   |
| SCD 1  | Overwrite                                                               | most attributes, when history irrelevant  |
| SCD 2  | Add a new row with effective dates and current flag                     | sales territory, employee dept, pricing   |
| SCD 3  | Add a "previous" column                                                 | rare; only one prior version              |
| SCD 6  | Combination (1+2+3)                                                     | when both stable + history needed         |

SCD 2 schema:

```
DimEmployee
├── EmployeeSK   (surrogate key — used in joins)
├── EmployeeID   (business key)
├── Name, Dept, Manager, ...
├── EffectiveFrom
├── EffectiveTo
└── IsCurrent    (boolean)
```

The fact table joins to the SCD2 dim by **surrogate key**, capturing the attribute value as of the transaction date.

## 5. Role-playing dimensions

The same dim used in multiple roles (e.g. Date as OrderDate, ShipDate, DueDate).

- Power BI: load `DimDate` once, create active relationship to `OrderDate`, inactive to `ShipDate`/`DueDate`. Activate via `USERELATIONSHIP` in measures.
- Tableau: create date calc fields (or use field aliases) — Tableau doesn't have explicit role-play.
- SQL/BI tool agnostic: load `DimDate` multiple times with different aliases (`DimOrderDate`, `DimShipDate`).

## 6. Many-to-many & bridge tables

Avoid bidirectional relationships when possible. If a customer can be in multiple segments:

```
DimCustomer ── BridgeCustSegment ── DimSegment
```

Bridge table is a fact table with no measures, just keys. Filter from `DimSegment` flows through bridge to `DimCustomer` via `CROSSFILTER` (DAX) or row-level security context.

## 7. Star schema beats wide flat tables

| Concern             | Star schema           | One big flat table            |
| ------------------- | --------------------- | ----------------------------- |
| File size (extract) | small (dim text once) | bloated (dim text per fact)   |
| Query speed         | fast (VertiPaq joins) | depends; cardinality balloon  |
| Maintenance         | dims reused           | duplicated logic              |
| Multi-fact          | conformed dims work   | needs UNION + nulls           |

## 8. Surrogate keys

Use integer surrogate keys (`CustomerSK`) on facts, not business keys. Reasons:

- Stable across SCD changes
- Compresses better than strings (especially in VertiPaq)
- Faster joins
- Decouples warehouse from source systems

## 9. Calendar / fiscal calendar handling

For non-standard calendars (4-4-5, broadcast, retail 52/53):

- Store `WeekKey`, `PeriodKey`, `QuarterKey`, `YearKey` columns
- Store `WeekStart`, `WeekEnd` dates for tooltips
- Time-intelligence functions in DAX assume Gregorian — replace with offset-based filtering:

```DAX
Sales PY (Fiscal) =
VAR _curYearOffset = SELECTEDVALUE ( 'Date'[YearOffset] )
RETURN
    CALCULATE ( [Sales],
        ALL ( 'Date' ),
        'Date'[YearOffset] = _curYearOffset - 1 )
```

## 10. Snapshot vs. transactional facts

Inventory, headcount, AR balance, ARR — these are **semi-additive**: you sum across regions but pick the **last** value over time.

```DAX
Closing Inventory =
CALCULATE (
    SUM ( Inventory[OnHand] ),
    LASTNONBLANKVALUE ( 'Date'[Date], CALCULATE ( COUNTROWS ( Inventory ) ) )
)
```

Tableau:

```tableau
{ FIXED [Region],[SKU] : SUM(IIF([Date]={MAX([Date])}, [OnHand], 0)) }
```

Excel:

```excel
=SUMPRODUCT( (Inventory[Date]=MAX(Inventory[Date])) * Inventory[OnHand] )
```

## 11. Aggregation tables (composite models)

For massive facts (>100M rows), build pre-aggregated tables (`AggSales_Monthly`) at the queried grain. Power BI's **aggregations** feature redirects queries automatically when the grain matches.

## 12. Storage modes (Power BI)

| Mode        | When                                                          |
| ----------- | ------------------------------------------------------------- |
| Import      | Default. Fastest. Compressed in VertiPaq.                     |
| DirectQuery | Real-time, large data, source-of-truth in DB                  |
| Dual        | Used for dim tables in composite models with aggs             |
| Live        | Connected to Analysis Services / Power BI dataset             |

## 13. Naming conventions

- Tables: `Fact*`, `Dim*` (or just plural nouns, but consistent).
- Measures: noun phrases (`Sales`, `Avg Order Value`), variants suffixed (`Sales PY`, `Sales YoY %`).
- Hidden columns: prefix with `_` (e.g. `_SortOrder`, `_DateKey`).
- Dimension keys end with `Key` or `SK`/`BK` (surrogate / business).

## 14. Validation routine

After every model change, verify:

1. **Grand totals** match source-of-truth report (TB, ERP).
2. **Subtotals** roll up to grand total — no orphan rows.
3. **Distinct counts** at row, dim, and total levels are correct.
4. **Time intelligence** YoY at full prior year matches; partial period guard works.
5. **Filter propagation** — pick a slicer value on every dim and confirm fact responds.
6. **Empty result** — apply a filter that yields zero rows; charts shouldn't error.
7. **Performance** — render a worst-case page in < 5 seconds.

## 15. Anti-patterns — avoid

| Anti-pattern                            | Why it hurts                                     |
| --------------------------------------- | ------------------------------------------------ |
| Bidirectional relationships everywhere  | ambiguous filter paths, perf hit                 |
| One big "flat" table from a SQL view    | breaks compression, blocks reuse                 |
| Calculated columns instead of measures  | bloats model, can't react to slicers correctly   |
| Date column on fact instead of DimDate  | no time intelligence, missing dates              |
| Many-to-many without bridge             | unpredictable results                            |
| Mixing grains in one fact               | wrong totals at every level                      |
| Auto date/time (Power BI default)       | hidden tables per date col, model bloat          |
| Inactive relationships used as fallback | silent miscalculation if `USERELATIONSHIP` missed|
