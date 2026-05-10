# Dashboard Design — Maven Analytics-style Patterns

Reference patterns based on the Maven Analytics dashboard playbook (used in their Power BI / Tableau bootcamps and "Maven Showcase" entries). Equally applicable to Power BI, Tableau, and Excel dashboards.

## 1. The 5-second test

A user should be able to answer "what story is this dashboard telling?" in 5 seconds. If not, you have too many visuals, weak hierarchy, or no clear headline metric.

## 2. Layout — "F" pattern with a banner

```
┌─────────────────────────────────────────────────────────┐
│ TITLE  /  Subtitle (period · scope)         [filters]   │  ← page header
├─────────────────────────────────────────────────────────┤
│  KPI 1   │   KPI 2   │   KPI 3   │   KPI 4   │  KPI 5   │  ← KPI banner
├──────────┴───────────┴───────────┴───────────┴──────────┤
│                                                         │
│   PRIMARY VISUAL (trend / time series)                  │  ← anchor (largest)
│                                                         │
├─────────────────────────────────┬───────────────────────┤
│   CATEGORY BREAKDOWN            │   GEO / SECONDARY     │
│   (bar chart, ranked desc)      │   (map / matrix)      │
├─────────────────────────────────┼───────────────────────┤
│   DETAIL TABLE w/ in-cell bars                          │  ← drill / proof
└─────────────────────────────────────────────────────────┘
```

- 1 anchor visual, 2-3 supporting visuals, 1 detail table.
- Reading flow: top-left → right (KPIs), down to anchor, then breakdown.
- Use a **15-16 column grid** in Power BI (default desktop is 1280×720) or Tableau's tiled containers.

## 3. KPI cards (the banner)

Each card shows:
- **Big number** (current period value)
- **vs. PY/Target** (Δ in % with up/down arrow + color)
- **Sparkline** of last 12 periods (optional but powerful)
- **Plain-English label** (avoid acronyms; "Avg Order Value" not "AOV")

Power BI: use the **New Card** visual (multi-row card replacement) or build a card with measures + conditional formatting.

DAX for the variance label:

```DAX
KPI Headline =
VAR _val  = [Sales]
VAR _yoy  = [Sales YoY %]
VAR _arrow = SWITCH ( TRUE (), _yoy > 0, "▲", _yoy < 0, "▼", "▬" )
RETURN
    FORMAT ( _val, "$#,0,K" ) & UNICHAR(10) &
    _arrow & " " & FORMAT ( _yoy, "0.0%" ) & " vs PY"
```

## 4. Color system

Pick **one accent**, **one warning**, **one success**. Everything else is neutral grey. Maven dashboards typically use:

| Role        | Hex         | Use                                       |
| ----------- | ----------- | ----------------------------------------- |
| Background  | `#F5F5F5`   | page background                           |
| Card        | `#FFFFFF`   | KPI tiles, visual containers              |
| Primary     | `#1F4E79`   | main bars, primary line                   |
| Accent      | `#2E75B6`   | comparison series                         |
| Success     | `#2CA02C`   | favorable variance, on-target             |
| Warning     | `#D62728`   | unfavorable variance, off-target          |
| Neutral 1   | `#595959`   | titles, axis labels                       |
| Neutral 2   | `#A6A6A6`   | gridlines, secondary text                 |

Rules:

- **Categorical**: use ColorBrewer "Set2" or Tableau 10 — never the rainbow.
- **Sequential** (low→high single hue): "Blues", "YlOrRd".
- **Diverging** (variance, +/−): "RdBu" — red for unfavorable, blue/green for favorable.
- Color **only data** — chrome and labels stay neutral.
- 1 accent color max per dashboard. Highlights stand out only when 95% of the page is grey.

## 5. Typography

- Single font family. **Segoe UI** (Power BI native) or **Source Sans 3** / **Inter** (Tableau, Maven).
- Size ladder: 28pt (page title) · 18pt (visual title) · 14pt (axis/legend) · 11pt (body) · 9pt (footnote).
- Bold only for: page title, KPI numbers, and the answer to the question being asked.
- Left-align text; right-align numbers (so decimals line up).

## 6. Chart selection — choose by question

| Question                        | Recommended chart                                        | Avoid              |
| ------------------------------- | -------------------------------------------------------- | ------------------ |
| Trend over time                 | line / area                                              | bar w/ time on X   |
| Compare categories              | horizontal bar (sorted desc)                             | pie, 3D            |
| Part-to-whole (≤5 parts)        | 100% stacked bar / treemap                               | pie chart          |
| Distribution                    | histogram, box plot, violin                              | sorted bar         |
| Correlation                     | scatter w/ trendline                                     | dual-axis line     |
| Geo                             | filled (choropleth) or bubble map                        | symbol-on-map mess |
| Two metrics, same scale         | grouped bar                                              | dual axis          |
| Two metrics, different scales   | scatter or two charts side by side                       | dual axis          |
| Variance to budget              | bullet chart, lollipop, deviation bar                    | gauge              |
| Funnel / pipeline               | funnel, stage bar                                        | nested pie         |
| Cohort retention                | heatmap matrix                                           | line spaghetti     |
| Single number with context      | KPI card w/ sparkline + Δ                                | gauge              |

