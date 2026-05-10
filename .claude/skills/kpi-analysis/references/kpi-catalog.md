# KPI Catalog — by Domain

Each KPI lists: **definition**, **formula** (plain), **grain**, **target direction**, **common pitfalls**. Cross-tool implementations use the patterns from `excel-formulas.md`, `dax-measures.md`, `tableau-calcs.md`.

---

## SALES

| KPI                          | Formula                                                          | Grain          | Direction |
| ---------------------------- | ---------------------------------------------------------------- | -------------- | --------- |
| Revenue / Sales              | Σ (Qty × Net Unit Price)                                         | order line     | ↑         |
| Gross Sales                  | Σ (Qty × List Price)                                             | order line     | ↑         |
| Net Sales                    | Gross − returns − discounts                                      | order line     | ↑         |
| Average Order Value (AOV)    | Revenue ÷ Orders                                                 | order          | ↑         |
| Units per Transaction (UPT)  | Units ÷ Transactions                                             | transaction    | ↑         |
| Sales per Customer           | Revenue ÷ Distinct Customers                                     | customer       | ↑         |
| Win Rate                     | Won Opps ÷ Closed Opps                                           | opportunity    | ↑         |
| Quota Attainment             | Actual ÷ Quota                                                   | rep · period   | ↑         |
| Sales Cycle Length           | avg(Close Date − Created Date) for won deals                     | opportunity    | ↓         |
| Pipeline Coverage            | Pipeline Value ÷ Quota Remaining                                 | rep · period   | ↑ (≥3×)   |
| YoY Growth %                 | (Current − Prior Year) ÷ Prior Year                              | period         | ↑         |
| Same-store Sales (LFL)       | Revenue from stores open ≥ 12 months, vs. their PY               | store · period | ↑         |

Pitfalls: discounts double-counted; returns posted in different period than the sale; mixing gross and net across reports.

## PROFITABILITY / FINANCE

| KPI                      | Formula                                            | Direction |
| ------------------------ | -------------------------------------------------- | --------- |
| Gross Profit             | Revenue − COGS                                     | ↑         |
| Gross Margin %           | Gross Profit ÷ Revenue                             | ↑         |
| Contribution Margin      | Revenue − Variable Costs                           | ↑         |
| Operating Profit (EBIT)  | Gross Profit − Operating Expenses                  | ↑         |
| Operating Margin %       | EBIT ÷ Revenue                                     | ↑         |
| EBITDA                   | EBIT + D&A                                         | ↑         |
| Net Profit Margin %      | Net Income ÷ Revenue                               | ↑         |
| Break-even Units         | Fixed Cost ÷ (Price − Variable Cost per unit)      | n/a       |
| Cash Conversion Cycle    | DSO + DIO − DPO                                    | ↓         |
| DSO                      | (AR ÷ Revenue) × Days                              | ↓         |
| DIO                      | (Inventory ÷ COGS) × Days                          | ↓         |
| DPO                      | (AP ÷ COGS) × Days                                 | ↑         |
| Working Capital          | Current Assets − Current Liabilities               | ↑         |
| Current Ratio            | Current Assets ÷ Current Liabilities               | ↑ (≥1.5)  |
| Quick Ratio              | (CA − Inventory) ÷ CL                              | ↑ (≥1)    |
| ROA                      | Net Income ÷ Total Assets                          | ↑         |
| ROE                      | Net Income ÷ Equity                                | ↑         |
| ROI                      | (Gain − Cost) ÷ Cost                               | ↑         |
| ROIC                     | NOPAT ÷ Invested Capital                           | ↑         |
| Variance to Budget       | Actual − Budget; sign-flip for cost lines          | =0        |
| Forecast Accuracy        | 1 − |Actual − Forecast| ÷ Actual                   | ↑         |

## CUSTOMER

| KPI                              | Formula                                                                  | Direction |
| -------------------------------- | ------------------------------------------------------------------------ | --------- |
| New Customers                    | distinct customers whose first order is in period                        | ↑         |
| Active Customers                 | distinct customers with ≥1 order in period                               | ↑         |
| Customer Retention Rate          | (E − N) ÷ S, where E = end customers, N = new, S = start                 | ↑         |
| Customer Churn Rate              | Lost ÷ Start                                                             | ↓         |
| Logo Churn (B2B)                 | Lost Accounts ÷ Start Accounts                                           | ↓         |
| Revenue Churn / Gross Revenue Churn | Lost ARR ÷ Start ARR                                                  | ↓         |
| Net Revenue Retention (NRR)      | (Start ARR + Expansion − Contraction − Churn) ÷ Start ARR                | ↑ (>100%) |
| Gross Revenue Retention (GRR)    | (Start ARR − Contraction − Churn) ÷ Start ARR                            | ↑         |
| Customer Lifetime Value (LTV)    | ARPU × Gross Margin × (1 ÷ churn rate)                                   | ↑         |
| Customer Acquisition Cost (CAC)  | S&M Spend ÷ New Customers                                                | ↓         |
| LTV:CAC Ratio                    | LTV ÷ CAC                                                                | ↑ (≥3)    |
| CAC Payback (months)             | CAC ÷ (ARPU × Gross Margin)                                              | ↓ (<12)   |
| Net Promoter Score (NPS)         | %Promoters (9–10) − %Detractors (0–6)                                    | ↑         |
| Customer Satisfaction (CSAT)     | %Satisfied responses                                                     | ↑         |
| Customer Effort Score (CES)      | avg of effort question (1–7)                                             | ↓ effort  |

Pitfalls: cohort vs. snapshot retention (different denominators); CAC excluding salaries; LTV using gross margin instead of contribution.

## MARKETING

| KPI                          | Formula                                            | Direction |
| ---------------------------- | -------------------------------------------------- | --------- |
| Impressions                  | total ad views                                     | ↑         |
| CTR                          | Clicks ÷ Impressions                               | ↑         |
| CPC                          | Spend ÷ Clicks                                     | ↓         |
| CPM                          | (Spend ÷ Impressions) × 1000                       | ↓         |
| CPL                          | Spend ÷ Leads                                      | ↓         |
| MQL → SQL Conversion         | SQLs ÷ MQLs                                        | ↑         |
| Lead → Customer Conversion   | Customers ÷ Leads                                  | ↑         |
| ROAS                         | Attributed Revenue ÷ Ad Spend                      | ↑ (≥4)    |
| MER (Marketing Efficiency)   | Total Revenue ÷ Total Marketing Spend              | ↑         |
| Bounce Rate                  | Single-page sessions ÷ Sessions                    | ↓         |
| Session Duration             | avg time on site                                   | ↑         |
| Pages per Session            | Pageviews ÷ Sessions                               | ↑         |
| Email Open Rate              | Opens ÷ Delivered                                  | ↑         |
| Email Click Rate             | Clicks ÷ Delivered                                 | ↑         |
| Unsubscribe Rate             | Unsubscribes ÷ Delivered                           | ↓         |

## E-COMMERCE / RETAIL

| KPI                          | Formula                                            | Direction |
| ---------------------------- | -------------------------------------------------- | --------- |
| Conversion Rate              | Orders ÷ Sessions                                  | ↑         |
| Cart Abandonment             | (Carts − Orders) ÷ Carts                           | ↓         |
| Average Basket Size          | Units ÷ Transactions                               | ↑         |
| Sell-through %               | Units sold ÷ Units received                        | ↑         |
| GMROI                        | Gross Margin ÷ Avg Inventory Cost                  | ↑         |
| Stock-to-Sales Ratio         | Avg Inventory ÷ Sales                              | ↓         |
| Inventory Turnover           | COGS ÷ Avg Inventory                               | ↑         |
| Days of Supply               | (Inventory ÷ Avg Daily Sales)                      | ↓         |
| Out-of-Stock Rate            | OOS SKU-days ÷ Total SKU-days                      | ↓         |
| Sales per sq ft              | Revenue ÷ Selling Area                             | ↑         |
| Foot Traffic Conversion      | Transactions ÷ Visitors                            | ↑         |
| Return Rate                  | Returned Units ÷ Sold Units                        | ↓         |

## SUPPLY CHAIN / OPERATIONS