Hard bans: **gauges, donuts > 4 slices, 3D charts, dual axes that don't share zero**.

## 7. Whitespace & alignment

- 16-24px padding inside every visual container.
- 8-16px gap between visuals.
- Align edges to a 4-column or 12-column grid; never let visuals "almost line up".
- Page margins ≥ 24px so visuals don't feel cramped.

## 8. Annotation & narrative

A great dashboard *narrates*. Add:

- **Subtitle** under the page title summarizing the headline ("Q1 sales up 12% vs PY, driven by EU expansion").
- **Callouts** on the trend chart for events (product launches, price changes).
- **Reference lines** for target/budget.
- **Tooltip** with extra context (Power BI: page tooltips; Tableau: viz-in-tooltip).

## 9. Filters / slicers

- Place at top-right of the page, in a thin filter bar.
- Group: Date → Region → Segment → Product (most general → most specific).
- Use **dropdown** (not list) for slicers with > 6 values.
- Provide a **Reset filters** bookmark/button.
- Show selected values in the page subtitle so a screenshot/PDF makes sense out of context.

## 10. Power BI specifics

- Page size: **16:9 1920×1080** for full dashboards, **letter** for paginated reports.
- Use **page background** (Format → Canvas → Image) with a pre-rendered Figma layout for the Maven look.
- Bookmarks → buttons for view-switcher (e.g., "By Region" / "By Product").
- Drill-through pages for detail.
- Personalize visuals + Field parameters for analyst self-service.
- Theme JSON: define the color palette + font + visual defaults once, apply globally.

```json
{
  "name": "Maven-style",
  "dataColors": ["#1F4E79","#2E75B6","#9DC3E6","#2CA02C","#D62728","#F1C40F"],
  "background": "#F5F5F5",
  "foreground": "#1F4E79",
  "tableAccent": "#2E75B6",
  "textClasses": {
    "title":  { "fontFace": "Segoe UI", "fontSize": 18, "color": "#1F4E79" },
    "header": { "fontFace": "Segoe UI Semibold", "fontSize": 11, "color": "#595959" },
    "label":  { "fontFace": "Segoe UI", "fontSize": 10, "color": "#595959" }
  }
}
```

## 11. Tableau specifics

- Use **Tiled** layout for fixed dashboards; **Floating** only for layered design (logo, callout cards).
- Set fixed **dashboard size** (1300×800 desktop) with **device-specific** layouts for tablet/phone.
- Use **layout containers** (horizontal/vertical) so resizing remains aligned.
- **Show/Hide** containers via a button for clean drill-down panels.
- Hover actions to highlight, filter actions to drill, parameter actions to swap measures.

## 12. Excel dashboard specifics

- One sheet for **dashboard**, one for **data model** (Tables / Power Pivot), one for **parameters**.
- Hide gridlines and headings on the dashboard sheet.
- Use **Slicers** (PivotTable-driven) for filters; sync slicers across pivots via Report Connections.
- KPI cards = merged cells with formula refs + cell styles; or use Linked Pictures to mirror a hidden formula range with conditional formatting.
- Charts: clean axis (no minor ticks), remove gridlines, hide chart title (use cell-level title instead).

## 13. Accessibility

- Color contrast: text ≥ 4.5:1 against background.
- Don't rely on color alone — use shape (▲▼) or labels for variance.
- Provide alt text on visuals (Power BI) / titles + captions (Tableau).
- Avoid red/green pairs without secondary cue (color-blind safe alt: red/blue).

## 14. The Maven dashboard checklist

Before publishing, check every box:

- [ ] Title says **what** the dashboard shows + **for what period**
- [ ] KPI banner has Δ vs. comparison (PY / Budget / Target) for every card
- [ ] Anchor visual is a **trend** or the **most-asked question**
- [ ] All visuals share consistent color, font, padding
- [ ] No charts banned in §6
- [ ] Filter state is visible in the subtitle
- [ ] Hover tooltip adds context (not just the number already shown)
- [ ] Variances use diverging colors (red/green or red/blue) with sign
- [ ] Numbers formatted: thousands "K", millions "M", percent with 1 decimal
- [ ] Decimals line up (right-align all numeric columns)
- [ ] Refresh date / data source noted in footer
- [ ] Tested with the longest dimension value (no clipping)
- [ ] Tested with empty filter result (no broken visuals)
- [ ] Loads in < 5 seconds on the target device