| KPI                          | Formula                                                   | Direction |
| ---------------------------- | --------------------------------------------------------- | --------- |
| OTIF (On-Time In-Full)       | Orders shipped on time and complete ÷ Total orders        | ↑         |
| Order Fill Rate              | Units shipped ÷ Units ordered                             | ↑         |
| Perfect Order Rate           | Orders OTIF AND undamaged AND correctly invoiced ÷ Total  | ↑         |
| Lead Time                    | Order Date → Delivery Date                                | ↓         |
| Forecast Accuracy (MAPE)     | mean( |A−F| ÷ A )                                         | ↓         |
| Inventory Accuracy           | Counted ÷ System                                          | ↑ (>99%)  |
| Backorder Rate               | Backordered Units ÷ Ordered                               | ↓         |
| Carrying Cost of Inventory   | (Storage + Capital + Risk) ÷ Avg Inventory Value          | ↓         |
| Defect Rate / DPMO           | Defects ÷ Opportunities × 1,000,000                       | ↓         |
| OEE                          | Availability × Performance × Quality                      | ↑ (>85%)  |
| MTBF                         | Operating Hours ÷ Failures                                | ↑         |
| MTTR                         | Repair Hours ÷ Failures                                   | ↓         |

## HR / PEOPLE

| KPI                          | Formula                                                   | Direction |
| ---------------------------- | --------------------------------------------------------- | --------- |
| Headcount                    | active employees at period end                            | =plan     |
| Voluntary Turnover %         | Voluntary Leavers ÷ Avg Headcount (annualized)            | ↓         |
| Regrettable Turnover %       | High-performer leavers ÷ Avg High Performers              | ↓         |
| Time to Hire                 | days from req open → offer accepted                       | ↓         |
| Cost per Hire                | Recruiting Spend ÷ Hires                                  | ↓         |
| Offer Acceptance Rate        | Accepted ÷ Offered                                        | ↑         |
| Internal Mobility Rate       | Internal moves ÷ Headcount                                | ↑         |
| Engagement Score             | survey composite (0–100)                                  | ↑         |
| eNPS                         | %Promoters − %Detractors                                  | ↑         |
| Absenteeism Rate             | Absent days ÷ Working days                                | ↓         |
| Diversity Ratio              | Underrepresented HC ÷ Total HC                            | =plan     |
| Span of Control              | Direct reports ÷ Manager                                  | ≈8        |
| Revenue per FTE              | Revenue ÷ FTE                                             | ↑         |

## SAAS

| KPI                          | Formula                                                      | Direction |
| ---------------------------- | ------------------------------------------------------------ | --------- |
| MRR                          | Σ active recurring monthly revenue                           | ↑         |
| ARR                          | MRR × 12                                                     | ↑         |
| New MRR                      | MRR from new logos in month                                  | ↑         |
| Expansion MRR                | MRR added from existing customers (upsell/seats)             | ↑         |
| Contraction MRR              | MRR lost from downgrades                                     | ↓         |
| Churned MRR                  | MRR lost from cancellations                                  | ↓         |
| Net New MRR                  | New + Expansion − Contraction − Churn                        | ↑         |
| Quick Ratio                  | (New + Expansion) ÷ (Contraction + Churn)                    | ↑ (>4)    |
| Magic Number                 | (Net New ARR × 4) ÷ Sales & Marketing Spend prior quarter    | ↑ (>1)    |
| Rule of 40                   | Growth % + EBITDA Margin %                                   | ↑ (≥40)   |
| Burn Multiple                | Net Burn ÷ Net New ARR                                       | ↓ (<1)    |
| Activation Rate              | users hitting "aha" event ÷ signups                          | ↑         |
| DAU / MAU                    | DAU ÷ MAU                                                    | ↑         |

## PROJECT / EXECUTION

| KPI                          | Formula                                                      |
| ---------------------------- | ------------------------------------------------------------ |
| Schedule Variance (SV)       | Earned Value − Planned Value                                 |
| Cost Variance (CV)           | Earned Value − Actual Cost                                   |
| Schedule Performance Index   | EV ÷ PV (≥1 = ahead)                                         |
| Cost Performance Index       | EV ÷ AC (≥1 = under budget)                                  |
| Estimate at Completion (EAC) | BAC ÷ CPI                                                    |

## How to choose a KPI

1. Tie to a **decision**: who acts on this number, and what changes if it moves?
2. Pair **leading + lagging**: sales = lagging; pipeline coverage = leading.
3. Define **target + threshold**: "good", "watch", "alert".
4. Specify **grain & period**: a KPI without a period is meaningless.
5. Avoid vanity metrics (raw page views, total followers) unless tied to a downstream business outcome.
